#!/usr/bin/env python3
"""hermes/bridge_state.py — v0.2（Stage 2.4d-1）

Stage 2 session bridge 的處理狀態（bookkeeping）SQLite 存取層。
格式契約正本：registry/bridge_state_schema.yaml（claudecodeos.bridge_state.v2，
bridge_sessions 22 欄＋bridge_cursors 5 欄）；設計說明：
docs/memory-bridge-state.md、docs/stage2.4d-episode-capture-proposal.md
（已核准提案＝episode capture 的規格正本）。

這個 DB 是什麼、不是什麼（硬邊界，全部沿用既有決策）：

- **WSL 部署側的本機 runtime state**。預設路徑 `hermes/state/bridge_state.db`——
  `hermes/state/` 已在 .gitignore 與部署同步排除清單（deployment-sync-plan.md §2
  第 3 類），所以這個檔案**不進版控、不被同步**，只存在 WSL 部署側；
  Windows 開發側只有這份程式碼，不該出現實際的 db 檔。
- **可拋棄（disposable）**：它只是管線簿記，不是任何資料的正本。整個檔案刪掉後，
  可由 Hermes state.db（session 事件來源，經 read-only session_adapter）＋
  memory/inbox/ 與其 .processed/.failed 目錄（inbox 檔案狀態的唯一真相）
  重新 discover/rebuild。
- **絕不寫回 Hermes state.db**：本模組唯一的 SQLite 連線入口（get_connection）
  只開 bridge 自己的 db 檔（預設或呼叫端注入的路徑），沒有任何 code path 會開啟
  Hermes 的資料庫。
- **不是 Hermes 的 memory DB、也不是第二份 Hermes state.db**：只記 ClaudeCodeOS
  側「這個 session 處理到哪」的狀態，用途是去重（UNIQUE(event_id)）與可追蹤性。

語義備忘（與其他元件劃清界線）：

- `import_status` 用 schema enum（discovered/skipped/to_inbox/imported/failed/
  needs_review）——enum 值**不在本模組寫死第二份**，一律從 registry yaml 讀取驗證。
- `retry_count` 只代表 **bridge 層級**對同一 session 的匯入/discovery 重新嘗試次數
  （例如前次 failed 後重跑，重跑開始時呼叫 increment_retry_count()）。
  與 hermes/db.py jobs.attempts（job 執行重試，claim 時 +1、達 max_attempts 進
  dead_letter）完全不同層、兩者不互通。mark_failed() 不動 retry_count。
- `error_reason` 只記 bridge 層錯誤摘要，**不得含 session 敏感內容**
  （schema description 的既有約束）。

除了 bridge_sessions（22 欄），另有：

- bridge_meta（key-value；Stage 2.4a）：scanner 的 scan_watermark——最近一次
  真實 scan 的窗口上界，**只前進不後退**（get_scan_watermark /
  advance_scan_watermark），同樣是可拋棄的部署側狀態：db 重建後 watermark
  消失，scanner 退回 hermes/config/bridge.yaml 的 cutover 底線重掃，
  event_id 去重保證無害。
- bridge_cursors（Stage 2.4d）：per-session episode 游標（複合主鍵
  (source_profile, session_id)），純簿記、**沒有狀態機**（session「處理到哪」
  的答案是它的 episode 列集合，提案 §4.2）。cursor 只前進不後退。
  可拋棄語義的誠實界定（提案 §1.2）：db 重建後**必要前置**是 reconcile 的
  recovery 流程（§3.2，從 inbox 目錄真相重建 cursor）再掃——「可拋棄」＝
  db 消失不損失任何可重建資訊，不是「消失後不需要 recovery 步驟」。

Stage 2.4d 的核心不變量（提案 §1.2、風險 3——**不得被未來改動拆開**）：
create_episode() 的「episode 列建立＋cursor 前進」在**同一個 SQLite
transaction** 內完成，要嘛都成立、要嘛都不成立——這是「每則訊息至多屬於
一個 episode、cursor 永不回退」的機械保證。撞既有 event_id（同 boundary
已存在）時走冪等路徑：不動既有列、只把 cursor 推進到該 boundary 的 last。

CLI（供 WSL 部署側手動初始化/檢視/migration 用；Windows 開發側不要對預設路徑執行 init/migrate）：
    python3 hermes/bridge_state.py init [--db-path PATH]
    python3 hermes/bridge_state.py show <event_id> [--db-path PATH]
    python3 hermes/bridge_state.py list [--import-status X] [--db-path PATH]
    python3 hermes/bridge_state.py watermark [--db-path PATH]
    python3 hermes/bridge_state.py migrate [--db-path PATH]   # v1→v2（冪等，提案 §3.1）
"""
import argparse
import contextlib
import hashlib
import json
import re
import sqlite3
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 預設路徑常數——實際檔案只該存在 WSL 部署側（見模組 docstring）。
# 所有 API 都接受 db_path 注入自訂路徑（測試一律注入 temp 路徑）。
DEFAULT_DB_PATH = ROOT / "hermes" / "state" / "bridge_state.db"
SCHEMA_PATH = ROOT / "registry" / "bridge_state_schema.yaml"

TABLE_NAME = "bridge_sessions"

# registry yaml type → SQLite 型別對映（int→INTEGER、bool→INTEGER(0/1)、其餘 TEXT）。
# 測試用這份對映做「CREATE TABLE 與 registry yaml 的程式化對齊比對」。
SQL_TYPE_BY_SCHEMA_TYPE = {
    "string": "TEXT",
    "enum": "TEXT",
    "bool": "INTEGER",
    "int": "INTEGER",
}

