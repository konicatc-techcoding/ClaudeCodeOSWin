#!/usr/bin/env python3
"""hermes/session_adapter/adapter.py — v0.1

Read-only importer：讀取 HermesAgent（NousResearch Hermes）的 session 資料，
轉成 ClaudeCodeOS 內部的 normalized memory/event 格式。

資料來源（實地確認，2026-07-07）：
- Hermes 所有 session（CLI/TUI/gateway）存在 `state.db`（SQLite, WAL mode）：
  - macOS/Linux/WSL: ~/.hermes/state.db
  - Windows: %LOCALAPPDATA%/hermes/state.db
- `sessions/sessions.json` 只是 gateway routing index（messaging session key →
  active session id），不是 session 正本，本 adapter 不讀它。
- 相關 table：
  - sessions(id TEXT PK, source TEXT('cli'|'tui'|'telegram'|'cron'), model,
    started_at REAL epoch, ended_at REAL, title, message_count, chat_id,
    chat_type, thread_id, ...)
  - messages(id INTEGER PK, session_id, role('user'|'assistant'|'tool'|
    'system'|'session_meta'), content TEXT, tool_call_id, tool_calls TEXT(JSON),
    tool_name, timestamp REAL epoch NOT NULL, finish_reason, token_count,
    active INTEGER, compacted INTEGER, ...)

Read-only 保證（技術上強制，不是自律）：
1. SQLite 一律用 URI `mode=ro` 開啟，再加 `PRAGMA query_only=ON` 雙保險——
   任何寫入語句都會直接丟 sqlite3.OperationalError。
2. 模組內沒有任何以寫入模式開啟來源路徑的 code path。
3. 可選的 snapshot 模式（`snapshot=True`）只「讀」來源檔、複製到 temp 目錄
   後開副本——處理 Hermes 正在寫入 WAL 時的鎖競爭，仍然不碰來源。
   一致性補強（2026-07-11）：複製前後比對來源三檔（state.db／-wal／-shm）
   的 fingerprint（存在與否、size、mtime_ns），不一致代表複製期間來源被寫入
   （副本可能撕裂）；副本另跑唯讀 PRAGMA quick_check。兩關任一沒過 → 清掉
   該次 temp 目錄重試（最多 3 次），全失敗丟 HermesSessionReadError（fail
   loud），任何失敗路徑都不留 temp 目錄。副本一律用 mode=ro 開啟，不使用
   immutable=1（來源可能正被寫入，對 live 檔案假設 immutable 是錯的）。
4. `write_inbox_file()` 只會在指定的 inbox 目錄「新增」檔案（open mode="x"，
   永不覆寫既有檔案），且拒絕把輸出寫進來源 db 所在目錄。

Idempotency（同一 session 重跑不產生重複檔）：
- 檔名是 deterministic key：`hermes_session_<session_id>.md`，**不含落地時間戳**
  ——同 session 不管何時重跑都對到同一個檔名，mode="x" 天然擋掉重複落地。
- 落地前掃描 inbox 本層、`.processed/`、`.failed/`：檔名含
  `hermes_session_<session_id>`（涵蓋舊時間戳格式檔名）或 frontmatter 的
  `session_id` 相符，都視為已匯入過，丟 InboxAlreadyImportedError——
  已整併歸檔的 session 不會重新落地。
- 人工重匯用 force=True（CLI `--force`）：只略過已匯入掃描，exclusive create
  仍然生效——inbox 本層若已有同名檔，force 也不會覆寫。

輸出落地慣例（對齊 ARCHITECTURE.md 第 4 節）：
- adapter 本身不主動寫任何東西；要落地時由呼叫端呼叫 write_inbox_file()，
  在 memory/inbox/ 新增 `hermes_session_<session_id>.md`——符合「背景管線
  只能新增 inbox 檔案，不能編輯既有檔案或 memory/*.md 正本」的規則。之後由
  consolidate-memory skill 整併進正本。
- 檔案帶 `claudecodeos.inbox.v1` YAML frontmatter（docs/memory-taxonomy.md §5）。
  usefulness/sensitivity 一律是 pending——adapter 不做內容判斷與敏感偵測，
  那是落地後呼叫端／consolidation 的責任。

Stage 2.4d-2：`list_session_activity()` 提供每個 session 的 max(rowid)／
max(timestamp)（active=1 訊息）輕量查詢，供 bridge_scanner 的 episode
偵測用（不 export 訊息內容，見該方法 docstring 與提案 §5.3）。

Stage 2.4d-3（importer episode 化＋recovery，規格正本：
docs/stage2.4d-episode-capture-proposal.md）：

- `export_session_range()`／`iter_events(min_rowid=, max_rowid=)`：匯出單一
  session 在 boundary（[first..last]，含端點）內的訊息，供 importer 重算
  `source_content_hash`（§4.5）比對，也是 episode 落地內容的來源——匯入
  單位從整個 session 縮小為單一 episode。
- episode inbox 檔名（deterministic，§2）：
  `hermes_session_<sid>_ep<first>-<last>.md`。`episode_inbox_filename()` 產生、
  `parse_inbox_filename()` 解析（legacy 檔名與 episode 檔名共用同一實作，
  classmethod 供 bridge_scanner／bridge_importer 共用，避免各自維護一份
  規則不同的 filename regex）。
- `write_episode_inbox_file()`：episode 版本的 `write_inbox_file()`，
  frontmatter 額外帶 `episode`／`capture_trigger`，`event_id_range`＝episode
  event_id 本身（由呼叫端顯式傳入的 boundary 產生，不靠內容推算）。
- `_find_existing_import()` 的 episode-aware 修正（§2 的核心陷阱）：加
  `first_message_id`／`last_message_id` 可選參數——給定時查重 needle 精確到
  boundary（不誤擋 legacy、不誤擋其他 episode）；不給定時只認 legacy 檔名
  （無 `_ep` 段）或非 episode 格式的 frontmatter（矩陣 #12 雙向）。
- `has_landed_episode_file()`（module 層級函式）：episode 落地檔「存在性
  探測」的單一實作（提案 §3.2）——bridge_scanner 的 cursor 遺失防護
  （矩陣 #8）與 bridge_importer 共用，不重複維護一份探測邏輯（2.4d-2
  遺留的收斂債務，2.4d-3 收斂）。放在 adapter.py 而非 bridge_state.py／
  bridge_importer.py 的理由：filename 的產生與解析本來就是 adapter 的既有
  職責（`_find_existing_import`／`write_inbox_file`／`_ARCHIVE_SUBDIRS`
  已在這裡），且 bridge_scanner 與 bridge_importer 都已經 import 這個
  module，不需要新模組、也不會造成循環 import（scanner／importer 互不
  import 對方）。

CLI 用法（手動測試/操作用；Windows 用 `py -3.11`，WSL 用 python3；
`--db`/`--snapshot` 是全域 flag，要放在子指令前面）：
    python3 hermes/session_adapter/adapter.py [--snapshot] [--db PATH] list [--source telegram]
    python3 hermes/session_adapter/adapter.py [--snapshot] [--db PATH] export <session_id>
    python3 hermes/session_adapter/adapter.py [--snapshot] [--db PATH] to-inbox <session_id> \
        [--inbox DIR] [--force] [--full]
    # to-inbox：同 session 已匯入過 → stderr 訊息 + exit code 3（不靜默成功）
"""
import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

