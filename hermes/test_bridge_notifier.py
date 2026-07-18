#!/usr/bin/env python3
"""hermes/test_bridge_notifier.py — Stage 2.7a

notifier 核心＋notification_log migration 的測試。對應提案
docs/stage2.7-notification-scheduling-proposal.md v2 §9「2.7a」DoD：

- migration 冪等測試（notification_log 表、UNIQUE(message_key)、append-only）
- 事件偵測分類測試：三種 decision（memory_only 絕不通知）＋triage/dispatch
  dead_letter＋dispatch completed＋anomaly（defensive parse＋stale record）
  ——沙箱 jobs.db fixture
- message-key 決定性測試（agentos27:<event_type>:<subject_id>、全小寫、
  無時間戳、重跑恆同）
- 訊息樣板快照測試（斷言 episode/artifact 內容、triage reason、jobs.result
  全文不出現；summary 截斷 200 字元＋「未經人審」標註）
- 冪等測試（mock send：重跑零重送；send 失敗不落 log、下輪補送；
  「送成功未落 log」fault-injection 驗證第二層語意——mock ledger no-op）
- --dry-run 零寫入零外呼（偵測/過濾邏輯與真實模式一致）
- 唯讀鐵律（§0.1）：notifier 跑完 jobs／dispatch_records 一列不變、零新
  dispatch job
- enqueuer --max-new 截斷測試在 test_bridge_triage_enqueuer.py（同屬 2.7a）

全部沙箱化：暫存 jobs.db、mock subprocess——不打真 Slack、不呼叫任何
模型、不碰真實 hermes/jobs.db。

執行：.venv/Scripts/python.exe hermes/test_bridge_notifier.py
"""
import contextlib
import io
import json
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bridge_dispatch  # noqa: E402
import bridge_notifier as bn  # noqa: E402
import db  # noqa: E402

TRIAGE_SOURCE = bn.TRIAGE_SOURCE
DISPATCH_SOURCE = bn.DISPATCH_SOURCE
PV = "bridge_episode_triage_v2"
HASH = "c" * 64


def canonical_result(decision="action_candidate", owner="engineering",
                     summary="摘要文字", reason="理由文字", pv=PV):
    return json.dumps(
        {"decision": decision, "summary": summary, "suggested_owner": owner,
         "reason": reason, "prompt_version": pv},
        ensure_ascii=False, sort_keys=True)


def _fake_proc(returncode=0, stdout="mock sent", stderr=""):
    return types.SimpleNamespace(returncode=returncode, stdout=stdout,
                                 stderr=stderr)


class FakeLedgerSend:
    """模擬 hermes send 的 message-key ledger（冪等第二層）：同 key 重送
    no-op（exit 0、不重貼）。posts＝實際「貼到 Slack」的次數。"""

    def __init__(self, returncode=0):
        self.returncode = returncode
        self.calls: list[list[str]] = []
        self.posts: list[str] = []
        self._seen: set[str] = set()

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        key = argv[argv.index("--message-key") + 1]
        if self.returncode != 0:
            return _fake_proc(self.returncode, "", "mock failure")
        if key in self._seen:
            return _fake_proc(0, "already sent (ledger no-op)")
        self._seen.add(key)
        self.posts.append(key)
        return _fake_proc(0, "sent")