# 22 欄與 registry/bridge_state_schema.yaml 一一對應（順序照 yaml）；
# required → NOT NULL；event_id 做 UNIQUE（去重 key：session 層級
# "hermes:<session_id>"（legacy）或 episode 層級 "hermes:<sid>:<first>..<last>"）。
# enum 的合法值不寫進 DDL（SQLite 沒有原生 enum，也避免寫死第二份）——
# 由 upsert/list/create_episode 從 yaml 讀取驗證。五個 episode 欄的條件必填
# （episode 列全必填、legacy 列 NULL）同樣不進 DDL，由 repository 驗證
# （比照 error_reason／imported_inbox_path 慣例）。
CREATE_TABLE_SQL = f"""
    CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
        session_id                TEXT    NOT NULL,
        source_profile            TEXT    NOT NULL,
        session_source            TEXT    NOT NULL,
        import_status             TEXT    NOT NULL,
        memory_type               TEXT    NOT NULL,
        useful_chat               INTEGER NOT NULL,
        selected_capability_lane  TEXT,
        decision_reason           TEXT    NOT NULL,
        imported_inbox_path       TEXT,
        processed_path            TEXT,
        first_seen_at             TEXT    NOT NULL,
        last_seen_at              TEXT    NOT NULL,
        updated_at                TEXT    NOT NULL,
        retry_count               INTEGER NOT NULL DEFAULT 0,
        error_reason              TEXT,
        event_id                  TEXT    NOT NULL UNIQUE,
        event_id_range            TEXT,
        episode_seq               INTEGER,
        capture_trigger           TEXT,
        first_message_id          INTEGER,
        last_message_id           INTEGER,
        source_content_hash       TEXT
    )
"""

# v1（17 欄）→ v2 的加欄清單：ensure_schema／migrate 對既有 db 冪等補欄
# （先查 PRAGMA table_info，已存在則跳過——比照 bridge_meta 的升級路徑先例）。
EPISODE_COLUMNS = (
    ("episode_seq", "INTEGER"),
    ("capture_trigger", "TEXT"),
    ("first_message_id", "INTEGER"),
    ("last_message_id", "INTEGER"),
    ("source_content_hash", "TEXT"),
)

# bridge scanner 的 runtime 中繼資料（key-value；Stage 2.4a）。目前唯一的 key
# 是 scan_watermark：最近一次「真實」（非 dry-run）scan 成功完成時的掃描窗口
# 上界（見 advance_scan_watermark docstring）。與 bridge_sessions 同屬部署側
# 可拋棄狀態：db 重建後 watermark 消失，scanner 退回 config cutover
# （hermes/config/bridge.yaml）底線重掃，event_id 去重保證重掃無害。
META_TABLE_NAME = "bridge_meta"
SCAN_WATERMARK_KEY = "scan_watermark"

CREATE_META_TABLE_SQL = f"""
    CREATE TABLE IF NOT EXISTS {META_TABLE_NAME} (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
"""

# per-session episode 游標（Stage 2.4d，提案 §1.2）：純簿記、無狀態機。
# 複合主鍵 (source_profile, session_id)——不同 profile 的同名 session
# 結構性不可能共用 cursor（提案 §6.1）。
CURSOR_TABLE_NAME = "bridge_cursors"

CREATE_CURSOR_TABLE_SQL = f"""
    CREATE TABLE IF NOT EXISTS {CURSOR_TABLE_NAME} (
        source_profile            TEXT    NOT NULL,
        session_id                TEXT    NOT NULL,
        last_captured_message_id  INTEGER NOT NULL,
        last_episode_seq          INTEGER NOT NULL,
        updated_at                TEXT    NOT NULL,
        PRIMARY KEY (source_profile, session_id)
    )
"""

# 本階段唯一支援的 profile（提案 §6.1 fail-closed 邊界）：現行 event_id
# namespace 無 profile 段，非 default 的 episode 會寫出與 default 無法區分的
# event_id（namespace 污染），create_episode 一律拒絕。未來擴充格式已預留：
# "hermes/<profile>:..."——本階段讀到這個前綴同樣一律拒絕（parse_event_id）。
DEFAULT_SOURCE_PROFILE = "default"
PROFILE_NAMESPACE_PREFIX = "hermes/"

# episode 內容雜湊（提案 §4.5）的 normalized 欄位——固定清單、與 render
# 格式解耦（render 改版不會假性 mismatch）。
EPISODE_HASH_FIELDS = ("rowid", "role", "type", "content", "tool_calls", "timestamp")

_lock = threading.Lock()
_schema_doc_cache: dict | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_iso_utc(value: str) -> datetime:
    """ISO 8601 → aware UTC datetime。接受 'Z' 結尾；naive 視為 UTC。"""
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"無法解析 ISO 8601 時間：{value!r}（{exc}）") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def session_event_id(session_id: str) -> str:
    """session 層級去重 key（legacy，pre-2.4d），沿用 adapter 的 event_id 慣例：
    "hermes:<session_id>"。2.4d 起不再新增這個層級的記錄，但既有列原樣保留。"""
    return f"hermes:{session_id}"


def _validate_boundary(first_message_id, last_message_id) -> tuple[int, int]:
    """episode boundary 驗證：兩者都是正整數（Hermes rowid）且 first <= last。"""
    for name, value in (("first_message_id", first_message_id),
                        ("last_message_id", last_message_id)):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{name} 必須是 int（Hermes rowid），得到 {value!r}")
        if value < 1:
            raise ValueError(f"{name} 必須 >= 1（rowid 從 1 起算），得到 {value}")
    if first_message_id > last_message_id:
        raise ValueError(
            f"boundary 不合法：first_message_id={first_message_id} > "
            f"last_message_id={last_message_id}")
    return first_message_id, last_message_id


def episode_event_id(session_id: str, first_message_id: int, last_message_id: int) -> str:
    """episode 層級去重 key："hermes:<sid>:<first>..<last>"（提案 §2）。

    boundary 是穩定值：first/last 在 create_episode 當下固定、之後 immutable
    ——這滿足「event_id 必含穩定 episode boundary」的拍板公理。格式與既有
    event_id_range 完全相同。
    """
    first, last = _validate_boundary(first_message_id, last_message_id)
    if not session_id or ":" in str(session_id) or "/" in str(session_id):
        raise ValueError(f"session_id 不合法（不得含 ':' 或 '/'）：{session_id!r}")
    return f"hermes:{session_id}:{first}..{last}"


_EPISODE_BOUNDS_RE = re.compile(r"^(\d+)\.\.(\d+)$")
_MESSAGE_ROWID_RE = re.compile(r"^\d+$")


