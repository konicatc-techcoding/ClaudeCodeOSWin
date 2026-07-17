#!/usr/bin/env python3
"""hermes/test_db_triage.py — Stage 2.5a

`enqueue_once`／`requeue_dead_letter`／schema migration 的回歸測試。
對應提案 docs/stage2.5-episode-triage-proposal.md（v6）第 14 節測試矩陣中
屬於 2.5a 的項目：第 2、3、4、5、6、7、8、9、14、21、22、23、30、31、32
項，外加 migration 冪等性（§16 2.5a DoD 第一條）。
用暫存的 SQLite 檔案，不動真正的 hermes/jobs.db。

執行：.venv/Scripts/python.exe hermes/test_db_triage.py
"""
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import db  # noqa: E402

SOURCE = "bridge_episode_triage"
EK = "hermes:sid-abc:1..5"
PV1 = "bridge_episode_triage_v1"
PV2 = "bridge_episode_triage_v2"
HASH_A = "a" * 64
HASH_B = "b" * 64


class TriageDbTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self._original_db_path = db.DB_PATH
        db.DB_PATH = Path(self._tmp.name)
        db.init_db()

    def tearDown(self):
        db.DB_PATH = self._original_db_path
        for suffix in ("", "-wal", "-shm"):
            Path(self._tmp.name + suffix).unlink(missing_ok=True)

    # ---- helpers ----

    def _row(self, job_id):
        return db.show_job(job_id)

    def _audit_rows(self, job_id):
        with db._db() as conn:
            return conn.execute(
                "SELECT * FROM job_requeue_events WHERE job_id=? ORDER BY requeue_seq",
                (job_id,),
            ).fetchall()

    def _job_count(self):
        with db._db() as conn:
            return conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"]

    def _make_dead_letter_job(self, external_key=EK):
        job_id, created = db.enqueue_once(
            SOURCE, external_key, PV1, HASH_A, "triage prompt",
            payload={"artifact": "memory/inbox/x.md"},
        )
        self.assertTrue(created)
        claimed = db.claim_next_job("worker-1")  # attempts -> 1 == max_attempts(1)
        self.assertEqual(claimed["id"], job_id)
        db.mark_failed(job_id, "boom", cost_usd=0.05)
        self.assertEqual(self._row(job_id)["status"], "dead_letter")
        return job_id


class MigrationTests(TriageDbTestCase):
    """§16 2.5a DoD：migration 冪等（五欄＋三元組 unique index＋新表）。"""

    def test_new_columns_index_and_table_exist(self):
        with db._db() as conn:
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(jobs)")}
            for col in ("external_key", "payload_hash", "prompt_version",
                        "requeue_count", "last_requeued_at"):
                self.assertIn(col, cols)
            idx = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND name='idx_jobs_triage_identity'"
            ).fetchone()
            self.assertIsNotNone(idx)
            table = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='job_requeue_events'"
            ).fetchone()
            self.assertIsNotNone(table)

    def test_init_db_is_idempotent(self):
        db.init_db()
        db.init_db()  # 重複跑不可失敗、不可重複加欄位

    def test_migrate_legacy_db_preserves_existing_rows(self):
        # 模擬 2.5a 之前的資料庫：沒有五個新欄、沒有稽核表，且已有資料
        legacy_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        legacy_tmp.close()
        legacy_path = Path(legacy_tmp.name)
        legacy_conn = sqlite3.connect(legacy_path)
        legacy_conn.execute("""
            CREATE TABLE jobs (
                id TEXT PRIMARY KEY, source TEXT NOT NULL, payload TEXT NOT NULL,
                prompt TEXT NOT NULL, thread_id TEXT, session_id TEXT,
                status TEXT NOT NULL DEFAULT 'queued', priority INTEGER NOT NULL DEFAULT 0,
                attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 3,
                next_attempt_at TEXT, worker_id TEXT, locked_at TEXT, result TEXT,
                cost_usd REAL, error_message TEXT, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, completed_at TEXT, delivered_at TEXT
            )
        """)
        legacy_conn.execute(
            "INSERT INTO jobs (id, source, payload, prompt, created_at, updated_at) "
            "VALUES ('legacy-1', 'rss', '{}', 'old job', 'x', 'x')"
        )
        legacy_conn.execute(
            "INSERT INTO jobs (id, source, payload, prompt, created_at, updated_at) "
            "VALUES ('legacy-2', 'rss', '{}', 'old job 2', 'x', 'x')"
        )
        legacy_conn.commit()
        legacy_conn.close()

        try:
            db.DB_PATH = legacy_path
            db.init_db()
            db.init_db()  # 冪等：第二次也不能失敗
            row = db.show_job("legacy-1")
            self.assertIsNotNone(row, "migration 不能弄丟既有資料")
            self.assertEqual(row["prompt"], "old job")
            self.assertIn("external_key", row.keys())
            self.assertIsNone(row["external_key"])
            self.assertEqual(row["requeue_count"], 0)
            # 兩筆 legacy 列 external_key/prompt_version 皆 NULL，unique index
            # 建立時不得視為互相衝突
            self.assertIsNotNone(db.show_job("legacy-2"))
        finally:
            db.DB_PATH = Path(self._tmp.name)
            for suffix in ("", "-wal", "-shm"):
                Path(str(legacy_path) + suffix).unlink(missing_ok=True)


