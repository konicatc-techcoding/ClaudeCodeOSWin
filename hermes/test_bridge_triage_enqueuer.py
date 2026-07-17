#!/usr/bin/env python3
"""hermes/test_bridge_triage_enqueuer.py — Stage 2.5b

手動 enqueuer CLI 的測試。對應提案 docs/stage2.5-episode-triage-proposal.md
（v6）第 14 節測試矩陣中屬於 2.5b 的項目：第 12、13、17、24、25 項，外加
§6.5 條件 2 的唯一定位（找不到／多處定位 → ineligible）、legacy 列候選、
bridge_state.db 唯讀、CLI exit code。

全部沙箱化：tmp 目錄下的 bridge_state.db／inbox 樹／jobs.db（monkeypatch
db.DB_PATH），絕不碰真實 DB 與真實 memory/inbox/。

執行：.venv/Scripts/python.exe hermes/test_bridge_triage_enqueuer.py
"""
import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bridge_state  # noqa: E402
import bridge_triage_enqueuer as enq  # noqa: E402
import db  # noqa: E402

PV = enq.DEFAULT_PROMPT_VERSION


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class EnqueuerTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="triage-enq-"))
        self.bridge_db = self.tmp / "bridge_state.db"
        self.inbox = self.tmp / "inbox"
        for sub in ("", ".processed", ".failed"):
            (self.inbox / sub if sub else self.inbox).mkdir(parents=True,
                                                            exist_ok=True)
        self.jobs_db = self.tmp / "jobs.db"
        self._original_db_path = db.DB_PATH
        db.DB_PATH = self.jobs_db
        bridge_state.init_db(self.bridge_db)

    def tearDown(self):
        db.DB_PATH = self._original_db_path
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- fixtures ----

    def _episode_row(self, sid="sess1", first=1, last=5, seq=1,
                     status="to_inbox"):
        """建立一筆 episode 列，回傳 event_id。"""
        rel = f"memory/inbox/hermes_session_{sid}_ep{first}-{last}.md"
        kwargs = dict(
            session_id=sid, source_profile="default", session_source="cli",
            episode_seq=seq, capture_trigger="ended",
            first_message_id=first, last_message_id=last,
            source_content_hash="c" * 64, decision_reason="test fixture",
            import_status=status, db_path=self.bridge_db,
        )
        if status in ("to_inbox", "imported"):
            kwargs["imported_inbox_path"] = rel
        if status == "failed":
            kwargs["error_reason"] = "test failure"
        res = bridge_state.create_episode(**kwargs)
        return res["row"]["event_id"]

    def _legacy_row(self, sid="legacy1", status="to_inbox"):
        row = bridge_state.upsert_session_state(
            session_id=sid, source_profile="default", session_source="cli",
            import_status=status, memory_type="episodic", useful_chat=True,
            decision_reason="test fixture",
            imported_inbox_path=f"memory/inbox/hermes_session_{sid}.md",
            db_path=self.bridge_db)
        return row["event_id"]

    def _write_artifact(self, name, content="episode body\n", sub=""):
        directory = self.inbox / sub if sub else self.inbox
        path = directory / name
        path.write_text(content, encoding="utf-8")
        return path

    def _run(self, dry_run=False, **kw):
        return enq.enqueue_candidates(
            dry_run=dry_run, bridge_db=self.bridge_db, inbox_dir=self.inbox,
            **kw)

    def _job_rows(self):
        with db._db() as conn:
            return conn.execute(
                "SELECT * FROM jobs ORDER BY created_at").fetchall()


