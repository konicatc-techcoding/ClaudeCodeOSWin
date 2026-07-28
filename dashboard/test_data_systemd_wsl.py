#!/usr/bin/env python3
"""dashboard/test_data_systemd_wsl.py — Windows 側經 WSL 查 systemd 狀態的測試。

核心測試(比照 test_data_resident.py 慣例):
- **無副作用原則**:第一層守門(data_resident._distro_state)未通過時,
  斷言本模組的 subprocess **完全未被呼叫**——distro Stopped/查詢失敗都
  不得出現任何 `wsl -d` 指令(那會喚醒 distro)。
- 誠實三分支:ok / wsl_down / unavailable;「查不到」不得偽裝成單元缺席。
- list-units 輸出解析(mock 輸出,含非 hermes 單元排除、格式異常行略過);
  list-timers 的 **--output=json** 解析(真實形狀 JSON fixture、µs epoch →
  本地時區 ISO、0/null/缺失 → n/a、壞 JSON 回 {});並以負面測試固定
  2026-07-28 的教訓:文字版輸出右對齊欄位間可能只剩單一空格,任何
  「以空格切欄」的解析都會整行切壞——JSON 方案天然避開。
- 5 秒 TTL 快取與全域容錯。
- 唯讀靜態鎖定:subprocess.run 位點恰為兩個凍結常數;原始碼無任何
  systemd/wsl 寫入動詞字面值。

fixture 一律假資料;不觸碰真實 wsl。
執行:.venv/Scripts/python.exe dashboard/test_data_systemd_wsl.py
"""
import json
import re
import subprocess as real_subprocess
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import data_resident  # noqa: E402
import data_systemd_wsl  # noqa: E402

DASHBOARD_DIR = Path(__file__).resolve().parent


# wsl --list --verbose 的真實輸出形態(UTF-16LE + BOM;守門層用)
def _wsl_list_bytes(state: str, distro: str = "Ubuntu") -> bytes:
    text = (
        "  NAME            STATE           VERSION\r\n"
        f"* {distro}          {state}         2\r\n"
    )
    return b"\xff\xfe" + text.encode("utf-16-le")


LIST_UNITS_OUTPUT = (
    "  hermes-worker.service                     loaded active   running Hermes worker\n"
    "  hermes-telegram.service                   loaded active   running Hermes telegram\n"
    "  hermes-rss.timer                          loaded active   waiting Hermes RSS timer\n"
    "  hermes-cron-daily-memory-check.timer      loaded inactive dead    Hermes memory check\n"
    "  other-app.service                         loaded active   running Not ours\n"
    "  broken-line\n"
)

# list-timers --output=json 的真實形狀 fixture(欄位/型別取自 2026-07-28
# WSL Ubuntu 實測樣本:next/last/left/passed 為 µs epoch 或相對 µs 整數,
# 0/null 代表無;unit/activates 為字串)。時間值本身為假造測試值。
RSS_NEXT_US = 1785204716923886
RSS_LAST_US = 1785202916922768
MEMCHECK_NEXT_US = 1785283200000000

LIST_TIMERS_JSON = json.dumps([
    {"next": RSS_NEXT_US, "left": 1785204716923886, "last": RSS_LAST_US,
     "passed": 76314541265, "unit": "hermes-rss.timer",
     "activates": "hermes-rss.service"},
    {"next": MEMCHECK_NEXT_US, "left": 78483076114, "last": 0, "passed": 0,
     "unit": "hermes-cron-daily-memory-check.timer",
     "activates": "hermes-cron-daily-memory-check.service"},
    {"next": None, "left": None, "last": None, "passed": None,
     "unit": "hermes-never.timer", "activates": "hermes-never.service"},
    {"next": 1785204716923886, "left": 1, "last": 1, "passed": 1,
     "unit": "other-app.timer", "activates": "other-app.service"},
])