class EnqueueOnceTests(TriageDbTestCase):
    def test_new_tuple_creates_job_with_expected_fields(self):
        job_id, created = db.enqueue_once(
            SOURCE, EK, PV1, HASH_A, "triage prompt", payload={"k": 1},
        )
        self.assertTrue(created)
        row = self._row(job_id)
        self.assertEqual(row["status"], "queued")
        self.assertEqual(row["source"], SOURCE)
        self.assertEqual(row["external_key"], EK)
        self.assertEqual(row["prompt_version"], PV1)
        self.assertEqual(row["payload_hash"], HASH_A)
        self.assertEqual(row["requeue_count"], 0)
        self.assertEqual(json.loads(row["payload"]), {"k": 1})
        # 矩陣第 9 項（2.5a 部分）：這個 job source 從未使用 thread_id
        self.assertIsNone(row["thread_id"])
        # §3.2 Option A：at-most-one automatic attempt
        self.assertEqual(row["max_attempts"], 1)

    def test_same_tuple_same_hash_is_noop(self):
        # 矩陣第 2 項
        job_id1, created1 = db.enqueue_once(SOURCE, EK, PV1, HASH_A, "p")
        job_id2, created2 = db.enqueue_once(SOURCE, EK, PV1, HASH_A, "p")
        self.assertTrue(created1)
        self.assertFalse(created2)
        self.assertEqual(job_id1, job_id2)
        self.assertEqual(self._job_count(), 1)

    def test_same_tuple_different_hash_fails_closed(self):
        # 矩陣第 3 項
        job_id, _ = db.enqueue_once(SOURCE, EK, PV1, HASH_A, "p")
        with self.assertRaises(db.TriageEnqueueConflict):
            db.enqueue_once(SOURCE, EK, PV1, HASH_B, "p")
        self.assertEqual(self._job_count(), 1)
        self.assertEqual(self._row(job_id)["payload_hash"], HASH_A)

    def test_concurrent_enqueue_once_creates_exactly_one_job(self):
        # 矩陣第 4 項：並行呼叫同一 identity tuple → 恰好建立一筆 job
        n = 8
        barrier = threading.Barrier(n)
        results, errors = [], []

        def call():
            barrier.wait()
            try:
                results.append(db.enqueue_once(SOURCE, EK, PV1, HASH_A, "p"))
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=call) for _ in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(results), n)
        self.assertEqual(sum(1 for _, created in results if created), 1)
        self.assertEqual(len({job_id for job_id, _ in results}), 1)
        self.assertEqual(self._job_count(), 1)

    def test_new_prompt_version_creates_new_job(self):
        # 矩陣第 5 項：同 event_id、不同 prompt_version → 新 job，不衝突
        job_id1, _ = db.enqueue_once(SOURCE, EK, PV1, HASH_A, "p")
        db.claim_next_job("worker-1")
        db.mark_completed(job_id1, '{"decision": "memory_only"}')
        job_id2, created2 = db.enqueue_once(SOURCE, EK, PV2, HASH_A, "p")
        self.assertTrue(created2)
        self.assertNotEqual(job_id1, job_id2)
        # 舊版本結果不被覆蓋
        self.assertEqual(self._row(job_id1)["status"], "completed")
        self.assertEqual(self._row(job_id1)["result"], '{"decision": "memory_only"}')
        self.assertEqual(self._job_count(), 2)

    def test_crash_then_reap_goes_to_dead_letter_not_queued(self):
        # 矩陣第 6 項：max_attempts=1 下回收後直接 dead_letter，不重新排入
        job_id, _ = db.enqueue_once(SOURCE, EK, PV1, HASH_A, "p")
        db.claim_next_job("worker-1")  # attempts -> 1 == max_attempts
        stale_time = (datetime.now(timezone.utc) - timedelta(seconds=9999)).isoformat()
        with db._db() as conn:
            conn.execute("UPDATE jobs SET locked_at=? WHERE id=?", (stale_time, job_id))
        db.reap_stale_jobs(stale_after_seconds=600)
        row = self._row(job_id)
        self.assertEqual(row["status"], "dead_letter")
        self.assertEqual(self._job_count(), 1)

    def test_legacy_sources_with_null_identity_do_not_conflict(self):
        # 矩陣第 14 項：三元組 unique index 下，external_key IS NULL AND
        # prompt_version IS NULL 的既有 source 列彼此不互相衝突
        ids = [
            db.enqueue("rss", "a"),
            db.enqueue("rss", "b"),
            db.enqueue("telegram", "c", thread_id="telegram:1"),
            db.enqueue("telegram", "d", thread_id="telegram:2"),
            db.enqueue("cron", "e"),
            db.enqueue("cron", "f"),
        ]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(self._job_count(), 6)
        for job_id in ids:
            row = self._row(job_id)
            self.assertIsNone(row["external_key"])
            self.assertIsNone(row["prompt_version"])