class CandidateEligibilityTests(EnqueuerTestCase):
    def test_episode_to_inbox_created_with_correct_job_fields(self):
        event_id = self._episode_row()
        artifact = self._write_artifact("hermes_session_sess1_ep1-5.md")
        result = self._run()
        self.assertEqual(result["counts"],
                         {"created": 1, "existing": 0, "conflict": 0,
                          "ineligible": 0})
        rows = self._job_rows()
        self.assertEqual(len(rows), 1)
        job = rows[0]
        self.assertEqual(job["source"], "bridge_episode_triage")
        self.assertEqual(job["external_key"], event_id)
        self.assertEqual(job["prompt_version"], PV)
        self.assertEqual(job["payload_hash"], _sha256_file(artifact))
        self.assertEqual(job["max_attempts"], 1)  # §3.2 Option A
        self.assertIsNone(job["thread_id"])  # 這個 source 從不 resume
        payload = json.loads(job["payload"])
        self.assertEqual(payload["event_id"], event_id)
        self.assertEqual(payload["payload_hash"], _sha256_file(artifact))
        self.assertEqual(payload["prompt_version"], PV)
        self.assertIn("hermes_session_sess1_ep1-5.md", payload["artifact_hint"])
        # §7.4：payload 不存 episode 全文
        self.assertNotIn("episode body", job["payload"])
        self.assertNotIn("episode body", job["prompt"])

    def test_matrix12_imported_row_with_artifact_in_processed_is_candidate(self):
        """矩陣 #12：imported（模擬 reconcile 已回填）＋artifact 在
        .processed/ 的 episode，必須出現在候選集合並可 enqueue。"""
        event_id = self._episode_row(status="imported")
        self._write_artifact("hermes_session_sess1_ep1-5.md", sub=".processed")
        result = self._run()
        self.assertEqual(result["candidates"], 1)
        self.assertEqual(result["items"][0]["event_id"], event_id)
        self.assertEqual(result["items"][0]["category"], "created")

    def test_matrix13_excluded_statuses_are_not_candidates(self):
        """矩陣 #13：needs_review／skipped／failed／discovered 一律不入候選
        ——即使 artifact 存在（排除是狀態層的，不是定位層的）。"""
        statuses = ("needs_review", "skipped", "failed", "discovered")
        for i, status in enumerate(statuses, start=1):
            sid = f"ex{i}"
            self._episode_row(sid=sid, first=1, last=3, status=status)
            self._write_artifact(f"hermes_session_{sid}_ep1-3.md")
        for dry_run in (True, False):
            result = self._run(dry_run=dry_run)
            self.assertEqual(result["candidates"], 0)
            self.assertEqual(result["items"], [])
        # 零候選 → 真跑也不需要（也沒有）建立 jobs.db
        self.assertFalse(self.jobs_db.exists())

    def test_legacy_row_is_candidate_and_no_cross_matching(self):
        """legacy event_id（hermes:<sid>）也是合格候選（§2.1 沿用兩種格式）；
        同 session 的 episode 檔不得干擾 legacy 定位、反之亦然。"""
        legacy_id = self._legacy_row(sid="mix")
        episode_id = self._episode_row(sid="mix", first=1, last=5)
        self._write_artifact("hermes_session_mix.md", content="legacy body\n")
        self._write_artifact("hermes_session_mix_ep1-5.md",
                             content="episode body\n")
        result = self._run()
        self.assertEqual(result["counts"]["created"], 2)
        by_key = {r["external_key"]: r for r in self._job_rows()}
        self.assertEqual(set(by_key), {legacy_id, episode_id})
        self.assertIn("hermes_session_mix.md",
                      json.loads(by_key[legacy_id]["payload"])["artifact_hint"])
        self.assertIn("hermes_session_mix_ep1-5.md",
                      json.loads(by_key[episode_id]["payload"])["artifact_hint"])

    def test_artifact_not_found_is_ineligible(self):
        self._episode_row()  # 不寫 artifact
        result = self._run()
        self.assertEqual(result["counts"]["ineligible"], 1)
        item = result["items"][0]
        self.assertEqual(item["category"], "ineligible")
        self.assertIn("定位不到", item["label"])
        self.assertEqual(len(self._job_rows()), 0)

    def test_artifact_in_multiple_dirs_is_ineligible(self):
        self._episode_row()
        self._write_artifact("hermes_session_sess1_ep1-5.md")
        self._write_artifact("hermes_session_sess1_ep1-5.md", sub=".processed")
        result = self._run()
        self.assertEqual(result["counts"]["ineligible"], 1)
        label = result["items"][0]["label"]
        self.assertIn("多處", label)
        self.assertIn("hermes_session_sess1_ep1-5.md", label)
        self.assertEqual(len(self._job_rows()), 0)

    def test_frontmatter_event_id_range_locates_renamed_artifact(self):
        """§7.4：比對依據是 deterministic 檔名**或** frontmatter
        event_id_range——檔名不合慣例但 frontmatter 相符時仍定位得到。"""
        event_id = self._episode_row()
        self._write_artifact(
            "hermes_session_renamed-by-hand.md",
            content=f"---\nevent_id_range: {event_id}\n---\nbody\n")
        result = self._run()
        self.assertEqual(result["counts"]["created"], 1)

    def test_unparseable_event_id_is_ineligible(self):
        bridge_state.upsert_session_state(
            session_id="weird", source_profile="default", session_source="cli",
            import_status="to_inbox", memory_type="episodic", useful_chat=True,
            decision_reason="test", imported_inbox_path="memory/inbox/x.md",
            event_id="rss:not-hermes", db_path=self.bridge_db)
        result = self._run()
        self.assertEqual(result["counts"]["ineligible"], 1)
        self.assertIn("無法解析", result["items"][0]["label"])