class NotifierTestCase(unittest.TestCase):
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

    # ---- fixtures ----

    def _make_triage_job(self, event_id, pv=PV, result=None, **kw):
        job_id, created = db.enqueue_once(
            TRIAGE_SOURCE, event_id, pv, HASH, "triage prompt",
            payload={"event_id": event_id,
                     "artifact_hint": "memory/inbox/x.md",
                     "payload_hash": HASH, "prompt_version": pv})
        self.assertTrue(created)
        db.mark_completed(job_id, result if result is not None
                          else canonical_result(pv=pv, **kw))
        return job_id

    def _make_dispatch_job(self, event_id, status="completed",
                           cost=0.846, result="dispatch result text"):
        job_id, created = db.enqueue_once(
            DISPATCH_SOURCE, event_id, "bridge_domain_dispatch_v1", HASH,
            "dispatch prompt", max_attempts=1)
        self.assertTrue(created)
        self._force_status(job_id, status, cost=cost, result=result)
        return job_id

    def _force_status(self, job_id, status, cost=None, result=None,
                      error="mock error"):
        """fixture 專用：直接把沙箱 job 推進到目標狀態（completed/
        dead_letter）——只為佈置偵測輸入，不經 worker。"""
        with db._db() as conn:
            if status == "completed":
                conn.execute(
                    "UPDATE jobs SET status='completed', result=?, "
                    "cost_usd=?, completed_at=updated_at WHERE id=?",
                    (result, cost, job_id))
            else:
                conn.execute(
                    "UPDATE jobs SET status=?, cost_usd=?, error_message=? "
                    "WHERE id=?", (status, cost, error, job_id))

    def _make_triage_dead_letter(self, event_id):
        job_id, created = db.enqueue_once(
            TRIAGE_SOURCE, event_id, PV, HASH, "triage prompt",
            max_attempts=1)
        self.assertTrue(created)
        self._force_status(job_id, "dead_letter")
        return job_id

    def _detect(self):
        return bn.detect_events(Path(db.DB_PATH))

    def _run_notify(self, dry_run=False, send=None, channel="CTEST"):
        """send＝可呼叫（攔 subprocess.run）；None＝一律成功的 mock。"""
        send = send or (lambda argv, **kw: _fake_proc(0))
        with mock.patch.object(bn.subprocess, "run", side_effect=send):
            return bn.run_notify(dry_run=dry_run, channel=channel,
                                 send_cli="mock-hermes")

    def _log_rows(self):
        with db._db() as conn:
            return conn.execute(
                "SELECT * FROM notification_log ORDER BY message_key").fetchall()

    def _cli(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = bn._cli(argv)
        return code, out.getvalue(), err.getvalue()


# ---------- migration（§2.3／§9 2.7a DoD 第 1 項） ----------

class NotificationLogMigrationTests(NotifierTestCase):
    def test_table_columns_exact(self):
        with db._db() as conn:
            cols = {r["name"] for r in conn.execute(
                "PRAGMA table_info(notification_log)")}
        self.assertEqual(cols, {"message_key", "event_type", "subject_id",
                                "channel", "sent_at", "send_result"})

    def test_migration_idempotent_preserves_rows(self):
        db.init_db()
        db.init_db()
        db.record_notification("agentos27:anomaly:j1", "anomaly", "j1",
                               "CTEST", send_result="exit=0")
        db.init_db()
        rows = self._log_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["message_key"], "agentos27:anomaly:j1")
        self.assertEqual(rows[0]["channel"], "CTEST")
        self.assertIsNotNone(rows[0]["sent_at"])

    def test_migration_on_legacy_db_adds_table(self):
        """2.6 時代的舊 jobs.db（沒有 notification_log）——migration 之後
        表補上、既有 jobs 列原封不動。"""
        legacy = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        legacy.close()
        try:
            conn = sqlite3.connect(legacy.name)
            conn.execute(
                "CREATE TABLE jobs (id TEXT PRIMARY KEY, source TEXT NOT NULL,"
                " payload TEXT NOT NULL, prompt TEXT NOT NULL, thread_id TEXT,"
                " session_id TEXT, status TEXT NOT NULL DEFAULT 'queued',"
                " priority INTEGER NOT NULL DEFAULT 0,"
                " attempts INTEGER NOT NULL DEFAULT 0,"
                " max_attempts INTEGER NOT NULL DEFAULT 3, next_attempt_at TEXT,"
                " worker_id TEXT, locked_at TEXT, result TEXT,"
                " error_message TEXT, created_at TEXT NOT NULL,"
                " updated_at TEXT NOT NULL, completed_at TEXT)")
            conn.execute(
                "INSERT INTO jobs (id, source, payload, prompt, created_at,"
                " updated_at) VALUES ('old1', 'telegram', '{}', 'p', 't', 't')")
            conn.commit()
            conn.close()
            db.DB_PATH = Path(legacy.name)
            db.init_db()
            with db._db() as conn:
                cols = {r["name"] for r in conn.execute(
                    "PRAGMA table_info(notification_log)")}
                old = conn.execute(
                    "SELECT * FROM jobs WHERE id='old1'").fetchone()
            self.assertTrue(cols)
            self.assertEqual(old["source"], "telegram")
        finally:
            db.DB_PATH = Path(self._tmp.name)
            for suffix in ("", "-wal", "-shm"):
                Path(legacy.name + suffix).unlink(missing_ok=True)

    def test_unique_message_key_enforced(self):
        db.record_notification("agentos27:anomaly:j1", "anomaly", "j1", "C")
        with self.assertRaises(sqlite3.IntegrityError):
            db.record_notification("agentos27:anomaly:j1", "anomaly", "j1", "C")
        self.assertEqual(len(self._log_rows()), 1)


