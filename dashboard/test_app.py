#!/usr/bin/env python3
"""dashboard/test_app.py — v0.1

用 Streamlit 官方的 AppTest API 實際「跑」dashboard/app.py（不需要瀏覽器、
不需要真的啟動 server），確認畫面渲染不會噴例外、關鍵元件都有出現。
這是對著真正的專案資料跑的（read-only，不會寫入任何東西）。

執行：.venv/Scripts/python.exe dashboard/test_app.py
"""
import json
import sys
import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest

sys.path.insert(0, str(Path(__file__).resolve().parent))


class DashboardAppTests(unittest.TestCase):
    def setUp(self):
        self.at = AppTest.from_file(
            str(Path(__file__).resolve().parent / "app.py"), default_timeout=30
        )
        self.at.run()

    def test_no_exceptions(self):
        self.assertEqual(len(self.at.exception), 0, msg=[str(e) for e in self.at.exception])

    def test_title_and_tabs(self):
        self.assertEqual(self.at.title[0].value, "Claude Code OS — Dashboard")
        self.assertEqual(len(self.at.tabs), 5)

    def test_status_metrics_present(self):
        labels = {m.label for m in self.at.metric}
        for status in ["queued", "running", "completed", "failed", "dead_letter"]:
            self.assertIn(status, labels)

    def test_adapter_config_never_leaks_secret_in_render(self):
        # telegram.json 裡真的有 bot_token，這裡是確認「渲染出來的畫面」不含它
        config_path = Path(__file__).resolve().parent.parent / "hermes" / "config" / "telegram.json"
        rendered = json.dumps([j.value for j in self.at.json])
        if config_path.exists():
            real_config = json.loads(config_path.read_text(encoding="utf-8"))
            token = real_config.get("bot_token", "")
            if token:
                self.assertNotIn(token, rendered)
        self.assertNotIn("bot_token", rendered)

    def test_job_detail_lookup_with_real_job_id(self):
        import data

        recent = data.get_recent_jobs(limit=1)
        if not recent:
            self.skipTest("jobs.db 目前沒有任何 job 可以測")
        job_id = recent[0]["id"]
        self.at.text_input[0].set_value(job_id).run()
        self.assertEqual(len(self.at.exception), 0)
        self.assertGreaterEqual(len(self.at.json), 2)  # adapter config + job detail

    def test_job_detail_lookup_with_unknown_id_shows_error(self):
        self.at.text_input[0].set_value("this-job-id-does-not-exist").run()
        self.assertEqual(len(self.at.exception), 0)
        self.assertGreaterEqual(len(self.at.error), 1)


if __name__ == "__main__":
    unittest.main()
