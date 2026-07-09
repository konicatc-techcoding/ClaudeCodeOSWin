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
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

EVENT_SCHEMA = "claudecodeos.event.v1"
SESSION_SCHEMA = "claudecodeos.session.v1"
SOURCE_NAME = "hermes"

KNOWN_ROLES = {"user", "assistant", "tool", "system", "session_meta"}

# 正常訊息流之外的角色，一律歸成 meta 事件
_META_ROLES = {"session_meta"}


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
        self._snapshot_dir: str | None = None
        self._read_path = self.db_path
        if snapshot:
            self._read_path = self._make_snapshot()

    # ---------- 連線（唯一入口，強制 read-only） ----------

    def _make_snapshot(self) -> Path:
        """把 db（含 -wal/-shm，如果存在）複製到 temp 目錄，之後只讀副本。
        來源只被讀取，永不寫入。"""
        self._snapshot_dir = tempfile.mkdtemp(prefix="hermes_state_snapshot_")
        dest = Path(self._snapshot_dir) / self.db_path.name
        shutil.copy2(self.db_path, dest)
        for suffix in ("-wal", "-shm"):
            side = Path(str(self.db_path) + suffix)
            if side.is_file():
                shutil.copy2(side, Path(str(dest) + suffix))
        return dest

    def close(self):
        if self._snapshot_dir:
            shutil.rmtree(self._snapshot_dir, ignore_errors=True)
            self._snapshot_dir = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _connect(self) -> sqlite3.Connection:
        uri = f"file:{self._read_path.as_posix()}?mode=ro"
        try:
            conn = sqlite3.connect(uri, uri=True, timeout=10)
        except sqlite3.Error as exc:
            raise HermesSessionReadError(f"無法以 read-only 開啟 {self._read_path}：{exc}") from exc
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA query_only=ON")
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
        """回傳 normalized session 摘要（claudecodeos.session.v1）。"""
        query = (
            "SELECT id, source, user_id, model, started_at, ended_at, end_reason, "
            "message_count, title, session_key, chat_id, chat_type, thread_id, "
            "parent_session_id, archived FROM sessions"
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
        conn = self._connect()
        try:
            rows = conn.execute(query, params).fetchall()
        finally:
            conn.close()
        return [self._normalize_session(row) for row in rows]

    @staticmethod
    def _normalize_session(row) -> dict:
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
            },
        }

    def iter_events(self, session_id: str | None = None,
                    include_inactive: bool = False):
        """逐筆產出 normalized event（claudecodeos.event.v1）。
        單筆訊息壞掉不會中斷整批——壞欄位進 metadata.warnings。"""
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

    def export_session(self, session_id: str, include_inactive: bool = False) -> dict:
        """單一 session 的完整 normalized 匯出：{session, events}。"""
        sessions = [s for s in self.list_sessions() if s["session_id"] == session_id]
        if not sessions:
            raise KeyError(f"Hermes state.db 裡沒有 session：{session_id}")
        events = list(self.iter_events(session_id=session_id,
                                       include_inactive=include_inactive))
        return {"session": sessions[0], "events": events}

    # ---------- 落地（只新增，永不覆寫；由呼叫端決定要不要用） ----------

    # 已整併/失敗歸檔的子目錄（docs/memory-taxonomy.md：目錄位置是狀態的唯一真相）
    _ARCHIVE_SUBDIRS = (".processed", ".failed")

    @staticmethod
    def _frontmatter_session_id(path: Path) -> str | None:
        """讀檔案開頭的 YAML frontmatter，取 session_id（沒有就 None）。
        只掃前 50 行，容錯：讀不到、格式不對都當作沒有。"""
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                if fh.readline().strip() != "---":
                    return None
                for _ in range(50):
                    line = fh.readline()
                    if not line or line.strip() == "---":
                        return None
                    if line.startswith("session_id:"):
                        return line.split(":", 1)[1].strip().strip("\"'") or None
        except OSError:
            return None
        return None

    def _find_existing_import(self, inbox_dir: Path, session_id: str) -> Path | None:
        """在 inbox 本層與 .processed/ / .failed/ 找同 session 的既有落地檔。
        比對兩種方式（涵蓋舊時間戳檔名與其他來源命名）：
        1. 檔名含 `hermes_session_<session_id>` 子字串
           （新格式 hermes_session_<id>.md 與舊格式 <stamp>_hermes_session_<id>.md 都中）
        2. frontmatter 的 session_id 欄位相符
        """
        needle = f"hermes_session_{session_id}"
        dirs = [inbox_dir] + [inbox_dir / d for d in self._ARCHIVE_SUBDIRS]
        for directory in dirs:
            if not directory.is_dir():
                continue
            for candidate in sorted(directory.glob("*.md")):
                if needle in candidate.name:
                    return candidate
                if self._frontmatter_session_id(candidate) == session_id:
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

    @staticmethod
    def _render_frontmatter(export: dict) -> list[str]:
        """claudecodeos.inbox.v1 frontmatter（docs/memory-taxonomy.md §5）。
        usefulness/sensitivity 固定 pending：adapter 不做內容判斷與敏感偵測，
        不假裝判斷完成——那是落地後呼叫端／consolidation 的責任。
        待處理/已處理狀態依政策不設欄位（目錄位置是唯一真相）。"""
        session = export["session"]
        session_id = session["session_id"]
        raw_ids = [e["metadata"]["raw_message_id"] for e in export["events"]
                   if isinstance(e.get("metadata"), dict)
                   and isinstance(e["metadata"].get("raw_message_id"), int)]
        lines = [
            "---",
            "schema: claudecodeos.inbox.v1",
            "source: hermes-session",
            f"session_id: {session_id}",
        ]
        if raw_ids:
            lines.append(
                f'event_id_range: "hermes:{session_id}:{min(raw_ids)}..{max(raw_ids)}"')
        lines += [
            "created_at: " + datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "usefulness: pending",
            "usefulness_reason: adapter 不做內容判斷；待依 memory-taxonomy.md §4.2 評定",
            "sensitivity: pending",
            "---",
            "",
        ]
        return lines

    @classmethod
    def _render_markdown(cls, export: dict, max_excerpt_events: int,
                         full: bool = False) -> str:
        session = export["session"]
        events = export["events"]
        lines = cls._render_frontmatter(export)
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
            f"## 對話摘錄（只列 message 事件，{excerpt_note}）",
            "",
        ]
        # TODO(truncation)：預設摘錄「尾端 30 則 + 每則 500 字元」可能截掉
        # 有價值的上下文；本次主線是 idempotency，暫以 --full 提供完整匯出，
        # 更聰明的摘錄策略（依 usefulness 訊號挑段落）留待後續。
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
