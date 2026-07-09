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

輸出落地慣例（對齊 ARCHITECTURE.md 第 4 節）：
- adapter 本身不主動寫任何東西；要落地時由呼叫端呼叫 write_inbox_file()，
  在 memory/inbox/ 新增一個帶時間戳的新檔案——符合「背景管線只能新增
  inbox 檔案，不能編輯既有檔案或 memory/*.md 正本」的規則。之後由
  consolidate-memory skill 整併進正本。

CLI 用法（手動測試/操作用；Windows 用 `py -3.11`，WSL 用 python3）：
    python3 hermes/session_adapter/adapter.py list [--source telegram] [--db PATH]
    python3 hermes/session_adapter/adapter.py export <session_id> [--db PATH]
    python3 hermes/session_adapter/adapter.py to-inbox <session_id> [--inbox DIR] [--db PATH]
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

    def write_inbox_file(self, export: dict, inbox_dir: str | Path,
                         max_excerpt_events: int = 30) -> Path:
        """把 export_session() 的結果寫成 memory/inbox/ 的一個「新」檔案。
        - open mode="x"：既有檔案永遠不會被覆寫；撞名就加序號。
        - 拒絕寫進來源 state.db 所在目錄（read-only 保證的一部分）。
        """
        inbox_dir = Path(inbox_dir)
        if inbox_dir.resolve() == self.db_path.parent.resolve():
            raise ValueError("拒絕把輸出寫進 Hermes 來源資料目錄")
        if not inbox_dir.is_dir():
            raise FileNotFoundError(f"inbox 目錄不存在：{inbox_dir}（不代建目錄，避免寫錯地方）")

        session = export["session"]
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        base = f"{stamp}_hermes_session_{session['session_id']}"
        body = self._render_markdown(export, max_excerpt_events)

        for attempt in range(1000):
            name = f"{base}.md" if attempt == 0 else f"{base}_{attempt}.md"
            path = inbox_dir / name
            try:
                with open(path, "x", encoding="utf-8", newline="\n") as fh:
                    fh.write(body)
                return path
            except FileExistsError:
                continue
        raise RuntimeError("inbox 檔名重試次數用盡")

    @staticmethod
    def _render_markdown(export: dict, max_excerpt_events: int) -> str:
        session = export["session"]
        events = export["events"]
        lines = [
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
            f"## 對話摘錄（只列 message 事件，最多 {max_excerpt_events} 則，工具呼叫略過）",
            "",
        ]
        message_events = [e for e in events if e["type"] == "message"]
        for event in message_events[-max_excerpt_events:]:
            excerpt = event["content"].strip().replace("\r\n", "\n")
            if len(excerpt) > 500:
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
            path = adapter.write_inbox_file(export, args.inbox)
            print(f"已新增 inbox 檔案：{path}")


if __name__ == "__main__":
    _cli()