# ---------- message-key（§2.4） ----------

class MessageKeyTests(unittest.TestCase):
    def test_deterministic_format_no_timestamp(self):
        k1 = bn.build_message_key("candidate_pending", "abc-123")
        k2 = bn.build_message_key("candidate_pending", "abc-123")
        self.assertEqual(k1, k2)
        self.assertEqual(k1, "agentos27:candidate_pending:abc-123")

    def test_lowercased(self):
        self.assertEqual(bn.build_message_key("ANOMALY", "JOB-X"),
                         "agentos27:anomaly:job-x")

    def test_distinct_across_event_types_same_subject(self):
        keys = {bn.build_message_key(et, "j1") for et in bn.EVENT_TYPES}
        self.assertEqual(len(keys), len(bn.EVENT_TYPES))


# ---------- 事件偵測分類（§3／§9 2.7a DoD 第 2 項） ----------

class DetectionTests(NotifierTestCase):
    def test_memory_only_never_notified(self):
        self._make_triage_job("hermes:s1:1..5", decision="memory_only")
        self.assertEqual(self._detect(), [])

    def test_candidate_without_record_is_pending(self):
        job_id = self._make_triage_job("hermes:s1:1..5")
        events = self._detect()
        self.assertEqual(len(events), 1)
        e = events[0]
        self.assertEqual(e["event_type"], "candidate_pending")
        self.assertEqual(e["subject_id"], job_id)
        self.assertEqual(e["event_id"], "hermes:s1:1..5")
        self.assertEqual(e["message_key"],
                         f"agentos27:candidate_pending:{job_id}")

    def test_candidate_with_proposed_record_is_pending(self):
        job_id = self._make_triage_job("hermes:s1:1..5")
        db.register_dispatch_record(job_id, "hermes:s1:1..5", "engineering",
                                    "tester")
        events = self._detect()
        self.assertEqual([e["event_type"] for e in events],
                         ["candidate_pending"])

    def test_candidate_already_decided_not_notified(self):
        """approved/rejected/dispatched：人工操作時人在場（§3.2）。"""
        for status_setup in ("approved", "rejected", "dispatched"):
            eid = f"hermes:{status_setup}:1..5"
            job_id = self._make_triage_job(eid)
            db.register_dispatch_record(job_id, eid, "engineering", "tester")
            if status_setup == "rejected":
                db.reject_dispatch(job_id, "tester")
            else:
                db.approve_dispatch(job_id, "tester", "task desc")
                if status_setup == "dispatched":
                    db.mark_dispatched(job_id, f"dj-{status_setup}", "tester")
        self.assertEqual(self._detect(), [])

    def test_needs_review_notified(self):
        job_id = self._make_triage_job("hermes:s1:1..5",
                                       decision="needs_review")
        events = self._detect()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "needs_review")
        self.assertEqual(events[0]["subject_id"], job_id)

    def test_triage_dead_letter_notified(self):
        job_id = self._make_triage_dead_letter("hermes:s1:1..5")
        events = self._detect()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "triage_dead_letter")
        self.assertEqual(events[0]["subject_id"], job_id)

    def test_dispatch_completed_and_dead_letter_notified(self):
        j_ok = self._make_dispatch_job("hermes:a:1..5", status="completed",
                                       cost=0.846)
        j_dead = self._make_dispatch_job("hermes:b:1..5",
                                         status="dead_letter", cost=0.1)
        events = self._detect()
        by_type = {e["event_type"]: e for e in events}
        self.assertEqual(set(by_type), {"dispatch_completed",
                                        "dispatch_dead_letter"})
        self.assertEqual(by_type["dispatch_completed"]["subject_id"], j_ok)
        self.assertEqual(by_type["dispatch_dead_letter"]["subject_id"], j_dead)
        self.assertIn("0.846", by_type["dispatch_completed"]["message"])

    def test_queued_running_dispatch_jobs_not_notified(self):
        db.enqueue_once(DISPATCH_SOURCE, "hermes:q:1..2",
                        "bridge_domain_dispatch_v1", HASH,
                        "prompt", max_attempts=1)
        self.assertEqual(self._detect(), [])

    def test_parse_anomaly_notified(self):
        job_id, _ = db.enqueue_once(TRIAGE_SOURCE, "hermes:bad:1..2", PV,
                                    HASH, "prompt")
        db.mark_completed(job_id, "not-json{{{")
        events = self._detect()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "anomaly")
        self.assertEqual(events[0]["subject_id"], job_id)

    def test_stale_record_is_anomaly_not_pending(self):
        """同 episode 既有 record 指向舊版 triage job（§3.2 anomaly 涵蓋
        stale record）——新基準不是 candidate_pending。"""
        eid = "hermes:s1:1..5"
        old_id = self._make_triage_job(eid, pv="bridge_episode_triage_v1")
        db.register_dispatch_record(old_id, eid, "engineering", "tester")
        new_id = self._make_triage_job(eid, pv="bridge_episode_triage_v2")
        events = self._detect()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "anomaly")
        self.assertEqual(events[0]["subject_id"], new_id)
        self.assertIn("stale record", events[0]["message"])

    def test_superseded_old_version_not_notified(self):
        eid = "hermes:s1:1..5"
        self._make_triage_job(eid, pv="bridge_episode_triage_v1")
        new_id = self._make_triage_job(eid, pv="bridge_episode_triage_v2")
        events = self._detect()
        self.assertEqual(len(events), 1)  # 只有基準筆，舊筆不通知
        self.assertEqual(events[0]["subject_id"], new_id)

    def test_detection_deterministic_order_and_keys(self):
        self._make_triage_job("hermes:a:1..5")
        self._make_triage_job("hermes:b:1..5", decision="needs_review")
        self._make_dispatch_job("hermes:c:1..5")
        first = self._detect()
        second = self._detect()
        self.assertEqual([e["message_key"] for e in first],
                         [e["message_key"] for e in second])
        self.assertEqual(len({e["message_key"] for e in first}), 3)


