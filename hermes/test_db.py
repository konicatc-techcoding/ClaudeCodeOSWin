#!/usr/bin/env python3
"""hermes/test_db.py — v0.1

hermes/db.py 的邏輯測試：claim 的原子性/排序、retry vs dead-letter 判斷、
reaper 的 stale job 回收、session TTL。用暫存的 SQLite 檔案，不動真正的
hermes/jobs.db。

執行：.venv/Scripts/python.exe hermes/test_db.py
"""
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import db  # noqa: E402


class DbTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self._original_db_path = db.DB_PATH
        db.DB_PATH = Path(self._tmp.name)
        db.init_db()

    def tearDown(self):
        db.DB_PATH = self._original_db_path
        Path(self._tmp.name).unlink(missing_ok=True)

    def test_claim_on_empty_queue_returns_none(self):
        self.assertIsNone(db.claim_next_job("worker-1"))

    def test_enqueue_and_claim(self):
        job_id = db.enqueue("manual", "hello")
        job = db.claim_next_job("worker-1")
        self.assertEqual(job["id"], job_id)
        self.assertEqual(job["attempts"], 1)

        row = db.show_job(job_id)
        self.assertEqual(row["status"], "running")
        self.assertEqual(row["worker_id"], "worker-1")

    def test_claim_is_exclusive(self):
        db.enqueue("manual", "only one job")
        first = db.claim_next_job("worker-1")
        second = db.claim_next_job("worker-2")
        self.assertIsNotNone(first)
        self.assertIsNone(second)

    def test_claim_respects_priority(self):
        db.enqueue("manual", "low priority", priority=0)
        high_id = db.enqueue("manual", "high priority", priority=10)
        job = db.claim_next_job("worker-1")
        self.assertEqual(job["id"], high_id)

    def test_claim_ignores_future_next_attempt_at(self):
        job_id = db.enqueue("manual", "not ready yet")
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        with db._db() as conn:
            conn.execute("UPDATE jobs SET next_attempt_at=? WHERE id=?", (future, job_id))
        self.assertIsNone(db.claim_next_job("worker-1"))

    def test_mark_completed(self):
        job_id = db.enqueue("manual", "hello")
        db.claim_next_job("worker-1")
        db.mark_completed(job_id, "the answer")
        row = db.show_job(job_id)
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["result"], "the answer")
        self.assertIsNotNone(row["completed_at"])

    def test_mark_completed_stores_cost_usd(self):
        job_id = db.enqueue("manual", "hello")
        db.claim_next_job("worker-1")
        db.mark_completed(job_id, "the answer", cost_usd=0.1234)
        row = db.show_job(job_id)
        self.assertAlmostEqual(row["cost_usd"], 0.1234)

    def test_mark_failed_records_cost_when_provided(self):
        job_id = db.enqueue("manual", "flaky", max_attempts=3)
        db.claim_next_job("worker-1")
        db.mark_failed(job_id, "CoS reported is_error", cost_usd=0.05)
        row = db.show_job(job_id)
        self.assertAlmostEqual(row["cost_usd"], 0.05)

    def test_mark_failed_preserves_cost_when_not_provided(self):
        job_id = db.enqueue("manual", "flaky", max_attempts=3)
        db.claim_next_job("worker-1")
        db.mark_failed(job_id, "CoS reported is_error", cost_usd=0.05)
        # 模擬下一次嘗試在 invoke_cos.sh 這層就失敗，沒有 cost 資訊（cost_usd=None）
        db.claim_next_job("worker-1")
        db.mark_failed(job_id, "invoke_cos.sh exit code 1", cost_usd=None)
        row = db.show_job(job_id)
        self.assertAlmostEqual(row["cost_usd"], 0.05)  # 沒被 None 洗掉

    def test_mark_failed_requeues_when_attempts_below_max(self):
        job_id = db.enqueue("manual", "flaky", max_attempts=3)
        db.claim_next_job("worker-1")  # attempts -> 1
        db.mark_failed(job_id, "boom")
        row = db.show_job(job_id)
        self.assertEqual(row["status"], "queued")
        self.assertEqual(row["error_message"], "boom")
        self.assertIsNotNone(row["next_attempt_at"])
        self.assertGreater(row["next_attempt_at"], db._now_iso())

    def test_mark_failed_dead_letters_after_max_attempts(self):
        job_id = db.enqueue("manual", "always fails", max_attempts=1)
        db.claim_next_job("worker-1")  # attempts -> 1 == max_attempts
        db.mark_failed(job_id, "boom")
        row = db.show_job(job_id)
        self.assertEqual(row["status"], "dead_letter")

    def test_reap_requeues_stale_running_job(self):
        job_id = db.enqueue("manual", "stuck", max_attempts=3)
        db.claim_next_job("worker-1")
        stale_time = (datetime.now(timezone.utc) - timedelta(seconds=9999)).isoformat()
        with db._db() as conn:
            conn.execute("UPDATE jobs SET locked_at=? WHERE id=?", (stale_time, job_id))
        reaped = db.reap_stale_jobs(stale_after_seconds=600)
        self.assertEqual(reaped, 1)
        row = db.show_job(job_id)
        self.assertEqual(row["status"], "queued")

    def test_reap_dead_letters_stale_job_past_max_attempts(self):
        job_id = db.enqueue("manual", "stuck forever", max_attempts=1)
        db.claim_next_job("worker-1")  # attempts -> 1 == max_attempts
        stale_time = (datetime.now(timezone.utc) - timedelta(seconds=9999)).isoformat()
        with db._db() as conn:
            conn.execute("UPDATE jobs SET locked_at=? WHERE id=?", (stale_time, job_id))
        db.reap_stale_jobs(stale_after_seconds=600)
        row = db.show_job(job_id)
        self.assertEqual(row["status"], "dead_letter")

    def test_reap_ignores_fresh_running_job(self):
        job_id = db.enqueue("manual", "still working")
        db.claim_next_job("worker-1")
        reaped = db.reap_stale_jobs(stale_after_seconds=600)
        self.assertEqual(reaped, 0)
        row = db.show_job(job_id)
        self.assertEqual(row["status"], "running")

    def test_session_resume_within_ttl(self):
        db.upsert_session("telegram:1", "session-abc")
        self.assertEqual(db.get_resumable_session("telegram:1"), "session-abc")

    def test_session_resume_expired(self):
        db.upsert_session("telegram:1", "session-abc")
        stale_time = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        with db._db() as conn:
            conn.execute(
                "UPDATE sessions SET last_used_at=? WHERE thread_id=?", (stale_time, "telegram:1")
            )
        self.assertIsNone(db.get_resumable_session("telegram:1"))

    def test_session_resume_unknown_thread(self):
        self.assertIsNone(db.get_resumable_session("telegram:does-not-exist"))

    def test_session_resume_stateless_job_is_none(self):
        self.assertIsNone(db.get_resumable_session(None))

    def test_migrate_adds_cost_usd_to_legacy_db(self):
        # 模擬「舊版」資料庫：手動建一個沒有 cost_usd 欄位的 jobs 表
        legacy_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        legacy_tmp.close()
        legacy_path = Path(legacy_tmp.name)
        legacy_conn = db.sqlite3.connect(legacy_path)
        legacy_conn.execute("""
            CREATE TABLE jobs (
                id TEXT PRIMARY KEY, source TEXT NOT NULL, payload TEXT NOT NULL,
                prompt TEXT NOT NULL, thread_id TEXT, session_id TEXT,
                status TEXT NOT NULL DEFAULT 'queued', priority INTEGER NOT NULL DEFAULT 0,
                attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 3,
                next_attempt_at TEXT, worker_id TEXT, locked_at TEXT, result TEXT,
                error_message TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                completed_at TEXT
            )
        """)
        legacy_conn.execute(
            "INSERT INTO jobs (id, source, payload, prompt, created_at, updated_at) "
            "VALUES ('legacy-1', 'manual', '{}', 'old job', 'x', 'x')"
        )
        legacy_conn.commit()
        legacy_conn.close()

        try:
            db.DB_PATH = legacy_path
            db.init_db()  # 應該要在既有資料庫上補上 cost_usd 欄位，不動到既有資料列
            row = db.show_job("legacy-1")
            self.assertIsNotNone(row, "既有的舊資料列應該還在，migration 不能把資料弄丟")
            self.assertIn("cost_usd", row.keys())
            self.assertIsNone(row["cost_usd"])
            self.assertEqual(row["prompt"], "old job")
        finally:
            legacy_path.unlink(missing_ok=True)

    def test_list_jobs_filters_by_status(self):
        db.enqueue("manual", "a")
        b_id = db.enqueue("manual", "b")
        db.claim_next_job("worker-1")  # claims whichever is first (priority/created_at order); just check filtering works
        running = db.list_jobs(status="running")
        queued = db.list_jobs(status="queued")
        self.assertEqual(len(running), 1)
        self.assertEqual(len(queued), 1)


if __name__ == "__main__":
    unittest.main()