EVENT_SCHEMA = "claudecodeos.event.v1"
SESSION_SCHEMA = "claudecodeos.session.v1"
SOURCE_NAME = "hermes"

KNOWN_ROLES = {"user", "assistant", "tool", "system", "session_meta"}

# 正常訊息流之外的角色，一律歸成 meta 事件
_META_ROLES = {"session_meta"}

# inbox 檔名的單一解析實作（Stage 2.4d-3，提案 §2）：legacy（無 ep 段）與
# episode（`_ep<first>-<last>` 段）共用同一 regex。sid 用非貪婪 `.+?`——
# 配合結尾錨點 `$` 與 optional group，回溯時第一個成功的切法必然是「ep 段
# 存在時優先辨識為 episode」，不會被貪婪 sid 誤吃掉 `_ep` 段（已用手算
# 回溯驗證過兩種檔名皆正確切分，不能改成貪婪 `.+`）。
_INBOX_FILENAME_RE = re.compile(
    r"(?:^|_)hermes_session_(?P<sid>.+?)(?:_ep(?P<first>\d+)-(?P<last>\d+))?\.md$"
)

# snapshot 一致性驗證：重試上限與重試間隔（秒，線性遞增；測試可覆寫歸零）
_SNAPSHOT_MAX_ATTEMPTS = 3
_SNAPSHOT_RETRY_DELAY_S = 0.05

# SQLite WAL 模式的側檔（fingerprint 與複製都涵蓋這兩個）
_DB_SIDECAR_SUFFIXES = ("-wal", "-shm")


class HermesSessionReadError(Exception):
    """來源 db 打不開、不是 SQLite、或缺少預期的 table 時丟出。"""


class InboxAlreadyImportedError(Exception):
    """同一 session 已經落地過（inbox 本層、.processed/ 或 .failed/ 有對應檔案）。

    不是錯誤狀態的「失敗」，而是 idempotency 的明確訊號——呼叫端據此決定
    回報「already imported」還是用 force 重匯。"""

    def __init__(self, session_id: str, existing_path):
        self.session_id = session_id
        self.existing_path = Path(existing_path)
        super().__init__(
            f"session {session_id} 已匯入過：{self.existing_path}")


def default_state_db_path() -> Path:
    """依平台找 Hermes state.db。找不到就 FileNotFoundError——
    不猜、不建立任何檔案。"""
    candidates = [Path.home() / ".hermes" / "state.db"]
    local_app = os.environ.get("LOCALAPPDATA")
    if local_app:
        candidates.append(Path(local_app) / "hermes" / "state.db")
    for cand in candidates:
        if cand.is_file():
            return cand
    raise FileNotFoundError(
        "找不到 Hermes state.db，找過：" + "; ".join(str(c) for c in candidates)
    )


def profile_state_db_path(profile: str,
                          main_db_path: str | Path | None = None) -> Path:
    """named profile 的 state.db 路徑（memory-lifecycle A1，2026-07-30）。

    Hermes 的 profile db 佈局：`<hermes 資料目錄>/profiles/<name>/state.db`
    （Windows：%LOCALAPPDATA%/hermes/profiles/...；2026-07-30 查證：四顆
    profile db 都在 Windows 側，WSL 側只 symlink 了主 db 本體）。

    定位方式刻意**相對於主 db 的實路徑**（resolve symlink 後的 parent）而非
    另設平台別設定：WSL 部署側 ~/.hermes/state.db resolve 後即得
    /mnt/c/.../hermes/，profiles/ 子目錄天然跟著對——單一實作兩平台通用，
    bridge.yaml 不需要寫死任何平台路徑。只推導路徑、不檢查存在性——
    「configured 但 db 不存在」由呼叫端誠實回報，不在這裡猜。
    """
    main = Path(main_db_path) if main_db_path else default_state_db_path()
    return main.resolve().parent / "profiles" / profile / "state.db"


def _file_fingerprint(path: Path) -> tuple:
    """單一檔案的 fingerprint：(存在與否, size, mtime_ns)。
    不存在（或 stat 失敗）→ (False, None, None)。只 stat、不開檔——
    取 fingerprint 這個動作本身不會動到來源任何東西。"""
    try:
        st = path.stat()
    except OSError:
        return (False, None, None)
    return (True, st.st_size, st.st_mtime_ns)


def _source_fingerprints(db_path: Path) -> dict[str, tuple]:
    """來源三檔（state.db、-wal、-shm）各自的 fingerprint。
    粒度是「每檔一組 (existence, size, mtime_ns)」，複製前後任一檔任一項
    變動都視為來源在複製期間被寫入。"""
    fps = {"db": _file_fingerprint(db_path)}
    for suffix in _DB_SIDECAR_SUFFIXES:
        fps[suffix] = _file_fingerprint(Path(str(db_path) + suffix))
    return fps


def _open_ro(path: Path) -> sqlite3.Connection:
    """模組內唯一的 SQLite 連線入口：URI mode=ro + PRAGMA query_only=ON。
    live 來源與 snapshot 副本都走這裡；一律不用 immutable=1——來源可能
    正被 Hermes 寫入，副本則沿用同一套 read-only 慣例，不需要 immutable。"""
    uri = f"file:{path.as_posix()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=10)
    except sqlite3.Error as exc:
        raise HermesSessionReadError(f"無法以 read-only 開啟 {path}：{exc}") from exc
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only=ON")
    except sqlite3.Error as exc:
        conn.close()
        raise HermesSessionReadError(f"無法以 read-only 開啟 {path}：{exc}") from exc
    return conn