def parse_event_id(event_id: str) -> dict:
    """三層 event_id namespace 的機械判別（提案 §2）。

    回傳 dict：
    - session（legacy）："hermes:<sid>"        → {"kind": "session", "session_id"}
    - 訊息層級："hermes:<sid>:<rowid>"          → {"kind": "message", "session_id", "rowid"}
    - episode："hermes:<sid>:<first>..<last>"   → {"kind": "episode", "session_id",
                                                   "first_message_id", "last_message_id"}

    區分規則（照提案逐字實作）：含 ".." ＝ episode；含 ":" 但無 ".." 且冒號後
    是整數 ＝ 訊息；其餘 ＝ session 層級（Hermes sid 不含冒號，無歧義）。

    fail-closed（提案 §6.1）：讀到 "hermes/" 前綴（未來 profile namespace，
    本階段不啟用）或非 "hermes:" 開頭的值一律丟 ValueError，**不得默默視為
    default**；含 ".." 但格式不合法（非整數、first > last）同樣拒絕。
    """
    if not isinstance(event_id, str) or not event_id:
        raise ValueError(f"event_id 必須是非空字串，得到 {event_id!r}")
    if event_id.startswith(PROFILE_NAMESPACE_PREFIX):
        raise ValueError(
            f"event_id {event_id!r} 帶 profile namespace 前綴（'hermes/'）——"
            "本階段僅支援 default profile，一律 fail-closed 拒絕處理"
            "（提案 §2、§6.1），不得默默視為 default")
    if not event_id.startswith("hermes:"):
        raise ValueError(f"未知的 event_id namespace：{event_id!r}（僅支援 'hermes:' 前綴）")
    rest = event_id[len("hermes:"):]
    if not rest:
        raise ValueError(f"event_id 缺 session_id 段：{event_id!r}")
    if ".." in rest:
        sid, sep, bounds = rest.rpartition(":")
        match = _EPISODE_BOUNDS_RE.match(bounds) if sep else None
        if not sid or match is None:
            raise ValueError(
                f"episode event_id 格式不合法：{event_id!r}"
                "（應為 hermes:<sid>:<first>..<last>，first/last 為整數）")
        first, last = int(match.group(1)), int(match.group(2))
        _validate_boundary(first, last)
        return {"kind": "episode", "session_id": sid,
                "first_message_id": first, "last_message_id": last}
    sid, sep, tail = rest.rpartition(":")
    if sep and sid and _MESSAGE_ROWID_RE.match(tail):
        return {"kind": "message", "session_id": sid, "rowid": int(tail)}
    return {"kind": "session", "session_id": rest}


def is_episode_event_id(event_id: str) -> bool:
    """event_id 是否為 episode 層級（含 ".."——提案 §2 的機械區分規則）。
    注意：這只是快速判別；完整驗證（含 fail-closed）走 parse_event_id()。"""
    return isinstance(event_id, str) and ".." in event_id


