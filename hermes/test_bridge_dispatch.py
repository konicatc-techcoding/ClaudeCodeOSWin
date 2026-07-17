#!/usr/bin/env python3
"""hermes/test_bridge_dispatch.py — Stage 2.6b

dispatch 資料層（dispatch_records／dispatch_events）＋核准 CLI 的測試。
對應提案 docs/stage2.6-domain-dispatch-proposal.md v2 §9「2.6b」DoD：

- migration 冪等測試（含對既有 legacy DB 零破壞）
- list 冪等（重跑零重複登記）
- approve/reject 狀態機（含非法轉換拒絕：reject 後不能 approve、重複
  approve 明確報錯）
- actor 空/純空白拒絕
- --dry-run 零寫入（分類結果與真實模式一致）
- defensive parse 異常呈現（壞 JSON／壞 enum／缺欄位／髒 owner）
- superseded／stale record／needs_review 呈現、decision 計數
- 既有 jobs 路徑零回歸（由既有 test_*.py 套件覆蓋，本檔不重複）

全部沙箱化：暫存 jobs.db，不動真正的 hermes/jobs.db、不碰 bridge_state.db、
不呼叫任何模型。

執行：.venv/Scripts/python.exe hermes/test_bridge_dispatch.py
"""
import contextlib
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bridge_dispatch  # noqa: E402
import db  # noqa: E402

SOURCE = "bridge_episode_triage"
PV1 = "bridge_episode_triage_v1"
PV2 = "bridge_episode_triage_v2"
HASH = "c" * 64


def canonical_result(decision="action_candidate", owner="engineering",
                     summary="摘要文字", reason="理由文字", pv=PV1):
    return json.dumps(
        {"decision": decision, "summary": summary, "suggested_owner": owner,
         "reason": reason, "prompt_version": pv},
        ensure_ascii=False, sort_keys=True)


