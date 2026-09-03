#!/usr/bin/env python3
"""scripts/test_jobs_freshness_watchdog.py — v0.1（F5）

scripts/jobs_freshness_watchdog.py 的 deterministic 測試。

原則：
- **絕不碰真的 jobs.db**：每個測試在 tempdir 自建臨時 SQLite（只造本 script
  需要的 jobs 欄位子集），時間以 `--now` / now 參數固定。
- **絕不真的送 Slack**：send CLI 一律注入 mock（動態寫出的 python 腳本，
  把 argv 落到檔案供斷言），或走 --dry-run。
- 三態（健康／執行端死了／觸發端死了）各有獨立測試，另加誤報防線
  （事件驅動 source 零進件 = 健康）與 fail-closed（db 不存在）。

執行：.venv/Scripts/python.exe scripts/test_jobs_freshness_watchdog.py
"""
import json
import subprocess
import sqlite3
import sys
import tempfile
import textwrap
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import jobs_freshness_watchdog as wd  # noqa: E402

NOW = datetime(2026, 9, 3, 12, 0, 0, tzinfo=timezone.utc)

CONFIG_TEMPLATE = """
version: 1
defaults:
  lookback_hours: 48
  min_expected_enqueued: 1
  stuck_backlog_hours: 2
  dead_letter_ratio_threshold: 0.5
  min_terminal_sample: 4
sources:
  - id: cron
    expect_enqueue: true
  - id: telegram
    expect_enqueue: false
alert:
  channel: CTEST00000
  send_cli: hermes
  message_key_prefix: agentos-watchdog-test
"""

SCHEMA = """
CREATE TABLE jobs (
    id TEXT PRIMARY KEY, source TEXT NOT NULL, status TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, completed_at TEXT
);
"""


def iso(dt: datetime) -> str:
    return dt.isoformat()


class WatchdogTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.db = self.tmp / "jobs.db"
        self.config = self.tmp / "jobs_watchdog.yaml"
        self.config.write_text(CONFIG_TEMPLATE, encoding="utf-8")
        conn = sqlite3.connect(self.db)
        conn.executescript(SCHEMA)
        conn.commit()
        conn.close()
        self._n = 0
        self.addCleanup(self._tmp.cleanup)

    def add_job(self, source, status, *, hours_ago=1.0, completed=True):
        """插一筆 job（臨時 db，不是 production）。"""
        self._n += 1
        ts = iso(NOW - timedelta(hours=hours_ago))
        completed_at = ts if (status == "completed" and completed) else None
        conn = sqlite3.connect(self.db)
        conn.execute(
            "INSERT INTO jobs (id, source, status, created_at, updated_at, "
            "completed_at) VALUES (?,?,?,?,?,?)",
            (f"job-{self._n}", source, status, ts, ts, completed_at))
        conn.commit()
        conn.close()

    def run_watchdog(self, **kwargs):
        kwargs.setdefault("dry_run", True)
        return wd.run(jobs_db=self.db, config_path=self.config, now=NOW, **kwargs)

    def state_of(self, result, source):
        return next(f["state"] for f in result["findings"] if f["source"] == source)


class ThreeStateTest(WatchdogTestBase):
    """三態判別——這是看門狗有沒有用的核心。"""

    def test_healthy_has_enqueue_and_completed(self):
        for _ in range(5):
            self.add_job("cron", "completed", hours_ago=3)
        self.add_job("cron", "dead_letter", hours_ago=4)
        result = self.run_watchdog()
        self.assertEqual(self.state_of(result, "cron"), wd.STATE_HEALTHY)
        self.assertEqual(result["alerting"], [])
        self.assertIsNone(result["message"])

    def test_executor_dead_enqueued_but_zero_completed(self):
        """2026-08 的實際情況：有進件、全部 dead_letter。"""
        for _ in range(3):
            self.add_job("cron", "dead_letter", hours_ago=5)
        result = self.run_watchdog()
        self.assertEqual(self.state_of(result, "cron"), wd.STATE_EXECUTOR_DEAD)
        self.assertEqual(result["alerting"], ["cron"])
        self.assertIn("執行端死了", result["message"])

    def test_executor_dead_when_jobs_stuck_in_queue(self):
        """worker 完全沒在跑：job 卡在 queued 超過門檻，連 dead_letter 都沒有。"""
        for _ in range(2):
            self.add_job("cron", "queued", hours_ago=10)
        result = self.run_watchdog()
        self.assertEqual(self.state_of(result, "cron"), wd.STATE_EXECUTOR_DEAD)

    def test_trigger_dead_no_enqueue_at_all(self):
        """timer/task 掛了：window 內完全沒有進件（舊資料不算數）。"""
        self.add_job("cron", "completed", hours_ago=1000)
        result = self.run_watchdog()
        self.assertEqual(self.state_of(result, "cron"), wd.STATE_TRIGGER_DEAD)
        self.assertEqual(result["alerting"], ["cron"])
        self.assertIn("觸發端死了", result["message"])

    def test_three_states_are_distinguished_from_each_other(self):
        """同一份判準必須把三態分開——不是「都叫異常」就好。"""
        counts_healthy = {"enqueued": 5, "completed": 5, "dead_letter": 0,
                          "stuck": 0, "last_completed_at": None}
        counts_exec = {"enqueued": 5, "completed": 0, "dead_letter": 5,
                       "stuck": 0, "last_completed_at": None}
        counts_trigger = {"enqueued": 0, "completed": 0, "dead_letter": 0,
                          "stuck": 0, "last_completed_at": None}
        kw = dict(expect_enqueue=True, min_expected_enqueued=1,
                  dead_letter_ratio_threshold=0.5, min_terminal_sample=4)
        self.assertEqual(wd.classify(counts_healthy, **kw)[0], wd.STATE_HEALTHY)
        self.assertEqual(wd.classify(counts_exec, **kw)[0], wd.STATE_EXECUTOR_DEAD)
        self.assertEqual(wd.classify(counts_trigger, **kw)[0], wd.STATE_TRIGGER_DEAD)