class RequeueDeadLetterTests(TriageDbTestCase):
    def test_requeue_resets_job_and_writes_audit_row(self):
        # 矩陣第 7 項
        job_id = self._make_dead_letter_job()
        before = self._row(job_id)
        event = db.requeue_dead_letter(job_id, "cli:alice", reason="transient failure")

        row = self._row(job_id)
        self.assertEqual(row["status"], "queued")
        self.assertEqual(row["attempts"], 0)
        self.assertIsNone(row["next_attempt_at"])
        self.assertIsNone(row["worker_id"])
        self.assertIsNone(row["locked_at"])
        self.assertEqual(row["requeue_count"], 1)
        self.assertIsNotNone(row["last_requeued_at"])
        # identity／payload_hash／prompt_version／payload 不變
        self.assertEqual(row["source"], before["source"])
        self.assertEqual(row["external_key"], before["external_key"])
        self.assertEqual(row["prompt_version"], before["prompt_version"])
        self.assertEqual(row["payload_hash"], before["payload_hash"])
        self.assertEqual(row["payload"], before["payload"])
        # 不建立第二筆 job
        self.assertEqual(self._job_count(), 1)

        audits = self._audit_rows(job_id)
        self.assertEqual(len(audits), 1)
        audit = audits[0]
        self.assertEqual(audit["requeue_seq"], 1)
        self.assertEqual(audit["actor"], "cli:alice")
        self.assertEqual(audit["reason"], "transient failure")
        # previous_* 捕捉 requeue 之前的值
        self.assertEqual(audit["previous_error"], "boom")
        self.assertEqual(audit["previous_attempts"], 1)

        self.assertEqual(event["job_id"], job_id)
        self.assertEqual(event["requeue_seq"], 1)
        self.assertEqual(event["actor"], "cli:alice")

    def test_requeue_seq_is_monotonic_across_requeues(self):
        job_id = self._make_dead_letter_job()
        db.requeue_dead_letter(job_id, "cli:alice")
        # 第二次失敗 → 再次 dead_letter → 再次 requeue
        db.claim_next_job("worker-2")
        db.mark_failed(job_id, "boom again")
        event = db.requeue_dead_letter(job_id, "cli:bob")
        self.assertEqual(event["requeue_seq"], 2)
        row = self._row(job_id)
        self.assertEqual(row["requeue_count"], 2)
        audits = self._audit_rows(job_id)
        self.assertEqual([a["requeue_seq"] for a in audits], [1, 2])
        self.assertEqual(audits[1]["previous_error"], "boom again")

    def _assert_rejected_without_side_effects(self, job_id):
        before = dict(self._row(job_id)) if self._row(job_id) else None
        count_before = self._job_count()
        with self.assertRaises(db.RequeueRejected):
            db.requeue_dead_letter(job_id, "cli:alice")
        after = dict(self._row(job_id)) if self._row(job_id) else None
        self.assertEqual(before, after)
        self.assertEqual(self._audit_rows(job_id), [])
        self.assertEqual(self._job_count(), count_before)

    def test_rejects_non_dead_letter_states_cleanly(self):
        # 矩陣第 8 項（決定性測試分類 1：乾淨 rowcount=0，不使用 fault injection）
        # queued
        job_id, _ = db.enqueue_once(SOURCE, EK, PV1, HASH_A, "p")
        self._assert_rejected_without_side_effects(job_id)
        # running
        db.claim_next_job("worker-1")
        self._assert_rejected_without_side_effects(job_id)
        # completed
        db.mark_completed(job_id, "done")
        self._assert_rejected_without_side_effects(job_id)
        # 查無此 job_id
        with self.assertRaises(db.RequeueRejected):
            db.requeue_dead_letter("no-such-job-id", "cli:alice")
        with db._db() as conn:
            n = conn.execute("SELECT COUNT(*) AS n FROM job_requeue_events").fetchone()["n"]
        self.assertEqual(n, 0)


