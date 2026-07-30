#!/usr/bin/env python3
"""scripts/test_log_recall.py — v0.1

log_recall.py 的最小測試：append 格式、多次 append 不互相破壞、
參數驗證（非法 entry/result 拒收）、logs 目錄自動建立、單行錯誤契約。

執行：.venv/Scripts/python.exe scripts/test_log_recall.py
（WSL：.venv/bin/python scripts/test_log_recall.py）
"""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import log_recall  # noqa: E402


def run_main(argv):
    """跑 main() 並攔截 stdout/stderr。回傳 (exit_code, stdout, stderr)。
    參數錯誤走 SystemExit（argparse 慣例），一併轉成 exit code。"""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = log_recall.main(argv)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return code, out.getvalue(), err.getvalue()


class AppendFormatTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.log_file = Path(self._tmp.name) / "logs" / "recall_log.jsonl"

    def test_append_writes_valid_jsonl_with_expected_fields(self):
        code, out, err = run_main([
            "--entry", "interactive", "--result", "hit_memory",
            "--hit-ids", "hermes-profile-intended-config.md, stage25-ready-to-start.md",
            "--task-hint", "查 Hermes profile 正解",
            "--log-file", str(self.log_file),
        ])
        self.assertEqual(code, 0, f"stderr: {err}")
        lines = self.log_file.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        rec = json.loads(lines[0])
        self.assertEqual(rec["entry"], "interactive")
        self.assertEqual(rec["result"], "hit_memory")
        self.assertEqual(rec["hit_ids"], [
            "hermes-profile-intended-config.md", "stage25-ready-to-start.md"])
        self.assertEqual(rec["task_hint"], "查 Hermes profile 正解")
        # ts 是 UTC ISO 8601（datetime.fromisoformat 可解析且帶 tz）
        from datetime import datetime
        ts = datetime.fromisoformat(rec["ts"])
        self.assertIsNotNone(ts.tzinfo)

    def test_optional_fields_default_to_empty(self):
        code, _, err = run_main([
            "--entry", "headless", "--result", "miss",
            "--log-file", str(self.log_file),
        ])
        self.assertEqual(code, 0, f"stderr: {err}")
        rec = json.loads(self.log_file.read_text(encoding="utf-8"))
        self.assertEqual(rec["hit_ids"], [])
        self.assertEqual(rec["task_hint"], "")

    def test_non_ascii_written_verbatim_not_escaped(self):
        code, _, _ = run_main([
            "--entry", "interactive", "--result", "hit_skill",
            "--task-hint", "整併記憶",
            "--log-file", str(self.log_file),
        ])
        self.assertEqual(code, 0)
        raw = self.log_file.read_text(encoding="utf-8")
        self.assertIn("整併記憶", raw)  # ensure_ascii=False，兩側人類可讀

    def test_creates_logs_directory_if_missing(self):
        nested = Path(self._tmp.name) / "deep" / "logs" / "recall_log.jsonl"
        self.assertFalse(nested.parent.exists())
        code, _, err = run_main([
            "--entry", "interactive", "--result", "miss",
            "--log-file", str(nested),
        ])
        self.assertEqual(code, 0, f"stderr: {err}")
        self.assertTrue(nested.is_file())


class AppendOnlyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.log_file = Path(self._tmp.name) / "recall_log.jsonl"

    def test_multiple_appends_do_not_clobber_earlier_lines(self):
        for i, result in enumerate(["miss", "hit_skill", "hit_memory"]):
            code, _, err = run_main([
                "--entry", "interactive", "--result", result,
                "--task-hint", f"task-{i}",
                "--log-file", str(self.log_file),
            ])
            self.assertEqual(code, 0, f"stderr: {err}")
        lines = self.log_file.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 3)
        records = [json.loads(line) for line in lines]
        self.assertEqual([r["result"] for r in records],
                         ["miss", "hit_skill", "hit_memory"])
        self.assertEqual([r["task_hint"] for r in records],
                         ["task-0", "task-1", "task-2"])

    def test_preexisting_foreign_lines_left_untouched(self):
        # 既有內容（例如另一側 sync 併過來的行）不能被改寫
        foreign = '{"ts": "2026-07-01T00:00:00+00:00", "entry": "headless", ' \
                  '"result": "miss", "hit_ids": [], "task_hint": "舊行"}\n'
        self.log_file.write_text(foreign, encoding="utf-8")
        code, _, _ = run_main([
            "--entry", "interactive", "--result", "hit_memory",
            "--log-file", str(self.log_file),
        ])
        self.assertEqual(code, 0)
        content = self.log_file.read_text(encoding="utf-8")
        self.assertTrue(content.startswith(foreign))
        self.assertEqual(len(content.splitlines()), 2)


class ArgumentValidationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.log_file = Path(self._tmp.name) / "recall_log.jsonl"

    def test_rejects_invalid_entry(self):
        code, _, err = run_main([
            "--entry", "cron", "--result", "miss",
            "--log-file", str(self.log_file),
        ])
        self.assertNotEqual(code, 0)
        self.assertFalse(self.log_file.exists(), "拒收時不得留下任何寫入")
        self.assertEqual(len(err.strip().splitlines()), 1, "錯誤訊息必須只有一行")

    def test_rejects_invalid_result(self):
        code, _, err = run_main([
            "--entry", "interactive", "--result", "hit_everything",
            "--log-file", str(self.log_file),
        ])
        self.assertNotEqual(code, 0)
        self.assertFalse(self.log_file.exists())
        self.assertEqual(len(err.strip().splitlines()), 1)

    def test_rejects_missing_required_args(self):
        code, _, err = run_main(["--log-file", str(self.log_file)])
        self.assertNotEqual(code, 0)
        self.assertEqual(len(err.strip().splitlines()), 1)

    def test_write_failure_exits_nonzero_with_single_line_error(self):
        # 把 log 路徑指向「父層是一個檔案」→ mkdir/開檔必失敗
        blocker = Path(self._tmp.name) / "not_a_dir"
        blocker.write_text("x", encoding="utf-8")
        code, _, err = run_main([
            "--entry", "interactive", "--result", "miss",
            "--log-file", str(blocker / "recall_log.jsonl"),
        ])
        self.assertEqual(code, 1)
        self.assertEqual(len(err.strip().splitlines()), 1, "錯誤訊息必須只有一行")


class HitIdsParsingTests(unittest.TestCase):
    def test_parse_hit_ids_variants(self):
        self.assertEqual(log_recall.parse_hit_ids(""), [])
        self.assertEqual(log_recall.parse_hit_ids("a"), ["a"])
        self.assertEqual(log_recall.parse_hit_ids("a, b ,c"), ["a", "b", "c"])
        self.assertEqual(log_recall.parse_hit_ids(",,a,"), ["a"])


if __name__ == "__main__":
    unittest.main()
