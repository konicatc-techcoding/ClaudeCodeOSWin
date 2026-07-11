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
    """session 層級去重 key，沿用 adapter 的 event_id 慣例："hermes:<session_id>"。"""
    return f"hermes:{session_id}"


def load_schema_fields() -> dict:
    """讀取 registry/bridge_state_schema.yaml 的 fields 區塊（cached）。

    enum 合法值、required 清單都以這份 registry 正本為準，程式內不複製第二份。
    """
    global _schema_fields_cache
    if _schema_fields_cache is None:
        import yaml  # lazy import：只有需要驗證/比對 schema 時才要求 pyyaml

        doc = yaml.safe_load(SCHEMA_PATH.read_text(encoding="utf-8"))
        _schema_fields_cache = doc["fields"]
    return _schema_fields_cache


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


def init_db(db_path: Path | str = DEFAULT_DB_PATH):
    """建立 bridge_sessions 與 bridge_meta table（冪等：CREATE TABLE IF NOT
    EXISTS，重複呼叫不影響既有資料）。db 檔整個刪掉後再呼叫即可重建
    （disposable）。對 Stage 2.4a 之前只有 bridge_sessions 的舊 db 呼叫時，
    冪等地補建 bridge_meta（既有 db 的升級路徑）。"""
    with _lock, _db(db_path) as conn:
        conn.execute(CREATE_TABLE_SQL)
        conn.execute(CREATE_META_TABLE_SQL)


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
    now = seen_at or _now_iso()

    with _lock, _db(db_path) as conn:
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

    args = parser.parse_args()
    db_path = Path(args.db_path) if args.db_path else DEFAULT_DB_PATH

    if args.cmd == "init":
        init_db(db_path)
        print(f"schema ready: {db_path}")
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