def episode_content_hash(events: list[dict]) -> str:
    """episode 內容雜湊——**純函式**（提案 §4.5），不碰任何 db、不依賴呼叫時間。

    定義：對 eligible events 的 normalized 欄位（EPISODE_HASH_FIELDS：rowid、
    role、type、content、tool_calls 原始值、timestamp）做固定鍵序 JSON 序列化
    （sort_keys、緊湊分隔符、不轉義非 ASCII）後 SHA-256，回傳 hex digest。

    - 「eligible」（active=1、rowid ∈ [first..last]）由呼叫端的 snapshot 查詢
      負責——scanner 切刀與 importer 重算必須餵**同一定義**的事件集合；
      本函式只做 normalize（取固定欄位、依 rowid 升冪排序）＋序列化＋雜湊。
    - 排序在函式內做（依 rowid 升冪），輸入順序不影響結果——確定性。
    - 與 render 格式解耦：render 改版不會假性 mismatch。
    - 每個 event 必須帶 int 的 rowid；缺欄位的 normalized 值為 None（照實記錄，
      不同缺欄組合會得到不同 hash——這正是完整性檢查要偵測的差異）。
    """
    normalized = []
    for event in events:
        rowid = event.get("rowid")
        if isinstance(rowid, bool) or not isinstance(rowid, int):
            raise ValueError(
                f"episode_content_hash：每個 event 必須帶 int 的 rowid，得到 {rowid!r}")
        normalized.append({key: event.get(key) for key in EPISODE_HASH_FIELDS})
    normalized.sort(key=lambda item: item["rowid"])
    payload = json.dumps(normalized, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _load_schema_doc() -> dict:
    global _schema_doc_cache
    if _schema_doc_cache is None:
        import yaml  # lazy import：只有需要驗證/比對 schema 時才要求 pyyaml

        _schema_doc_cache = yaml.safe_load(SCHEMA_PATH.read_text(encoding="utf-8"))
    return _schema_doc_cache


def load_schema_fields() -> dict:
    """讀取 registry/bridge_state_schema.yaml 的 fields 區塊（bridge_sessions，cached）。

    enum 合法值、required 清單都以這份 registry 正本為準，程式內不複製第二份。
    """
    return _load_schema_doc()["fields"]


def load_cursor_schema() -> dict:
    """讀取 registry yaml 的 bridge_cursors 區塊（primary_key＋fields，cached）。"""
    return _load_schema_doc()["bridge_cursors"]


def schema_enum_values(field_name: str) -> set[str]:
    return set(load_schema_fields()[field_name]["values"])


def _validate_enum(field_name: str, value: str):
    allowed = schema_enum_values(field_name)
    if value not in allowed:
        raise ValueError(
            f"{field_name}={value!r} 不在 registry schema 的合法值內：{sorted(allowed)}"
        )


def get_connection(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """唯一的 SQLite 連線入口——只開 bridge 自己的 db，絕不開 Hermes 的資料庫。"""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@contextlib.contextmanager
def _db(db_path: Path | str = DEFAULT_DB_PATH):
    conn = get_connection(db_path)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _ensure_v2_schema_locked(conn: sqlite3.Connection) -> list[str]:
    """（呼叫端須已持 _lock 與 transaction）冪等確保 v2 schema：
    建三個 table＋對舊 db 的 bridge_sessions 補 5 個 episode 欄
    （PRAGMA table_info 查存在性，比照 bridge_meta 升級路徑先例）。
    回傳本次實際新增的欄位名清單（已齊全時為空——冪等）。"""
    conn.execute(CREATE_TABLE_SQL)
    conn.execute(CREATE_META_TABLE_SQL)
    conn.execute(CREATE_CURSOR_TABLE_SQL)
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({TABLE_NAME})")}
    added = []
    for name, sql_type in EPISODE_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE {TABLE_NAME} ADD COLUMN {name} {sql_type}")
            added.append(name)
    return added


def init_db(db_path: Path | str = DEFAULT_DB_PATH):
    """建立 bridge_sessions／bridge_meta／bridge_cursors table（冪等：CREATE
    TABLE IF NOT EXISTS，重複呼叫不影響既有資料）。db 檔整個刪掉後再呼叫即可
    重建（disposable——但 2.4d 起 db 重建後的必要前置是 reconcile recovery，
    見模組 docstring）。升級路徑（皆冪等）：對 Stage 2.4a 之前的舊 db 補建
    bridge_meta；對 2.4d 之前的 17 欄 db 補 5 個 episode 欄＋建 bridge_cursors。
    注意：init/ensure_schema 只補結構，不做資料回填——legacy 列的
    capture_trigger='legacy' 回填與 cursor 種子屬 migrate()（提案 §3.1）。"""
    with _lock, _db(db_path) as conn:
        _ensure_v2_schema_locked(conn)


def ensure_schema(db_path: Path | str = DEFAULT_DB_PATH):
    """init_db 的語義化別名：呼叫端只想確保 schema 存在時用這個名字。"""
    init_db(db_path)


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["useful_chat"] = bool(d["useful_chat"])  # SQLite INTEGER(0/1) → bool
    return d


def upsert_session_state(
    *,
    session_id: str,
    source_profile: str,
    session_source: str,
    import_status: str,
    memory_type: str,
    useful_chat: bool,
    decision_reason: str,
    selected_capability_lane: str | None = None,
    imported_inbox_path: str | None = None,
    processed_path: str | None = None,
    error_reason: str | None = None,
    event_id: str | None = None,
    event_id_range: str | None = None,
    seen_at: str | None = None,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> dict:
    """以 event_id 為去重 key 的 upsert：同一 event_id 重跑是更新既有列，不新增。

    - event_id 省略時依慣例取 "hermes:<session_id>"。
    - 首次 insert：first_seen_at = last_seen_at = seen_at（預設現在，UTC ISO 8601），
      retry_count 從 0 開始。
    - 再次 upsert（同 event_id）：first_seen_at 保持首次值不變；retry_count 不在
      upsert 更新清單內（只由 increment_retry_count() 控制）；其餘欄位以本次為準，
      last_seen_at / updated_at 更新為本次時間。
    - enum 欄位（import_status / memory_type）對 registry yaml 驗證；
      failed 必附 error_reason（bridge 層錯誤摘要，不得含 session 敏感內容）；
      to_inbox / imported 必附 imported_inbox_path（schema 的必填條件）。

    Stage 2.4d 邊界（v2）：
    - episode 層級 event_id（含 ".."）的**新列**必須由 create_episode() 建立
      （五個 episode 欄是條件必填，且要跟 cursor 前進同 transaction）——
      upsert 只能**更新**既有 episode 列（importer 的狀態轉換路徑），且刻意
      不把五個 episode 欄放進 INSERT/UPDATE 清單：episode 的 identity 欄位
      不可能被 upsert 洗掉（immutability，提案 §4.4）。
    - "hermes/" 前綴（未來 profile namespace）一律 fail-closed 拒絕（§6.1）。

    回傳 upsert 後的完整列（dict）。
    """
    _validate_enum("import_status", import_status)
    _validate_enum("memory_type", memory_type)
    if import_status == "failed" and not error_reason:
        raise ValueError("import_status=failed 時 error_reason 必填（schema 約束）")
    if import_status in ("to_inbox", "imported") and not imported_inbox_path:
        raise ValueError(
            "import_status 為 to_inbox/imported 時 imported_inbox_path 必填（schema 約束）"
        )

    if event_id is None:
        event_id = session_event_id(session_id)
    if event_id.startswith(PROFILE_NAMESPACE_PREFIX):
        raise ValueError(
            f"event_id {event_id!r} 帶 'hermes/' profile namespace 前綴——"
            "本階段 fail-closed 拒絕（提案 §6.1）")
    episode_parsed = None
    if is_episode_event_id(event_id):
        episode_parsed = parse_event_id(event_id)  # 格式不合法會在這裡 fail loud
        if episode_parsed["kind"] != "episode":
            raise ValueError(f"event_id {event_id!r} 含 '..' 但非合法 episode 格式")
        if episode_parsed["session_id"] != session_id:
            raise ValueError(
                f"不一致寫入被拒（矩陣 #24）：event_id 的 session_id="
                f"{episode_parsed['session_id']!r} != 參數 session_id={session_id!r}")
    now = seen_at or _now_iso()

    with _lock, _db(db_path) as conn:
        if episode_parsed is not None:
            exists = conn.execute(
                f"SELECT 1 FROM {TABLE_NAME} WHERE event_id=?", (event_id,)
            ).fetchone()
            if exists is None:
                raise ValueError(
                    f"episode 列 {event_id!r} 不存在——episode 的新列必須由 "
                    "create_episode() 建立（五個 episode 欄條件必填＋cursor 同 "
                    "transaction 前進），upsert_session_state 只能更新既有 episode 列")
        conn.execute(
            f"""
            INSERT INTO {TABLE_NAME} (
                session_id, source_profile, session_source, import_status,
                memory_type, useful_chat, selected_capability_lane, decision_reason,
                imported_inbox_path, processed_path, first_seen_at, last_seen_at,
                updated_at, retry_count, error_reason, event_id, event_id_range
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
            ON CONFLICT(event_id) DO UPDATE SET
                session_id=excluded.session_id,
                source_profile=excluded.source_profile,
                session_source=excluded.session_source,
                import_status=excluded.import_status,
                memory_type=excluded.memory_type,
                useful_chat=excluded.useful_chat,
                selected_capability_lane=excluded.selected_capability_lane,
                decision_reason=excluded.decision_reason,
                imported_inbox_path=excluded.imported_inbox_path,
                processed_path=excluded.processed_path,
                last_seen_at=excluded.last_seen_at,
                updated_at=excluded.updated_at,
                error_reason=excluded.error_reason,
                event_id_range=excluded.event_id_range
            """,
            # 注意：first_seen_at 與 retry_count 刻意不在 UPDATE 清單——
            # 首次發現時間不可被後續掃描洗掉；retry_count 只由 increment_retry_count() 遞增。
            (
                session_id, source_profile, session_source, import_status,
                memory_type, int(bool(useful_chat)), selected_capability_lane,
                decision_reason, imported_inbox_path, processed_path, now, now,
                now, error_reason, event_id, event_id_range,
            ),
        )
        row = conn.execute(
            f"SELECT * FROM {TABLE_NAME} WHERE event_id=?", (event_id,)
        ).fetchone()
        return _row_to_dict(row)


def get_session_state(event_id: str, db_path: Path | str = DEFAULT_DB_PATH) -> dict | None:
    """依 event_id（"hermes:<session_id>"，可用 session_event_id() 產生）讀回一筆狀態。"""
    with _db(db_path) as conn:
        row = conn.execute(
            f"SELECT * FROM {TABLE_NAME} WHERE event_id=?", (event_id,)
        ).fetchone()
        return _row_to_dict(row) if row else None


def list_by_import_status(
    import_status: str, db_path: Path | str = DEFAULT_DB_PATH
) -> list[dict]:
    """列出指定 import_status 的所有記錄（依 first_seen_at 排序）。"""
    _validate_enum("import_status", import_status)
    with _db(db_path) as conn:
        rows = conn.execute(
            f"SELECT * FROM {TABLE_NAME} WHERE import_status=? ORDER BY first_seen_at ASC",
            (import_status,),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


def mark_failed(
    event_id: str, error_reason: str, db_path: Path | str = DEFAULT_DB_PATH
) -> dict | None:
    """把既有記錄標成 failed 並寫入 error_reason（bridge 層錯誤摘要，
    不得含 session 敏感內容——這是 schema description 的硬約束，呼叫端負責遵守）。

    **不遞增 retry_count**：retry_count 記的是「重新嘗試匯入」的次數，遞增時機是
    下一次 bridge 對同一 session 重跑匯入時（由呼叫端在 re-attempt 開始時呼叫
    increment_retry_count()），不是失敗當下。找不到 event_id 時回傳 None。
    """
    if not error_reason:
        raise ValueError("mark_failed 需要 error_reason（schema：failed 時必填）")
    _validate_enum("import_status", "failed")  # 確保 'failed' 仍是 registry 合法值
    now = _now_iso()
    with _lock, _db(db_path) as conn:
        cur = conn.execute(
            f"UPDATE {TABLE_NAME} SET import_status='failed', error_reason=?, "
            "updated_at=? WHERE event_id=? "
            "RETURNING *",
            (error_reason, now, event_id),
        )
        row = cur.fetchone()
        return _row_to_dict(row) if row else None


def touch_last_seen(
    event_id: str, seen_at: str | None = None,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> dict | None:
    """只更新 last_seen_at（「最近一次掃描仍看到這個 session」的心跳）。

    給 scanner 對「已存在的記錄」用的安全路徑——與 upsert_session_state 的
    全欄位語義刻意分開：**絕不動 import_status / first_seen_at / retry_count /
    decision_reason 等任何其他欄位**，所以既有的 imported/failed/skipped/
    needs_review 狀態不可能被重掃洗回 discovered。也不動 updated_at——
    schema 定義它是「最後一次狀態變更時間」，touch 不是狀態變更。

    回傳更新後的完整列；找不到 event_id 時回傳 None。
    """
    now = seen_at or _now_iso()
    with _lock, _db(db_path) as conn:
        cur = conn.execute(
            f"UPDATE {TABLE_NAME} SET last_seen_at=? WHERE event_id=? RETURNING *",
            (now, event_id),
        )
        row = cur.fetchone()
        return _row_to_dict(row) if row else None


def increment_retry_count(
    event_id: str, db_path: Path | str = DEFAULT_DB_PATH
) -> int | None:
    """retry_count +1 並回傳新值（找不到 event_id 時回傳 None）。

    呼叫時機：bridge 對同一 session **重新**嘗試匯入的當下（例如前次 failed 後
    重跑）。與 hermes/db.py jobs.attempts（claim 時 +1 的 job 執行重試）無關。
    """
    now = _now_iso()
    with _lock, _db(db_path) as conn:
        cur = conn.execute(
            f"UPDATE {TABLE_NAME} SET retry_count=retry_count+1, updated_at=? "
            "WHERE event_id=? RETURNING retry_count",
            (now, event_id),
        )
        row = cur.fetchone()
        return row["retry_count"] if row else None


def get_scan_watermark(db_path: Path | str = DEFAULT_DB_PATH) -> str | None:
    """讀取 scan watermark（bridge_meta 的 scan_watermark 值）；讀不到回 None。

    純讀取路徑：db 檔不存在時直接回 None、**不建立 db 檔**（get_connection
    會建檔，所以這裡先檢查存在性——dry-run 與「決定 effective since」的呼叫端
    因此可以無條件呼叫）；舊 db 尚無 bridge_meta table 時同樣回 None
    （table 補建交給 ensure_schema / advance_scan_watermark 的升級路徑）。
    """
    path = Path(db_path)
    if not path.exists():
        return None
    with _db(path) as conn:
        try:
            row = conn.execute(
                f"SELECT value FROM {META_TABLE_NAME} WHERE key=?",
                (SCAN_WATERMARK_KEY,),
            ).fetchone()
        except sqlite3.OperationalError:
            return None  # 舊版 db 還沒有 meta table
        return row["value"] if row else None


def advance_scan_watermark(
    new_value: str, db_path: Path | str = DEFAULT_DB_PATH
) -> dict:
    """把 scan_watermark 推進到 new_value——**只前進不後退**。

    watermark 語義（Stage 2.4a 定案）：最近一次**真實**（非 dry-run）scan
    成功完成時「該次掃描窗口的上界」＝ scanner 在建立 Hermes state.db snapshot
    **之前**取的時間戳。選 snapshot 建立時間而非 max(ended_at) 的理由：後者在
    窗口內沒有任何新完結 session 時不會前進，重複掃描範圍會無限增長；snapshot
    時間則每次真實 scan 都前進，且在 snapshot 之後才完結的 session 一定
    >= watermark，下次掃描（含端點比較 ended_at >= since）必然涵蓋——邊界重疊
    由 event_id 去重與 touch-only 語義保證冪等無害（寧可保守重疊、不可跳漏）。

    - new_value <= 現值：no-op，回報現值（人工帶 --since 掃舊區間因此不會把
      watermark 往回拉——真實 scan 一律嘗試 advance，只前進語義自然處理）。
    - 寫入值正規化為 UTC isoformat（+00:00 形式）；new_value 解析失敗丟
      ValueError，不寫入。
    - 對尚無 bridge_meta table 的舊 db 呼叫時冪等補建（升級路徑）。

    回傳 {"advanced": bool, "watermark": 目前生效值}。
    """
    new_dt = parse_iso_utc(new_value)
    with _lock, _db(db_path) as conn:
        conn.execute(CREATE_META_TABLE_SQL)
        row = conn.execute(
            f"SELECT value FROM {META_TABLE_NAME} WHERE key=?",
            (SCAN_WATERMARK_KEY,),
        ).fetchone()
        current = row["value"] if row else None
        if current is not None and new_dt <= parse_iso_utc(current):
            return {"advanced": False, "watermark": current}
        normalized = new_dt.isoformat()
        conn.execute(
            f"INSERT INTO {META_TABLE_NAME} (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (SCAN_WATERMARK_KEY, normalized),
        )
        return {"advanced": True, "watermark": normalized}


# ---------- Stage 2.4d：episode 建立與 per-session cursor（提案 §1.2） ----------

def _cursor_row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row)


def _get_cursor_locked(conn: sqlite3.Connection, source_profile: str,
                       session_id: str) -> sqlite3.Row | None:
    return conn.execute(
        f"SELECT * FROM {CURSOR_TABLE_NAME} WHERE source_profile=? AND session_id=?",
        (source_profile, session_id),
    ).fetchone()


def _advance_cursor_locked(conn: sqlite3.Connection, source_profile: str,
                           session_id: str, last_captured_message_id: int,
                           last_episode_seq: int | None, now: str) -> dict:
    """（呼叫端須已持 _lock 與 transaction）cursor 前進——**只前進不後退**
    （比照 scan_watermark 慣例）：兩個值各自取 max(現值, 新值)，都沒變大時
    no-op（updated_at 也不動）。last_episode_seq=None＝不嘗試推進序號
    （新建列時取 0）。回傳 {"advanced": bool, "cursor": dict}。"""
    if isinstance(last_captured_message_id, bool) or not isinstance(last_captured_message_id, int):
        raise ValueError(
            f"last_captured_message_id 必須是 int，得到 {last_captured_message_id!r}")
    if last_captured_message_id < 1:
        raise ValueError(
            f"last_captured_message_id 必須 >= 1，得到 {last_captured_message_id}")
    if last_episode_seq is not None:
        if isinstance(last_episode_seq, bool) or not isinstance(last_episode_seq, int):
            raise ValueError(f"last_episode_seq 必須是 int，得到 {last_episode_seq!r}")
        if last_episode_seq < 0:
            raise ValueError(f"last_episode_seq 必須 >= 0，得到 {last_episode_seq}")

    row = _get_cursor_locked(conn, source_profile, session_id)
    if row is None:
        conn.execute(
            f"INSERT INTO {CURSOR_TABLE_NAME} (source_profile, session_id, "
            "last_captured_message_id, last_episode_seq, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (source_profile, session_id, last_captured_message_id,
             last_episode_seq if last_episode_seq is not None else 0, now),
        )
        return {"advanced": True,
                "cursor": _cursor_row_to_dict(
                    _get_cursor_locked(conn, source_profile, session_id))}
    new_msg = max(row["last_captured_message_id"], last_captured_message_id)
    new_seq = (row["last_episode_seq"] if last_episode_seq is None
               else max(row["last_episode_seq"], last_episode_seq))
    if (new_msg == row["last_captured_message_id"]
            and new_seq == row["last_episode_seq"]):
        return {"advanced": False, "cursor": _cursor_row_to_dict(row)}
    conn.execute(
        f"UPDATE {CURSOR_TABLE_NAME} SET last_captured_message_id=?, "
        "last_episode_seq=?, updated_at=? WHERE source_profile=? AND session_id=?",
        (new_msg, new_seq, now, source_profile, session_id),
    )
    return {"advanced": True,
            "cursor": _cursor_row_to_dict(
                _get_cursor_locked(conn, source_profile, session_id))}


def get_cursor(session_id: str, *, source_profile: str = DEFAULT_SOURCE_PROFILE,
               db_path: Path | str = DEFAULT_DB_PATH) -> dict | None:
    """讀取 per-session cursor；讀不到回 None。

    純讀取路徑（比照 get_scan_watermark）：db 檔不存在時直接回 None、
    **不建立 db 檔**；舊 db 尚無 bridge_cursors table 時同樣回 None
    （table 補建交給 ensure_schema / advance_cursor / migrate 的升級路徑）。
    「無 cursor」的語義由呼叫端決定（scanner：episode_cutover 底線起算，
    或 fail-closed 拒切——提案 §3.2、§5.1，2.4d-2/3 實作）。
    """
    path = Path(db_path)
    if not path.exists():
        return None
    with _db(path) as conn:
        try:
            row = _get_cursor_locked(conn, source_profile, session_id)
        except sqlite3.OperationalError:
            return None  # 舊版 db 還沒有 cursors table
        return _cursor_row_to_dict(row) if row else None


def advance_cursor(session_id: str, last_captured_message_id: int, *,
                   last_episode_seq: int | None = None,
                   source_profile: str = DEFAULT_SOURCE_PROFILE,
                   seen_at: str | None = None,
                   db_path: Path | str = DEFAULT_DB_PATH) -> dict:
    """把 (source_profile, session_id) 的 cursor 推進——**只前進不後退**
    （新值較小或相等時 no-op，比照 watermark 慣例；recovery 對健康 db 重跑
    因此天然冪等，提案 §3.2）。對尚無 bridge_cursors 的舊 db 冪等補建
    （升級路徑）。回傳 {"advanced": bool, "cursor": dict}。

    一般 episode 路徑**不要**直接呼叫這個函式——create_episode() 已在同一
    transaction 內前進 cursor；這個 API 給 recovery／migration（cursor 種子
    與重建）用。
    """
    now = seen_at or _now_iso()
    with _lock, _db(db_path) as conn:
        conn.execute(CREATE_CURSOR_TABLE_SQL)
        return _advance_cursor_locked(conn, source_profile, session_id,
                                      last_captured_message_id, last_episode_seq, now)


def create_episode(
    *,
    session_id: str,
    source_profile: str,
    session_source: str,
    episode_seq: int,
    capture_trigger: str,
    first_message_id: int,
    last_message_id: int,
    source_content_hash: str,
    decision_reason: str,
    import_status: str = "discovered",
    memory_type: str = "none",
    useful_chat: bool = False,
    selected_capability_lane: str | None = None,
    imported_inbox_path: str | None = None,
    processed_path: str | None = None,
    error_reason: str | None = None,
    event_id_range: str | None = None,
    seen_at: str | None = None,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> dict:
    """建立一個 episode 列並把 per-session cursor 推進到 boundary 的 last——
    **兩者在同一個 SQLite transaction 內完成**（核心不變量，提案 §1.2／風險 3：
    要嘛「episode 列＋cursor 前進」都成立、要嘛都不成立；這條不變量若被未來
    改動拆開，「每則訊息至多屬於一個 episode」即失效。測試矩陣 #3 釘死）。

    一致性保證（矩陣 #24）：event_id 與 event_id_range 由**同一組** boundary
    值（session_id, first_message_id, last_message_id）在函式內產生，顯式欄位
    與 event_id 不可能分家；呼叫端傳入不一致的 event_id_range 會被拒絕。
    不接受外部傳入 event_id。

    冪等路徑（矩陣 #23）：撞既有 event_id（同 boundary 已存在，UNIQUE
    conflict 的語義）時視為 no-op——**不動既有列**（狀態、判定、時間戳全部
    原樣，連 last_seen_at 都不碰），只把 cursor 推進到該 boundary 的 last
    （只前進語義：cursor 已在更前面時連 cursor 都不動）。這是重掃／recovery
    後重切同一刀的安全路徑。

    fail-closed（提案 §6.1）：source_profile != 'default' 一律拒絕——現行
    event_id namespace 無 profile 段，非 default 的 episode 會污染 namespace。

    條件必填（提案 §1.2）：episode 列的五個新欄全必填；capture_trigger 必須是
    ended/archived/inactivity/manual（'legacy' 保留給 migration 回填的
    session-level 列，episode 列用它是語義矛盾）。archived＝使用者在 Hermes
    UI 對 session 執行 Archive（2026-07-12 前置任務 2 拍板，實地驗證：
    archived 是可靠且持久的使用者明確 checkpoint 訊號，不是永久結束）——
    與 inactivity 的差異只在「誰觸發」與「是否等門檻」，不影響本函式的
    boundary／immutability 保證。source_content_hash 必填（scanner 切刀路徑，
    §4.5）；reconcile 回填（hash 無法自檔案還原，§3.2）屬 2.4d-3，屆時另議
    放寬方式，本 API 不留後門。

    回傳 {"created": bool, "row": dict, "cursor": dict}。
    """
    if source_profile != DEFAULT_SOURCE_PROFILE:
        raise ValueError(
            f"episode capture 本階段僅支援 source_profile='{DEFAULT_SOURCE_PROFILE}'"
            f"（得到 {source_profile!r}）——fail-closed 拒絕（提案 §6.1）：現行 "
            "event_id namespace 無 profile 段，非 default 會寫出無法區分的 event_id")
    _validate_enum("import_status", import_status)
    _validate_enum("memory_type", memory_type)
    _validate_enum("capture_trigger", capture_trigger)
    if capture_trigger == "legacy":
        raise ValueError(
            "capture_trigger='legacy' 保留給 migration 回填的 legacy session-level "
            "列——episode 列必須是 ended/archived/inactivity/manual（提案 §1.2）")
    if isinstance(episode_seq, bool) or not isinstance(episode_seq, int) or episode_seq < 1:
        raise ValueError(f"episode_seq 必須是 >= 1 的 int（1 起算），得到 {episode_seq!r}")
    if not source_content_hash or not isinstance(source_content_hash, str):
        raise ValueError(
            "source_content_hash 必填（episode 列條件必填，提案 §1.2／§4.5——"
            "由 scanner 切刀時的同一 snapshot 以 episode_content_hash() 計算）")
    if import_status == "failed" and not error_reason:
        raise ValueError("import_status=failed 時 error_reason 必填（schema 約束）")
    if import_status in ("to_inbox", "imported") and not imported_inbox_path:
        raise ValueError(
            "import_status 為 to_inbox/imported 時 imported_inbox_path 必填（schema 約束）")

    event_id = episode_event_id(session_id, first_message_id, last_message_id)
    if event_id_range is None:
        event_id_range = event_id
    elif event_id_range != event_id:
        raise ValueError(
            f"不一致寫入被拒（矩陣 #24）：event_id_range={event_id_range!r} != "
            f"boundary 衍生的 event_id={event_id!r}——兩者必須由同一組 boundary 產生")
    now = seen_at or _now_iso()

    with _lock, _db(db_path) as conn:
        _ensure_v2_schema_locked(conn)  # 升級路徑冪等（比照 advance_scan_watermark 先例）
        existing = conn.execute(
            f"SELECT * FROM {TABLE_NAME} WHERE event_id=?", (event_id,)
        ).fetchone()
        if existing is not None:
            # 冪等路徑（矩陣 #23）：不動既有列、只推 cursor 到該 boundary 的 last。
            seq_hint = existing["episode_seq"]
            cursor_result = _advance_cursor_locked(
                conn, source_profile, session_id, last_message_id,
                seq_hint if isinstance(seq_hint, int) else None, now)
            return {"created": False, "row": _row_to_dict(existing),
                    "cursor": cursor_result["cursor"]}
        conn.execute(
            f"""
            INSERT INTO {TABLE_NAME} (
                session_id, source_profile, session_source, import_status,
                memory_type, useful_chat, selected_capability_lane, decision_reason,
                imported_inbox_path, processed_path, first_seen_at, last_seen_at,
                updated_at, retry_count, error_reason, event_id, event_id_range,
                episode_seq, capture_trigger, first_message_id, last_message_id,
                source_content_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id, source_profile, session_source, import_status,
                memory_type, int(bool(useful_chat)), selected_capability_lane,
                decision_reason, imported_inbox_path, processed_path, now, now,
                now, error_reason, event_id, event_id_range,
                episode_seq, capture_trigger, first_message_id, last_message_id,
                source_content_hash,
            ),
        )
        row = conn.execute(
            f"SELECT * FROM {TABLE_NAME} WHERE event_id=?", (event_id,)
        ).fetchone()
        cursor_result = _advance_cursor_locked(
            conn, source_profile, session_id, last_message_id, episode_seq, now)
        return {"created": True, "row": _row_to_dict(row),
                "cursor": cursor_result["cursor"]}


# ---------- Stage 2.4d：v1 → v2 migration（提案 §3.1，冪等） ----------

def migrate(db_path: Path | str = DEFAULT_DB_PATH) -> dict:
    """v1（17 欄）→ v2 的 migration，**冪等**（重跑第二次全程 no-op）。

    步驟（提案 §3.1，全部在同一 transaction 內）：
    1. bridge_sessions 補 5 個 episode 欄（PRAGMA table_info 查存在性，已存在
       則跳過）；
    2. CREATE TABLE IF NOT EXISTS bridge_cursors（複合主鍵）＋bridge_meta；
    3. 既有 legacy 列（event_id 不含 ".."）回填 capture_trigger='legacy'
       （episode_seq 與 boundary／hash 欄維持 NULL——不捏造 boundary）；
    4. cursor 種子（belt-and-suspenders）：對 import_status='imported' 的
       legacy 列，若 event_id_range 可解析為 episode 格式
       （hermes:<sid>:<first>..<last>），把 (source_profile, session_id) 的
       cursor 推進到 <last>（last_episode_seq=0）——保證已匯入過的內容即使落在
       episode_cutover 之後也絕不二次擷取。skipped／needs_review 不種
       （episode_cutover 底線已足以擋自動擷取）。非 default profile 的列
       fail-closed 跳過不種（提案 §6.1）；只前進語義使重跑天然冪等。

    對象是**既有** db——檔案不存在時 fail loud（FileNotFoundError），
    不默默建一個空 db 再宣稱 migrate 成功；全新 db 用 init。

    回傳摘要 dict：columns_added（list）、cursor_table_created（bool）、
    legacy_backfilled（int）、cursors_seeded（int）、
    seed_skipped_non_default（int）、seed_skipped_unparseable（int）。
    """
    path = Path(db_path)
    if not path.exists():
        raise FileNotFoundError(
            f"bridge_state.db 不存在：{path}——migrate 只對既有 db 執行"
            "（全新 db 用 init；不默默建空 db）")
    with _lock, _db(path) as conn:
        cursor_table_existed = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (CURSOR_TABLE_NAME,),
        ).fetchone() is not None
        added = _ensure_v2_schema_locked(conn)
        backfilled = conn.execute(
            f"UPDATE {TABLE_NAME} SET capture_trigger='legacy' "
            "WHERE capture_trigger IS NULL AND event_id NOT LIKE '%..%'"
        ).rowcount
        seeded = 0
        skipped_non_default = 0
        skipped_unparseable = 0
        now = _now_iso()
        imported_legacy = conn.execute(
            f"SELECT session_id, source_profile, event_id_range FROM {TABLE_NAME} "
            "WHERE import_status='imported' AND event_id NOT LIKE '%..%' "
            "AND event_id_range IS NOT NULL"
        ).fetchall()
        for row in imported_legacy:
            if row["source_profile"] != DEFAULT_SOURCE_PROFILE:
                skipped_non_default += 1  # fail-closed：不動非 default 的任何狀態（§6.1）
                continue
            try:
                parsed = parse_event_id(row["event_id_range"])
            except ValueError:
                skipped_unparseable += 1  # 保守不種——episode_cutover 底線仍在（§5.1）
                continue
            if parsed["kind"] != "episode" or parsed["session_id"] != row["session_id"]:
                skipped_unparseable += 1
                continue
            result = _advance_cursor_locked(
                conn, row["source_profile"], row["session_id"],
                parsed["last_message_id"], 0, now)
            if result["advanced"]:
                seeded += 1
        return {
            "columns_added": added,
            "cursor_table_created": not cursor_table_existed,
            "legacy_backfilled": backfilled,
            "cursors_seeded": seeded,
            "seed_skipped_non_default": skipped_non_default,
            "seed_skipped_unparseable": skipped_unparseable,
        }


def _cli():
    # Windows console 預設 cp950——比照 bridge_scanner，stdout/stderr 強制 UTF-8
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        description="hermes/bridge_state.py — bridge 處理狀態 DB（WSL 部署側）CLI"
    )
    parser.add_argument(
        "--db-path", default=None,
        help=f"自訂 db 路徑（預設 {DEFAULT_DB_PATH}；Windows 開發側請勿對預設路徑 init）",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="初始化/確保 schema（冪等；供部署側手動初始化）")

    p_show = sub.add_parser("show", help="顯示單一 event_id 的完整記錄")
    p_show.add_argument("event_id")

    p_list = sub.add_parser("list", help="列出記錄")
    p_list.add_argument("--import-status", default=None)

    sub.add_parser("watermark", help="顯示目前的 scan watermark（bridge_meta）")

    sub.add_parser(
        "migrate",
        help="v1（17 欄）→ v2 migration（冪等；加欄＋建 bridge_cursors＋legacy 回填"
             "＋imported 列 cursor 種子——提案 §3.1，2.4d-4 部署時執行）")

    args = parser.parse_args()
    db_path = Path(args.db_path) if args.db_path else DEFAULT_DB_PATH

    if args.cmd == "init":
        init_db(db_path)
        print(f"schema ready: {db_path}")
    elif args.cmd == "migrate":
        try:
            summary = migrate(db_path)
        except FileNotFoundError as exc:
            print(str(exc), file=sys.stderr)
            sys.exit(1)
        print(f"migrate done: {db_path}")
        for key, value in summary.items():
            print(f"  {key}: {value}")
    elif args.cmd == "show":
        rec = get_session_state(args.event_id, db_path)
        if rec is None:
            print("找不到這個 event_id", file=sys.stderr)
            sys.exit(1)
        for k, v in rec.items():
            print(f"{k}: {v}")
    elif args.cmd == "watermark":
        wm = get_scan_watermark(db_path)
        print(wm if wm else "(尚未設定 scan watermark——scanner 將以 config cutover 為下界)")
    elif args.cmd == "list":
        if args.import_status:
            recs = list_by_import_status(args.import_status, db_path)
        else:
            with _db(db_path) as conn:
                recs = [
                    _row_to_dict(r)
                    for r in conn.execute(
                        f"SELECT * FROM {TABLE_NAME} ORDER BY first_seen_at ASC"
                    ).fetchall()
                ]
        if not recs:
            print("(沒有符合的記錄)")
        for r in recs:
            print(
                f"{r['event_id']}  {r['import_status']:<12} retry={r['retry_count']}  "
                f"profile={r['source_profile']:<10} first_seen={r['first_seen_at']}"
            )


if __name__ == "__main__":
    _cli()
