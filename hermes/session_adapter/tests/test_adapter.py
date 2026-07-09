#!/usr/bin/env python3
"""hermes/session_adapter/tests/test_adapter.py — v0.1

HermesSessionAdapter 的單元測試。每個測試都在 temp 目錄用
fixtures/seed_state_db.sql 建一個假的 state.db，不碰真正的 Hermes 資料。

涵蓋：
1. 正常解析（sessions/events、type 推導、時間轉換、排序）
2. 欄位缺漏／格式損壞的容錯（NULL content、壞 tool_calls JSON、未知 role、
   壞 timestamp、整個檔案不是 SQLite）
3. read-only 保證（來源檔 bytes 不變、寫入語句被 SQLite 拒絕、
   inbox 輸出不覆寫既有檔案、拒絕寫進來源目錄）
4. normalized 輸出 schema 驗證（validate_event）

執行（Windows）：py -3.11 hermes/session_adapter/tests/test_adapter.py
執行（WSL/macOS）：python3 hermes/session_adapter/tests/test_adapter.py
"""
import hashlib
import shutil
import sqlite3
import sys
import tempfile
import unittest
from datetime import timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import adapter as adapter_module  # noqa: E402
from adapter import (  # noqa: E402
    EVENT_SCHEMA,
    SESSION_SCHEMA,
    HermesSessionAdapter,
    HermesSessionReadError,
    InboxAlreadyImportedError,
    validate_event,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SEED_SQL = FIXTURES / "seed_state_db.sql"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class AdapterTestBase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="hermes_adapter_test_"))
        self.db_path = self.tmpdir / "state.db"
        conn = sqlite3.connect(self.db_path)
        try:
            conn.executescript(SEED_SQL.read_text(encoding="utf-8"))
            conn.commit()
        finally:
            conn.close()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)


class TestNormalParse(AdapterTestBase):
    def test_list_sessions(self):
        adapter = HermesSessionAdapter(self.db_path)
        sessions = adapter.list_sessions()
        self.assertEqual(len(sessions), 3)
        by_id = {s["session_id"]: s for s in sessions}
        tg = by_id["20260630_183709_063b4e40"]
        self.assertEqual(tg["schema"], SESSION_SCHEMA)
        self.assertEqual(tg["source"], "hermes")
        self.assertEqual(tg["session_source"], "telegram")
        self.assertEqual(tg["title"], "Garmin 健康日報")
        self.assertEqual(tg["metadata"]["chat_id"], "1034113120")
        # epoch → ISO 8601 UTC
        self.assertTrue(tg["started_at"].startswith("2026-06-30"))
        self.assertTrue(tg["started_at"].endswith("+00:00"))
        self.assertIsNone(tg["ended_at"])

    def test_list_sessions_filter_by_source(self):
        adapter = HermesSessionAdapter(self.db_path)
        sessions = adapter.list_sessions(source="cli")
        self.assertEqual([s["session_id"] for s in sessions],
                         ["20260706_155721_18145a"])

    def test_event_types_and_order(self):
        adapter = HermesSessionAdapter(self.db_path)
        events = list(adapter.iter_events(session_id="20260630_183709_063b4e40"))
        self.assertEqual([e["type"] for e in events],
                         ["meta", "message", "tool_call", "tool_result", "message"])
        self.assertEqual([e["role"] for e in events],
                         ["session_meta", "user", "assistant", "tool", "assistant"])
        # event_id 穩定可去重
        self.assertEqual(events[1]["event_id"],
                         "hermes:20260630_183709_063b4e40:102")
        # tool_calls 有被 parse 成 list
        self.assertEqual(events[2]["metadata"]["tool_calls"][0]["id"], "call_abc123")
        self.assertEqual(events[3]["metadata"]["tool_name"], "terminal")
        # id 遞增排序
        ids = [e["metadata"]["raw_message_id"] for e in events]
        self.assertEqual(ids, sorted(ids))

    def test_inactive_messages_excluded_by_default(self):
        adapter = HermesSessionAdapter(self.db_path)
        events = list(adapter.iter_events(session_id="20260706_155721_18145a"))
        self.assertEqual(len(events), 1)  # compacted/inactive 那筆被排除
        all_events = list(adapter.iter_events(
            session_id="20260706_155721_18145a", include_inactive=True))
        self.assertEqual(len(all_events), 2)
        self.assertTrue(all_events[1]["metadata"]["compacted"])

    def test_export_session(self):
        adapter = HermesSessionAdapter(self.db_path)
        export = adapter.export_session("20260630_183709_063b4e40")
        self.assertEqual(export["session"]["session_id"], "20260630_183709_063b4e40")
        self.assertEqual(len(export["events"]), 5)

    def test_export_unknown_session_raises(self):
        adapter = HermesSessionAdapter(self.db_path)
        with self.assertRaises(KeyError):
            adapter.export_session("no_such_session")

    def test_snapshot_mode_reads_copy(self):
        with HermesSessionAdapter(self.db_path, snapshot=True) as adapter:
            self.assertNotEqual(adapter._read_path, self.db_path)
            self.assertEqual(len(adapter.list_sessions()), 3)
            snapshot_path = adapter._read_path
        self.assertFalse(snapshot_path.exists())  # close 後清掉副本


