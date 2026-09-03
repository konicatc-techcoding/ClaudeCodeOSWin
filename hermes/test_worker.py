#!/usr/bin/env python3
"""hermes/test_worker.py — v0.1（F2）

hermes/worker.py 的 `build_failure_message()` 測試——「失敗 job 的
error_message 要有診斷價值」這件事的迴歸防線。

背景：`claude -p --output-format json` 把錯誤寫在 **stdout 的 JSON**，
stderr 是空的；原本只取 stderr → error_message 永遠是
`invoke_cos.sh exit code 1: `，導致 2026-08 全線 dead_letter 28 天查不出
原因。

本檔不啟動 worker、不碰 jobs.db、不呼叫任何子程序——只測純函式。

執行：.venv/Scripts/python.exe hermes/test_worker.py
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import worker  # noqa: E402


class BuildFailureMessageTest(unittest.TestCase):
    def test_stderr_wins_when_present(self):
        msg = worker.build_failure_message(1, '{"result": "x"}', "boom: no such file")
        self.assertIn("exit code 1", msg)
        self.assertIn("boom: no such file", msg)

    def test_falls_back_to_stdout_json_result_on_error_payload(self):
        """這次事故的形狀：exit 1、stderr 空、錯誤在 stdout JSON 裡。"""
        stdout = json.dumps({
            "type": "result", "subtype": "error_during_execution",
            "is_error": True, "session_id": "abc",
            "result": "Not logged in · Please run /login",
        })
        msg = worker.build_failure_message(1, stdout, "")
        self.assertIn("Not logged in", msg)
        self.assertIn("subtype=error_during_execution", msg)
        self.assertIn("is_error=True", msg)

    def test_success_payload_result_is_not_stored(self):
        """exit != 0 但 payload 自稱成功：result 是給使用者的完整答案，
        可能含使用者資料 → 只記 metadata，內容不入庫。"""
        secret = "使用者的私人資料 12345"
        stdout = json.dumps({"subtype": "success", "is_error": False,
                             "result": secret})
        msg = worker.build_failure_message(1, stdout, "")
        self.assertNotIn(secret, msg)
        self.assertIn("subtype=success", msg)
        self.assertIn("不入庫", msg)

    def test_non_json_stdout_is_truncated_short(self):
        stdout = "x" * 5000
        msg = worker.build_failure_message(1, stdout, "")
        self.assertIn("stdout 非 JSON", msg)
        self.assertLessEqual(len(msg), worker.ERROR_MESSAGE_MAX_CHARS)
        self.assertLess(msg.count("x"), 5000)

    def test_error_result_is_truncated(self):
        stdout = json.dumps({"subtype": "error", "is_error": True,
                             "result": "E" * 5000})
        msg = worker.build_failure_message(1, stdout, "")
        self.assertLessEqual(len(msg), worker.ERROR_MESSAGE_MAX_CHARS)

    def test_total_length_capped(self):
        msg = worker.build_failure_message(1, "", "S" * 5000)
        self.assertLessEqual(len(msg), worker.ERROR_MESSAGE_MAX_CHARS)

    def test_both_empty_says_so_instead_of_blank(self):
        msg = worker.build_failure_message(137, "", "")
        self.assertIn("exit code 137", msg)
        self.assertIn("皆為空", msg)

    def test_json_array_stdout_is_handled(self):
        msg = worker.build_failure_message(1, "[1, 2, 3]", "")
        self.assertIn("JSON 非物件", msg)


if __name__ == "__main__":
    unittest.main()