# ---------- 訊息樣板（§3.3／§9 2.7a DoD 快照＋防洩漏） ----------

class MessageTemplateTests(NotifierTestCase):
    def test_candidate_pending_snapshot(self):
        event = bn._make_event(
            "candidate_pending", "job-123", "hermes:s1:1..5",
            suggested_owner="engineering", summary="這是摘要")
        expected = (
            "[AgentOS 2.7] candidate_pending：新 triage 候選待人工核准\n"
            "event_id：hermes:s1:1..5\n"
            "triage_job：job-123\n"
            "suggested_owner：engineering（僅供參考，分派仍依 delegation "
            "policy）\n"
            "summary（模型摘要，未經人審）：這是摘要\n"
            "下一步（人工）：python3 hermes/bridge_dispatch.py list "
            "--actor <identifier>，再逐筆 approve/reject（核准 gate "
            "100% 人工，§0.1）")
        self.assertEqual(event["message"], expected)
        self.assertEqual(event["message_key"],
                         "agentos27:candidate_pending:job-123")

    def test_dispatch_completed_snapshot(self):
        event = bn._make_event("dispatch_completed", "job-9",
                               "hermes:s1:1..5", cost_usd=0.846)
        expected = (
            "[AgentOS 2.7] dispatch_completed：dispatch job 已完成\n"
            "event_id：hermes:s1:1..5\n"
            "dispatch_job：job-9\n"
            "cost_usd：0.846\n"
            "結果全文不入通知（§3.3 拍板）——查看：python3 "
            "hermes/bridge_dispatch.py status")
        self.assertEqual(event["message"], expected)

    def test_summary_truncated_at_200_with_label(self):
        long_summary = "甲" * 250
        event = bn._make_event("candidate_pending", "j1", "e1",
                               suggested_owner="engineering",
                               summary=long_summary)
        self.assertIn("模型摘要，未經人審", event["message"])
        self.assertIn("甲" * 200 + "…（已截斷至 200 字元）", event["message"])
        self.assertNotIn("甲" * 201, event["message"])

    def test_short_summary_not_truncated(self):
        self.assertEqual(bn.truncate_summary("甲" * 200), "甲" * 200)
        self.assertEqual(bn.truncate_summary("短摘要"), "短摘要")

    def test_no_untrusted_content_in_any_message(self):
        """§3.3 防洩漏：episode/artifact 內容、triage reason、jobs.result
        全文、dispatch error_message 都不出現在任何通知訊息。"""
        reason_sentinel = "REASON-SENTINEL-不該出現"
        result_sentinel = "RESULT-SENTINEL-不該出現"
        error_sentinel = "ERROR-SENTINEL-不該出現"
        artifact_sentinel = "memory/inbox/x.md"  # artifact 位置提示也不入通知
        self._make_triage_job("hermes:a:1..5", reason=reason_sentinel)
        self._make_triage_job("hermes:b:1..5", decision="needs_review",
                              reason=reason_sentinel)
        self._make_dispatch_job("hermes:c:1..5", status="completed",
                                result=result_sentinel)
        j = self._make_dispatch_job("hermes:d:1..5", status="dead_letter")
        self._force_status(j, "dead_letter", error=error_sentinel)
        events = self._detect()
        self.assertEqual(len(events), 4)
        blob = "\n\n".join(e["message"] for e in events)
        self.assertNotIn(reason_sentinel, blob)
        self.assertNotIn(result_sentinel, blob)
        self.assertNotIn(error_sentinel, blob)
        self.assertNotIn(artifact_sentinel, blob)

    def test_dead_letter_messages_point_to_manual_requeue(self):
        j1 = self._make_triage_dead_letter("hermes:a:1..5")
        j2 = self._make_dispatch_job("hermes:b:1..5", status="dead_letter")
        by_type = {e["event_type"]: e for e in self._detect()}
        for et, jid in (("triage_dead_letter", j1),
                        ("dispatch_dead_letter", j2)):
            msg = by_type[et]["message"]
            self.assertIn(f"logs/hermes/{jid}.log", msg)
            self.assertIn(f"python3 hermes/db.py requeue {jid}", msg)