class FalsePositiveGuardTest(WatchdogTestBase):
    """誤報防線——看門狗一旦會亂叫，就會被忽略，等於沒有。"""

    def test_event_driven_source_with_no_enqueue_is_healthy(self):
        """telegram（expect_enqueue: false）零進件是正常，不能報 trigger_dead。"""
        result = self.run_watchdog()
        self.assertEqual(self.state_of(result, "telegram"), wd.STATE_HEALTHY)

    def test_fresh_jobs_without_result_yet_are_inconclusive(self):
        """剛進件、還在跑（未超過 stuck 門檻）→ 不告警。"""
        self.add_job("cron", "queued", hours_ago=0.5)
        result = self.run_watchdog()
        self.assertEqual(self.state_of(result, "cron"), wd.STATE_INCONCLUSIVE)
        self.assertEqual(result["alerting"], [])

    def test_small_sample_dead_letter_does_not_trip_ratio(self):
        """樣本不足時比例無意義（1 死 1 成＝50% 但只有 2 筆）。"""
        self.add_job("cron", "completed", hours_ago=2)
        self.add_job("cron", "dead_letter", hours_ago=2)
        result = self.run_watchdog()
        self.assertEqual(self.state_of(result, "cron"), wd.STATE_HEALTHY)

    def test_degraded_when_ratio_exceeds_with_enough_sample(self):
        self.add_job("cron", "completed", hours_ago=2)
        for _ in range(4):
            self.add_job("cron", "dead_letter", hours_ago=2)
        result = self.run_watchdog()
        self.assertEqual(self.state_of(result, "cron"), wd.STATE_EXECUTOR_DEGRADED)
        self.assertEqual(result["alerting"], ["cron"])


class FailClosedTest(WatchdogTestBase):
    def test_missing_db_is_error_not_silence(self):
        with self.assertRaises(wd.WatchdogError) as ctx:
            wd.run(jobs_db=self.tmp / "nope.db", config_path=self.config,
                   dry_run=True, now=NOW)
        self.assertIn("jobs.db 不存在", str(ctx.exception))

    def test_missing_config_is_error(self):
        with self.assertRaises(wd.WatchdogError):
            wd.run(jobs_db=self.db, config_path=self.tmp / "nope.yaml",
                   dry_run=True, now=NOW)

    def test_incomplete_config_is_rejected(self):
        bad = self.tmp / "bad.yaml"
        bad.write_text("defaults: {lookback_hours: 1}\nsources: []\n", encoding="utf-8")
        with self.assertRaises(wd.WatchdogError):
            wd.load_config(bad)

    def test_db_is_opened_read_only(self):
        conn = wd._read_only_conn(self.db)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("INSERT INTO jobs (id, source, status, created_at, "
                             "updated_at) VALUES ('x','cron','queued','a','a')")
        finally:
            conn.close()


class MessageAndKeyTest(WatchdogTestBase):
    def test_message_contains_no_job_content_fields(self):
        for _ in range(3):
            self.add_job("cron", "dead_letter", hours_ago=5)
        msg = self.run_watchdog()["message"]
        for forbidden in ("prompt", "result=", "error_message", "session_id"):
            self.assertNotIn(forbidden, msg)

    def test_message_key_is_stable_for_same_state_and_day(self):
        for _ in range(3):
            self.add_job("cron", "dead_letter", hours_ago=5)
        k1 = self.run_watchdog()["message_key"]
        k2 = self.run_watchdog()["message_key"]
        self.assertEqual(k1, k2)
        self.assertTrue(k1.startswith("agentos-watchdog-test:2026-09-03:"))

    def test_message_key_changes_when_state_changes(self):
        for _ in range(3):
            self.add_job("cron", "dead_letter", hours_ago=5)
        k1 = self.run_watchdog()["message_key"]
        # telegram 也壞掉 → fingerprint 必須改變（不會被昨天的 key 吃掉）
        for _ in range(3):
            self.add_job("telegram", "dead_letter", hours_ago=5)
        k2 = self.run_watchdog()["message_key"]
        self.assertNotEqual(k1, k2)


