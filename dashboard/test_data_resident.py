#!/usr/bin/env python3
"""dashboard/test_data_resident.py — 背景常駐狀態燈號資料層測試。

核心測試(提案 §3 風險表第 1 項的行為鎖定):
- **無副作用原則**:第一層回 Stopped 時,斷言第二層 subprocess **完全未被
  呼叫**(mock 記錄所有呼叫;不得出現任何含 "-d" 的 wsl 指令)。
- 五態狀態機:綠/黃(activating、gateway 暖機中)/橙/紅/灰各情境。
- wsl --list 輸出的 UTF-16LE 解碼。
- 5 秒 TTL 快取。
- 唯讀靜態鎖定:subprocess.run 呼叫位點只允許兩個凍結常數;原始碼無任何
  systemd 寫入動詞字面值(start/stop/restart/--terminate/--shutdown)。

fixture 一律假資料;不觸碰真實 wsl / %LOCALAPPDATA%。
執行:.venv/Scripts/python.exe dashboard/test_data_resident.py
"""
import json
import re
import subprocess as real_subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import data_resident  # noqa: E402

DASHBOARD_DIR = Path(__file__).resolve().parent

# wsl --list --verbose 的真實輸出形態:UTF-16LE + BOM,含表頭與 * 預設標記
def _wsl_list_bytes(state: str, distro: str = "Ubuntu") -> bytes:
    text = (
        "  NAME            STATE           VERSION\r\n"
        f"* {distro}          {state}         2\r\n"
    )
    return b"\xff\xfe" + text.encode("utf-16-le")


SYSTEMCTL_ALL_ACTIVE = (
    "  hermes-worker.service    loaded active   running Hermes worker\n"
    "  hermes-telegram.service  loaded active   running Hermes telegram\n"
    "  hermes-rss.service       loaded inactive dead    Hermes rss oneshot\n"
)
SYSTEMCTL_PARTIAL = (
    "  hermes-worker.service    loaded active   running Hermes worker\n"
    "  hermes-telegram.service  loaded failed   failed  Hermes telegram\n"
)
SYSTEMCTL_ACTIVATING = (
    "  hermes-worker.service    loaded active     running Hermes worker\n"
    "  hermes-telegram.service  loaded activating auto-restart Hermes telegram\n"
)
SYSTEMCTL_ALL_STOPPED = (
    "  hermes-worker.service    loaded inactive dead Hermes worker\n"
    "  hermes-telegram.service  loaded failed   failed Hermes telegram\n"
)


class FakeSubprocess:
    """記錄型 subprocess 替身:calls 蒐集所有 run() 的 argv,依指令分派回應。

    list_result / systemctl_result 可為 CompletedProcess 或 Exception。"""

    TimeoutExpired = real_subprocess.TimeoutExpired

    def __init__(self, list_result, systemctl_result=None):
        self.list_result = list_result
        self.systemctl_result = systemctl_result
        self.calls: list[tuple] = []

    def run(self, argv, **kwargs):
        self.calls.append(tuple(argv))
        if "--list" in argv:
            result = self.list_result
        else:
            result = self.systemctl_result
        if isinstance(result, BaseException):
            raise result
        if result is None:
            raise AssertionError(f"未預期的 subprocess 呼叫:{argv}")
        return result


def _completed(argv_hint, stdout, returncode=0):
    return real_subprocess.CompletedProcess(argv_hint, returncode, stdout=stdout, stderr=b"")


class ResidentProbeTestCase(unittest.TestCase):
    def setUp(self):
        self._orig_subprocess = data_resident.subprocess
        self._orig_gateway_path = data_resident.GATEWAY_STATE_PATH
        self._orig_gateway_ready = data_resident._gateway_ready
        data_resident._cache = None
        self._tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        data_resident.subprocess = self._orig_subprocess
        data_resident.GATEWAY_STATE_PATH = self._orig_gateway_path
        data_resident._gateway_ready = self._orig_gateway_ready
        data_resident._cache = None
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _install(self, fake: FakeSubprocess) -> FakeSubprocess:
        data_resident.subprocess = fake
        return fake

    def _write_gateway_state(self, state: str = "running"):
        path = self._tmpdir / "gateway_state.json"
        path.write_text(json.dumps({"gateway_state": state, "pid": 12345}), encoding="utf-8")
        data_resident.GATEWAY_STATE_PATH = path
        return path