# 文字版 list-timers 的真實輸出形狀(右對齊欄位,LEFT/PASSED 與相鄰欄之間
# 可能只剩**單一空格**)——舊的「2+ 空格切欄」解析在這種行上整行切壞,
# 導致 timers 永遠回空(2026-07-28 真實環境驗證抓到的 bug)。保留此樣本
# 作負面測試:餵給 JSON parser 必須安全回 {}(壞輸入不噴例外),
# 也把「不得回頭用文字對齊解析」的教訓固定下來。
LEGACY_TEXT_TIMERS_OUTPUT = (
    "NEXT                          LEFT LAST                            PASSED UNIT"
    "                                   ACTIVATES\n"
    "Tue 2026-07-28 12:11:56 CST         10min Tue 2026-07-28 11:41:56 CST "
    "19min ago hermes-rss.timer  hermes-rss.service\n"
)


def _iso_local(epoch_us: int) -> str:
    """測試端期望值:µs epoch → 本地時區 ISO(timespec=seconds)。
    與被測函式同義的 stdlib 直算(鎖定輸出格式,防止日後改壞相容性)。"""
    return datetime.fromtimestamp(
        epoch_us / 1_000_000, tz=timezone.utc).astimezone().isoformat(timespec="seconds")


def _completed(stdout, returncode=0):
    return real_subprocess.CompletedProcess(["wsl.exe"], returncode, stdout=stdout, stderr="")


class FakeGateSubprocess:
    """守門層(data_resident)的 subprocess 替身:只回應 wsl --list。"""

    TimeoutExpired = real_subprocess.TimeoutExpired

    def __init__(self, list_result):
        self.list_result = list_result
        self.calls: list[tuple] = []

    def run(self, argv, **kwargs):
        self.calls.append(tuple(argv))
        if isinstance(self.list_result, BaseException):
            raise self.list_result
        return self.list_result


class FakeProbeSubprocess:
    """本模組(第二層)的 subprocess 替身:依指令分派 list-units/list-timers。"""

    TimeoutExpired = real_subprocess.TimeoutExpired

    def __init__(self, units_result=None, timers_result=None):
        self.units_result = units_result
        self.timers_result = timers_result
        self.calls: list[tuple] = []

    def run(self, argv, **kwargs):
        self.calls.append(tuple(argv))
        if "list-timers" in argv:
            result = self.timers_result
        elif "list-units" in argv:
            result = self.units_result
        else:
            raise AssertionError(f"未預期的 subprocess 呼叫:{argv}")
        if isinstance(result, BaseException):
            raise result
        if result is None:
            raise AssertionError(f"未預期的 subprocess 呼叫:{argv}")
        return result


class SystemdWslTestCase(unittest.TestCase):
    def setUp(self):
        self._orig_gate_subprocess = data_resident.subprocess
        self._orig_probe_subprocess = data_systemd_wsl.subprocess
        self._orig_distro_state = data_resident._distro_state
        data_systemd_wsl._cache = None
        data_resident._cache = None

    def tearDown(self):
        data_resident.subprocess = self._orig_gate_subprocess
        data_systemd_wsl.subprocess = self._orig_probe_subprocess
        data_resident._distro_state = self._orig_distro_state
        data_systemd_wsl._cache = None
        data_resident._cache = None

    def _gate(self, running: bool | None, detail: str = "TEST"):
        """直接替換守門結果(守門本體已由 test_data_resident.py 鎖定)。"""
        data_resident._distro_state = lambda: (running, detail)

    def _install_probe(self, fake: FakeProbeSubprocess) -> FakeProbeSubprocess:
        data_systemd_wsl.subprocess = fake
        return fake


class NoSideEffectTests(SystemdWslTestCase):
    """硬性設計原則:守門未通過就不下任何 `wsl -d` 指令(不喚醒 distro)。"""

    def test_stopped_distro_never_calls_this_modules_subprocess(self):
        # 端到端:守門層走真的 _distro_state(mock 其 subprocess 回 Stopped),
        # 本模組的 subprocess 裝上「一被呼叫就爆」的替身。
        gate = FakeGateSubprocess(_completed(_wsl_list_bytes("Stopped")))
        data_resident.subprocess = gate
        probe = self._install_probe(FakeProbeSubprocess())
        payload = data_systemd_wsl._probe()
        self.assertEqual(payload["status"], "wsl_down")
        self.assertEqual(probe.calls, [], "distro Stopped 時不得執行任何 wsl -d 指令")
        for argv in gate.calls:
            self.assertNotIn("-d", argv)
        self.assertIsNone(payload["units"])
        self.assertIsNone(payload["timers"])
        self.assertIn("避免喚醒", payload["reason"])

    def test_gate_failure_never_calls_this_modules_subprocess(self):
        data_resident.subprocess = FakeGateSubprocess(FileNotFoundError("wsl.exe"))
        probe = self._install_probe(FakeProbeSubprocess())
        payload = data_systemd_wsl._probe()
        self.assertEqual(payload["status"], "unavailable")
        self.assertEqual(probe.calls, [])
        self.assertIn("無法查詢", payload["reason"])