class EnqueueOnceAuthorityTests(EnqueuerTestCase):
    def test_second_run_is_existing_noop(self):
        """不做 jobs.db 前置過濾：第二次真跑對同一候選仍呼叫 enqueue_once，
        由它回報 existing no-op，不建第二筆。"""
        self._episode_row()
        self._write_artifact("hermes_session_sess1_ep1-5.md")
        first = self._run()
        second = self._run()
        self.assertEqual(first["counts"]["created"], 1)
        self.assertEqual(second["counts"],
                         {"created": 0, "existing": 1, "conflict": 0,
                          "ineligible": 0})
        self.assertEqual(len(self._job_rows()), 1)
        self.assertEqual(second["items"][0]["job_id"],
                         first["items"][0]["job_id"])

    def test_matrix24_content_drift_surfaces_as_conflict(self):
        """矩陣 #24：artifact 內容漂移的候選仍出現在候選集合並被呼叫
        enqueue_once，結果分類 conflict——不被候選層靜默濾掉。"""
        event_id = self._episode_row()
        artifact = self._write_artifact("hermes_session_sess1_ep1-5.md",
                                        content="original\n")
        original_hash = _sha256_file(artifact)
        self._run()
        artifact.write_text("drifted content\n", encoding="utf-8")
        # dry-run 與真跑都必須把它分類成 conflict
        dry = self._run(dry_run=True)
        self.assertEqual(dry["counts"]["conflict"], 1)
        real = self._run()
        self.assertEqual(real["candidates"], 1)  # 沒有被候選層濾掉
        self.assertEqual(real["counts"]["conflict"], 1)
        item = real["items"][0]
        self.assertEqual(item["event_id"], event_id)
        self.assertIn("payload_hash", item["label"])
        self.assertIn("人工調查", item["label"])
        # fail closed：沒有第二筆、既有 job 未被覆蓋
        rows = self._job_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["payload_hash"], original_hash)

    def test_new_prompt_version_creates_new_job(self):
        """§2.2：同 episode、新 prompt_version → 新 job，不與舊版本衝突。"""
        self._episode_row()
        self._write_artifact("hermes_session_sess1_ep1-5.md")
        self._run()
        result = self._run(prompt_version="bridge_episode_triage_v2")
        self.assertEqual(result["counts"]["created"], 1)
        self.assertEqual(len(self._job_rows()), 2)


