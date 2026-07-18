#!/usr/bin/env python3
"""hermes/test_systemd_units.py — v0.2（Stage 2.7b 擴充自 2.4b）

hermes/systemd/ 底下 bridge scanner／pipeline／notifier unit 檔的靜態驗證：
純文字檢查、零副作用（不跑 systemctl、不碰任何 db／inbox）。目的：避免
手改 unit 檔時默默壞掉排程語義——尤其是「排程一律無範圍／dry-run 參數」
與「Persistent=true」這兩條。

守住的硬條件（scanner，2.4b 既有）：
- .service 存在、Type=oneshot、ExecStart 指向 bridge_scanner.py 且子命令是
  無參數 scan（不得帶 --since/--all-history/--dry-run、不得排程 reconcile）
- 失敗語義靠「非零 exit → unit failed」呈現，不得設 Restart=
- .timer 存在、Persistent=true、OnCalendar 排程、正確指回 .service、
  掛在 timers.target
- install.sh／uninstall.sh 的文件化用法涵蓋新 unit（腳本本身是 name-generic，
  這裡守的是「官方支援面」不被改掉）

新增守住的硬條件（pipeline／notifier，2.7b，見
docs/stage2.7-notification-scheduling-proposal.md §9 2.7b）：
- pipeline .service：Type=oneshot、兩行 ExecStart 依序為
  `bridge_importer.py import --limit 10` 與
  `bridge_triage_enqueuer.py enqueue --max-new 5`，不得帶其他範圍／
  dry-run 參數；不得設 Restart=
- pipeline .timer：每天 08:15、Persistent=true、指回
  hermes-bridge-pipeline.service
- notifier .service：Type=oneshot、ExecStart 是無額外參數的
  `bridge_notifier.py notify`（不得帶 --dry-run/--channel/--send-cli，
  排程走程式內建預設頻道／send-cli）；不得設 Restart=
- notifier .timer：每天 08:25、Persistent=true、指回
  hermes-bridge-notifier.service

執行：.venv/Scripts/python.exe hermes/test_systemd_units.py
"""
import re
import sys
import unittest
from pathlib import Path

SYSTEMD_DIR = Path(__file__).resolve().parent / "systemd"
SERVICE = SYSTEMD_DIR / "hermes-bridge-scanner.service"
TIMER = SYSTEMD_DIR / "hermes-bridge-scanner.timer"

PIPELINE_SERVICE = SYSTEMD_DIR / "hermes-bridge-pipeline.service"
PIPELINE_TIMER = SYSTEMD_DIR / "hermes-bridge-pipeline.timer"
NOTIFIER_SERVICE = SYSTEMD_DIR / "hermes-bridge-notifier.service"
NOTIFIER_TIMER = SYSTEMD_DIR / "hermes-bridge-notifier.timer"


def _directives(path: Path) -> dict:
    """把 unit 檔解析成 {key: [values...]}（忽略註解與 section 標頭）。"""
    result: dict = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith(("#", ";", "[")):
            continue
        key, _, value = line.partition("=")
        result.setdefault(key.strip(), []).append(value.strip())
    return result


class TestBridgeScannerService(unittest.TestCase):
    def test_service_file_exists(self):
        self.assertTrue(SERVICE.is_file(), f"缺少 {SERVICE}")

    def test_oneshot_with_workingdirectory(self):
        d = _directives(SERVICE)
        self.assertEqual(d.get("Type"), ["oneshot"])
        self.assertTrue(d.get("WorkingDirectory"),
                        "scan 依賴 repo 根為工作目錄（config/相對路徑）")

    def test_execstart_is_bare_scan(self):
        d = _directives(SERVICE)
        self.assertEqual(len(d.get("ExecStart", [])), 1)
        exec_start = d["ExecStart"][0]
        self.assertIn("bridge_scanner.py", exec_start)
        self.assertRegex(exec_start, r"bridge_scanner\.py\s+scan\s*$",
                         "排程必須是無參數 scan（安全預設），不得帶任何旗標")
        for forbidden in ("--since", "--all-history", "--dry-run", "reconcile"):
            self.assertNotIn(forbidden, exec_start,
                             f"排程的 ExecStart 不得出現 {forbidden}")

    def test_no_restart_directive(self):
        self.assertNotIn("Restart", _directives(SERVICE),
                         "失敗語義＝unit failed，可觀測；下次 timer 重跑，"
                         "watermark 保證不跳漏——不得設 Restart")


class TestBridgeScannerTimer(unittest.TestCase):
    def test_timer_file_exists(self):
        self.assertTrue(TIMER.is_file(), f"缺少 {TIMER}")

    def test_timer_schedule_and_persistence(self):
        d = _directives(TIMER)
        self.assertEqual(d.get("Persistent"), ["true"],
                         "與既有 timer 一致：喚醒後 catch-up 補跑")
        self.assertEqual(len(d.get("OnCalendar", [])), 1,
                         "每日一次的 wall-clock 排程")
        self.assertRegex(d["OnCalendar"][0], r"^\*-\*-\* \d{2}:\d{2}:\d{2}$")
        self.assertEqual(d.get("Unit"), ["hermes-bridge-scanner.service"])
        self.assertEqual(d.get("WantedBy"), ["timers.target"])