# ---------- 冪等與失敗語意（§2.3／§5／§8） ----------

class IdempotencyTests(NotifierTestCase):
    def _fixture_three_events(self):
        self._make_triage_job("hermes:a:1..5")
        self._make_triage_job("hermes:b:1..5", decision="needs_review")
        self._make_dispatch_job("hermes:c:1..5")

    def test_send_success_writes_log_and_rerun_sends_nothing(self):
        self._fixture_three_events()
        fake = FakeLedgerSend()
        result = self._run_notify(send=fake)
        self.assertEqual(len(result["sent"]), 3)
        self.assertEqual(result["failed"], [])
        self.assertEqual(len(self._log_rows()), 3)
        self.assertEqual(len(fake.posts), 3)
        # 重跑：零重送（notification_log 第一層過濾，send CLI 連呼叫都沒有）
        result2 = self._run_notify(send=fake)
        self.assertEqual(result2["sent"], [])
        self.assertEqual(len(result2["already_notified"]), 3)
        self.assertEqual(len(fake.calls), 3)  # 沒有第 4 次呼叫
        self.assertEqual(len(self._log_rows()), 3)

    def test_send_failure_no_log_then_next_run_resends(self):
        self._make_triage_job("hermes:a:1..5")
        result = self._run_notify(send=FakeLedgerSend(returncode=1))
        self.assertEqual(result["sent"], [])
        self.assertEqual(len(result["failed"]), 1)
        self.assertEqual(self._log_rows(), [])  # 失敗不落 log（§2.3）
        # 下輪補送：成功 → 落 log
        result2 = self._run_notify(send=FakeLedgerSend())
        self.assertEqual(len(result2["sent"]), 1)
        self.assertEqual(len(self._log_rows()), 1)

    def test_sent_but_not_logged_gap_covered_by_second_layer(self):
        """§2.3／§8 fault-injection：送成功、寫 log 前 crash → 下輪重送同
        key → mock ledger no-op（Slack 端恰好一則）→ 本輪補寫 log。"""
        self._make_triage_job("hermes:a:1..5")
        fake = FakeLedgerSend()
        with mock.patch.object(db, "record_notification",
                               side_effect=RuntimeError("crash before log")):
            with self.assertRaises(RuntimeError):
                self._run_notify(send=fake)
        self.assertEqual(len(fake.posts), 1)      # 已真的送出一次
        self.assertEqual(self._log_rows(), [])    # 但 log 沒寫成
        # 下輪：同 key 重送 → ledger no-op（posts 不增）→ 補寫 log
        result = self._run_notify(send=fake)
        self.assertEqual(len(result["sent"]), 1)
        self.assertEqual(len(fake.posts), 1)      # Slack 端仍恰好一則
        self.assertEqual(len(self._log_rows()), 1)

    def test_send_cli_unavailable_aborts_fail_loud(self):
        self._fixture_three_events()

        def missing_cli(argv, **kw):
            raise FileNotFoundError("mock-hermes not found")

        result = self._run_notify(send=missing_cli)
        self.assertIsNotNone(result["aborted"])
        self.assertEqual(result["sent"], [])
        self.assertEqual(self._log_rows(), [])  # 零通知被標記為已送出

    def test_partial_failure_continues_and_flags(self):
        """一筆失敗不擋其他筆（per-item 獨立）；失敗筆下輪補送。"""
        self._fixture_three_events()
        keys_to_fail = set()

        def flaky(argv, **kw):
            key = argv[argv.index("--message-key") + 1]
            if "needs_review" in key:
                keys_to_fail.add(key)
                return _fake_proc(1, "", "boom")
            return _fake_proc(0)

        result = self._run_notify(send=flaky)
        self.assertEqual(len(result["sent"]), 2)
        self.assertEqual(len(result["failed"]), 1)
        self.assertEqual(len(self._log_rows()), 2)
        result2 = self._run_notify()  # 全成功 mock：只補失敗那筆
        self.assertEqual(len(result2["sent"]), 1)
        self.assertEqual({r["message_key"] for r in self._log_rows()},
                         {e["message_key"] for e in result["detected"]})