class ThreeBranchTests(SystemdWslTestCase):
    """誠實三分支:ok / wsl_down / unavailable。"""

    def test_ok_parses_units_and_timers(self):
        self._gate(True, "distro 狀態:Running")
        self._install_probe(FakeProbeSubprocess(
            units_result=_completed(LIST_UNITS_OUTPUT),
            timers_result=_completed(LIST_TIMERS_JSON),
        ))
        payload = data_systemd_wsl._probe()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["status_text"], "查詢成功")
        self.assertIsNone(payload["reason"])
        # 輸出結構與 data.get_systemd_status() 相同(換源不換形)
        self.assertEqual(payload["units"]["hermes-worker.service"],
                         {"pid": "-", "last_exit": "active/running", "load": "loaded"})
        self.assertEqual(payload["units"]["hermes-cron-daily-memory-check.timer"],
                         {"pid": "-", "last_exit": "inactive/dead", "load": "loaded"})
        self.assertNotIn("other-app.service", payload["units"])
        # timers(JSON):µs epoch → 本地時區 ISO;last=0 → n/a(從未觸發);
        # 全 null → n/a;非 hermes 排除
        self.assertEqual(payload["timers"]["hermes-rss.timer"],
                         {"next": _iso_local(RSS_NEXT_US),
                          "last": _iso_local(RSS_LAST_US)})
        self.assertEqual(payload["timers"]["hermes-cron-daily-memory-check.timer"],
                         {"next": _iso_local(MEMCHECK_NEXT_US), "last": "n/a"})
        self.assertEqual(payload["timers"]["hermes-never.timer"],
                         {"next": "n/a", "last": "n/a"})
        self.assertNotIn("other-app.timer", payload["timers"])

    def test_wsl_down_branch_text(self):
        self._gate(False, "distro 狀態:Stopped")
        probe = self._install_probe(FakeProbeSubprocess())
        payload = data_systemd_wsl._probe()
        self.assertEqual(payload["status"], "wsl_down")
        self.assertEqual(payload["status_text"], "WSL 未運作")
        self.assertIn("未運作", payload["reason"])
        self.assertEqual(probe.calls, [])

    def test_unavailable_when_gate_cannot_query(self):
        self._gate(None, "wsl 指令不存在或逾時")
        probe = self._install_probe(FakeProbeSubprocess())
        payload = data_systemd_wsl._probe()
        self.assertEqual(payload["status"], "unavailable")
        self.assertEqual(payload["status_text"], "無法查詢")
        self.assertEqual(probe.calls, [])

    def test_unavailable_when_units_query_fails(self):
        self._gate(True)
        self._install_probe(FakeProbeSubprocess(units_result=OSError("boom")))
        payload = data_systemd_wsl._probe()
        self.assertEqual(payload["status"], "unavailable")
        self.assertIsNone(payload["units"])
        self.assertIn("查詢失敗", payload["reason"])

    def test_unavailable_when_units_nonzero_exit(self):
        self._gate(True)
        self._install_probe(FakeProbeSubprocess(
            units_result=_completed("", returncode=1)))
        self.assertEqual(data_systemd_wsl._probe()["status"], "unavailable")

    def test_unavailable_when_units_timeout(self):
        self._gate(True)
        self._install_probe(FakeProbeSubprocess(
            units_result=real_subprocess.TimeoutExpired(cmd=["wsl.exe"], timeout=10)))
        self.assertEqual(data_systemd_wsl._probe()["status"], "unavailable")

    def test_timers_failure_keeps_ok_with_empty_timers(self):
        """timers 查詢失敗只影響 NEXT/LAST 欄位,不拖垮整份快照。"""
        self._gate(True)
        self._install_probe(FakeProbeSubprocess(
            units_result=_completed(LIST_UNITS_OUTPUT),
            timers_result=OSError("boom"),
        ))
        payload = data_systemd_wsl._probe()
        self.assertEqual(payload["status"], "ok")
        self.assertIn("hermes-worker.service", payload["units"])
        self.assertEqual(payload["timers"], {})

    def test_ok_with_no_hermes_units_is_still_ok(self):
        """查得到但一個 hermes 單元都沒有=真的未安裝(units 空 dict),
        不是 unavailable——這是「未安裝」唯一誠實成立的情境。"""
        self._gate(True)
        self._install_probe(FakeProbeSubprocess(
            units_result=_completed("  other-app.service  loaded active running x\n"),
            timers_result=_completed(""),
        ))
        payload = data_systemd_wsl._probe()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["units"], {})