class TestTolerance(AdapterTestBase):
    def test_bad_rows_still_emit_events_with_warnings(self):
        adapter = HermesSessionAdapter(self.db_path)
        events = list(adapter.iter_events(session_id="20260707_000000_baddata"))
        self.assertEqual(len(events), 3)  # 沒有任何一筆讓整批中斷
        by_id = {e["metadata"]["raw_message_id"]: e for e in events}

        null_content = by_id[301]
        self.assertEqual(null_content["content"], "")
        self.assertTrue(any("content" in w for w in null_content["metadata"]["warnings"]))

        bad_json = by_id[302]
        self.assertIsNone(bad_json["metadata"]["tool_calls"])
        self.assertEqual(bad_json["metadata"]["tool_calls_raw"], "{{{not-valid-json")
        self.assertEqual(bad_json["type"], "message")  # parse 失敗不當 tool_call

        weird = by_id[303]
        self.assertEqual(weird["role"], "unknown")
        self.assertIsNone(weird["timestamp"])
        self.assertTrue(any("timestamp" in w for w in weird["metadata"]["warnings"]))

    def test_not_a_sqlite_file_raises_read_error(self):
        bogus = self.tmpdir / "not_a_db.db"
        bogus.write_bytes(b"this is not sqlite at all")
        with self.assertRaises(HermesSessionReadError):
            HermesSessionAdapter(bogus).list_sessions()

    def test_sqlite_without_expected_tables_raises_read_error(self):
        other = self.tmpdir / "other.db"
        conn = sqlite3.connect(other)
        conn.execute("CREATE TABLE foo (x)")
        conn.commit()
        conn.close()
        with self.assertRaises(HermesSessionReadError):
            HermesSessionAdapter(other).list_sessions()

    def test_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            HermesSessionAdapter(self.tmpdir / "nowhere.db")


class TestReadOnlyGuarantee(AdapterTestBase):
    def test_source_bytes_unchanged_after_full_read(self):
        before = _sha256(self.db_path)
        adapter = HermesSessionAdapter(self.db_path)
        adapter.list_sessions()
        list(adapter.iter_events())
        export = adapter.export_session("20260630_183709_063b4e40")
        inbox = self.tmpdir / "inbox"
        inbox.mkdir()
        adapter.write_inbox_file(export, inbox)
        self.assertEqual(_sha256(self.db_path), before)

    def test_connection_rejects_writes(self):
        adapter = HermesSessionAdapter(self.db_path)
        conn = adapter._connect()
        try:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("INSERT INTO messages (session_id, role, timestamp) "
                             "VALUES ('x', 'user', 1.0)")
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("DELETE FROM sessions")
        finally:
            conn.close()

    def test_refuses_to_write_inbox_into_source_dir(self):
        adapter = HermesSessionAdapter(self.db_path)
        export = adapter.export_session("20260630_183709_063b4e40")
        with self.assertRaises(ValueError):
            adapter.write_inbox_file(export, self.db_path.parent)

    def test_module_source_has_no_write_mode_open_on_db(self):
        """程式碼層面的靜態檢查：adapter.py 裡對 sqlite 的連線只有 mode=ro
        一種，且沒有任何非 read-only 的 sqlite3.connect。"""
        src = (Path(__file__).resolve().parent.parent / "adapter.py").read_text(
            encoding="utf-8")
        connect_count = src.count("sqlite3.connect")
        self.assertEqual(connect_count, 1, "adapter.py 應該只有一個 sqlite 連線入口")
        self.assertIn("mode=ro", src)