class DispatchTestCase(unittest.TestCase):
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

    def _make_triage_job(self, event_id, pv=PV1, result=None, **kw):
        job_id, created = db.enqueue_once(
            SOURCE, event_id, pv, HASH, "triage prompt",
            payload={"event_id": event_id,
                     "artifact_hint": "memory/inbox/x.md",
                     "payload_hash": HASH, "prompt_version": pv})
        self.assertTrue(created)
        db.mark_completed(job_id, result if result is not None
                          else canonical_result(pv=pv, **kw))
        return job_id

    def _records(self):
        with db._db() as conn:
            return conn.execute(
                "SELECT * FROM dispatch_records ORDER BY created_at").fetchall()

    def _events(self, triage_job_id=None):
        with db._db() as conn:
            if triage_job_id is None:
                return conn.execute(
                    "SELECT * FROM dispatch_events "
                    "ORDER BY triage_job_id, event_seq").fetchall()
            return conn.execute(
                "SELECT * FROM dispatch_events WHERE triage_job_id=? "
                "ORDER BY event_seq", (triage_job_id,)).fetchall()

    def _run_list(self, dry_run=False, actor="tester"):
        return bridge_dispatch.run_list(dry_run=dry_run, actor=actor)

    def _cli(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = bridge_dispatch._cli(argv)
        return code, out.getvalue(), err.getvalue()


class MigrationTests(DispatchTestCase):
    """§9 2.6b DoD：migration 冪等；對既有 DB 零破壞。"""

    def test_tables_and_columns_exist(self):
        with db._db() as conn:
            rec_cols = {r["name"] for r in conn.execute(
                "PRAGMA table_info(dispatch_records)")}
            evt_cols = {r["name"] for r in conn.execute(
                "PRAGMA table_info(dispatch_events)")}
        self.assertEqual(rec_cols, {"triage_job_id", "event_id",
                                    "suggested_owner", "status",
                                    "task_description", "dispatch_job_id",
                                    "created_at", "updated_at"})
        self.assertEqual(evt_cols, {"triage_job_id", "event_seq",
                                    "occurred_at", "action", "actor",
                                    "reason"})

    def test_migration_idempotent(self):
        db.init_db()
        db.init_db()  # 多次執行不炸、不重建
        db.register_dispatch_record("j1", "hermes:s:1..2", "engineering", "a")
        db.init_db()
        self.assertEqual(len(self._records()), 1)

    def test_migration_on_legacy_db_preserves_rows(self):
        """模擬 2.5 時代的舊 jobs.db（沒有 dispatch 表）——migration 之後
        既有列原封不動、兩張新表補上。"""
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
                " worker_id TEXT, locked_at TEXT, result TEXT, error_message TEXT,"
                " created_at TEXT NOT NULL, updated_at TEXT NOT NULL,"
                " completed_at TEXT)")
            conn.execute(
                "CREATE TABLE sessions (thread_id TEXT PRIMARY KEY,"
                " session_id TEXT NOT NULL, last_used_at TEXT NOT NULL)")
            conn.execute(
                "INSERT INTO jobs (id, source, payload, prompt, created_at, "
                "updated_at) VALUES ('old-1', 'manual', '{}', 'p', 't', 't')")
            conn.commit()
            conn.close()
            db.DB_PATH = Path(legacy.name)
            db.init_db()
            with db._db() as conn:
                row = conn.execute(
                    "SELECT * FROM jobs WHERE id='old-1'").fetchone()
                self.assertEqual(row["source"], "manual")
                names = {r["name"] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertIn("dispatch_records", names)
            self.assertIn("dispatch_events", names)
        finally:
            db.DB_PATH = Path(self._tmp.name)
            for suffix in ("", "-wal", "-shm"):
                Path(legacy.name + suffix).unlink(missing_ok=True)


class DataLayerTests(DispatchTestCase):
    """dispatch_records 狀態機＋dispatch_events 稽核（§5.1／§5.3 第一層）。"""

    def test_register_idempotent_with_proposed_event(self):
        self.assertTrue(db.register_dispatch_record(
            "j1", "hermes:s:1..2", "engineering", "alice"))
        self.assertFalse(db.register_dispatch_record(
            "j1", "hermes:s:1..2", "engineering", "alice"))
        recs = self._records()
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["status"], "proposed")
        self.assertIsNone(recs[0]["task_description"])
        self.assertIsNone(recs[0]["dispatch_job_id"])
        events = self._events("j1")
        self.assertEqual(len(events), 1)  # 重跑零重複稽核
        self.assertEqual(events[0]["action"], "proposed")
        self.assertEqual(events[0]["actor"], "alice")

    def test_register_actor_validation(self):
        for bad in ("", "   ", None, 123):
            with self.assertRaises(ValueError):
                db.register_dispatch_record("jX", "e", "engineering", bad)
        self.assertEqual(len(self._records()), 0)

    def test_approve_happy_path(self):
        db.register_dispatch_record("j1", "e1", "engineering", "alice")
        event = db.approve_dispatch("j1", "  bob  ", "做這件事",
                                    reason="看過了")
        self.assertEqual(event["actor"], "bob")  # strip() 正規化
        rec = db.get_dispatch_record("j1")
        self.assertEqual(rec["status"], "approved")
        self.assertEqual(rec["task_description"], "做這件事")
        self.assertIsNone(rec["dispatch_job_id"])  # 2.6b：不派工
        events = self._events("j1")
        self.assertEqual([e["action"] for e in events],
                         ["proposed", "approved"])
        self.assertEqual(events[1]["event_seq"], 2)
        self.assertEqual(events[1]["reason"], "看過了")

    def test_duplicate_approve_rejected_explicitly(self):
        db.register_dispatch_record("j1", "e1", "engineering", "a")
        db.approve_dispatch("j1", "a", "task")
        with self.assertRaises(db.DispatchTransitionRejected):
            db.approve_dispatch("j1", "a", "task")
        self.assertEqual(len(self._events("j1")), 2)  # 失敗不寫稽核

    def test_reject_then_approve_rejected(self):
        db.register_dispatch_record("j1", "e1", "engineering", "a")
        db.reject_dispatch("j1", "a", reason="不做")
        rec = db.get_dispatch_record("j1")
        self.assertEqual(rec["status"], "rejected")
        self.assertIsNone(rec["task_description"])
        with self.assertRaises(db.DispatchTransitionRejected):
            db.approve_dispatch("j1", "a", "task")
        with self.assertRaises(db.DispatchTransitionRejected):
            db.reject_dispatch("j1", "a")
        self.assertEqual([e["action"] for e in self._events("j1")],
                         ["proposed", "rejected"])

    def test_approve_after_approve_cannot_reject(self):
        db.register_dispatch_record("j1", "e1", "engineering", "a")
        db.approve_dispatch("j1", "a", "task")
        with self.assertRaises(db.DispatchTransitionRejected):
            db.reject_dispatch("j1", "a")

    def test_transition_on_unknown_record(self):
        with self.assertRaises(db.DispatchTransitionRejected):
            db.approve_dispatch("nope", "a", "task")
        with self.assertRaises(db.DispatchTransitionRejected):
            db.reject_dispatch("nope", "a")

    def test_approve_task_validation(self):
        db.register_dispatch_record("j1", "e1", "engineering", "a")
        for bad_task in ("", "   ", None):
            with self.assertRaises(ValueError):
                db.approve_dispatch("j1", "a", bad_task)
        for bad_actor in ("", "   "):
            with self.assertRaises(ValueError):
                db.approve_dispatch("j1", bad_actor, "task")
        self.assertEqual(db.get_dispatch_record("j1")["status"], "proposed")