class SendPathTest(WatchdogTestBase):
    """告警發送路徑——驗證「能發出去」，但用 mock CLI，不真的往 Slack 送。"""

    def _mock_cli(self, exit_code=0):
        capture = self.tmp / "sent.json"
        script = self.tmp / "mock_send.py"
        script.write_text(textwrap.dedent(f"""
            import json, sys
            with open(r"{capture}", "w", encoding="utf-8") as f:
                json.dump(sys.argv[1:], f)
            sys.exit({exit_code})
        """), encoding="utf-8")
        return f'"{sys.executable}" "{script}"', capture

    def test_alert_is_sent_with_expected_argv_shape(self):
        for _ in range(3):
            self.add_job("cron", "dead_letter", hours_ago=5)
        send_cli, capture = self._mock_cli()
        result = self.run_watchdog(dry_run=False, send_cli=send_cli)
        self.assertTrue(result["sent"])
        argv = json.loads(capture.read_text(encoding="utf-8"))
        self.assertEqual(argv[0], "send")
        self.assertEqual(argv[1:3], ["-t", "slack:CTEST00000"])
        self.assertEqual(argv[3], "--message-key")
        self.assertEqual(argv[4], result["message_key"])
        self.assertIn("執行端死了", argv[5])

    def test_no_send_when_healthy(self):
        for _ in range(3):
            self.add_job("cron", "completed", hours_ago=2)
        send_cli, capture = self._mock_cli()
        result = self.run_watchdog(dry_run=False, send_cli=send_cli)
        self.assertFalse(capture.exists())
        self.assertFalse(result["sent"])

    def test_dry_run_never_calls_send_cli(self):
        for _ in range(3):
            self.add_job("cron", "dead_letter", hours_ago=5)
        send_cli, capture = self._mock_cli()
        result = self.run_watchdog(dry_run=True, send_cli=send_cli)
        self.assertFalse(capture.exists())
        self.assertIsNotNone(result["message"])

    def test_send_failure_is_fail_closed(self):
        for _ in range(3):
            self.add_job("cron", "dead_letter", hours_ago=5)
        send_cli, _ = self._mock_cli(exit_code=7)
        with self.assertRaises(wd.WatchdogError):
            self.run_watchdog(dry_run=False, send_cli=send_cli)

    def test_missing_send_cli_is_fail_loud(self):
        for _ in range(3):
            self.add_job("cron", "dead_letter", hours_ago=5)
        with self.assertRaises(wd.WatchdogError):
            self.run_watchdog(dry_run=False,
                              send_cli="definitely-not-a-real-binary-xyz")


class CliTest(WatchdogTestBase):
    def _cli(self, *args):
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "jobs_freshness_watchdog.py"),
             "--jobs-db", str(self.db), "--config", str(self.config),
             "--now", NOW.isoformat(), *args],
            cwd=ROOT, capture_output=True, encoding="utf-8", timeout=60)

    def test_exit_0_when_healthy(self):
        for _ in range(3):
            self.add_job("cron", "completed", hours_ago=2)
        proc = self._cli("--dry-run")
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)

    def test_exit_3_when_anomaly(self):
        for _ in range(3):
            self.add_job("cron", "dead_letter", hours_ago=5)
        proc = self._cli("--dry-run", "--json")
        self.assertEqual(proc.returncode, 3, msg=proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["alerting"], ["cron"])

    def test_exit_1_when_db_missing(self):
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "jobs_freshness_watchdog.py"),
             "--jobs-db", str(self.tmp / "nope.db"), "--config", str(self.config),
             "--now", NOW.isoformat(), "--dry-run"],
            cwd=ROOT, capture_output=True, encoding="utf-8", timeout=60)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("看門狗失敗", proc.stderr)


class RealConfigTest(unittest.TestCase):
    def test_shipped_config_is_valid(self):
        cfg = wd.load_config(ROOT / "registry" / "jobs_watchdog.yaml")
        ids = {s["id"] for s in cfg["sources"]}
        self.assertIn("cron", ids)
        self.assertIn("rss", ids)
        expect = {s["id"]: s["expect_enqueue"] for s in cfg["sources"]}
        self.assertTrue(expect["cron"])
        self.assertFalse(expect["telegram"])


class SystemdUnitTest(unittest.TestCase):
    """掛載形狀的迴歸鎖：unit 存在、是 oneshot、**沒有 .timer**（排程權在
    Windows Task Scheduler，不新建排程工作）、ExecStart 指向本 script。"""

    UNIT = ROOT / "hermes" / "systemd" / "hermes-jobs-watchdog.service"

    def test_unit_exists_and_is_oneshot(self):
        text = self.UNIT.read_text(encoding="utf-8")
        self.assertIn("Type=oneshot", text)
        self.assertIn("scripts/jobs_freshness_watchdog.py", text)
        self.assertNotIn("Restart=", text)

    def test_no_timer_shipped(self):
        self.assertFalse((ROOT / "hermes" / "systemd"
                          / "hermes-jobs-watchdog.timer").exists())

    def test_path_includes_local_bin_for_hermes_cli(self):
        self.assertIn(".local/bin", self.UNIT.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