class TestInboxOutput(AdapterTestBase):
    def setUp(self):
        super().setUp()
        self.adapter = HermesSessionAdapter(self.db_path)
        self.export = self.adapter.export_session("20260630_183709_063b4e40")
        self.inbox = self.tmpdir / "inbox"
        self.inbox.mkdir()

    def test_creates_new_file_never_overwrites(self):
        path1 = self.adapter.write_inbox_file(self.export, self.inbox)
        content1 = path1.read_text(encoding="utf-8")
        # deterministic 檔名（無時間戳）
        self.assertEqual(path1.name, "hermes_session_20260630_183709_063b4e40.md")
        self.assertIn("Garmin 健康日報", content1)
        self.assertIn("read-only importer", content1)
        # 第二次呼叫 → 明確擋下，第一個檔案內容不被動到、不產生第二份
        with self.assertRaises(InboxAlreadyImportedError):
            self.adapter.write_inbox_file(self.export, self.inbox)
        self.assertEqual(path1.read_text(encoding="utf-8"), content1)
        self.assertEqual(len(list(self.inbox.glob("*.md"))), 1)

    def test_missing_inbox_dir_raises_instead_of_creating(self):
        with self.assertRaises(FileNotFoundError):
            self.adapter.write_inbox_file(self.export, self.tmpdir / "no_inbox")

    def test_markdown_excludes_tool_noise(self):
        path = self.adapter.write_inbox_file(self.export, self.inbox)
        content = path.read_text(encoding="utf-8")
        self.assertIn("請給我今天的健康日報", content)
        self.assertNotIn("call_abc123", content.split("```json")[0])  # 摘錄區不含 tool id


class TestInboxIdempotency(AdapterTestBase):
    """to-inbox 去重：同 session 重跑不產生重複檔（Stage 2 自動化前提）。"""

    SID = "20260630_183709_063b4e40"

    def setUp(self):
        super().setUp()
        self.adapter = HermesSessionAdapter(self.db_path)
        self.export = self.adapter.export_session(self.SID)
        self.inbox = self.tmpdir / "inbox"
        self.inbox.mkdir()

    # (1) 首次匯入建立 inbox 檔案
    def test_first_import_creates_inbox_file(self):
        path = self.adapter.write_inbox_file(self.export, self.inbox)
        self.assertTrue(path.is_file())
        self.assertEqual(path.name, f"hermes_session_{self.SID}.md")

    # (2) 同 session 第二次匯入不建立第二份——即使系統時間不同
    def test_second_import_blocked_even_at_different_time(self):
        self.adapter.write_inbox_file(self.export, self.inbox)

        real_datetime = adapter_module.datetime

        class _LaterDateTime(real_datetime):
            @classmethod
            def now(cls, tz=None):
                # 模擬「另一天再重跑」——舊實作會因時間戳不同而產生重複檔
                return real_datetime(2027, 3, 15, 12, 34, 56, tzinfo=timezone.utc)

        adapter_module.datetime = _LaterDateTime
        try:
            with self.assertRaises(InboxAlreadyImportedError) as ctx:
                self.adapter.write_inbox_file(self.export, self.inbox)
        finally:
            adapter_module.datetime = real_datetime
        self.assertEqual(ctx.exception.session_id, self.SID)
        self.assertEqual(len(list(self.inbox.glob("*.md"))), 1)

    # (3) 同 session 已在 .processed/（舊時間戳檔名格式）→ 不重新落地
    def test_session_in_processed_with_legacy_timestamp_name_blocks_import(self):
        processed = self.inbox / ".processed"
        processed.mkdir()
        legacy = processed / f"20260709T150648Z_hermes_session_{self.SID}.md"
        legacy.write_text("# 舊格式歸檔（無 frontmatter）\n", encoding="utf-8")

        with self.assertRaises(InboxAlreadyImportedError) as ctx:
            self.adapter.write_inbox_file(self.export, self.inbox)
        self.assertEqual(ctx.exception.existing_path, legacy)
        self.assertEqual(list(self.inbox.glob("*.md")), [])  # 沒有落地

    # (3b) .failed/ 同樣算已處理過
    def test_session_in_failed_blocks_import(self):
        failed = self.inbox / ".failed"
        failed.mkdir()
        (failed / f"20260701T000000Z_hermes_session_{self.SID}.md").write_text(
            "x\n", encoding="utf-8")
        with self.assertRaises(InboxAlreadyImportedError):
            self.adapter.write_inbox_file(self.export, self.inbox)
        self.assertEqual(list(self.inbox.glob("*.md")), [])

    # (3c) 歸檔檔名不含 session_id 時，靠 frontmatter 比對也要擋
    def test_session_in_processed_matched_by_frontmatter(self):
        processed = self.inbox / ".processed"
        processed.mkdir()
        (processed / "2026-07-01T00-00-00Z-some-other-name.md").write_text(
            "---\nschema: claudecodeos.inbox.v1\nsource: hermes-session\n"
            f"session_id: {self.SID}\n---\n\n內容\n", encoding="utf-8")
        with self.assertRaises(InboxAlreadyImportedError):
            self.adapter.write_inbox_file(self.export, self.inbox)
        self.assertEqual(list(self.inbox.glob("*.md")), [])

    # (4) 不同 session 各自可建立，互不干擾
    def test_different_sessions_each_create_their_own_file(self):
        other_export = self.adapter.export_session("20260706_155721_18145a")
        p1 = self.adapter.write_inbox_file(self.export, self.inbox)
        p2 = self.adapter.write_inbox_file(other_export, self.inbox)
        self.assertNotEqual(p1, p2)
        self.assertEqual(sorted(p.name for p in self.inbox.glob("*.md")), [
            f"hermes_session_{self.SID}.md",
            "hermes_session_20260706_155721_18145a.md",
        ])

    # (5) force：略過 .processed 掃描可重匯，但仍不覆寫 inbox 既有同名檔
    def test_force_bypasses_processed_scan_but_never_overwrites(self):
        processed = self.inbox / ".processed"
        processed.mkdir()
        (processed / f"20260709T150648Z_hermes_session_{self.SID}.md").write_text(
            "x\n", encoding="utf-8")
        path = self.adapter.write_inbox_file(self.export, self.inbox, force=True)
        self.assertTrue(path.is_file())
        content = path.read_text(encoding="utf-8")
        # 同名檔已在 inbox 本層：force 也不覆寫
        with self.assertRaises(InboxAlreadyImportedError):
            self.adapter.write_inbox_file(self.export, self.inbox, force=True)
        self.assertEqual(path.read_text(encoding="utf-8"), content)