def _epoch_to_iso(value) -> str | None:
    """REAL epoch 秒 → ISO 8601 UTC。壞值回 None（容錯，不中斷整批匯入）。"""
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def validate_event(event) -> list[str]:
    """檢查 normalized event 是否符合 claudecodeos.event.v1，回傳問題清單
    （空 list = 合法）。給呼叫端在落地前做防線用。"""
    problems = []
    if not isinstance(event, dict):
        return ["event 不是 dict"]
    expected = {
        "schema": (str,),
        "source": (str,),
        "session_id": (str,),
        "event_id": (str,),
        "timestamp": (str, type(None)),
        "role": (str,),
        "type": (str,),
        "content": (str,),
        "metadata": (dict,),
    }
    for key, types in expected.items():
        if key not in event:
            problems.append(f"缺少欄位 {key}")
        elif not isinstance(event[key], types):
            problems.append(f"{key} 型別錯誤：{type(event[key]).__name__}")
    if event.get("schema") not in (None, EVENT_SCHEMA):
        problems.append(f"schema 應為 {EVENT_SCHEMA}")
    if event.get("source") not in (None, SOURCE_NAME):
        problems.append(f"source 應為 {SOURCE_NAME}")
    if isinstance(event.get("type"), str) and event["type"] not in (
        "message", "tool_call", "tool_result", "meta"
    ):
        problems.append(f"未知的 type：{event['type']}")
    return problems