class NoSideEffectTests(ResidentProbeTestCase):
    """硬性設計原則:探測不得喚醒 distro(提案 §1.1/§3 第 1 項)。"""

    def test_stopped_distro_never_triggers_second_layer(self):
        fake = self._install(FakeSubprocess(_completed(["wsl.exe"], _wsl_list_bytes("Stopped"))))
        payload = data_resident._probe()
        self.assertEqual(payload["light"], "red")
        # 斷言:只有第一層那一次呼叫,且沒有任何 `wsl -d`(會喚醒 distro)
        self.assertEqual(len(fake.calls), 1)
        for argv in fake.calls:
            self.assertNotIn("-d", argv, f"distro Stopped 時不得執行 wsl -d:{argv}")
            self.assertNotIn("systemctl", argv)
        self.assertIsNone(payload["units"])
        self.assertIsNone(payload["gateway"])
        self.assertIn("避免喚醒", payload["detail"])

    def test_wsl_query_failure_never_triggers_second_layer(self):
        fake = self._install(FakeSubprocess(FileNotFoundError("wsl.exe")))
        payload = data_resident._probe()
        self.assertEqual(payload["light"], "gray")
        self.assertEqual(len(fake.calls), 1)

    def test_distro_missing_from_list_is_red_and_stops(self):
        fake = self._install(
            FakeSubprocess(_completed(["wsl.exe"], _wsl_list_bytes("Running", distro="Debian")))
        )
        payload = data_resident._probe()
        self.assertEqual(payload["light"], "red")
        self.assertEqual(len(fake.calls), 1)

    def test_gateway_layer_not_probed_when_units_partial(self):
        self._install(FakeSubprocess(
            _completed(["wsl.exe"], _wsl_list_bytes("Running")),
            _completed(["wsl.exe"], SYSTEMCTL_PARTIAL),
        ))
        calls = []
        data_resident._gateway_ready = lambda: calls.append(1) or {"ready": True}
        payload = data_resident._probe()
        self.assertEqual(payload["light"], "orange")
        self.assertEqual(calls, [], "第三層只在常駐單元全 active 時才探測")
        self.assertIsNone(payload["gateway"])


class StateMachineTests(ResidentProbeTestCase):
    """五態狀態機(提案 §1.2)。"""

    def _running_with(self, systemctl_stdout: str, returncode: int = 0):
        return self._install(FakeSubprocess(
            _completed(["wsl.exe"], _wsl_list_bytes("Running")),
            _completed(["wsl.exe"], systemctl_stdout, returncode=returncode),
        ))

    def test_green_all_active_and_gateway_running(self):
        self._running_with(SYSTEMCTL_ALL_ACTIVE)
        self._write_gateway_state("running")
        payload = data_resident._probe()
        self.assertEqual(payload["light"], "green")
        self.assertEqual(payload["text"], "背景常駐中")
        self.assertTrue(payload["gateway"]["ready"])
        self.assertEqual(payload["units"]["hermes-worker.service"]["state"], "active")
        self.assertTrue(payload["units"]["hermes-worker.service"]["resident"])
        self.assertFalse(payload["units"]["hermes-rss.service"]["resident"])

    def test_yellow_all_active_but_gateway_state_file_missing(self):
        self._running_with(SYSTEMCTL_ALL_ACTIVE)
        data_resident.GATEWAY_STATE_PATH = self._tmpdir / "not-there.json"
        payload = data_resident._probe()
        self.assertEqual(payload["light"], "yellow")
        self.assertIn("暖機", payload["gateway"]["detail"])

    def test_yellow_all_active_but_gateway_not_running_state(self):
        self._running_with(SYSTEMCTL_ALL_ACTIVE)
        self._write_gateway_state("starting")
        payload = data_resident._probe()
        self.assertEqual(payload["light"], "yellow")
        self.assertEqual(payload["gateway"]["state"], "starting")

    def test_yellow_all_active_but_gateway_file_unparsable(self):
        self._running_with(SYSTEMCTL_ALL_ACTIVE)
        path = self._tmpdir / "gateway_state.json"
        path.write_text("{not json", encoding="utf-8")
        data_resident.GATEWAY_STATE_PATH = path
        payload = data_resident._probe()
        self.assertEqual(payload["light"], "yellow")
        self.assertIn("無法解析", payload["gateway"]["detail"])

    def test_yellow_unit_activating(self):
        self._running_with(SYSTEMCTL_ACTIVATING)
        payload = data_resident._probe()
        self.assertEqual(payload["light"], "yellow")
        self.assertIn("hermes-telegram.service", payload["detail"])

    def test_orange_partial_active_lists_broken_units(self):
        self._running_with(SYSTEMCTL_PARTIAL)
        payload = data_resident._probe()
        self.assertEqual(payload["light"], "orange")
        self.assertIn("hermes-telegram.service", payload["detail"])
        self.assertIn("failed", payload["detail"])

    def test_red_all_resident_units_stopped(self):
        self._running_with(SYSTEMCTL_ALL_STOPPED)
        payload = data_resident._probe()
        self.assertEqual(payload["light"], "red")
        self.assertEqual(payload["distro"]["running"], True)

    def test_red_units_missing_from_output_treated_as_stopped(self):
        self._running_with("  hermes-rss.service  loaded inactive dead rss\n")
        payload = data_resident._probe()
        self.assertEqual(payload["light"], "red")
        self.assertIn("未載入", payload["detail"])

    def test_gray_wsl_timeout(self):
        self._install(FakeSubprocess(
            real_subprocess.TimeoutExpired(cmd=["wsl.exe"], timeout=10)
        ))
        self.assertEqual(data_resident._probe()["light"], "gray")

    def test_gray_wsl_list_nonzero_exit(self):
        self._install(FakeSubprocess(_completed(["wsl.exe"], b"", returncode=1)))
        self.assertEqual(data_resident._probe()["light"], "gray")

    def test_gray_systemctl_layer_failure(self):
        self._install(FakeSubprocess(
            _completed(["wsl.exe"], _wsl_list_bytes("Running")),
            OSError("boom"),
        ))
        payload = data_resident._probe()
        self.assertEqual(payload["light"], "gray")
        self.assertIsNone(payload["units"])

    def test_gray_systemctl_nonzero_exit(self):
        self._running_with(SYSTEMCTL_ALL_ACTIVE, returncode=1)
        self.assertEqual(data_resident._probe()["light"], "gray")