class DryRunTests(EnqueuerTestCase):
    def _mixed_fixture(self):
        """三個候選：new／existing（同 hash）／conflict（漂移）。"""
        self._episode_row(sid="new1", first=1, last=2)
        self._write_artifact("hermes_session_new1_ep1-2.md")
        self._episode_row(sid="same1", first=1, last=3)
        self._write_artifact("hermes_session_same1_ep1-3.md")
        self._episode_row(sid="drift1", first=1, last=4)
        drift = self._write_artifact("hermes_session_drift1_ep1-4.md",
                                     content="v1\n")
        # 先把 same1／drift1 enqueue 進去，再漂移 drift1 的內容
        for sid, first, last in (("same1", 1, 3), ("drift1", 1, 4)):
            path = self.inbox / f"hermes_session_{sid}_ep{first}-{last}.md"
            db.init_db()
            db.enqueue_once(
                "bridge_episode_triage", f"hermes:{sid}:{first}..{last}", PV,
                _sha256_file(path), "prompt", payload={})
        drift.write_text("v2 drifted\n", encoding="utf-8")

    def test_matrix17_dry_run_zero_writes_to_jobs_db(self):
        """矩陣 #17：--dry-run 對 jobs.db 零寫入（含既有 db 的 byte-level
        不變、與 db 不存在時不建檔兩種情況）。"""
        # 情況 1：jobs.db 尚不存在——dry-run 不得建檔
        self._episode_row(sid="new0", first=1, last=2)
        self._write_artifact("hermes_session_new0_ep1-2.md")
        result = self._run(dry_run=True)
        self.assertEqual(result["counts"]["created"], 1)
        self.assertFalse(self.jobs_db.exists())
        # 情況 2：jobs.db 已存在且含三種現況——dry-run 後 byte 不變
        self._mixed_fixture()
        before = self.jobs_db.read_bytes()
        result = self._run(dry_run=True)
        self.assertEqual(result["counts"],
                         {"created": 2, "existing": 1, "conflict": 1,
                          "ineligible": 0})
        self.assertEqual(self.jobs_db.read_bytes(), before)

    def test_matrix25_dry_run_classification_matches_real_mode(self):
        """矩陣 #25：dry-run 對每個候選的三分支分類與真實模式一致。"""
        self._mixed_fixture()
        dry = self._run(dry_run=True)
        real = self._run()
        dry_by_id = {i["event_id"]: i["category"] for i in dry["items"]}
        real_by_id = {i["event_id"]: i["category"] for i in real["items"]}
        self.assertEqual(dry_by_id, real_by_id)
        self.assertEqual(dry["counts"], real["counts"])
        self.assertEqual(dry_by_id["hermes:new1:1..2"], "created")
        self.assertEqual(dry_by_id["hermes:same1:1..3"], "existing")
        self.assertEqual(dry_by_id["hermes:drift1:1..4"], "conflict")
        # 真跑後只多了 new1 這一筆
        keys = {r["external_key"] for r in self._job_rows()}
        self.assertEqual(keys, {"hermes:new1:1..2", "hermes:same1:1..3",
                                "hermes:drift1:1..4"})

    def test_dry_run_output_same_shape_as_real(self):
        """dry-run 的逐筆結果與總計欄位形狀與真跑一致（canonical category
        同一組，只有顯示標籤不同）。"""
        self._episode_row()
        self._write_artifact("hermes_session_sess1_ep1-5.md")
        dry = self._run(dry_run=True)
        real = self._run()
        self.assertEqual(set(dry["counts"]), set(real["counts"]))
        self.assertEqual(
            {k for i in dry["items"] for k in i},
            {k for i in real["items"] for k in i})
        self.assertEqual(dry["items"][0]["display"], "would-create")
        self.assertEqual(real["items"][0]["display"], "created")


class ReadOnlyBridgeDbTests(EnqueuerTestCase):
    def test_bridge_db_bytes_unchanged_after_real_run(self):
        """只讀 bridge_state.db，不寫入、零 schema 變更。"""
        self._episode_row()
        self._write_artifact("hermes_session_sess1_ep1-5.md")
        before = self.bridge_db.read_bytes()
        self._run()
        self._run(dry_run=True)
        self.assertEqual(self.bridge_db.read_bytes(), before)

    def test_missing_bridge_db_reports_and_creates_nothing(self):
        missing = self.tmp / "nope" / "bridge_state.db"
        result = enq.enqueue_candidates(bridge_db=missing,
                                        inbox_dir=self.inbox)
        self.assertFalse(result["bridge_db_exists"])
        self.assertEqual(result["candidates"], 0)
        self.assertFalse(missing.exists())
        self.assertFalse(self.jobs_db.exists())


class CliTests(EnqueuerTestCase):
    def _cli(self, *extra):
        return enq._cli([
            "--bridge-db", str(self.bridge_db),
            "--jobs-db", str(self.jobs_db),
            "enqueue", "--inbox", str(self.inbox), *extra])

    def test_cli_dry_run_then_real_exit_zero(self):
        self._episode_row()
        self._write_artifact("hermes_session_sess1_ep1-5.md")
        self.assertEqual(self._cli("--dry-run"), 0)
        self.assertFalse(self.jobs_db.exists())
        self.assertEqual(self._cli(), 0)
        self.assertEqual(len(self._job_rows()), 1)

    def test_cli_exit_code_3_on_conflict(self):
        self._episode_row()
        artifact = self._write_artifact("hermes_session_sess1_ep1-5.md",
                                        content="v1\n")
        self.assertEqual(self._cli(), 0)
        artifact.write_text("v2\n", encoding="utf-8")
        self.assertEqual(self._cli(), 3)
        self.assertEqual(self._cli("--dry-run"), 3)

    def test_cli_missing_inbox_dir_exit_1(self):
        self._episode_row()
        code = enq._cli([
            "--bridge-db", str(self.bridge_db),
            "--jobs-db", str(self.jobs_db),
            "enqueue", "--inbox", str(self.tmp / "no-such-inbox")])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