class HermesSessionAdapter:
    """Read-only 讀取 Hermes state.db，輸出 normalized session/event dict。"""

    def __init__(self, db_path: str | Path | None = None, snapshot: bool = False):
        self.db_path = Path(db_path) if db_path else default_state_db_path()
        if not self.db_path.is_file():
            raise FileNotFoundError(f"Hermes state.db 不存在：{self.db_path}")
        # WAL sidecar 修正（2026-07-30，memory-lifecycle A1 前置查證 2）：
        # WSL 部署側 ~/.hermes/state.db 是 symlink → /mnt/c/... 的實檔，但
        # sidecar（-wal/-shm）路徑是用「db 路徑字串 + 後綴」拼接推導的——對
        # symlink 會得到不存在的 symlink 側路徑而漏拷 WAL（snapshot 讀不到
        # 尚未 checkpoint 的最新寫入）。這裡先 resolve 成實路徑：單點修正，
        # 之後所有 fingerprint／複製／sidecar 推導（_source_fingerprints、
        # _copy_and_verify_snapshot）與 profile db 相對定位
        # （profile_state_db_path）都以實路徑為準。選 adapter 端 resolve 而非
        # 部署側改吃 /mnt/c 實路徑的理由：一處修正涵蓋所有呼叫端（scanner
        # scan/checkpoint、importer、CLI、全部 profile db），不需要平台別設定，
        # 也不會留著「下一個呼叫端再踩一次」的坑。
        self.db_path = self.db_path.resolve()
        self._snapshot_dir: str | None = None
        self._read_path = self.db_path
        if snapshot:
            self._read_path = self._make_snapshot()

    # ---------- 連線（唯一入口，強制 read-only） ----------

    def _make_snapshot(self) -> Path:
        """把 db（含 -wal/-shm，如果存在）複製到 temp 目錄，之後只讀副本。
        來源只被讀取，永不寫入。

        一致性保證（Hermes 可能同時在寫來源）：
        1. 複製前後各取一次來源三檔的 fingerprint（見 _source_fingerprints），
           兩次不一致代表複製期間來源變動，副本可能撕裂，整組作廢。
        2. 副本再跑唯讀 PRAGMA quick_check，非 ok 同樣作廢。
        3. 作廢 → 清掉該次 temp 目錄重試（間隔 _SNAPSHOT_RETRY_DELAY_S 線性
           遞增），最多 _SNAPSHOT_MAX_ATTEMPTS 次；全部失敗丟
           HermesSessionReadError（fail loud）。任何失敗路徑（含複製中途
           例外）都不留 temp 目錄。"""
        failures: list[str] = []
        for attempt in range(1, _SNAPSHOT_MAX_ATTEMPTS + 1):
            if attempt > 1 and _SNAPSHOT_RETRY_DELAY_S > 0:
                time.sleep(_SNAPSHOT_RETRY_DELAY_S * (attempt - 1))
            snapshot_dir = tempfile.mkdtemp(prefix="hermes_state_snapshot_")
            keep = False
            try:
                dest = Path(snapshot_dir) / self.db_path.name
                failure = self._copy_and_verify_snapshot(dest)
                if failure is None:
                    keep = True
                    self._snapshot_dir = snapshot_dir
                    return dest
                failures.append(
                    f"attempt {attempt}/{_SNAPSHOT_MAX_ATTEMPTS}: {failure}")
            finally:
                if not keep:
                    shutil.rmtree(snapshot_dir, ignore_errors=True)
        raise HermesSessionReadError(
            f"無法取得 {self.db_path} 的一致 snapshot"
            f"（重試 {_SNAPSHOT_MAX_ATTEMPTS} 次仍失敗）：" + "；".join(failures))

    def _copy_and_verify_snapshot(self, dest: Path) -> str | None:
        """單次複製 + 兩關驗證。成功回 None；失敗回原因字串
        （temp 目錄的清理由 _make_snapshot 的 finally 負責）。"""
        before = _source_fingerprints(self.db_path)
        try:
            shutil.copy2(self.db_path, dest)
            for suffix in _DB_SIDECAR_SUFFIXES:
                side = Path(str(self.db_path) + suffix)
                if side.is_file():
                    shutil.copy2(side, Path(str(dest) + suffix))
        except OSError as exc:
            # 例：-wal 在複製瞬間被 Hermes checkpoint 移除——視同來源變動
            return f"複製途中讀取來源失敗：{exc}"
        after = _source_fingerprints(self.db_path)
        if before != after:
            changed = sorted(k for k in before if before[k] != after[k])
            return ("來源在複製期間變動（fingerprint 不一致："
                    + ", ".join(changed) + "）")
        return self._snapshot_quick_check(dest)

    @staticmethod
    def _snapshot_quick_check(dest: Path) -> str | None:
        """對 snapshot 副本跑唯讀 PRAGMA quick_check（mode=ro + query_only，
        走 _open_ro 同一入口）。回傳 None = ok；否則回傳失敗原因。"""
        try:
            conn = _open_ro(dest)
        except HermesSessionReadError as exc:
            return f"副本無法以 read-only 開啟：{exc}"
        try:
            rows = conn.execute("PRAGMA quick_check").fetchall()
        except sqlite3.Error as exc:
            return f"副本 PRAGMA quick_check 執行失敗：{exc}"
        finally:
            conn.close()
        results = [str(row[0]) for row in rows]
        if results == ["ok"]:
            return None
        return "副本 quick_check 回報異常：" + "; ".join(results[:5])

    def close(self):
        if self._snapshot_dir:
            shutil.rmtree(self._snapshot_dir, ignore_errors=True)
            self._snapshot_dir = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _connect(self) -> sqlite3.Connection:
        conn = _open_ro(self._read_path)
        try:
            conn.execute("SELECT 1 FROM sessions LIMIT 1")
            conn.execute("SELECT 1 FROM messages LIMIT 1")
        except sqlite3.Error as exc:
            conn.close()
            raise HermesSessionReadError(
                f"{self._read_path} 不是預期的 Hermes state.db（讀取 sessions/messages 失敗）：{exc}"
            ) from exc
        return conn

    # ---------- normalized 輸出 ----------

    def list_sessions(self, source: str | None = None,
                      since_epoch: float | None = None) -> list[dict]:
        """回傳 normalized session 摘要（claudecodeos.session.v1）。

        profile_name（memory-lifecycle A1，2026-07-30）：主 db 的 sessions 有
        `profile_name` 欄標記 named-profile 的 telegram session（2026-07-30
        查證實例：profile_name='gptcoding'）——bridge_scanner 依此把主 db 的
        session 分流到對應 profile namespace。欄位存在才 SELECT（測試 fixture
        與較舊 schema 沒有這欄），缺欄時 metadata.profile_name 一律 None
        ——additive、既有呼叫端零行為變化。
        """
        conn = self._connect()
        try:
            has_profile_col = any(
                row[1] == "profile_name"
                for row in conn.execute("PRAGMA table_info(sessions)"))
            query = (
                "SELECT id, source, user_id, model, started_at, ended_at, end_reason, "
                "message_count, title, session_key, chat_id, chat_type, thread_id, "
                "parent_session_id, archived"
                + (", profile_name" if has_profile_col else "")
                + " FROM sessions"
            )
            conditions, params = [], []
            if source:
                conditions.append("source = ?")
                params.append(source)
            if since_epoch is not None:
                conditions.append("started_at >= ?")
                params.append(since_epoch)
            if conditions:
                query += " WHERE " + " AND ".join(conditions)
            query += " ORDER BY started_at ASC"
            rows = conn.execute(query, params).fetchall()
        finally:
            conn.close()
        return [self._normalize_session(row) for row in rows]

    @staticmethod
    def _normalize_session(row) -> dict:
        profile_name = (row["profile_name"]
                        if "profile_name" in row.keys() else None)
        return {
            "schema": SESSION_SCHEMA,
            "source": SOURCE_NAME,
            "session_id": str(row["id"]),
            "session_source": row["source"],       # cli | tui | telegram | cron
            "title": row["title"],
            "model": row["model"],
            "started_at": _epoch_to_iso(row["started_at"]),
            "ended_at": _epoch_to_iso(row["ended_at"]),
            "end_reason": row["end_reason"],
            "message_count": row["message_count"],
            "metadata": {
                "user_id": row["user_id"],
                "session_key": row["session_key"],
                "chat_id": row["chat_id"],
                "chat_type": row["chat_type"],
                "thread_id": row["thread_id"],
                "parent_session_id": row["parent_session_id"],
                "archived": bool(row["archived"]),
                # None＝欄位缺失或未標記；非空字串＝主 db 裡的 named-profile
                # session（A1 分流訊號——profile db 的 session 不看這欄，
                # profile 辨識以「哪顆 db」為準，舊資料的 profile_name 有 null
                # 不可靠，2026-07-30 查證）。
                "profile_name": profile_name or None,
            },
        }

    def iter_events(self, session_id: str | None = None,
                    include_inactive: bool = False,
                    min_rowid: int | None = None,
                    max_rowid: int | None = None):
        """逐筆產出 normalized event（claudecodeos.event.v1）。
        單筆訊息壞掉不會中斷整批——壞欄位進 metadata.warnings。

        min_rowid／max_rowid（Stage 2.4d-3，含端點）：episode boundary 篩選，
        供 `export_session_range()` 用；預設 None＝不加此限制（既有呼叫端
        零行為變化）。"""
        query = (
            "SELECT m.id, m.session_id, m.role, m.content, m.tool_call_id, "
            "m.tool_calls, m.tool_name, m.timestamp, m.finish_reason, "
            "m.token_count, m.active, m.compacted, "
            "s.source AS session_source, s.title AS session_title, s.model AS session_model "
            "FROM messages m LEFT JOIN sessions s ON s.id = m.session_id"
        )
        conditions, params = [], []
        if session_id:
            conditions.append("m.session_id = ?")
            params.append(session_id)
        if not include_inactive:
            conditions.append("m.active = 1")
        if min_rowid is not None:
            conditions.append("m.id >= ?")
            params.append(min_rowid)
        if max_rowid is not None:
            conditions.append("m.id <= ?")
            params.append(max_rowid)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY m.session_id ASC, m.id ASC"
        conn = self._connect()
        try:
            for row in conn.execute(query, params):
                yield self._normalize_message(row)
        finally:
            conn.close()

    @staticmethod
    def _normalize_message(row) -> dict:
        warnings = []

        role = row["role"] if isinstance(row["role"], str) else ""
        if role not in KNOWN_ROLES:
            warnings.append(f"未知的 role：{role!r}")
            normalized_role = "unknown"
        else:
            normalized_role = role

        content = row["content"]
        if content is None:
            content = ""
            warnings.append("content 為 NULL，已補空字串")
        elif not isinstance(content, str):
            content = str(content)
            warnings.append("content 不是文字，已強制轉字串")

        tool_calls = None
        tool_calls_raw = row["tool_calls"]
        if tool_calls_raw:
            try:
                parsed = json.loads(tool_calls_raw)
                if isinstance(parsed, list):
                    tool_calls = parsed
                else:
                    warnings.append("tool_calls JSON 不是 list，保留原始字串")
            except (json.JSONDecodeError, TypeError):
                warnings.append("tool_calls 不是合法 JSON，保留原始字串")

        timestamp = _epoch_to_iso(row["timestamp"])
        if timestamp is None:
            warnings.append(f"timestamp 無法解析：{row['timestamp']!r}")

        if normalized_role == "tool":
            event_type = "tool_result"
        elif normalized_role == "assistant" and tool_calls:
            event_type = "tool_call"
        elif normalized_role in _META_ROLES:
            event_type = "meta"
        else:
            event_type = "message"

        metadata = {
            "raw_message_id": row["id"],
            "session_source": row["session_source"],
            "session_title": row["session_title"],
            "model": row["session_model"],
            "tool_name": row["tool_name"],
            "tool_call_id": row["tool_call_id"],
            "tool_calls": tool_calls,
            "finish_reason": row["finish_reason"],
            "token_count": row["token_count"],
            "active": bool(row["active"]),
            "compacted": bool(row["compacted"]),
        }
        if tool_calls is None and tool_calls_raw:
            metadata["tool_calls_raw"] = tool_calls_raw
        if warnings:
            metadata["warnings"] = warnings

        return {
            "schema": EVENT_SCHEMA,
            "source": SOURCE_NAME,
            "session_id": str(row["session_id"]),
            "event_id": f"{SOURCE_NAME}:{row['session_id']}:{row['id']}",
            "timestamp": timestamp,
            "role": normalized_role,
            "type": event_type,
            "content": content,
            "metadata": metadata,
        }

    def list_session_activity(self) -> dict[str, dict]:
        """輕量查詢：每個 session 目前的 max(rowid)、max(timestamp)（active=1
        訊息），**不 export 訊息內容**（Stage 2.4d-2，bridge_scanner episode
        偵測用；提案 §5.3：既有 scan watermark 過濾的是 ended_at，不能沿用
        到 episode 偵測——inactivity trigger 恰恰在「最近沒有活動」時才成立，
        若用「活動時間 >= watermark」過濾會系統性漏切。這個方法讓呼叫端改用
        episode_cutover 底線＋逐 session cursor 比對，不延伸 watermark 進
        episode 語義）。

        read-only：走既有 _connect() 入口（mode=ro＋query_only），是否
        snapshot 由呼叫端建構 adapter 時決定，本方法不另開連線邏輯。

        回傳 {session_id: {"max_rowid": int, "max_timestamp": ISO字串或None}}；
        沒有任何 active 訊息的 session 不會出現在結果裡（無從判斷活動時間，
        呼叫端應視為「無法切刀」）。
        """
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT session_id, MAX(id) AS max_rowid, "
                "MAX(timestamp) AS max_timestamp FROM messages "
                "WHERE active = 1 GROUP BY session_id"
            ).fetchall()
        finally:
            conn.close()
        result: dict[str, dict] = {}
        for row in rows:
            result[str(row["session_id"])] = {
                "max_rowid": row["max_rowid"],
                "max_timestamp": _epoch_to_iso(row["max_timestamp"]),
            }
        return result

    def export_session(self, session_id: str, include_inactive: bool = False) -> dict:
        """單一 session 的完整 normalized 匯出：{session, events}。"""
        sessions = [s for s in self.list_sessions() if s["session_id"] == session_id]
        if not sessions:
            raise KeyError(f"Hermes state.db 裡沒有 session：{session_id}")
        events = list(self.iter_events(session_id=session_id,
                                       include_inactive=include_inactive))
        return {"session": sessions[0], "events": events}

    def export_session_range(self, session_id: str, first_message_id: int,
                             last_message_id: int,
                             include_inactive: bool = False) -> dict:
        """單一 session 在 boundary（[first_message_id..last_message_id]，
        含端點）內的 normalized 匯出：{session, events}（Stage 2.4d-3）。

        匯入單位從整個 session 縮小為單一 episode（提案 §2／§4.5）：
        bridge_importer 用這個方法讀取 episode 邊界內的訊息，重算
        `bridge_state.episode_content_hash()` 與 `source_content_hash` 比對，
        也是 episode 落地內容（`write_episode_inbox_file()`）的來源。
        session 不存在丟 KeyError（同 export_session）。"""
        sessions = [s for s in self.list_sessions() if s["session_id"] == session_id]
        if not sessions:
            raise KeyError(f"Hermes state.db 裡沒有 session：{session_id}")
        events = list(self.iter_events(
            session_id=session_id, include_inactive=include_inactive,
            min_rowid=first_message_id, max_rowid=last_message_id))
        return {"session": sessions[0], "events": events}

    # ---------- 落地（只新增，永不覆寫；由呼叫端決定要不要用） ----------

    # 已整併/失敗歸檔的子目錄（docs/memory-taxonomy.md：目錄位置是狀態的唯一真相）
    _ARCHIVE_SUBDIRS = (".processed", ".failed")

    @staticmethod
    def episode_inbox_filename(session_id: str, first_message_id: int,
                               last_message_id: int) -> str:
        """episode inbox 檔名（deterministic，Stage 2.4d-3，提案 §2）：
        `hermes_session_<sid>_ep<first>-<last>.md`——從 boundary 衍生，不含
        匯入時間；同一 episode 任何時候重跑都對到同一檔名（矩陣 #9）。"""
        return f"hermes_session_{session_id}_ep{first_message_id}-{last_message_id}.md"

    @classmethod
    def parse_inbox_filename(cls, name: str) -> dict | None:
        """解析 inbox 檔名（legacy／episode 共用單一實作，提案 §2）：
        - legacy（新格式 `hermes_session_<sid>.md` 或舊時間戳格式
          `<stamp>_hermes_session_<sid>.md`）→
          {"session_id", "first_message_id": None, "last_message_id": None}
        - episode（`hermes_session_<sid>_ep<first>-<last>.md`）→
          {"session_id", "first_message_id": int, "last_message_id": int}
        不符合樣式（含非 hermes-session 來源的檔名）→ None。

        單一實作供 bridge_scanner（reconcile 對帳、cursor 遺失防護）與
        bridge_importer（`_find_existing_import` 查重）共用，避免兩處各自
        維護一份 regex 造成行為漂移（2.4d-2 遺留的收斂債務，2.4d-3 收斂）。
        """
        m = _INBOX_FILENAME_RE.search(name)
        if not m:
            return None
        first = m.group("first")
        last = m.group("last")
        return {
            "session_id": m.group("sid"),
            "first_message_id": int(first) if first is not None else None,
            "last_message_id": int(last) if last is not None else None,
        }

    @staticmethod
    def _frontmatter_fields(path: Path) -> dict:
        """讀檔案開頭 YAML frontmatter 的 session_id／event_id_range（沒有
        就缺該 key）。只掃前 50 行，容錯：讀不到、格式不對都回空 dict。"""
        fields: dict = {}
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                if fh.readline().strip() != "---":
                    return fields
                for _ in range(50):
                    line = fh.readline()
                    if not line or line.strip() == "---":
                        break
                    for key in ("session_id", "event_id_range"):
                        if line.startswith(key + ":"):
                            fields[key] = line.split(":", 1)[1].strip().strip("\"'")
        except OSError:
            return fields
        return fields

    @staticmethod
    def _frontmatter_session_id(path: Path) -> str | None:
        """讀檔案開頭的 YAML frontmatter，取 session_id（沒有就 None）。
        薄包裝：實際解析在 `_frontmatter_fields()`（同一實作，供
        `_find_existing_import` 與既有呼叫端共用）。"""
        return HermesSessionAdapter._frontmatter_fields(path).get("session_id")

    def _find_existing_import(self, inbox_dir: Path, session_id: str, *,
                              first_message_id: int | None = None,
                              last_message_id: int | None = None) -> Path | None:
        """在 inbox 本層與 .processed/ / .failed/ 找同 session 的既有落地檔。

        episode-aware（Stage 2.4d-3，提案 §2 的核心陷阱修正）：舊實作用子
        字串 `hermes_session_<sid>` 掃描——episode 檔名包含這個子字串，若
        不改，第一個 episode 落地後會把同 session 所有後續 episode 全部
        誤判為 already-imported。修正後查重精確到「這次查的是哪個層級」：

        - `first_message_id`／`last_message_id` 都給定 ＝ episode 查重：
          needle 精確到 boundary（檔名 `_ep<first>-<last>` 段完全相符，或
          frontmatter `event_id_range` 全值等於該 episode 的 event_id）——
          不誤擋 legacy 檔、不誤擋其他 boundary 的 episode（矩陣 #12）。
        - 都不給 ＝ legacy 查重（原行為，不變）：檔名相符**且無 `_ep` 段**、
          或 frontmatter session_id 相符**且該檔 event_id_range 不是
          episode 格式**——同樣不誤擋 episode 檔（矩陣 #12 反向）。
        """
        if (first_message_id is None) != (last_message_id is None):
            raise ValueError(
                "first_message_id 與 last_message_id 必須同時給定或同時省略")
        episode = first_message_id is not None
        target_event_id = (
            f"hermes:{session_id}:{first_message_id}..{last_message_id}"
            if episode else None)
        # A1（多 profile）：named profile 檔案的 event_id_range 帶
        # "hermes/<profile>:" 前綴，但檔名 needle（同 sid＋boundary）不分
        # profile——sid 是 UUID 等級唯一，同 sid＋同 boundary 就是同一刀。
        # frontmatter 比對接受裸與 namespaced 兩種形式（suffix 含 sid 與
        # boundary，不會誤中別的 session 或別的 boundary）。
        target_range_suffix = (
            f":{session_id}:{first_message_id}..{last_message_id}"
            if episode else None)

        dirs = [inbox_dir] + [inbox_dir / d for d in self._ARCHIVE_SUBDIRS]
        for directory in dirs:
            if not directory.is_dir():
                continue
            for candidate in sorted(directory.glob("*.md")):
                parsed = self.parse_inbox_filename(candidate.name)
                if episode:
                    if (parsed and parsed["session_id"] == session_id
                            and parsed["first_message_id"] == first_message_id
                            and parsed["last_message_id"] == last_message_id):
                        return candidate
                    fm = self._frontmatter_fields(candidate)
                    fm_range = fm.get("event_id_range") or ""
                    if fm_range == target_event_id or (
                            fm_range.startswith("hermes/")
                            and fm_range.endswith(target_range_suffix)):
                        return candidate
                else:
                    if (parsed and parsed["session_id"] == session_id
                            and parsed["first_message_id"] is None):
                        return candidate
                    fm = self._frontmatter_fields(candidate)
                    if (fm.get("session_id") == session_id
                            and ".." not in (fm.get("event_id_range") or "")):
                        return candidate
        return None

    def write_inbox_file(self, export: dict, inbox_dir: str | Path,
                         max_excerpt_events: int = 30,
                         force: bool = False, full: bool = False) -> Path:
        """把 export_session() 的結果寫成 memory/inbox/ 的一個「新」檔案。

        Idempotent：檔名是 deterministic key `hermes_session_<session_id>.md`
        （不含落地時間戳），落地前先掃 inbox 本層 + .processed/ + .failed/，
        同 session 已存在就丟 InboxAlreadyImportedError，不產生重複檔。

        - force=True：略過已匯入掃描（人工重匯用）；exclusive create 仍生效，
          inbox 本層有同名檔時照樣丟 InboxAlreadyImportedError，永不覆寫。
        - full=True：對話摘錄不做則數與字元截斷（完整匯出）。
        - 拒絕寫進來源 state.db 所在目錄（read-only 保證的一部分）。
        """
        inbox_dir = Path(inbox_dir)
        if inbox_dir.resolve() == self.db_path.parent.resolve():
            raise ValueError("拒絕把輸出寫進 Hermes 來源資料目錄")
        if not inbox_dir.is_dir():
            raise FileNotFoundError(f"inbox 目錄不存在：{inbox_dir}（不代建目錄，避免寫錯地方）")

        session = export["session"]
        session_id = session["session_id"]
        if not force:
            existing = self._find_existing_import(inbox_dir, session_id)
            if existing is not None:
                raise InboxAlreadyImportedError(session_id, existing)

        body = self._render_markdown(export, max_excerpt_events, full=full)
        path = inbox_dir / f"hermes_session_{session_id}.md"
        try:
            with open(path, "x", encoding="utf-8", newline="\n") as fh:
                fh.write(body)
        except FileExistsError:
            # 掃描與寫入之間的 race、或 force 下同名檔仍在 inbox 本層
            raise InboxAlreadyImportedError(session_id, path) from None
        return path

    def write_episode_inbox_file(
        self, export: dict, inbox_dir: str | Path, *,
        episode_seq: int, capture_trigger: str,
        first_message_id: int, last_message_id: int,
        force: bool = False, full: bool = False,
        max_excerpt_events: int = 30,
        source_profile: str = "default",
        tool_output_max_chars: int | None = None,
    ) -> Path:
        """episode 版本的 `write_inbox_file()`（Stage 2.4d-3，提案 §2）。

        - 檔名 deterministic：`hermes_session_<sid>_ep<first>-<last>.md`
          （見 `episode_inbox_filename()`）——同一 episode 任何時候重跑都對到
          同一檔名，`open(mode="x")` 天然擋重複落地（矩陣 #9）。
        - 查重（`_find_existing_import`）精確到 boundary，不誤擋 legacy 檔、
          不誤擋其他 episode（矩陣 #12）。
        - frontmatter 額外帶 `episode`／`capture_trigger`；`event_id_range`＝
          episode event_id 本身，由呼叫端傳入的 boundary 直接產生（不靠
          `export["events"]` 的 raw_message_id min/max 推算——即使 export
          只是 boundary 內容的子集，event_id 仍精確等於 DB 那筆 episode 列
          的 event_id）。
        - force／拒絕寫進來源目錄／找不到 inbox 目錄的行為與 `write_inbox_file`
          一致。

        memory-lifecycle A1（2026-07-30）新增兩個可選參數：
        - `source_profile`：非 "default" 時 frontmatter 額外帶
          `source_profile: <name>` 欄，且 `event_id_range` 用
          "hermes/<profile>:" namespace（2.4d §6.1 預留格式；default 維持裸
          "hermes:"，位元級不變）。
        - `tool_output_max_chars`：非 None 時改用 episode 縮減 render（A1
          拍板 (a)）——user/assistant 訊息**完整保留**（不做 30 則／500 字元
          截斷），tool 輸出（tool_result 內容、tool_call 的 tool_calls 參數）
          保留 tool_name＋每則截斷至此字元數並標注 `[truncated N chars]`。
          縮減只發生在「寫入 inbox 檔案」這一步，db 原文不動；敏感偵測由
          importer 對縮減前的完整原文執行（_full_session_text），縮減不是
          漏檢敏感內容的通道。None＝沿用 2.4d-3 的既有 render（向後相容）。
        """
        inbox_dir = Path(inbox_dir)
        if inbox_dir.resolve() == self.db_path.parent.resolve():
            raise ValueError("拒絕把輸出寫進 Hermes 來源資料目錄")
        if not inbox_dir.is_dir():
            raise FileNotFoundError(f"inbox 目錄不存在：{inbox_dir}（不代建目錄，避免寫錯地方）")

        session = export["session"]
        session_id = session["session_id"]
        if not force:
            existing = self._find_existing_import(
                inbox_dir, session_id,
                first_message_id=first_message_id, last_message_id=last_message_id)
            if existing is not None:
                raise InboxAlreadyImportedError(session_id, existing)

        namespace = ("hermes" if source_profile in (None, "default")
                     else f"hermes/{source_profile}")
        event_id_range = (f"{namespace}:{session_id}:"
                          f"{first_message_id}..{last_message_id}")
        body = self._render_markdown(
            export, max_excerpt_events, full=full,
            episode_seq=episode_seq, capture_trigger=capture_trigger,
            event_id_range=event_id_range,
            source_profile=(None if source_profile in (None, "default")
                            else source_profile),
            tool_output_max_chars=tool_output_max_chars)
        path = inbox_dir / self.episode_inbox_filename(
            session_id, first_message_id, last_message_id)
        try:
            with open(path, "x", encoding="utf-8", newline="\n") as fh:
                fh.write(body)
        except FileExistsError:
            raise InboxAlreadyImportedError(session_id, path) from None
        return path

    @staticmethod
    def _render_frontmatter(export: dict, *, episode_seq: int | None = None,
                            capture_trigger: str | None = None,
                            event_id_range: str | None = None,
                            source_profile: str | None = None) -> list[str]:
        """claudecodeos.inbox.v1 frontmatter（docs/memory-taxonomy.md §5）。
        usefulness/sensitivity 固定 pending：adapter 不做內容判斷與敏感偵測，
        不假裝判斷完成——那是落地後呼叫端／consolidation 的責任。
        待處理/已處理狀態依政策不設欄位（目錄位置是唯一真相）。

        episode 檔（Stage 2.4d-3，additive、不升版，提案 §2）：呼叫端顯式
        傳入 `event_id_range`（episode 的 boundary）時直接採用，不從
        `export["events"]` 推算（export 內容可能只是 boundary 子集）；
        `episode_seq`／`capture_trigger` 給定時額外輸出對應 frontmatter 欄。
        兩者皆為 None 時＝legacy 檔，行為與 2.4c 完全一致。"""
        session = export["session"]
        session_id = session["session_id"]
        lines = [
            "---",
            "schema: claudecodeos.inbox.v1",
            "source: hermes-session",
            f"session_id: {session_id}",
        ]
        if event_id_range is None:
            raw_ids = [e["metadata"]["raw_message_id"] for e in export["events"]
                       if isinstance(e.get("metadata"), dict)
                       and isinstance(e["metadata"].get("raw_message_id"), int)]
            if raw_ids:
                event_id_range = f"hermes:{session_id}:{min(raw_ids)}..{max(raw_ids)}"
        if event_id_range:
            lines.append(f'event_id_range: "{event_id_range}"')
        if source_profile is not None:
            # A1：named profile 檔案的 profile 標記（default 檔不帶此欄——
            # additive、既有檔案與 default 新檔位元級不變）
            lines.append(f"source_profile: {source_profile}")
        if episode_seq is not None:
            lines.append(f"episode: {episode_seq}")
        if capture_trigger is not None:
            lines.append(f"capture_trigger: {capture_trigger}")
        lines += [
            "created_at: " + datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "usefulness: pending",
            "usefulness_reason: adapter 不做內容判斷；待依 memory-taxonomy.md §4.2 評定",
            "sensitivity: pending",
            "---",
            "",
        ]
        return lines

    @staticmethod
    def _truncate_tool_text(text: str, max_chars: int) -> str:
        """tool 輸出縮減（A1 拍板 (a)）：保留前 max_chars 字元＋標注
        `[truncated N chars]`（N＝被截去的字元數）。不超長時原樣回傳。"""
        if len(text) <= max_chars:
            return text
        return text[:max_chars] + f"…[truncated {len(text) - max_chars} chars]"

    @classmethod
    def _render_markdown(cls, export: dict, max_excerpt_events: int,
                         full: bool = False, *, episode_seq: int | None = None,
                         capture_trigger: str | None = None,
                         event_id_range: str | None = None,
                         source_profile: str | None = None,
                         tool_output_max_chars: int | None = None) -> str:
        session = export["session"]
        events = export["events"]
        lines = cls._render_frontmatter(export, episode_seq=episode_seq,
                                        capture_trigger=capture_trigger,
                                        event_id_range=event_id_range,
                                        source_profile=source_profile)
        reduced = tool_output_max_chars is not None
        excerpt_note = ("全部，工具呼叫略過" if full
                        else f"最多 {max_excerpt_events} 則，工具呼叫略過")
        lines += [
            f"# Hermes session 匯入 — {session['session_id']}",
            "",
            f"- 來源：hermes/{session['session_source']}",
            f"- 標題：{session['title'] or '(無標題)'}",
            f"- 模型：{session['model'] or '(未知)'}",
            f"- 期間：{session['started_at']} → {session['ended_at'] or '(進行中)'}",
            f"- 訊息數：{session['message_count']}（匯出 event 數：{len(events)}）",
            "",
            "```json",
            json.dumps(session, ensure_ascii=False, indent=2),
            "```",
            "",
        ]
        if reduced:
            # A1 拍板 (a) 的 episode 縮減 render：user/assistant 完整保留
            # （不做則數與字元截斷），tool 輸出保留 tool_name＋每則截斷。
            # 縮減只影響寫出的 inbox 檔——db 原文不動，敏感偵測在縮減前的
            # 完整原文上跑（bridge_importer._full_session_text）。
            lines += [
                "## 對話摘錄（user/assistant 完整保留；tool 輸出每則截至 "
                f"{tool_output_max_chars} 字元）",
                "",
            ]
            for event in events:
                if event["type"] == "meta":
                    continue
                content = event["content"].strip().replace("\r\n", "\n")
                md = event.get("metadata") or {}
                if event["type"] == "message":
                    lines.append(f"### [{event['timestamp']}] {event['role']}")
                    lines.append("")
                    lines.append(content or "(空白內容)")
                elif event["type"] == "tool_call":
                    calls = md.get("tool_calls") or []
                    names = ", ".join(
                        (c.get("function") or {}).get("name") or "(unknown)"
                        for c in calls if isinstance(c, dict)) or "(unknown)"
                    lines.append(f"### [{event['timestamp']}] {event['role']}"
                                 f"（tool_call: {names}）")
                    lines.append("")
                    if content:
                        lines.append(content)  # assistant 自身文字完整保留
                    serialized = json.dumps(calls, ensure_ascii=False) if calls \
                        else (md.get("tool_calls_raw") or "")
                    if serialized:
                        lines.append(cls._truncate_tool_text(
                            serialized, tool_output_max_chars))
                    if not content and not serialized:
                        lines.append("(空白內容)")
                else:  # tool_result
                    tool_name = md.get("tool_name") or "(unknown tool)"
                    lines.append(f"### [{event['timestamp']}] tool（{tool_name}）")
                    lines.append("")
                    lines.append(cls._truncate_tool_text(
                        content, tool_output_max_chars) or "(空白內容)")
                lines.append("")
        else:
            lines += [
                f"## 對話摘錄（只列 message 事件，{excerpt_note}）",
                "",
            ]
            # TODO(truncation)：預設摘錄「尾端 30 則 + 每則 500 字元」可能截掉
            # 有價值的上下文；本次主線是 idempotency，暫以 --full 提供完整匯出，
            # 更聰明的摘錄策略（依 usefulness 訊號挑段落）留待後續。
            # （episode 匯入路徑自 A1 起改走上面的縮減 render——tool 保留
            # tool_name＋截斷、user/assistant 完整保留。）
            message_events = [e for e in events if e["type"] == "message"]
            if not full:
                message_events = message_events[-max_excerpt_events:]
            for event in message_events:
                excerpt = event["content"].strip().replace("\r\n", "\n")
                if not full and len(excerpt) > 500:
                    excerpt = excerpt[:500] + "…（截斷）"
                lines.append(f"### [{event['timestamp']}] {event['role']}")
                lines.append("")
                lines.append(excerpt or "(空白內容)")
                lines.append("")
        lines.append("---")
        lines.append("由 hermes/session_adapter/adapter.py（read-only importer）產生，")
        lines.append("等待 consolidate-memory skill 整併；來源 session 資料未被修改。")
        lines.append("")
        return "\n".join(lines)