class DecodeTests(unittest.TestCase):
    def test_utf16le_with_bom(self):
        raw = b"\xff\xfe" + "* Ubuntu Running 2".encode("utf-16-le")
        self.assertEqual(data_resident._decode_wsl_output(raw), "* Ubuntu Running 2")

    def test_utf16le_without_bom_detected_by_nul(self):
        raw = "Ubuntu".encode("utf-16-le")
        self.assertEqual(data_resident._decode_wsl_output(raw), "Ubuntu")

    def test_plain_utf8(self):
        self.assertEqual(data_resident._decode_wsl_output(b"Ubuntu Running"), "Ubuntu Running")


class CacheAndFallbackTests(ResidentProbeTestCase):
    def test_cache_hits_within_ttl(self):
        count = {"n": 0}
        orig_probe = data_resident._probe

        def counting_probe():
            count["n"] += 1
            return {"light": "green", "text": "背景常駐中", "detail": "t",
                    "checked_at": "now", "distro": {}, "resident_units": [],
                    "units": {}, "gateway": {}}

        data_resident._probe = counting_probe
        try:
            first = data_resident.get_resident_status()
            second = data_resident.get_resident_status()
            self.assertEqual(count["n"], 1, "5 秒內第二次呼叫應命中快取")
            self.assertEqual(first, second)
            # 快取過期後重新探測
            ts, payload = data_resident._cache
            data_resident._cache = (ts - data_resident.CACHE_TTL_SECONDS - 1, payload)
            data_resident.get_resident_status()
            self.assertEqual(count["n"], 2)
        finally:
            data_resident._probe = orig_probe

    def test_unexpected_exception_degrades_to_gray(self):
        orig_probe = data_resident._probe
        data_resident._probe = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            payload = data_resident.get_resident_status()
        finally:
            data_resident._probe = orig_probe
        self.assertEqual(payload["light"], "gray")
        self.assertEqual(payload["text"], "無法查詢")

    def test_payload_has_stable_shape(self):
        self._install(FakeSubprocess(_completed(["wsl.exe"], _wsl_list_bytes("Stopped"))))
        payload = data_resident.get_resident_status()
        for key in ["light", "text", "detail", "checked_at", "distro",
                    "resident_units", "units", "gateway"]:
            self.assertIn(key, payload)
        self.assertEqual(payload["resident_units"],
                         ["hermes-worker.service", "hermes-telegram.service"])


class ReadOnlyStaticTests(unittest.TestCase):
    """唯讀鎖定:本模組技術上不存在寫入指令位點(提案 §0.1——
    核准的只有唯讀燈號;寫入動詞一出現就是紅燈)。"""

    SOURCE = (DASHBOARD_DIR / "data_resident.py").read_text(encoding="utf-8")

    def test_subprocess_call_sites_only_frozen_constants(self):
        call_sites = re.findall(r"subprocess\.run\(\s*(\w+)", self.SOURCE)
        self.assertEqual(sorted(set(call_sites)),
                         ["WSL_LIST_COMMAND", "WSL_SYSTEMCTL_COMMAND"])
        self.assertEqual(len(call_sites), 2, "subprocess.run 位點恰為兩處凍結指令")

    def test_no_write_verbs_in_source(self):
        # systemd/wsl 寫入動詞的字面值不得出現(唯讀燈號零寫入入口)
        for forbidden in ['"start"', '"stop"', '"restart"', '"enable"', '"disable"',
                          "--terminate", "--shutdown", '"mask"', "daemon-reload"]:
            self.assertNotIn(forbidden, self.SOURCE, f"不得出現寫入動詞:{forbidden}")

    def test_frozen_commands_are_readonly_queries(self):
        self.assertEqual(data_resident.WSL_LIST_COMMAND, ("wsl.exe", "--list", "--verbose"))
        self.assertIn("list-units", data_resident.WSL_SYSTEMCTL_COMMAND)
        self.assertIn("--user", data_resident.WSL_SYSTEMCTL_COMMAND)


if __name__ == "__main__":
    unittest.main()
