#!/usr/bin/env python3
"""hermes/adapters/test_telegram.py — v0.1

telegram.py 的邏輯測試：用 unittest.mock 假掉所有真的網路呼叫
（get_updates/send_message），不需要真的 bot token 或網路連線。
真正打 Telegram API 這件事，需要你自己拿真的 bot token 驗證，
細節見 hermes/README.md。

執行：.venv/Scripts/python.exe hermes/adapters/test_telegram.py
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
import telegram  # noqa: E402


class TelegramAdapterTests(unittest.TestCase):
    def setUp(self):
        self._tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp_db.close()
        self._original_db_path = db.DB_PATH
        db.DB_PATH = Path(self._tmp_db.name)
        db.init_db()

        self._tmp_state = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self._tmp_state.close()
        self._original_state_path = telegram.STATE_PATH
        telegram.STATE_PATH = Path(self._tmp_state.name)
        Path(self._tmp_state.name).unlink()  # load_offset() 應該能處理檔案不存在的情況

    def tearDown(self):
        db.DB_PATH = self._original_db_path
        Path(self._tmp_db.name).unlink(missing_ok=True)
        telegram.STATE_PATH = self._original_state_path
        Path(self._tmp_state.name).unlink(missing_ok=True)

    # --- process_update ---

    def test_process_update_enqueues_for_allowed_chat(self):
        update = {
            "update_id": 100,
            "message": {"message_id": 1, "chat": {"id": 111}, "text": "hello"},
        }
        job_id = telegram.process_update(update, allowed_chat_ids={111})
        self.assertIsNotNone(job_id)
        row = db.show_job(job_id)
        self.assertEqual(row["source"], "telegram")
        self.assertEqual(row["prompt"], "hello")
        self.assertEqual(row["thread_id"], "telegram:111")
        payload = json.loads(row["payload"])
        self.assertEqual(payload["chat_id"], 111)
        self.assertEqual(payload["update_id"], 100)

    def test_process_update_ignores_unauthorized_chat(self):
        update = {
            "update_id": 101,
            "message": {"message_id": 2, "chat": {"id": 999}, "text": "hi"},
        }
        job_id = telegram.process_update(update, allowed_chat_ids={111})
        self.assertIsNone(job_id)
        self.assertEqual(len(db.list_jobs()), 0)

    def test_process_update_ignores_non_text_message(self):
        update = {
            "update_id": 102,
            "message": {"message_id": 3, "chat": {"id": 111}, "photo": [{"file_id": "abc"}]},
        }
        job_id = telegram.process_update(update, allowed_chat_ids={111})
        self.assertIsNone(job_id)

    def test_process_update_ignores_non_message_update(self):
        # 例如 edited_message、channel_post 等，目前這版只處理一般訊息
        update = {"update_id": 103, "edited_message": {"chat": {"id": 111}, "text": "edited"}}
        job_id = telegram.process_update(update, allowed_chat_ids={111})
        self.assertIsNone(job_id)

    # --- poll_once ---

    def test_poll_once_advances_offset(self):
        config = {"bot_token": "fake-token", "allowed_chat_ids": [111], "poll_timeout_seconds": 1}
        updates = [
            {"update_id": 5, "message": {"message_id": 1, "chat": {"id": 111}, "text": "a"}},
            {"update_id": 6, "message": {"message_id": 2, "chat": {"id": 111}, "text": "b"}},
        ]
        with patch("telegram.get_updates", return_value=updates) as mock_get:
            new_offset = telegram.poll_once(config, offset=0)
        mock_get.assert_called_once_with("fake-token", 0, 1)
        self.assertEqual(new_offset, 7)  # 最後一筆 update_id(6) + 1
        self.assertEqual(len(db.list_jobs()), 2)

    def test_poll_once_no_updates_keeps_offset(self):
        config = {"bot_token": "fake-token", "allowed_chat_ids": [111], "poll_timeout_seconds": 1}
        with patch("telegram.get_updates", return_value=[]):
            new_offset = telegram.poll_once(config, offset=42)
        self.assertEqual(new_offset, 42)

    # --- deliver_completed_jobs ---

    def test_deliver_sends_and_marks_delivered(self):
        job_id = db.enqueue(
            "telegram", "hi", thread_id="telegram:111", payload={"chat_id": 111}
        )
        db.claim_next_job("worker-1")
        db.mark_completed(job_id, "the answer", cost_usd=0.01)

        config = {"bot_token": "fake-token"}
        with patch("telegram.send_message") as mock_send:
            telegram.deliver_completed_jobs(config)
        mock_send.assert_called_once_with("fake-token", 111, "the answer")

        row = db.show_job(job_id)
        self.assertIsNotNone(row["delivered_at"])

    def test_deliver_skips_and_retries_on_send_failure(self):
        job_id = db.enqueue(
            "telegram", "hi", thread_id="telegram:111", payload={"chat_id": 111}
        )
        db.claim_next_job("worker-1")
        db.mark_completed(job_id, "the answer")

        config = {"bot_token": "fake-token"}
        with patch("telegram.send_message", side_effect=RuntimeError("network down")):
            telegram.deliver_completed_jobs(config)  # 不應該讓例外往外拋

        row = db.show_job(job_id)
        self.assertIsNone(row["delivered_at"])  # 沒送成功，留給下一輪重試

        # 下一輪送成功
        with patch("telegram.send_message") as mock_send:
            telegram.deliver_completed_jobs(config)
        mock_send.assert_called_once()
        row = db.show_job(job_id)
        self.assertIsNotNone(row["delivered_at"])

    def test_deliver_skips_job_without_chat_id_in_payload(self):
        job_id = db.enqueue("telegram", "hi", payload={})  # 沒有 chat_id
        db.claim_next_job("worker-1")
        db.mark_completed(job_id, "the answer")

        config = {"bot_token": "fake-token"}
        with patch("telegram.send_message") as mock_send:
            telegram.deliver_completed_jobs(config)
        mock_send.assert_not_called()

    def test_deliver_ignores_non_telegram_jobs(self):
        job_id = db.enqueue("manual", "hi")
        db.claim_next_job("worker-1")
        db.mark_completed(job_id, "the answer")

        config = {"bot_token": "fake-token"}
        with patch("telegram.send_message") as mock_send:
            telegram.deliver_completed_jobs(config)
        mock_send.assert_not_called()

    # --- offset persistence ---

    def test_offset_roundtrip(self):
        self.assertEqual(telegram.load_offset(), 0)
        telegram.save_offset(123)
        self.assertEqual(telegram.load_offset(), 123)

    # --- push_message（獨立推播進入點，不經過 job queue） ---

    def test_push_message_uses_default_chat_id_from_config(self):
        config = {"bot_token": "fake-token", "allowed_chat_ids": [111, 222]}
        with patch("telegram.send_message") as mock_send:
            result = telegram.push_message(config, "hello there")
        mock_send.assert_called_once_with("fake-token", 111, "hello there")
        self.assertEqual(result, {"chat_id": 111, "text": "hello there"})

    def test_push_message_uses_explicit_chat_id(self):
        config = {"bot_token": "fake-token", "allowed_chat_ids": [111, 222]}
        with patch("telegram.send_message") as mock_send:
            result = telegram.push_message(config, "hi", chat_id=999)
        mock_send.assert_called_once_with("fake-token", 999, "hi")
        self.assertEqual(result, {"chat_id": 999, "text": "hi"})

    def test_push_message_no_allowed_chat_ids_raises(self):
        config = {"bot_token": "fake-token", "allowed_chat_ids": []}
        with patch("telegram.send_message") as mock_send:
            with self.assertRaises(ValueError):
                telegram.push_message(config, "hi")
        mock_send.assert_not_called()

    def test_push_message_does_not_touch_job_queue(self):
        config = {"bot_token": "fake-token", "allowed_chat_ids": [111]}
        with patch("telegram.send_message") as mock_send:
            telegram.push_message(config, "hi")
        mock_send.assert_called_once()
        self.assertEqual(len(db.list_jobs()), 0)  # 不經過 enqueue

    # --- push_cli ---

    def test_push_cli_calls_push_message_with_parsed_args(self):
        with patch("telegram.load_config", return_value={
            "bot_token": "fake-token", "allowed_chat_ids": [111],
        }), patch("telegram.push_message") as mock_push:
            mock_push.return_value = {"chat_id": 111, "text": "hi there"}
            exit_code = telegram.push_cli(["hi there"])
        mock_push.assert_called_once_with(
            {"bot_token": "fake-token", "allowed_chat_ids": [111]},
            "hi there", chat_id=None,
        )
        self.assertEqual(exit_code, 0)

    def test_push_cli_passes_explicit_chat_id(self):
        with patch("telegram.load_config", return_value={
            "bot_token": "fake-token", "allowed_chat_ids": [111],
        }), patch("telegram.push_message") as mock_push:
            mock_push.return_value = {"chat_id": 999, "text": "hi"}
            telegram.push_cli(["hi", "--chat-id", "999"])
        mock_push.assert_called_once_with(
            {"bot_token": "fake-token", "allowed_chat_ids": [111]},
            "hi", chat_id=999,
        )

    # --- config loading ---

    def test_load_config_missing_file_exits(self):
        original = telegram.CONFIG_PATH
        try:
            telegram.CONFIG_PATH = Path(tempfile.gettempdir()) / "does-not-exist-telegram.json"
            with self.assertRaises(SystemExit):
                telegram.load_config()
        finally:
            telegram.CONFIG_PATH = original


if __name__ == "__main__":
    unittest.main()