class ListScanTests(DispatchTestCase):
    """§5.2 掃描＋防禦性 parse＋superseded＋§5.4 needs_review／計數。"""

    def test_candidate_registered_and_list_idempotent(self):
        j1 = self._make_triage_job("hermes:s1:1..3")
        result = self._run_list()
        self.assertEqual(result["counts"],
                         {"registered": 1, "already_registered": 0, "stale": 0})
        self.assertEqual(result["items"][0]["job_id"], j1)
        self.assertIsNone(result["items"][0]["owner_warning"])
        self.assertEqual(result["items"][0]["artifact"], "memory/inbox/x.md")
        # 重跑：零重複登記、零重複稽核
        again = self._run_list()
        self.assertEqual(again["counts"],
                         {"registered": 0, "already_registered": 1, "stale": 0})
        self.assertEqual(len(self._records()), 1)
        self.assertEqual(len(self._events(j1)), 1)

    def test_dry_run_zero_write_same_classification(self):
        self._make_triage_job("hermes:s1:1..3")
        dry = self._run_list(dry_run=True)
        self.assertEqual(dry["counts"]["registered"], 1)
        self.assertEqual(len(self._records()), 0)   # 零寫入
        self.assertEqual(len(self._events()), 0)
        real = self._run_list()
        # 分類結果與真實模式一致
        self.assertEqual(dry["counts"], real["counts"])
        self.assertEqual([i["category"] for i in dry["items"]],
                         [i["category"] for i in real["items"]])
        self.assertEqual(len(self._records()), 1)

    def test_memory_only_not_registered(self):
        self._make_triage_job("hermes:s1:1..3", decision="memory_only",
                              owner="")
        result = self._run_list()
        self.assertEqual(result["items"], [])
        self.assertEqual(result["decision_counts"]["memory_only"], 1)
        self.assertEqual(len(self._records()), 0)

    def test_needs_review_presented_only(self):
        j1 = self._make_triage_job("hermes:s1:1..3", decision="needs_review",
                                   owner="", summary="矛盾內容", reason="無法分類")
        result = self._run_list()
        self.assertEqual(result["items"], [])
        self.assertEqual(len(result["needs_review"]), 1)
        self.assertEqual(result["needs_review"][0]["job_id"], j1)
        self.assertEqual(len(self._records()), 0)  # 不登記、不派工

    def test_defensive_parse_anomalies(self):
        bad_json = self._make_triage_job("hermes:s1:1..2", result="not json{")
        missing = self._make_triage_job(
            "hermes:s2:1..2",
            result=json.dumps({"decision": "action_candidate",
                               "summary": "s", "prompt_version": PV1}))
        bad_enum = self._make_triage_job(
            "hermes:s3:1..2",
            result=json.dumps({"decision": "weird", "summary": "s",
                               "suggested_owner": "", "reason": "r",
                               "prompt_version": PV1}))
        result = self._run_list()
        anomaly_ids = {a["job_id"] for a in result["anomalies"]}
        self.assertEqual(anomaly_ids, {bad_json, missing, bad_enum})
        self.assertEqual(result["items"], [])       # 不當候選
        self.assertEqual(len(self._records()), 0)   # 不登記（標不可核准）
        # 異常不計入 decision 計數
        self.assertEqual(sum(result["decision_counts"].values()), 0)

    def test_dirty_owner_is_candidate_with_warning(self):
        j1 = self._make_triage_job("hermes:s1:1..3", owner="na")
        result = self._run_list()
        self.assertEqual(len(result["items"]), 1)
        self.assertIn("na", result["items"][0]["owner_warning"])
        self.assertEqual(result["items"][0]["category"], "registered")
        rec = db.get_dispatch_record(j1)
        self.assertEqual(rec["suggested_owner"], "na")  # 原樣保存

    def test_superseded_latest_prompt_version_is_basis(self):
        j_v1 = self._make_triage_job("hermes:s1:1..3", pv=PV1)
        j_v2 = self._make_triage_job("hermes:s1:1..3", pv=PV2)
        result = self._run_list()
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["job_id"], j_v2)
        self.assertEqual(len(result["superseded"]), 1)
        self.assertEqual(result["superseded"][0]["job_id"], j_v1)
        self.assertEqual(result["superseded"][0]["superseded_by"], j_v2)
        self.assertEqual(len(self._records()), 1)  # 同 episode 不會雙候選
        self.assertEqual(self._records()[0]["triage_job_id"], j_v2)
        # 計數含 superseded 舊筆（監測 triage 產出分布）
        self.assertEqual(result["decision_counts"]["action_candidate"], 2)

    def test_superseded_flip_to_memory_only(self):
        self._make_triage_job("hermes:s1:1..3", pv=PV1)
        self._make_triage_job("hermes:s1:1..3", pv=PV2,
                              decision="memory_only", owner="")
        result = self._run_list()
        self.assertEqual(result["items"], [])       # 基準不是 action_candidate
        self.assertEqual(len(result["superseded"]), 1)
        self.assertEqual(len(self._records()), 0)

    def test_stale_record_flagged_not_duplicated(self):
        j_v1 = self._make_triage_job("hermes:s1:1..3", pv=PV1)
        self._run_list()  # record 指向 v1
        j_v2 = self._make_triage_job("hermes:s1:1..3", pv=PV2)
        result = self._run_list()
        self.assertEqual(result["counts"]["stale"], 1)
        item = result["items"][0]
        self.assertEqual(item["job_id"], j_v2)
        self.assertEqual(item["stale_records"][0]["triage_job_id"], j_v1)
        self.assertEqual(len(self._records()), 1)   # 不另建第二筆
        self.assertEqual(self._records()[0]["triage_job_id"], j_v1)

    def test_non_triage_sources_ignored(self):
        other = db.enqueue("manual", "hi")
        db.mark_completed(other, "自由文字，不是 JSON")
        result = self._run_list()
        self.assertEqual(result["items"], [])
        self.assertEqual(result["anomalies"], [])   # source 過濾，不誤 parse

    def test_list_actor_validation(self):
        with self.assertRaises(ValueError):
            self._run_list(actor="   ")