def has_landed_episode_file(inbox_dir: str | Path, session_id: str) -> bool:
    """episode 落地檔「存在性探測」的單一實作（Stage 2.4d-3，提案 §3.2）。

    inbox 本層＋.processed/＋.failed/ 是否已有該 session 的**任一** episode
    落地檔（不論 boundary）。只判斷存在與否，**不回填任何欄位**——回填規則
    仍只留給 `bridge_scanner.reconcile()` 一份實作。

    單一實作供 bridge_scanner 的 cursor 遺失防護（矩陣 #8）呼叫，取代
    2.4d-2 遺留的、scanner 自建的 `_has_landed_episode_files()`（收斂債務，
    見 docs/stage2.4d-episode-capture-proposal.md §3.2：「偵測用的『存在性
    探測』復用 importer 的同一 needle helper（單處實作），不複製 reconcile
    的回填邏輯」）——這裡放在 adapter.py 而非 bridge_importer.py／
    bridge_state.py：檔名產生與解析本來就是 adapter 的既有職責
    （`_find_existing_import`／`episode_inbox_filename`／
    `parse_inbox_filename` 都在這裡），且 bridge_scanner／bridge_importer
    都已經 import 這個 module，不需要新模組，也不會造成循環 import
    （scanner／importer 互不 import 對方）。純檔案系統操作，不需要開
    Hermes state.db，也不需要 HermesSessionAdapter 實例。
    """
    inbox_dir = Path(inbox_dir)
    dirs = [inbox_dir] + [inbox_dir / d for d in HermesSessionAdapter._ARCHIVE_SUBDIRS]
    for directory in dirs:
        if not directory.is_dir():
            continue
        for candidate in directory.glob("*.md"):
            parsed = HermesSessionAdapter.parse_inbox_filename(candidate.name)
            if (parsed and parsed["session_id"] == session_id
                    and parsed["first_message_id"] is not None):
                return True
    return False