class ActorValidationTests(TriageDbTestCase):
    """矩陣第 30–32 項。"""

    def _assert_rejected_before_any_db_access(self, actor):
        original_get_connection = db.get_connection

        def _fail(*args, **kwargs):
            raise AssertionError("actor 驗證失敗時不得開啟任何 DB 連線")

        db.get_connection = _fail
        try:
            with self.assertRaises(ValueError):
                db.requeue_dead_letter("any-job-id", actor)
        finally:
            db.get_connection = original_get_connection

    def test_empty_actor_rejected_without_touching_db(self):
        job_id = self._make_dead_letter_job()
        self._assert_rejected_before_any_db_access("")
        self.assertEqual(self._row(job_id)["status"], "dead_letter")
        self.assertEqual(self._audit_rows(job_id), [])

    def test_whitespace_actor_rejected_without_touching_db(self):
        job_id = self._make_dead_letter_job()
        self._assert_rejected_before_any_db_access("   ")
        self.assertEqual(self._row(job_id)["status"], "dead_letter")
        self.assertEqual(self._audit_rows(job_id), [])

    def test_actor_is_normalized_with_strip(self):
        job_id = self._make_dead_letter_job()
        event = db.requeue_dead_letter(job_id, " cli:alice ")
        self.assertEqual(event["actor"], "cli:alice")
        audits = self._audit_rows(job_id)
        self.assertEqual(audits[0]["actor"], "cli:alice")
        self.assertEqual(self._row(job_id)["status"], "queued")