# ---------- --dry-run（零寫入零外呼；§9 2.7a DoD） ----------

class DryRunTests(NotifierTestCase):
    def test_dry_run_zero_writes_zero_subprocess(self):
        self._make_triage_job("hermes:a:1..5")
        with mock.patch.object(bn.subprocess, "run") as run_mock:
            result = bn.run_notify(dry_run=True, channel="CTEST",
                                   send_cli="mock-hermes")
        run_mock.assert_not_called()
        self.assertEqual(len(result["pending"]), 1)
        self.assertEqual(self._log_rows(), [])

    def test_dry_run_does_not_create_missing_db(self):
        missing = Path(self._tmp.name).parent / "no-such-jobs-27a.db"
        missing.unlink(missing_ok=True)
        result = bn.run_notify(dry_run=True, jobs_db=missing)
        self.assertFalse(result["jobs_db_exists"])
        self.assertFalse(missing.exists())
        db.DB_PATH = Path(self._tmp.name)

    def test_dry_run_pending_matches_real_mode(self):
        """偵測與冪等過濾邏輯一致：dry-run 的 pending＝真實模式將發送的
        集合（含已通知過的會被同樣略過）。"""
        self._make_triage_job("hermes:a:1..5")
        self._make_triage_job("hermes:b:1..5", decision="needs_review")
        real = self._run_notify()  # 送出 2、落 log 2
        self._make_dispatch_job("hermes:c:1..5")  # 新事件
        dry = bn.run_notify(dry_run=True, channel="CTEST",
                            send_cli="mock-hermes")
        self.assertEqual(len(dry["already_notified"]), 2)
        self.assertEqual([e["event_type"] for e in dry["pending"]],
                         ["dispatch_completed"])
        real2 = self._run_notify()
        self.assertEqual([e["message_key"] for e in dry["pending"]],
                         [e["message_key"] for e in real2["sent"]])

    def test_cli_dry_run_lists_key_and_full_message(self):
        job_id = self._make_triage_job("hermes:a:1..5")
        code, out, err = self._cli(["--jobs-db", self._tmp.name, "notify",
                                    "--dry-run", "--channel", "CTEST",
                                    "--send-cli", "mock-hermes"])
        self.assertEqual(code, 0)
        self.assertIn(f"agentos27:candidate_pending:{job_id}", out)
        self.assertIn("新 triage 候選待人工核准", out)   # 完整訊息內容
        self.assertIn("模型摘要，未經人審", out)
        self.assertIn("零寫入、零外呼", out)