# ---------- CLI ----------

def _cli():
    # Windows console 預設 cp950，session 內容常有 emoji/中文——強制 UTF-8
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Hermes session read-only importer")
    parser.add_argument("--db", default=None, help="state.db 路徑（預設自動偵測）")
    parser.add_argument("--snapshot", action="store_true",
                        help="先複製到 temp 再讀（避開 live WAL 鎖競爭）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="列出 sessions（normalized 摘要）")
    p_list.add_argument("--source", default=None, help="cli|tui|telegram|cron")

    p_export = sub.add_parser("export", help="匯出單一 session 為 normalized JSON")
    p_export.add_argument("session_id")
    p_export.add_argument("--include-inactive", action="store_true")

    p_inbox = sub.add_parser("to-inbox", help="把單一 session 寫成 memory/inbox/ 新檔案")
    p_inbox.add_argument("session_id")
    p_inbox.add_argument(
        "--inbox",
        default=str(Path(__file__).resolve().parent.parent.parent / "memory" / "inbox"),
    )
    p_inbox.add_argument(
        "--force", action="store_true",
        help="略過已匯入檢查（.processed/.failed/inbox 掃描）人工重匯；"
             "仍不覆寫 inbox 既有同名檔")
    p_inbox.add_argument(
        "--full", action="store_true",
        help="對話摘錄不截斷（預設：尾端 30 則、每則 500 字元）")

    args = parser.parse_args()
    with HermesSessionAdapter(db_path=args.db, snapshot=args.snapshot) as adapter:
        if args.cmd == "list":
            for s in adapter.list_sessions(source=args.source):
                print(f"{s['session_id']}  {s['session_source']:<9} "
                      f"msgs={s['message_count']:<5} started={s['started_at']}  "
                      f"title={s['title'] or ''}")
        elif args.cmd == "export":
            export = adapter.export_session(
                args.session_id, include_inactive=args.include_inactive)
            print(json.dumps(export, ensure_ascii=False, indent=2))
        elif args.cmd == "to-inbox":
            export = adapter.export_session(args.session_id)
            try:
                path = adapter.write_inbox_file(
                    export, args.inbox, force=args.force, full=args.full)
            except InboxAlreadyImportedError as exc:
                # 明確非零 exit（3）：已匯入過不是成功匯入，不靜默假裝成功
                print(f"already imported：session {exc.session_id} 已有落地檔 "
                      f"{exc.existing_path}，未重複落地（人工重匯用 --force）",
                      file=sys.stderr)
                sys.exit(3)
            print(f"已新增 inbox 檔案：{path}")


if __name__ == "__main__":
    _cli()