class TestInboxFrontmatter(AdapterTestBase):
    """claudecodeos.inbox.v1 frontmatter（docs/memory-taxonomy.md §5）。"""

    def setUp(self):
        super().setUp()
        self.adapter = HermesSessionAdapter(self.db_path)
        self.export = self.adapter.export_session("20260630_183709_063b4e40")
        self.inbox = self.tmpdir / "inbox"
        self.inbox.mkdir()

    def test_frontmatter_fields(self):
        path = self.adapter.write_inbox_file(self.export, self.inbox)
        lines = path.read_text(encoding="utf-8").split("\n")
        self.assertEqual(lines[0], "---")
        end = lines.index("---", 1)
        fm = lines[1:end]
        joined = "\n".join(fm)
        self.assertIn("schema: claudecodeos.inbox.v1", joined)
        self.assertIn("source: hermes-session", joined)
        self.assertIn("session_id: 20260630_183709_063b4e40", joined)
        self.assertIn("created_at: ", joined)
        # adapter 不判斷內容價值與敏感度——一律 pending，不假裝判斷完成
        self.assertIn("usefulness: pending", joined)
        self.assertIn("sensitivity: pending", joined)
        # event_id_range 對齊 claudecodeos.event.v1 的去重 key（rowid 101..105）
        self.assertIn('event_id_range: "hermes:20260630_183709_063b4e40:101..105"',
                      joined)
        # 目錄位置是狀態唯一真相：不設 consolidation 狀態欄位
        self.assertNotIn("status:", joined)

    def test_frontmatter_session_id_helper_reads_own_output(self):
        path = self.adapter.write_inbox_file(self.export, self.inbox)
        self.assertEqual(
            HermesSessionAdapter._frontmatter_session_id(path),
            "20260630_183709_063b4e40")

    def test_full_flag_disables_truncation(self):
        long_body = self.adapter.write_inbox_file(
            self.export, self.inbox, full=True).read_text(encoding="utf-8")
        self.assertNotIn("…（截斷）", long_body)
        self.assertIn("全部，工具呼叫略過", long_body)


class TestSchemaValidation(AdapterTestBase):
    def test_all_emitted_events_are_valid(self):
        adapter = HermesSessionAdapter(self.db_path)
        for event in adapter.iter_events(include_inactive=True):
            self.assertEqual(validate_event(event), [],
                             f"event 不合法：{event['event_id']}")
            self.assertEqual(event["schema"], EVENT_SCHEMA)

    def test_validate_event_reports_problems(self):
        self.assertTrue(validate_event("not a dict"))
        problems = validate_event({"schema": "wrong.schema", "source": "hermes"})
        self.assertTrue(any("缺少欄位" in p for p in problems))
        self.assertTrue(any("schema" in p for p in problems))
        bad_type = validate_event({
            "schema": EVENT_SCHEMA, "source": "hermes", "session_id": "s",
            "event_id": "e", "timestamp": None, "role": "user",
            "type": "bogus_type", "content": "", "metadata": {},
        })
        self.assertTrue(any("未知的 type" in p for p in bad_type))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    unittest.main(verbosity=2)