class FaultInjectionTests(TriageDbTestCase):
    """矩陣第 21、22 項：§4.1d 決定性 fault injection（monkeypatch
    db._execute_requeue_update，不動共用 _db()／連線設定、不用 sleep()、
    不依賴真實 SQLite 鎖爭用）。"""

    def test_busy_then_state_changed_normalizes_to_rejected(self):
        # 矩陣第 21 項（分類 2：busy 之後發現狀態已變 → RequeueRejected）
        job_id = self._make_dead_letter_job()
        calls = []
        original = db._execute_requeue_update

        def inject(conn, now, jid):
            calls.append(1)
            # 在 requeue_dead_letter 觸發重新查詢之前，用另一個獨立的直接
            # DB 操作模擬「已被另一個並行呼叫搶先贏走」（決定性，不跑第二個
            # process）——搶先者只改 status，不動 requeue_count／稽核表，
            # 以便斷言「這次被拒絕的呼叫」自己零寫入。
            winner = sqlite3.connect(db.DB_PATH, timeout=30)
            try:
                with winner:
                    winner.execute(
                        "UPDATE jobs SET status='queued' WHERE id=?", (jid,)
                    )
            finally:
                winner.close()
            raise sqlite3.OperationalError("database is locked (fault injection)")

        db._execute_requeue_update = inject
        try:
            with self.assertRaises(db.RequeueRejected) as ctx:
                db.requeue_dead_letter(job_id, "cli:alice")
        finally:
            db._execute_requeue_update = original

        # 結果是 RequeueRejected，不是 RequeueRetryableDBError
        self.assertNotIsInstance(ctx.exception, db.RequeueRetryableDBError)
        self.assertEqual(len(calls), 1)
        # 不新增任何稽核列
        self.assertEqual(self._audit_rows(job_id), [])
        # 這次呼叫不改動 requeue_count／last_requeued_at
        row = self._row(job_id)
        self.assertEqual(row["requeue_count"], 0)
        self.assertIsNone(row["last_requeued_at"])
        # 不建立第二筆 job
        self.assertEqual(self._job_count(), 1)

    def test_busy_with_state_unchanged_raises_retryable(self):
        # 矩陣第 22 項（分類 3：busy 之後發現狀態未變 → RequeueRetryableDBError）
        job_id = self._make_dead_letter_job()
        before = dict(self._row(job_id))
        calls = []
        original = db._execute_requeue_update

        def inject(conn, now, jid):
            calls.append(1)
            raise sqlite3.OperationalError("database is locked (fault injection)")

        db._execute_requeue_update = inject
        try:
            with self.assertRaises(db.RequeueRetryableDBError) as ctx:
                db.requeue_dead_letter(job_id, "cli:alice")
        finally:
            db._execute_requeue_update = original

        # 不得被誤報成 RequeueRejected
        self.assertNotIsInstance(ctx.exception, db.RequeueRejected)
        self.assertEqual(ctx.exception.job_id, job_id)
        self.assertIsInstance(ctx.exception.original, sqlite3.OperationalError)
        # conditional UPDATE 沒有被自動重試（底層呼叫恰好一次）
        self.assertEqual(len(calls), 1)
        row = self._row(job_id)
        # status 仍是 dead_letter（未被轉成 queued）
        self.assertEqual(row["status"], "dead_letter")
        # requeue_count／last_requeued_at 不變
        self.assertEqual(row["requeue_count"], 0)
        self.assertIsNone(row["last_requeued_at"])
        # 原本的失敗資訊沒有被清空
        self.assertEqual(row["error_message"], before["error_message"])
        self.assertEqual(row["result"], before["result"])
        self.assertEqual(row["cost_usd"], before["cost_usd"])
        # 零筆新增稽核列；沒有第二筆 job
        self.assertEqual(self._audit_rows(job_id), [])
        self.assertEqual(self._job_count(), 1)


class ConcurrentRequeueIntegrationTests(TriageDbTestCase):
    """矩陣第 23 項：真實並行整合測試——不做 fault injection、不用 sleep()、
    不假設哪一方贏，只驗證聚合不變量。"""

    def test_concurrent_requeue_aggregate_invariants(self):
        job_id = self._make_dead_letter_job()
        n = 2
        barrier = threading.Barrier(n)
        successes, failures = [], []

        def call(tag):
            barrier.wait()
            try:
                successes.append(db.requeue_dead_letter(job_id, f"cli:{tag}"))
            except (db.RequeueRejected, db.RequeueRetryableDBError) as exc:
                failures.append(exc)

        threads = [threading.Thread(target=call, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 至多一個呼叫成功完成 requeue
        self.assertLessEqual(len(successes), 1)
        self.assertEqual(len(successes) + len(failures), n)
        # 最終恰好新增一列稽核、requeue_count 恰好遞增一次
        self.assertEqual(len(self._audit_rows(job_id)), 1)
        self.assertEqual(self._row(job_id)["requeue_count"], 1)
        # 沒有建立重複的 job
        self.assertEqual(self._job_count(), 1)


if __name__ == "__main__":
    unittest.main()
