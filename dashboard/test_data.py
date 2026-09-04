#!/usr/bin/env python3
"""dashboard/test_data.py — v0.1

data.py 的測試。用暫存的 jobs.db／log／memory／registry／config 目錄，
不影響真正的專案資料。這裡用 hermes/db.py 建測試用的 fixture 資料
（只在測試程式碼裡這樣做——dashboard/data.py 本身完全不 import db.py）。

一個特別重要的測試：確認 mode=ro 真的會擋下寫入，不是只有「這次沒寫」。

執行：.venv/Scripts/python.exe dashboard/test_data.py
"""
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hermes"))
import data  # noqa: E402
import data_jobs_snapshot  # noqa: E402
import db  # noqa: E402


class DashboardDataTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = Path(tempfile.mkdtemp())

        self._original_jobs_db = data.JOBS_DB_PATH
        self._original_db_dbpath = db.DB_PATH
        db_path = self._tmpdir / "jobs.db"
        data.JOBS_DB_PATH = db_path
        db.DB_PATH = db_path
        db.init_db()

        self._original_log_dir = data.LOG_DIR
        data.LOG_DIR = self._tmpdir / "logs"
        data.LOG_DIR.mkdir()

        self._original_memory_dir = data.MEMORY_DIR
        data.MEMORY_DIR = self._tmpdir / "memory"
        data.MEMORY_DIR.mkdir()

        self._original_registry_dir = data.REGISTRY_DIR
        data.REGISTRY_DIR = self._tmpdir / "registry"
        data.REGISTRY_DIR.mkdir()

        self._original_config_dir = data.CONFIG_DIR
        data.CONFIG_DIR = self._tmpdir / "config"
        data.CONFIG_DIR.mkdir()

        # 2026-09-04:data.py 在 runtime jobs.db 不存在時會退到「WSL 推來的
        # 快照」(%LOCALAPPDATA%\AgentOS\jobs-snapshot)。測試必須把快照位置
        # 隔離到 tempdir,否則本機真的有快照時,「jobs.db 不存在」那一類測試會
        # 讀到真實資料——**測試絕不碰真實 runtime 產物**。
        self._original_snapshot_dir = data_jobs_snapshot.SNAPSHOT_DIR
        data_jobs_snapshot.SNAPSHOT_DIR = self._tmpdir / "snapshot"

    def tearDown(self):
        data.JOBS_DB_PATH = self._original_jobs_db
        db.DB_PATH = self._original_db_dbpath
        data.LOG_DIR = self._original_log_dir
        data.MEMORY_DIR = self._original_memory_dir
        data.REGISTRY_DIR = self._original_registry_dir
        data.CONFIG_DIR = self._original_config_dir
        data_jobs_snapshot.SNAPSHOT_DIR = self._original_snapshot_dir
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    # --- 最關鍵的一個測試：read-only 是真的 ---

    def test_readonly_connection_rejects_writes(self):
        conn = data._ro_connection()
        with self.assertRaises(sqlite3.OperationalError):
            conn.execute("INSERT INTO jobs (id, source, payload, prompt, created_at, updated_at) "
                         "VALUES ('x', 'manual', '{}', 'p', 'x', 'x')")
        conn.close()

    # --- jobs.db 不存在時（環境搬遷後、worker 還沒跑過）不能噴 exception ---

    def test_missing_jobs_db_returns_empty_results(self):
        data.JOBS_DB_PATH = self._tmpdir / "does-not-exist.db"
        self.assertFalse(data.jobs_db_exists())
        self.assertEqual(data.get_status_counts(), {})
        self.assertEqual(data.get_recent_jobs(), [])
        self.assertIsNone(data.get_job("any-id"))
        summary = data.get_cost_summary()
        self.assertEqual(summary["total"], 0.0)
        self.assertEqual(summary["count"], 0)
        self.assertEqual(summary["by_source"], [])

    # --- job 統計/查詢 ---

    def test_get_status_counts(self):
        job_id = db.enqueue("manual", "hello")
        db.claim_next_job("worker-1")
        db.mark_completed(job_id, "done", cost_usd=0.1)
        counts = data.get_status_counts()
        self.assertEqual(counts, {"completed": 1})

    def test_get_recent_jobs_filters_by_status_and_source(self):
        db.enqueue("telegram", "a")
        job_b = db.enqueue("cron", "b")
        db.claim_next_job("worker-1")
        db.mark_completed(job_b, "done")

        all_jobs = data.get_recent_jobs(limit=10)
        self.assertEqual(len(all_jobs), 2)

        completed_only = data.get_recent_jobs(limit=10, status="completed")
        self.assertEqual(len(completed_only), 1)
        self.assertEqual(completed_only[0]["id"], job_b)

        cron_only = data.get_recent_jobs(limit=10, source="cron")
        self.assertEqual(len(cron_only), 1)

    def test_get_recent_jobs_respects_limit(self):
        for i in range(5):
            db.enqueue("manual", f"job {i}")
        jobs = data.get_recent_jobs(limit=2)
        self.assertEqual(len(jobs), 2)

    def test_get_job_found_and_not_found(self):
        job_id = db.enqueue("manual", "hello")
        self.assertIsNotNone(data.get_job(job_id))
        self.assertIsNone(data.get_job("does-not-exist"))

    # --- 成本統計 ---

    def test_get_cost_summary(self):
        j1 = db.enqueue("telegram", "a")
        db.claim_next_job("worker-1")
        db.mark_completed(j1, "done", cost_usd=0.10)

        j2 = db.enqueue("cron", "b")
        db.claim_next_job("worker-1")
        db.mark_completed(j2, "done", cost_usd=0.20)

        summary = data.get_cost_summary()
        self.assertAlmostEqual(summary["total"], 0.30)
        self.assertEqual(summary["count"], 2)
        sources = {row["source"]: row["total"] for row in summary["by_source"]}
        self.assertAlmostEqual(sources["telegram"], 0.10)
        self.assertAlmostEqual(sources["cron"], 0.20)

    def test_get_cost_summary_empty_db(self):
        summary = data.get_cost_summary()
        self.assertEqual(summary["total"], 0.0)
        self.assertEqual(summary["count"], 0)
        self.assertEqual(summary["by_source"], [])

    # --- log tailing ---

    def test_tail_log_missing_file(self):
        result = data.tail_log("does-not-exist.log")
        self.assertIn("找不到", result)

    def test_tail_log_returns_last_n_lines(self):
        log_path = data.LOG_DIR / "worker.log"
        log_path.write_text("\n".join(f"line {i}" for i in range(10)), encoding="utf-8")
        result = data.tail_log("worker.log", lines=3)
        self.assertEqual(result, "line 7\nline 8\nline 9")

    def test_tail_log_large_file_uses_seek(self):
        log_path = data.LOG_DIR / "worker.log"
        # 製造一個超過 max_bytes 的檔案，確認還是拿得到最後幾行、不會整個讀完
        with open(log_path, "w", encoding="utf-8") as f:
            for i in range(5000):
                f.write(f"line {i}\n")
        result = data.tail_log("worker.log", lines=5, max_bytes=1024)
        self.assertIn("line 4999", result)
        self.assertEqual(len(result.splitlines()), 5)

    # --- memory ---

    def test_get_memory_inbox_counts(self):
        inbox = data.MEMORY_DIR / "inbox"
        (inbox / ".processed").mkdir(parents=True)
        (inbox / ".failed").mkdir(parents=True)
        (inbox / "a.md").write_text("x")
        (inbox / ".gitkeep").write_text("")
        (inbox / ".processed" / "b.md").write_text("x")

        counts = data.get_memory_inbox_counts()
        self.assertEqual(counts["pending"], 1)  # .gitkeep 不算
        self.assertEqual(counts["processed"], 1)
        self.assertEqual(counts["failed"], 0)

    def test_get_memory_files(self):
        (data.MEMORY_DIR / "MEMORY.md").write_text("x")
        (data.MEMORY_DIR / "project_a.md").write_text("x")
        files = data.get_memory_files()
        self.assertEqual(files, ["MEMORY.md", "project_a.md"])

    # --- registry ---

    def test_get_domain_status(self):
        (data.REGISTRY_DIR / "agents.yaml").write_text(
            "agents:\n  - id: engineering\n    status: active\n", encoding="utf-8"
        )
        domains = data.get_domain_status()
        self.assertEqual(domains, [{"id": "engineering", "status": "active"}])

    def test_get_domain_status_missing_file(self):
        self.assertEqual(data.get_domain_status(), [])

    # --- adapter config（不能洩漏密鑰）---

    def test_adapter_config_status_never_leaks_token(self):
        (data.CONFIG_DIR / "telegram.json").write_text(
            json.dumps({"bot_token": "super-secret-token", "allowed_chat_ids": [1, 2, 3]}),
            encoding="utf-8",
        )
        (data.CONFIG_DIR / "cron_jobs.yaml").write_text("jobs:\n  - name: a\n", encoding="utf-8")

        status = data.get_adapter_config_status()
        status_str = json.dumps(status)
        self.assertNotIn("super-secret-token", status_str)
        self.assertEqual(status["telegram"]["allowed_chat_ids_count"], 3)
        self.assertTrue(status["telegram"]["configured"])
        self.assertEqual(status["cron"]["job_count"], 1)
        self.assertFalse(status["rss"]["configured"])

    def test_adapter_config_status_not_configured(self):
        status = data.get_adapter_config_status()
        self.assertFalse(status["telegram"]["configured"])
        self.assertFalse(status["cron"]["configured"])
        self.assertFalse(status["rss"]["configured"])

    # --- launchd（parse 邏輯用假的 stdout）---

    def test_get_launchd_status_filters_and_parses(self):
        fake_stdout = (
            "PID\tStatus\tLabel\n"
            "123\t0\tcom.zackchiu.claudecodeos.hermes-worker\n"
            "-\t0\tcom.zackchiu.claudecodeos.hermes-rss\n"
            "456\t0\tcom.apple.something.unrelated\n"
        )
        from unittest.mock import patch, MagicMock
        fake_proc = MagicMock(stdout=fake_stdout)
        with patch("data.subprocess.run", return_value=fake_proc):
            status = data.get_launchd_status()
        self.assertIn("com.zackchiu.claudecodeos.hermes-worker", status)
        self.assertIn("com.zackchiu.claudecodeos.hermes-rss", status)
        self.assertNotIn("com.apple.something.unrelated", status)
        self.assertEqual(status["com.zackchiu.claudecodeos.hermes-worker"]["pid"], "123")

    # --- systemd（ClaudeCodeOSWin 複本新增，parse 邏輯用假的 stdout）---

    def test_get_systemd_status_filters_and_parses(self):
        fake_stdout = (
            "hermes-worker.service          loaded active running Hermes worker\n"
            "hermes-rss.timer               loaded active waiting Run hermes-rss.service\n"
            "some-unrelated.service         loaded active running Unrelated thing\n"
        )
        from unittest.mock import patch, MagicMock
        fake_proc = MagicMock(stdout=fake_stdout)
        with patch("data.subprocess.run", return_value=fake_proc):
            status = data.get_systemd_status()
        self.assertIn("hermes-worker.service", status)
        self.assertIn("hermes-rss.timer", status)
        self.assertNotIn("some-unrelated.service", status)
        self.assertEqual(status["hermes-worker.service"]["last_exit"], "active/running")

    def test_get_systemd_status_returns_empty_when_systemctl_missing(self):
        from unittest.mock import patch
        with patch("data.subprocess.run", side_effect=FileNotFoundError()):
            status = data.get_systemd_status()
        self.assertEqual(status, {})


if __name__ == "__main__":
    unittest.main()
