#!/usr/bin/env python3
"""hermes/adapters/test_rss.py — v0.1

rss.py 的邏輯測試：用假的 feedparser 回傳值跟暫存的 state/config/db，
不需要真的網路連線。真正打通 feedparser 解析真實 feed 這件事，
見 hermes/README.md 的 live 驗證紀錄。

執行：.venv/Scripts/python.exe hermes/adapters/test_rss.py
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import db  # noqa: E402
import rss  # noqa: E402


class FakeParsed:
    def __init__(self, entries, bozo=False, bozo_exception=None):
        self.entries = entries
        self.bozo = bozo
        self._bozo_exception = bozo_exception

    def get(self, key, default=None):
        if key == "bozo_exception":
            return self._bozo_exception
        return default


class RssAdapterTests(unittest.TestCase):
    def setUp(self):
        self._tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp_db.close()
        self._original_db_path = db.DB_PATH
        db.DB_PATH = Path(self._tmp_db.name)
        db.init_db()

        self._tmp_state = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self._tmp_state.close()
        Path(self._tmp_state.name).unlink()
        self._original_state_path = rss.STATE_PATH
        rss.STATE_PATH = Path(self._tmp_state.name)

    def tearDown(self):
        db.DB_PATH = self._original_db_path
        Path(self._tmp_db.name).unlink(missing_ok=True)
        rss.STATE_PATH = self._original_state_path
        Path(self._tmp_state.name).unlink(missing_ok=True)

    def _feed_config(self, name="test-feed"):
        return {
            "name": name,
            "url": "https://example.invalid/rss",
            "prompt_template": "新文章：「{title}」（{link}）",
        }

    # --- 第一次看到 feed：只建立基準線，不 enqueue ---

    def test_first_run_bootstraps_without_enqueueing(self):
        entries = [
            {"id": "guid-1", "title": "文章一", "link": "https://x/1"},
            {"id": "guid-2", "title": "文章二", "link": "https://x/2"},
        ]
        with patch("rss.feedparser.parse", return_value=FakeParsed(entries)):
            new_count = rss.process_feed(self._feed_config(), seen={})
        self.assertEqual(new_count, 0)
        self.assertEqual(len(db.list_jobs()), 0)

    def test_second_run_with_no_new_entries_enqueues_nothing(self):
        entries = [{"id": "guid-1", "title": "文章一", "link": "https://x/1"}]
        seen = {}
        with patch("rss.feedparser.parse", return_value=FakeParsed(entries)):
            rss.process_feed(self._feed_config(), seen)  # 第一次：建基準線
            new_count = rss.process_feed(self._feed_config(), seen)  # 第二次：同樣的內容
        self.assertEqual(new_count, 0)
        self.assertEqual(len(db.list_jobs()), 0)

    def test_second_run_enqueues_only_genuinely_new_entry(self):
        first_entries = [{"id": "guid-1", "title": "文章一", "link": "https://x/1"}]
        second_entries = first_entries + [{"id": "guid-2", "title": "文章二", "link": "https://x/2"}]
        seen = {}
        with patch("rss.feedparser.parse", return_value=FakeParsed(first_entries)):
            rss.process_feed(self._feed_config(), seen)
        with patch("rss.feedparser.parse", return_value=FakeParsed(second_entries)):
            new_count = rss.process_feed(self._feed_config(), seen)

        self.assertEqual(new_count, 1)
        jobs = db.list_jobs()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["source"], "rss")
        self.assertIn("文章二", jobs[0]["prompt"])
        payload = json.loads(jobs[0]["payload"])
        self.assertEqual(payload["guid"], "guid-2")
        self.assertEqual(payload["feed"], "test-feed")

    # --- 去重上限 ---

    def test_seen_list_capped_at_max_per_feed(self):
        seen = {"test-feed": [f"old-{i}" for i in range(rss.MAX_SEEN_PER_FEED)]}
        new_entries = [{"id": "guid-new", "title": "新的", "link": "https://x/new"}]
        with patch("rss.feedparser.parse", return_value=FakeParsed(new_entries)):
            rss.process_feed(self._feed_config(), seen)
        self.assertEqual(len(seen["test-feed"]), rss.MAX_SEEN_PER_FEED)
        self.assertIn("guid-new", seen["test-feed"])
        self.assertNotIn("old-0", seen["test-feed"])  # 最舊的被擠掉

    # --- 沒有 guid 的項目安全跳過 ---

    def test_entry_without_any_identifier_is_skipped(self):
        entries = [{"summary": "沒有 id/link/title"}]
        with patch("rss.feedparser.parse", return_value=FakeParsed(entries)):
            new_count = rss.process_feed(self._feed_config(), seen={"test-feed": []})
        self.assertEqual(new_count, 0)
        self.assertEqual(len(db.list_jobs()), 0)

    # --- 單一 feed 失敗不影響其他 feed（run_once 層級）---

    def test_run_once_isolates_per_feed_failures(self):
        good_config = {
            "feeds": [
                {"name": "broken-feed", "url": "https://bad", "prompt_template": "{title}"},
                {"name": "good-feed", "url": "https://good", "prompt_template": "{title}（{link}）"},
            ]
        }
        tmp_config = tempfile.NamedTemporaryFile(suffix=".yaml", delete=False, mode="w")
        import yaml
        yaml.safe_dump(good_config, tmp_config)
        tmp_config.close()
        original_config_path = rss.CONFIG_PATH
        rss.CONFIG_PATH = Path(tmp_config.name)
        try:
            def fake_parse(url):
                if url == "https://bad":
                    raise ConnectionError("network down")
                return FakeParsed([{"id": "g1", "title": "好文章", "link": "https://good/1"}])

            with patch("rss.feedparser.parse", side_effect=fake_parse):
                rss.run_once()  # 第一次：good-feed 建基準線
                total_new = rss.run_once()  # 第二次：good-feed 沒有新項目（同一批），broken-feed 持續失敗

            self.assertEqual(total_new, 0)  # broken-feed 從沒成功過，good-feed 第二輪也沒新內容
        finally:
            rss.CONFIG_PATH = original_config_path
            Path(tmp_config.name).unlink(missing_ok=True)

    # --- 設定檔不存在 ---

    def test_load_config_missing_file_exits(self):
        original = rss.CONFIG_PATH
        try:
            rss.CONFIG_PATH = Path(tempfile.gettempdir()) / "does-not-exist-rss.yaml"
            with self.assertRaises(SystemExit):
                rss.load_config()
        finally:
            rss.CONFIG_PATH = original

    # --- bozo 且沒有任何 entries 時視為失敗 ---

    def test_bozo_with_no_entries_raises(self):
        with patch("rss.feedparser.parse", return_value=FakeParsed([], bozo=True, bozo_exception="bad xml")):
            with self.assertRaises(ValueError):
                rss.process_feed(self._feed_config(), seen={})

    # --- state 讀寫 roundtrip ---

    def test_seen_state_roundtrip(self):
        self.assertEqual(rss.load_seen(), {})
        rss.save_seen({"feed-a": ["g1", "g2"]})
        self.assertEqual(rss.load_seen(), {"feed-a": ["g1", "g2"]})


if __name__ == "__main__":
    unittest.main()