class TimersJsonParserTests(unittest.TestCase):
    """_parse_list_timers(--output=json)與 µs epoch 轉換的邊界。"""

    def test_real_shape_json_parsed(self):
        parsed = data_systemd_wsl._parse_list_timers(LIST_TIMERS_JSON)
        self.assertEqual(set(parsed.keys()),
                         {"hermes-rss.timer", "hermes-cron-daily-memory-check.timer",
                          "hermes-never.timer"})
        self.assertEqual(parsed["hermes-rss.timer"]["next"], _iso_local(RSS_NEXT_US))
        self.assertEqual(parsed["hermes-rss.timer"]["last"], _iso_local(RSS_LAST_US))

    def test_legacy_text_output_safely_returns_empty(self):
        """2026-07-28 教訓固定:文字版 list-timers 右對齊欄位間可能只剩單一
        空格(見 LEGACY_TEXT_TIMERS_OUTPUT 樣本),「以空格切欄」的文字解析
        會整行切壞、timers 永遠回空——所以資料源改用 --output=json。
        本測試同時鎖住容錯:若指令被改回文字版(或 systemd 不支援 json 而
        吐文字),parser 必須安全回 {},不噴例外、不產生錯誤資料。"""
        self.assertEqual(
            data_systemd_wsl._parse_list_timers(LEGACY_TEXT_TIMERS_OUTPUT), {})

    def test_bad_json_and_wrong_shapes_return_empty(self):
        for bad in ["", "{broken", "null", '"str"', "42", '{"unit": "x"}']:
            self.assertEqual(data_systemd_wsl._parse_list_timers(bad), {}, bad)
        # 陣列內非 dict 項目略過,不中斷其他項目
        mixed = json.dumps([
            "not-a-dict",
            {"next": RSS_NEXT_US, "last": RSS_LAST_US,
             "unit": "hermes-rss.timer", "activates": "hermes-rss.service"},
        ])
        self.assertEqual(list(data_systemd_wsl._parse_list_timers(mixed)),
                         ["hermes-rss.timer"])

    def test_unit_filter_prefix_and_timer_suffix(self):
        entries = json.dumps([
            {"next": RSS_NEXT_US, "last": 0, "unit": "other-hermes-x.timer"},
            {"next": RSS_NEXT_US, "last": 0, "unit": "hermes-worker.service"},
            {"next": RSS_NEXT_US, "last": 0, "unit": None},
            {"next": RSS_NEXT_US, "last": 0, "unit": "hermes-ok.timer"},
        ])
        self.assertEqual(list(data_systemd_wsl._parse_list_timers(entries)),
                         ["hermes-ok.timer"])

    def test_timestamp_edge_cases_to_na(self):
        fmt = data_systemd_wsl._format_timer_timestamp
        for value in [0, None, -1, True, False, "1785204716923886", {}, []]:
            self.assertEqual(fmt(value), "n/a", repr(value))
        # 溢位大到 datetime 裝不下 → n/a,不噴例外
        self.assertEqual(fmt(10**24), "n/a")
        # 正常值:本地時區 ISO、帶 offset
        text = fmt(RSS_NEXT_US)
        self.assertEqual(text, _iso_local(RSS_NEXT_US))
        self.assertRegex(text, r"[+-]\d{2}:\d{2}$")