class TestBridgePipelineService(unittest.TestCase):
    def test_service_file_exists(self):
        self.assertTrue(PIPELINE_SERVICE.is_file(), f"缺少 {PIPELINE_SERVICE}")

    def test_oneshot_with_workingdirectory(self):
        d = _directives(PIPELINE_SERVICE)
        self.assertEqual(d.get("Type"), ["oneshot"])
        self.assertTrue(d.get("WorkingDirectory"),
                        "importer/enqueuer 依賴 repo 根為工作目錄（config/相對路徑）")

    def test_execstart_is_importer_then_enqueuer_with_fixed_flags(self):
        d = _directives(PIPELINE_SERVICE)
        execs = d.get("ExecStart", [])
        self.assertEqual(len(execs), 2,
                         "pipeline 必須是兩行 ExecStart：importer 接著 enqueuer"
                         "（systemd 既有語義：任一行非零 exit 即中止後續行）")
        importer_line, enqueuer_line = execs
        self.assertIn("bridge_importer.py", importer_line)
        self.assertRegex(importer_line,
                         r"bridge_importer\.py\s+import\s+--limit\s+10\s*$",
                         "排程的 importer 必須是 import --limit 10，不得帶其他旗標")
        self.assertIn("bridge_triage_enqueuer.py", enqueuer_line)
        self.assertRegex(enqueuer_line,
                         r"bridge_triage_enqueuer\.py\s+enqueue\s+--max-new\s+5\s*$",
                         "排程的 enqueuer 必須是 enqueue --max-new 5，不得帶其他旗標")
        for forbidden in ("--since", "--all-history", "--dry-run",
                          "--event-id", "reconcile"):
            self.assertNotIn(forbidden, importer_line + enqueuer_line,
                             f"排程的 ExecStart 不得出現 {forbidden}")

    def test_no_restart_directive(self):
        self.assertNotIn("Restart", _directives(PIPELINE_SERVICE),
                         "失敗語義＝unit failed，可觀測；下次 timer 重跑，"
                         "importer/enqueuer 的既有冪等機制保證重跑安全——"
                         "不得設 Restart")


class TestBridgePipelineTimer(unittest.TestCase):
    def test_timer_file_exists(self):
        self.assertTrue(PIPELINE_TIMER.is_file(), f"缺少 {PIPELINE_TIMER}")

    def test_timer_schedule_and_persistence(self):
        d = _directives(PIPELINE_TIMER)
        self.assertEqual(d.get("Persistent"), ["true"],
                         "與既有 timer 一致：喚醒後 catch-up 補跑")
        self.assertEqual(len(d.get("OnCalendar", [])), 1,
                         "每日一次的 wall-clock 排程")
        self.assertEqual(d.get("OnCalendar"), ["*-*-* 08:15:00"],
                         "拍板時刻：scanner（08:05）之後 10 分鐘")
        self.assertEqual(d.get("Unit"), ["hermes-bridge-pipeline.service"])
        self.assertEqual(d.get("WantedBy"), ["timers.target"])


class TestBridgeNotifierService(unittest.TestCase):
    def test_service_file_exists(self):
        self.assertTrue(NOTIFIER_SERVICE.is_file(), f"缺少 {NOTIFIER_SERVICE}")

    def test_oneshot_with_workingdirectory(self):
        d = _directives(NOTIFIER_SERVICE)
        self.assertEqual(d.get("Type"), ["oneshot"])
        self.assertTrue(d.get("WorkingDirectory"),
                        "notifier 依賴 repo 根為工作目錄（jobs.db 相對路徑）")

    def test_execstart_is_bare_notify(self):
        d = _directives(NOTIFIER_SERVICE)
        self.assertEqual(len(d.get("ExecStart", [])), 1)
        exec_start = d["ExecStart"][0]
        self.assertIn("bridge_notifier.py", exec_start)
        self.assertRegex(exec_start, r"bridge_notifier\.py\s+notify\s*$",
                         "排程必須是無額外參數的 notify（走程式內建預設頻道／"
                         "send-cli），不得帶任何旗標")
        for forbidden in ("--dry-run", "--channel", "--send-cli"):
            self.assertNotIn(forbidden, exec_start,
                             f"排程的 ExecStart 不得出現 {forbidden}"
                             "（測試頻道覆寫留待 2.7c 部署驗收人工帶入）")

    def test_no_restart_directive(self):
        self.assertNotIn("Restart", _directives(NOTIFIER_SERVICE),
                         "失敗語義＝unit failed，可觀測；下次 timer 重掃，"
                         "message-key 冪等保證補送不重複——不得設 Restart")


class TestBridgeNotifierTimer(unittest.TestCase):
    def test_timer_file_exists(self):
        self.assertTrue(NOTIFIER_TIMER.is_file(), f"缺少 {NOTIFIER_TIMER}")

    def test_timer_schedule_and_persistence(self):
        d = _directives(NOTIFIER_TIMER)
        self.assertEqual(d.get("Persistent"), ["true"],
                         "與既有 timer 一致：喚醒後 catch-up 補跑")
        self.assertEqual(len(d.get("OnCalendar", [])), 1,
                         "每日一次的 wall-clock 排程")
        self.assertEqual(d.get("OnCalendar"), ["*-*-* 08:25:00"],
                         "拍板時刻：pipeline（08:15）之後 10 分鐘")
        self.assertEqual(d.get("Unit"), ["hermes-bridge-notifier.service"])
        self.assertEqual(d.get("WantedBy"), ["timers.target"])


class TestInstallScriptsCoverNewUnit(unittest.TestCase):
    def test_install_and_uninstall_document_the_unit(self):
        for script in ("install.sh", "uninstall.sh"):
            text = (SYSTEMD_DIR / script).read_text(encoding="utf-8")
            for unit_name in ("hermes-bridge-scanner", "hermes-bridge-pipeline",
                             "hermes-bridge-notifier"):
                self.assertRegex(
                    text, re.compile(unit_name),
                    f"{script} 的文件化用法必須涵蓋 {unit_name}")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    unittest.main(verbosity=2)