# ---------- 唯讀鐵律（§0.1）與 CLI ----------

class ReadOnlyIroncladTests(NotifierTestCase):
    def _snapshot(self, table):
        with db._db() as conn:
            return [tuple(r) for r in conn.execute(
                f"SELECT * FROM {table} ORDER BY rowid")]

    def test_notifier_never_mutates_jobs_or_dispatch_tables(self):
        self._make_triage_job("hermes:a:1..5")
        job_id = self._make_triage_job("hermes:b:1..5")
        db.register_dispatch_record(job_id, "hermes:b:1..5", "engineering",
                                    "tester")
        self._make_dispatch_job("hermes:c:1..5")
        self._make_triage_dead_letter("hermes:d:1..5")
        before = {t: self._snapshot(t) for t in
                  ("jobs", "dispatch_records", "dispatch_events",
                   "job_requeue_events")}
        result = self._run_notify()
        self.assertGreater(len(result["sent"]), 0)
        after = {t: self._snapshot(t) for t in before}
        self.assertEqual(before, after)  # 一列不變、零新 dispatch job

    def test_no_dispatch_job_created_by_notifier(self):
        """鐵律抽查：notifier 跑完，bridge_domain_dispatch job 數不變。"""
        self._make_triage_job("hermes:a:1..5")
        self._run_notify()
        with db._db() as conn:
            n = conn.execute("SELECT COUNT(*) AS n FROM jobs WHERE source=?",
                             (DISPATCH_SOURCE,)).fetchone()["n"]
        self.assertEqual(n, 0)