class CacheAndFallbackTests(SystemdWslTestCase):
    def test_cache_hits_within_ttl(self):
        count = {"n": 0}
        orig_probe = data_systemd_wsl._probe

        def counting_probe():
            count["n"] += 1
            return {"checked_at": "now", "status": "ok", "status_text": "查詢成功",
                    "reason": None, "distro": {}, "units": {}, "timers": {}}

        data_systemd_wsl._probe = counting_probe
        try:
            first = data_systemd_wsl.get_wsl_systemd_snapshot()
            second = data_systemd_wsl.get_wsl_systemd_snapshot()
            self.assertEqual(count["n"], 1, "5 秒內第二次呼叫應命中快取")
            self.assertEqual(first, second)
            ts, payload = data_systemd_wsl._cache
            data_systemd_wsl._cache = (
                ts - data_systemd_wsl.CACHE_TTL_SECONDS - 1, payload)
            data_systemd_wsl.get_wsl_systemd_snapshot()
            self.assertEqual(count["n"], 2)
        finally:
            data_systemd_wsl._probe = orig_probe

    def test_unexpected_exception_degrades_to_unavailable(self):
        orig_probe = data_systemd_wsl._probe
        data_systemd_wsl._probe = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            payload = data_systemd_wsl.get_wsl_systemd_snapshot()
        finally:
            data_systemd_wsl._probe = orig_probe
        self.assertEqual(payload["status"], "unavailable")
        self.assertEqual(payload["status_text"], "無法查詢")

    def test_payload_has_stable_shape(self):
        self._gate(False, "distro 狀態:Stopped")
        self._install_probe(FakeProbeSubprocess())
        payload = data_systemd_wsl.get_wsl_systemd_snapshot()
        for key in ["checked_at", "status", "status_text", "reason",
                    "distro", "units", "timers"]:
            self.assertIn(key, payload)


class ReadOnlyStaticTests(unittest.TestCase):
    """唯讀鎖定:本模組技術上不存在寫入指令位點(比照 data_resident 慣例;
    webui_security_check.py 第 10 項亦以相同判準靜態掃描)。"""

    SOURCE = (DASHBOARD_DIR / "data_systemd_wsl.py").read_text(encoding="utf-8")

    def test_subprocess_call_sites_only_frozen_constants(self):
        call_sites = re.findall(r"subprocess\.run\(\s*(\w+)", self.SOURCE)
        self.assertEqual(sorted(set(call_sites)),
                         ["WSL_LIST_TIMERS_COMMAND", "WSL_LIST_UNITS_COMMAND"])
        self.assertEqual(len(call_sites), 2, "subprocess.run 位點恰為兩處凍結指令")

    def test_no_write_verbs_in_source(self):
        for forbidden in ['"start"', '"stop"', '"restart"', '"enable"', '"disable"',
                          "--terminate", "--shutdown", '"mask"', "daemon-reload"]:
            self.assertNotIn(forbidden, self.SOURCE, f"不得出現寫入動詞:{forbidden}")

    def test_frozen_commands_are_readonly_queries(self):
        self.assertIn("list-units", data_systemd_wsl.WSL_LIST_UNITS_COMMAND)
        self.assertIn("--user", data_systemd_wsl.WSL_LIST_UNITS_COMMAND)
        self.assertIn("list-timers", data_systemd_wsl.WSL_LIST_TIMERS_COMMAND)
        self.assertIn("--user", data_systemd_wsl.WSL_LIST_TIMERS_COMMAND)
        # 2026-07-28 定案:timers 一律 JSON 輸出(文字版右對齊單空格會切壞)
        self.assertIn("--output=json", data_systemd_wsl.WSL_LIST_TIMERS_COMMAND)
        # 兩個常數都是 wsl -d <distro> 包裹形式,distro 與 data_resident 同源
        for command in (data_systemd_wsl.WSL_LIST_UNITS_COMMAND,
                        data_systemd_wsl.WSL_LIST_TIMERS_COMMAND):
            self.assertEqual(command[:3], ("wsl.exe", "-d", data_resident.WSL_DISTRO))


if __name__ == "__main__":
    unittest.main()
