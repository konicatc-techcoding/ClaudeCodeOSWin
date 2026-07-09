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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from adapter import (  # noqa: E402
    EVENT_SCHEMA,
    SESSION_SCHEMA,
    HermesSessionAdapter,
    HermesSessionReadError,
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

    def test_creates_new_file_only(self):
        path1 = self.adapter.write_inbox_file(self.export, self.inbox)
        content1 = path1.read_text(encoding="utf-8")
        path2 = self.adapter.write_inbox_file(self.export, self.inbox)
        # 兩次呼叫 → 兩個不同檔案；第一個檔案內容不被動到
        self.assertNotEqual(path1, path2)
        self.assertEqual(path1.read_text(encoding="utf-8"), content1)
        self.assertIn("hermes_session_20260630_183709_063b4e40", path1.name)
        self.assertIn("Garmin 健康日報", content1)
        self.assertIn("read-only importer", content1)

    def test_missing_inbox_dir_raises_instead_of_creating(self):
        with self.assertRaises(FileNotFoundError):
            self.adapter.write_inbox_file(self.export, self.tmpdir / "no_inbox")

    def test_markdown_excludes_tool_noise(self):
        path = self.adapter.write_inbox_file(self.export, self.inbox)
        content = path.read_text(encoding="utf-8")
        self.assertIn("請給我今天的健康日報", content)
        self.assertNotIn("call_abc123", content.split("```json")[0])  # 摘錄區不含 tool id


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