class CliTests(NotifierTestCase):
    def _mock_send_script(self, exit_code=0):
        """§9 2.7a 測試策略：mock hermes send fixture 腳本（固定 exit／
        輸出＋message-key ledger 檔模擬第二層 no-op）。"""
        d = Path(tempfile.mkdtemp(prefix="mock-send-"))
        script = d / "mock_send.py"
        ledger = d / "ledger.txt"
        script.write_text(
            "import sys\n"
            "from pathlib import Path\n"
            f"sys.exit({exit_code}) if {exit_code} else None\n"
            "args = sys.argv[1:]\n"
            "key = args[args.index('--message-key') + 1]\n"
            f"ledger = Path(r'{ledger}')\n"
            "seen = ledger.read_text().splitlines() if ledger.exists() else []\n"
            "if key in seen:\n"
            "    print('already sent (ledger no-op)')\n"
            "    sys.exit(0)\n"
            "with ledger.open('a', encoding='utf-8') as f:\n"
            "    f.write(key + '\\n')\n"
            "print('sent')\n",
            encoding="utf-8")
        py = Path(sys.executable).as_posix()
        return f'"{py}" "{script.as_posix()}"', ledger

    def test_cli_notify_with_mock_script_then_rerun_zero_resend(self):
        job_id = self._make_triage_job("hermes:a:1..5")
        send_cli, ledger = self._mock_send_script()
        code, out, err = self._cli(["--jobs-db", self._tmp.name, "notify",
                                    "--channel", "CTEST",
                                    "--send-cli", send_cli])
        self.assertEqual(code, 0, msg=err)
        self.assertEqual(ledger.read_text().splitlines(),
                         [f"agentos27:candidate_pending:{job_id}"])
        self.assertEqual(len(self._log_rows()), 1)
        code2, out2, _ = self._cli(["--jobs-db", self._tmp.name, "notify",
                                    "--channel", "CTEST",
                                    "--send-cli", send_cli])
        self.assertEqual(code2, 0)
        self.assertIn("already-notified", out2)
        self.assertEqual(len(ledger.read_text().splitlines()), 1)

    def test_cli_exit_1_on_send_failure_no_log(self):
        self._make_triage_job("hermes:a:1..5")
        send_cli, ledger = self._mock_send_script(exit_code=1)
        code, out, err = self._cli(["--jobs-db", self._tmp.name, "notify",
                                    "--channel", "CTEST",
                                    "--send-cli", send_cli])
        self.assertEqual(code, 1)
        self.assertIn("SEND-FAILED", err)
        self.assertEqual(self._log_rows(), [])

    def test_cli_exit_1_when_send_cli_missing(self):
        self._make_triage_job("hermes:a:1..5")
        code, out, err = self._cli(
            ["--jobs-db", self._tmp.name, "notify", "--channel", "CTEST",
             "--send-cli", "definitely-not-a-real-cli-27a"])
        self.assertEqual(code, 1)
        self.assertIn("中止", err)
        self.assertEqual(self._log_rows(), [])

    def test_cli_notify_nothing_to_send_exit_0(self):
        self._make_triage_job("hermes:a:1..5", decision="memory_only")
        code, out, err = self._cli(["--jobs-db", self._tmp.name, "notify",
                                    "--channel", "CTEST",
                                    "--send-cli", "mock-hermes"])
        self.assertEqual(code, 0)  # 無事可通知：send CLI 根本不會被呼叫
        self.assertIn("偵測 0 筆", out)

    def test_cli_log_subcommand(self):
        db.record_notification("agentos27:anomaly:j1", "anomaly", "j1",
                               "CTEST", send_result="exit=0")
        code, out, err = self._cli(["--jobs-db", self._tmp.name, "log"])
        self.assertEqual(code, 0)
        self.assertIn("agentos27:anomaly:j1", out)
        self.assertIn("CTEST", out)

    def test_cli_missing_jobs_db_exit_0_no_file(self):
        missing = Path(self._tmp.name).parent / "no-such-jobs-27a-cli.db"
        missing.unlink(missing_ok=True)
        code, out, err = self._cli(["--jobs-db", str(missing), "notify",
                                    "--send-cli", "mock-hermes"])
        self.assertEqual(code, 0)
        self.assertIn("jobs.db 不存在", out)
        self.assertFalse(missing.exists())
        db.DB_PATH = Path(self._tmp.name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
