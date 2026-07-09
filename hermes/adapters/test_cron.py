#!/usr/bin/env python3
"""hermes/adapters/test_cron.py — v0.1

cron.py 的邏輯測試：用暫存的設定檔跟資料庫，不影響真正的
hermes/config/cron_jobs.yaml 或 hermes/jobs.db。

執行：.venv/Scripts/python.exe hermes/adapters/test_cron.py
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import db  # noqa: E402
import cron  # noqa: E402


class CronAdapterTests(unittest.TestCase):
    def setUp(self):
        self._tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp_db.close()
        self._original_db_path = db.DB_PATH
        db.DB_PATH = Path(self._tmp_db.name)
        db.init_db()

        # encoding 必須明確指定：cron.py 用 utf-8 讀設定檔，Windows 的預設
        # locale encoding（cp950）寫出的中文會讓讀取端 UnicodeDecodeError。
        self._tmp_config = tempfile.NamedTemporaryFile(
            suffix=".yaml", delete=False, mode="w", encoding="utf-8"
        )
        self._tmp_config.write(
            "jobs:\n"
            "  - name: daily-memory-check\n"
            '    prompt: "檢查 inbox"\n'
            "    max_attempts: 3\n"
            "  - name: no-max-attempts-job\n"
            '    prompt: "沒寫 max_attempts"\n'
        )
        self._tmp_config.close()
        self._original_config_path = cron.CONFIG_PATH
        cron.CONFIG_PATH = Path(self._tmp_config.name)

    def tearDown(self):
        db.DB_PATH = self._original_db_path
        Path(self._tmp_db.name).unlink(missing_ok=True)
        cron.CONFIG_PATH = self._original_config_path
        Path(self._tmp_config.name).unlink(missing_ok=True)

    def test_enqueue_job_success(self):
        job_id = cron.enqueue_job("daily-memory-check")
        row = db.show_job(job_id)
        self.assertEqual(row["source"], "cron")
        self.assertEqual(row["prompt"], "檢查 inbox")
        self.assertEqual(row["max_attempts"], 3)
        self.assertEqual(row["status"], "queued")

    def test_enqueue_job_unknown_name_raises(self):
        with self.assertRaises(KeyError):
            cron.enqueue_job("does-not-exist")
        self.assertEqual(len(db.list_jobs()), 0)

    def test_enqueue_job_default_max_attempts(self):
        job_id = cron.enqueue_job("no-max-attempts-job")
        row = db.show_job(job_id)
        self.assertEqual(row["max_attempts"], 3)  # db.enqueue() 的預設值

    def test_load_jobs_missing_config_exits(self):
        cron.CONFIG_PATH = Path(tempfile.gettempdir()) / "does-not-exist-cron.yaml"
        with self.assertRaises(SystemExit):
            cron.load_jobs()

    def test_each_call_is_independent(self):
        # 驗證「無狀態、一次呼叫只 enqueue 一次」——呼叫兩次應該產生兩筆不同的 job
        job_id_1 = cron.enqueue_job("daily-memory-check")
        job_id_2 = cron.enqueue_job("daily-memory-check")
        self.assertNotEqual(job_id_1, job_id_2)
        self.assertEqual(len(db.list_jobs()), 2)


if __name__ == "__main__":
    unittest.main()
