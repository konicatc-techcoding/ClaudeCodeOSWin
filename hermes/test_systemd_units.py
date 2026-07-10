#!/usr/bin/env python3
"""hermes/test_systemd_units.py — v0.1（Stage 2.4b）

hermes/systemd/ 底下 bridge scanner unit 檔的靜態驗證：純文字檢查、零副作用
（不跑 systemctl、不碰任何 db／inbox）。目的：避免手改 unit 檔時默默壞掉
排程語義——尤其是「排程一律無參數 scan」與「Persistent=true」這兩條。

守住的硬條件：
- .service 存在、Type=oneshot、ExecStart 指向 bridge_scanner.py 且子命令是
  無參數 scan（不得帶 --since/--all-history/--dry-run、不得排程 reconcile）
- 失敗語義靠「非零 exit → unit failed」呈現，不得設 Restart=
- .timer 存在、Persistent=true、OnCalendar 排程、正確指回 .service、
  掛在 timers.target
- install.sh／uninstall.sh 的文件化用法涵蓋新 unit（腳本本身是 name-generic，
  這裡守的是「官方支援面」不被改掉）

執行：.venv/Scripts/python.exe hermes/test_systemd_units.py
"""
import re
import sys
import unittest
from pathlib import Path

SYSTEMD_DIR = Path(__file__).resolve().parent / "systemd"
SERVICE = SYSTEMD_DIR / "hermes-bridge-scanner.service"
TIMER = SYSTEMD_DIR / "hermes-bridge-scanner.timer"


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


class TestInstallScriptsCoverNewUnit(unittest.TestCase):
    def test_install_and_uninstall_document_the_unit(self):
        for script in ("install.sh", "uninstall.sh"):
            text = (SYSTEMD_DIR / script).read_text(encoding="utf-8")
            self.assertRegex(
                text, re.compile(r"hermes-bridge-scanner"),
                f"{script} 的文件化用法必須涵蓋 hermes-bridge-scanner")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    unittest.main(verbosity=2)