class CliTests(DispatchTestCase):
    """CLI 介面（exit code、--dry-run、--task/--yes、fail-visible 紅旗）。"""

    def _prep_candidate(self, **kw):
        j1 = self._make_triage_job("hermes:s1:1..3", **kw)
        code, out, err = self._cli(["list", "--actor", "tester"])
        self.assertEqual(code, 0)
        return j1

    def test_list_exit_codes(self):
        self._make_triage_job("hermes:s1:1..3")
        code, out, err = self._cli(["list", "--actor", "tester"])
        self.assertEqual(code, 0)
        self.assertIn("registered", out)
        self._make_triage_job("hermes:s2:1..3", result="broken{")
        code, out, err = self._cli(["list", "--actor", "tester"])
        self.assertEqual(code, 3)                   # 異常紅旗
        self.assertIn("ANOMALY", err)

    def test_list_missing_jobs_db_reports_without_creating(self):
        missing = Path(self._tmp.name).parent / "no-such-jobs.db"
        code, out, err = self._cli(["--jobs-db", str(missing),
                                    "list", "--actor", "tester"])
        self.assertEqual(code, 0)
        self.assertIn("不存在", out)
        self.assertFalse(missing.exists())
        db.DB_PATH = Path(self._tmp.name)  # _cli 改了 process 級 DB_PATH，還原

    def test_approve_with_task(self):
        j1 = self._prep_candidate()
        code, out, err = self._cli([
            "approve", j1, "--actor", "alice", "--task", "去做這件事",
            "--reason", "ok"])
        self.assertEqual(code, 0, err)
        rec = db.get_dispatch_record(j1)
        self.assertEqual(rec["status"], "approved")
        self.assertEqual(rec["task_description"], "去做這件事")
        self.assertIsNone(rec["dispatch_job_id"])   # 2.6b：不派工
        # 沒有任何新 job 被 enqueue（jobs 表只有那筆 triage job）
        self.assertEqual(len(db.list_jobs()), 1)
        # 重複 approve → 明確失敗
        code, out, err = self._cli([
            "approve", j1, "--actor", "alice", "--task", "x"])
        self.assertEqual(code, 1)
        self.assertIn("不允許轉換", err)

    def test_approve_suggested_task_with_yes(self):
        j1 = self._prep_candidate(summary="整理 sync 腳本")
        code, out, err = self._cli([
            "approve", j1, "--actor", "alice", "--yes"])
        self.assertEqual(code, 0, err)
        rec = db.get_dispatch_record(j1)
        self.assertIn("整理 sync 腳本", rec["task_description"])
        self.assertIn("hermes:s1:1..3", rec["task_description"])

    def test_approve_unconfirmed_aborts(self):
        j1 = self._prep_candidate()
        original = bridge_dispatch._confirm
        bridge_dispatch._confirm = lambda prompt: False
        try:
            code, out, err = self._cli(["approve", j1, "--actor", "alice"])
        finally:
            bridge_dispatch._confirm = original
        self.assertEqual(code, 1)
        self.assertEqual(db.get_dispatch_record(j1)["status"], "proposed")

    def test_approve_dry_run_zero_write(self):
        j1 = self._prep_candidate()
        code, out, err = self._cli([
            "approve", j1, "--actor", "alice", "--task", "t", "--dry-run"])
        self.assertEqual(code, 0)
        self.assertEqual(db.get_dispatch_record(j1)["status"], "proposed")
        self.assertEqual(len(self._events(j1)), 1)
        # 分類與真實模式一致：非 proposed → dry-run 也報失敗
        db.approve_dispatch(j1, "alice", "t")
        code, out, err = self._cli([
            "approve", j1, "--actor", "alice", "--task", "t", "--dry-run"])
        self.assertEqual(code, 1)
        self.assertIn("不允許", err)

    def test_reject_flow(self):
        j1 = self._prep_candidate()
        code, out, err = self._cli([
            "reject", j1, "--actor", "alice", "--reason", "不需要"])
        self.assertEqual(code, 0, err)
        rec = db.get_dispatch_record(j1)
        self.assertEqual(rec["status"], "rejected")
        # reject 後 approve → 明確失敗
        code, out, err = self._cli([
            "approve", j1, "--actor", "alice", "--task", "x"])
        self.assertEqual(code, 1)

    def test_blank_actor_rejected_before_any_write(self):
        j1 = self._prep_candidate()
        code, out, err = self._cli(["approve", j1, "--actor", "   ",
                                    "--task", "x"])
        self.assertEqual(code, 1)
        self.assertIn("actor", err)
        self.assertEqual(db.get_dispatch_record(j1)["status"], "proposed")
        code, out, err = self._cli(["list", "--actor", ""])
        self.assertEqual(code, 1)

    def test_blank_task_rejected(self):
        j1 = self._prep_candidate()
        code, out, err = self._cli(["approve", j1, "--actor", "a",
                                    "--task", "   "])
        self.assertEqual(code, 1)
        self.assertEqual(db.get_dispatch_record(j1)["status"], "proposed")

    def test_transition_on_unregistered_candidate(self):
        j1 = self._make_triage_job("hermes:s1:1..3")  # 沒跑過 list
        code, out, err = self._cli(["approve", j1, "--actor", "a",
                                    "--task", "x"])
        self.assertEqual(code, 1)
        self.assertIn("查無", err)


if __name__ == "__main__":
    unittest.main(verbosity=2)
