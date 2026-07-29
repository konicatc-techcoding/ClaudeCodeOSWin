#!/usr/bin/env python3
"""scripts/test_dispatch_domain.py — v0.1

scripts/dispatch_domain.py 的 deterministic 測試。

原則（呼應任務要求）：
- 不呼叫網路、不呼叫真實模型、不碰 production hermes/jobs.db。
- Hermes lane 一律用 fake/mock hermes 執行檔（本檔 setUpClass 動態寫出）。
- native lane 直接真的呼叫 route_model.py 的 via=native 分支——它本來就不對外
  呼叫，offline、deterministic，沒有理由 mock。
- 2026-07-20：OpenRouter provider 相關路徑（三條 openrouter-* lane、
  complex_coding／google_ecosystem／bulk_research 走 OpenRouter 的 route）已
  隨使用者拍板全部移除（OPENROUTER_API_KEY 從未真正設定過，這幾條路徑實務上
  從未打通）。原本本檔測 OpenRouter 缺 API key 時的 fail-visible 路徑、以及
  engineering 預設 lane 失敗後 fallback 回 claude-native 的情境已隨之移除；
  fallback 機制本身仍然是 dispatch_domain.py 的既有功能，改用合成 lane 資料
  測試（見 test_lane_with_fallback_lane_falls_back_and_succeeds），不依賴目前
  registry 是否剛好有 fallback_lane 可觸發。
- 2026-07-29：hermes 執行檔解析改為平台感知（WSL 優先走 Windows 側 hermes.exe
  interop，見 dispatch_domain 檔頭 docstring）。WSL 偵測、interop 解析順序、
  wslpath 轉譯（成功／失敗兩種嚴格度）全部用 mock/fake 覆蓋，不執行真實
  hermes 也不執行真實 wslpath——真實 interop 端對端由主 session 實測。

執行：.venv/Scripts/python.exe scripts/test_dispatch_domain.py
"""
import json
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dispatch_domain  # noqa: E402
import route_model  # noqa: E402

ROOT = dispatch_domain.ROOT

# fake hermes 的情境對照真實 registry 的 hermes_profile 名稱，方便測試直接用
# --lane 指到真的 lane id，同時讓每個情境自我說明「這個 lane 在本測試模擬
# 哪一種 fail-visible 狀況」：
#   nemocoding        → 成功（也順便驗證中性 cwd 隔離、usage/model 回填）
#   gptcoding         → profile 不存在（比照 hermes 真實行為：exit 1 + 固定錯字串）
#   financialresearch → usage-file 內容損壞（exit 0，但 --usage-file 寫出非法 JSON）
#   intelligence      → 空輸出（比照 hermes -z 真實行為：exit 1 +固定錯字串）
#   codereviewer      → 逾時（sleep 超過測試給的 timeout）
#   unicodecheck      → stdout 直接寫原始 UTF-8 位元組（繞過 print()，見下方
#                       說明）——回歸測試真實事故：呼叫 hermes -z 拿回繁體中文
#                       研究內容時，子行程 stdout 若不明確用 encoding="utf-8"
#                       解碼，Windows 中文環境（本機預設 cp950）下會直接
#                       UnicodeDecodeError，把已完成的研究結果弄丟
#                       （2026-07-21 real-call incident）。
_FAKE_HERMES_SRC = textwrap.dedent(r"""
    import argparse, json, os, sys, time
    from pathlib import Path

    p = argparse.ArgumentParser()
    p.add_argument("--profile", required=True)
    p.add_argument("-z", "--oneshot", required=True)
    p.add_argument("--usage-file", default=None)
    args = p.parse_args()

    # 測試可攜性：windows_interop 測試用「identity 假譯」時，usage-file 引數是
    # 「父目錄 + '\\' + 檔名」的 Windows 形式接法；本測試套件也會在 WSL 部署
    # 複本上跑，Linux 下把反斜線正規化成 os.sep 才能寫進同一個檔案。Windows
    # 上 os.sep 就是反斜線，等同 no-op；非 interop 測試的路徑沒有反斜線接縫，
    # 也是 no-op。
    if args.usage_file:
        args.usage_file = args.usage_file.replace("\\", os.sep)

    marker = os.environ.get("DISPATCH_TEST_MARKER")
    if marker:
        Path(marker).write_text(json.dumps({
            "cwd": os.getcwd(),
            "agents_md_exists": os.path.exists("AGENTS.md"),
            "profile": args.profile,
            "prompt_head": args.oneshot[:200],
        }), encoding="utf-8")

    def write_usage(data):
        if args.usage_file:
            Path(args.usage_file).write_text(json.dumps(data), encoding="utf-8")

    if args.profile == "nemocoding":
        write_usage({"model": "fake-nemo-model", "provider": "fake-hermes-backend",
                      "estimated_cost_usd": 0.01, "completed": True, "failed": False})
        print("OK RESPONSE FROM nemocoding")
        sys.exit(0)
    elif args.profile == "gptcoding":
        sys.stderr.write("Error: Profile 'gptcoding' does not exist. Create it with: hermes profile create gptcoding\n")
        sys.exit(1)
    elif args.profile == "financialresearch":
        if args.usage_file:
            Path(args.usage_file).write_text("{not valid json", encoding="utf-8")
        print("OK RESPONSE FROM financialresearch")
        sys.exit(0)
    elif args.profile == "intelligence":
        write_usage({"failed": True})
        sys.stderr.write("hermes -z: no final response was produced; treating the run as failed.\n")
        sys.exit(1)
    elif args.profile == "codereviewer":
        time.sleep(30)
        sys.exit(0)
    elif args.profile == "unicodecheck":
        write_usage({"model": "fake-nemo-model", "provider": "fake-hermes-backend",
                      "estimated_cost_usd": 0.01, "completed": True, "failed": False})
        # 刻意繞過 print()：print() 的輸出編碼會受這個子行程自己的
        # sys.stdout.encoding 影響，那可能剛好跟父行程的 locale 一致，測不出
        # 問題。真實的 hermes CLI 是獨立執行檔，一律輸出 UTF-8，不受父行程
        # locale 影響——用 os.write 直接寫原始 UTF-8 位元組到 stdout fd，
        # 才是忠實模擬。內容特意選繁體中文＋常見會撞上 cp950 非法序列的字元。
        os.write(1, "研究結論：台股大盤觀察，關鍵字包含「風險」與「機會」。".encode("utf-8"))
        sys.exit(0)
    else:
        sys.stderr.write(f"fake hermes: unrecognized profile '{args.profile}'\n")
        sys.exit(1)
    """)


class FakeHermesFixture(unittest.TestCase):
    """共用 fixture：把 fake hermes 寫成 .py + Windows 用的 .bat 包裝。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp_dir = tempfile.mkdtemp(prefix="test_dispatch_domain_")
        cls.fake_py = Path(cls.tmp_dir) / "fake_hermes.py"
        cls.fake_py.write_text(_FAKE_HERMES_SRC, encoding="utf-8")
        cls.hermes_argv_prefix = [sys.executable, str(cls.fake_py)]
        # execute_hermes_profile 直呼測試用：一般（非 interop）invocation。
        cls.invocation = dispatch_domain.HermesInvocation(
            argv_prefix=cls.hermes_argv_prefix, windows_interop=False,
            degradation_note=None,
        )

        # 給 dispatch()／CLI 層級測試用：--hermes-bin 支援「多 token 命令字串」
        # （見 dispatch_domain._split_hermes_bin），直接用雙引號各自包住
        # python 直譯器與腳本路徑——不透過任何 shell／.bat 中介層，prompt 內容
        # 一律由 Python 自己組好 argv list 交給 subprocess.run(shell=False)，
        # 不會有 cmd.exe 轉發 argv 時把內嵌換行弄壞的問題。
        cls.hermes_bin_command = f'"{sys.executable}" "{cls.fake_py}"'

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls.tmp_dir, ignore_errors=True)

    def setUp(self):
        self._marker = None

    def tearDown(self):
        if self._marker and "DISPATCH_TEST_MARKER" in os.environ:
            del os.environ["DISPATCH_TEST_MARKER"]

    def make_marker(self):
        marker_file = Path(tempfile.mkdtemp(prefix="marker_")) / "marker.json"
        self._marker = marker_file
        os.environ["DISPATCH_TEST_MARKER"] = str(marker_file)
        return marker_file


class RegistryResolutionTests(unittest.TestCase):
    def setUp(self):
        self.agents_doc, self.router_doc, self.lanes_doc = dispatch_domain.load_registries()

    def test_get_agent_unknown_owner_fails_visible(self):
        with self.assertRaises(dispatch_domain.DispatchError) as ctx:
            dispatch_domain.get_agent(self.agents_doc, "not_a_real_domain")
        self.assertEqual(ctx.exception.exit_status, "registry_error")

    def test_get_agent_rejects_non_active_status(self):
        fake_doc = {"agents": [{"id": "ghost", "status": "planned"}]}
        with self.assertRaises(dispatch_domain.DispatchError) as ctx:
            dispatch_domain.get_agent(fake_doc, "ghost")
        self.assertEqual(ctx.exception.exit_status, "registry_error")

    def test_resolve_capability_unknown_override_fails_visible(self):
        agent = dispatch_domain.get_agent(self.agents_doc, "engineering")
        with self.assertRaises(dispatch_domain.DispatchError):
            dispatch_domain.resolve_capability(self.agents_doc, self.router_doc, agent, "totally_made_up")

    def test_select_lane_default_picks_active_lane_for_engineering(self):
        # 2026-07-20：openrouter-gpt55-coding 已移除（OpenRouter provider 路徑
        # 拍板全刪），capability='complex_coding' 底下排序最前的 active、
        # engineering 可用的 lane 現在是 hermes-nemocoding（engineering 的
        # default_capability 本身也已改成 claude_native，這裡仍用
        # capability='complex_coding' 明確測，驗證自動選路徑的候選順序邏輯）。
        lane = dispatch_domain.select_lane(
            self.lanes_doc, self.router_doc, "engineering", "complex_coding", None
        )
        self.assertEqual(lane["id"], "hermes-nemocoding")

    def test_select_lane_no_active_candidate_fails_visible(self):
        # architecture_reasoning 只有 claude-architecture-reasoning 一條 active lane，
        # allowed_agents 只有 engineering——intelligence 應該找不到自動候選。
        with self.assertRaises(dispatch_domain.DispatchError) as ctx:
            dispatch_domain.select_lane(
                self.lanes_doc, self.router_doc, "intelligence", "architecture_reasoning", None
            )
        self.assertEqual(ctx.exception.exit_status, "registry_error")

    def test_select_lane_explicit_override_reference_lane_allowed(self):
        # 明確 --lane 可以指到 status=reference 的 lane（自動選路徑不會，
        # 但明確 opt-in 可以）——這是本次 Phase 1 的核心設計決策。用合成 lane
        # 驗證，不依賴真實 registry 一定存在 status=reference 的 hermes_profile
        # lane——Phase 2d（2026-07-20）起，真實 registry 的四條 hermes-* lane
        # 已全部通過真實端對端 smoke test 轉為 active（見
        # registry/capability_lanes.yaml），這裡改用合成資料保留這個行為的
        # 測試覆蓋。
        synthetic_reference_lane = {
            "id": "synthetic-reference-lane", "capability": "complex_coding",
            "execution": "hermes_profile", "provider": "hermes", "model": None,
            "hermes_profile": "synthetic", "status": "reference",
            "cost_tier": "unknown", "risk_tier": "medium",
            "allowed_agents": ["engineering"], "intended_use": "test only",
            "guardrails": ["test only"],
        }
        fake_lanes_doc = {
            "lanes": self.lanes_doc.get("lanes", []) + [synthetic_reference_lane],
        }
        lane = dispatch_domain.select_lane(
            fake_lanes_doc, self.router_doc, "engineering", "complex_coding",
            "synthetic-reference-lane",
        )
        self.assertEqual(lane["id"], "synthetic-reference-lane")
        self.assertEqual(lane["status"], "reference")

    def test_select_lane_explicit_override_capability_mismatch_fails_visible(self):
        with self.assertRaises(dispatch_domain.DispatchError):
            dispatch_domain.select_lane(
                self.lanes_doc, self.router_doc, "engineering", "bulk_research", "hermes-nemocoding"
            )

    def test_select_lane_explicit_override_owner_not_allowed_fails_visible(self):
        # hermes-intelligence 只允許 intelligence，engineering 不該能用。
        with self.assertRaises(dispatch_domain.DispatchError):
            dispatch_domain.select_lane(
                self.lanes_doc, self.router_doc, "engineering", "bulk_research", "hermes-intelligence"
            )

    def test_select_lane_unknown_lane_id_fails_visible(self):
        with self.assertRaises(dispatch_domain.DispatchError):
            dispatch_domain.select_lane(
                self.lanes_doc, self.router_doc, "engineering", "complex_coding", "no-such-lane"
            )

    def test_validate_lane_detects_model_drift(self):
        # 用合成 router_doc（不依賴真實 registry）隔離測試 route_model 分支的
        # model／openrouter_model 漂移偵測本身——2026-07-20 起真實
        # model_router.yaml 已經沒有任何 via=openrouter 的 route，若改用
        # self.router_doc，會在「via 不是 openrouter」這關就先擋下，測不到
        # 這裡真正要驗證的 model 漂移邏輯。
        drifted_lane = {
            "id": "drifted", "capability": "complex_coding", "execution": "route_model",
            "provider": "openrouter", "model": "some/other-model",
            "allowed_agents": ["engineering"],
        }
        fake_router_doc = {
            "routes": {
                "complex_coding": {"via": "openrouter", "openrouter_model": "expected/model"},
            }
        }
        with self.assertRaises(dispatch_domain.DispatchError) as ctx:
            dispatch_domain.validate_lane(drifted_lane, fake_router_doc, "engineering")
        self.assertEqual(ctx.exception.exit_status, "registry_error")

    def test_validate_lane_detects_hermes_profile_missing_field(self):
        bad_lane = {
            "id": "broken-hermes", "capability": "complex_coding", "execution": "hermes_profile",
            "provider": "hermes", "model": None, "allowed_agents": ["engineering"],
        }
        with self.assertRaises(dispatch_domain.DispatchError):
            dispatch_domain.validate_lane(bad_lane, self.router_doc, "engineering")


class ResolveHermesBinTests(unittest.TestCase):
    """非 WSL 平台的解析行為（2026-07-29 平台感知改版後必須維持原邏輯不變）。
    一律把 is_wsl 釘成 False——本機是 Windows 本來就會回 False，但這套測試也
    會在 WSL 部署複本上跑，不釘住的話會走到 WSL 分支、測錯對象。"""

    def setUp(self):
        patcher = mock.patch("dispatch_domain.is_wsl", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_explicit_single_path_override_wins(self):
        inv = dispatch_domain.resolve_hermes_invocation("C:/some/hermes.exe")
        self.assertEqual(inv.argv_prefix, ["C:/some/hermes.exe"])
        # 非 WSL：即使指到 .exe 也不啟用 interop 路徑轉譯。
        self.assertFalse(inv.windows_interop)
        self.assertIsNone(inv.degradation_note)

    def test_explicit_multi_token_command_is_split(self):
        # Windows 路徑用反斜線，posix=False 不該把它當跳脫字元處理掉。
        inv = dispatch_domain.resolve_hermes_invocation(
            r'"C:\some dir\python.exe" "C:\some dir\fake_hermes.py"'
        )
        self.assertEqual(inv.argv_prefix, [r"C:\some dir\python.exe", r"C:\some dir\fake_hermes.py"])

    def test_missing_from_path_fails_visible(self):
        # 把 Path.home 釘到空目錄——這台機器若真的有 ~/.local/bin/hermes，
        # fallback 會命中，測不到「兩處都找不到」的報錯路徑。
        empty_home = Path(tempfile.mkdtemp(prefix="empty_home_"))
        with mock.patch("shutil.which", return_value=None), \
             mock.patch("dispatch_domain.Path.home", return_value=empty_home):
            with self.assertRaises(dispatch_domain.DispatchError) as ctx:
                dispatch_domain.resolve_hermes_invocation(None)
        self.assertEqual(ctx.exception.exit_status, "hermes_not_found")
        # 錯誤訊息要能區分「沒安裝」與「PATH 沒帶到」：指出查過的位置與 PATH 線索。
        self.assertIn("PATH", ctx.exception.message)
        self.assertIn(str(empty_home), ctx.exception.message)
        self.assertIn("--hermes-bin", ctx.exception.message)
        # 非 WSL 的錯誤訊息不應扯到 Windows interop 位置（那是 WSL 分支的事）。
        self.assertNotIn(dispatch_domain.WINDOWS_HERMES_INTEROP_PATH, ctx.exception.message)

    def test_missing_from_path_falls_back_to_local_bin(self):
        # 非 login shell 情境：~/.local/bin 不在 PATH，但 hermes 實際裝在那裡
        # ——PATH 查無時應 fallback 到這個已知安裝位置。
        fake_home = Path(tempfile.mkdtemp(prefix="fake_home_"))
        fake_hermes = fake_home / ".local" / "bin" / "hermes"
        fake_hermes.parent.mkdir(parents=True)
        fake_hermes.write_text("#!/bin/sh\n", encoding="utf-8")
        with mock.patch("shutil.which", return_value=None), \
             mock.patch("dispatch_domain.Path.home", return_value=fake_home):
            inv = dispatch_domain.resolve_hermes_invocation(None)
        self.assertEqual(inv.argv_prefix, [str(fake_hermes)])
        self.assertFalse(inv.windows_interop)

    def test_explicit_override_skips_local_bin_fallback(self):
        # 帶了 --hermes-bin 就不該再看 PATH 或 fallback 位置。
        with mock.patch("shutil.which") as mock_which:
            inv = dispatch_domain.resolve_hermes_invocation("/opt/custom/hermes")
        mock_which.assert_not_called()
        self.assertEqual(inv.argv_prefix, ["/opt/custom/hermes"])


class WslDetectionTests(unittest.TestCase):
    """is_wsl() 的偵測分支。判準是 /proc/version 含 'microsoft'（不分大小寫），
    刻意不用「/mnt/c 存在」——後者取決於 automount 設定且任何 Linux 都可能剛好
    有該目錄（理由詳見 dispatch_domain.is_wsl docstring）。"""

    def _fake_proc_version(self, content):
        f = Path(tempfile.mkdtemp(prefix="proc_")) / "version"
        f.write_text(content, encoding="utf-8")
        return f

    def test_non_linux_platform_is_false_without_touching_files(self):
        with mock.patch.object(dispatch_domain.sys, "platform", "win32"), \
             mock.patch.object(dispatch_domain, "_PROC_VERSION_PATH") as proc_path:
            self.assertFalse(dispatch_domain.is_wsl())
        proc_path.read_text.assert_not_called()

    def test_linux_with_microsoft_kernel_string_is_true(self):
        fake = self._fake_proc_version(
            "Linux version 5.15.167.4-microsoft-standard-WSL2 (root@...) ...\n"
        )
        with mock.patch.object(dispatch_domain.sys, "platform", "linux"), \
             mock.patch.object(dispatch_domain, "_PROC_VERSION_PATH", fake):
            self.assertTrue(dispatch_domain.is_wsl())

    def test_linux_without_microsoft_string_is_false(self):
        fake = self._fake_proc_version("Linux version 6.8.0-generic (buildd@...) ...\n")
        with mock.patch.object(dispatch_domain.sys, "platform", "linux"), \
             mock.patch.object(dispatch_domain, "_PROC_VERSION_PATH", fake):
            self.assertFalse(dispatch_domain.is_wsl())

    def test_linux_missing_proc_version_is_false_not_crash(self):
        missing = Path(tempfile.mkdtemp(prefix="proc_")) / "no_such_version"
        with mock.patch.object(dispatch_domain.sys, "platform", "linux"), \
             mock.patch.object(dispatch_domain, "_PROC_VERSION_PATH", missing):
            self.assertFalse(dispatch_domain.is_wsl())


class WslResolutionOrderTests(unittest.TestCase):
    """WSL 分支的解析順序（is_wsl 釘成 True）：
    --hermes-bin > WINDOWS_HERMES_INTEROP_PATH（存在才用）> PATH > ~/.local/bin。"""

    def setUp(self):
        patcher = mock.patch("dispatch_domain.is_wsl", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _fake_interop_exe(self):
        exe = Path(tempfile.mkdtemp(prefix="interop_")) / "hermes.exe"
        exe.write_text("fake exe\n", encoding="utf-8")
        return exe

    def test_frozen_constant_literal_is_asserted(self):
        # 凍結字面斷言（比照專案慣例，見 scripts/webui_security_check.py）：
        # 機器特定路徑集中在這一個常數，內容不得漂移。
        self.assertEqual(
            dispatch_domain.WINDOWS_HERMES_INTEROP_PATH,
            "/mnt/c/Users/razer/AppData/Local/hermes/hermes-agent/venv/Scripts/hermes.exe",
        )

    def test_interop_path_exists_wins_over_path_and_local_bin(self):
        exe = self._fake_interop_exe()
        with mock.patch.object(dispatch_domain, "WINDOWS_HERMES_INTEROP_PATH", str(exe)), \
             mock.patch("shutil.which") as mock_which:
            inv = dispatch_domain.resolve_hermes_invocation(None)
        mock_which.assert_not_called()
        self.assertEqual(inv.argv_prefix, [str(exe)])
        self.assertTrue(inv.windows_interop)
        self.assertIsNone(inv.degradation_note)

    def test_explicit_hermes_bin_wins_even_when_interop_exists(self):
        exe = self._fake_interop_exe()
        with mock.patch.object(dispatch_domain, "WINDOWS_HERMES_INTEROP_PATH", str(exe)), \
             mock.patch("shutil.which") as mock_which:
            inv = dispatch_domain.resolve_hermes_invocation("/opt/custom/hermes")
        mock_which.assert_not_called()
        self.assertEqual(inv.argv_prefix, ["/opt/custom/hermes"])
        self.assertFalse(inv.windows_interop)

    def test_explicit_exe_under_wsl_enables_interop_translation(self):
        # --hermes-bin 明確指到 /mnt/c/... 的 .exe：仍是最優先，且要啟用
        # windows_interop（路徑型引數的 wslpath 轉譯跟著生效）。
        inv = dispatch_domain.resolve_hermes_invocation("/mnt/c/elsewhere/hermes.exe")
        self.assertEqual(inv.argv_prefix, ["/mnt/c/elsewhere/hermes.exe"])
        self.assertTrue(inv.windows_interop)

    def test_interop_missing_falls_back_to_wsl_hermes_with_honest_note(self):
        # Windows hermes 不在預期位置 → 落回 WSL 側 hermes，且降級說明要誠實
        # 講「其無 lane profile」（寫進 degradation_note 與 stderr）。
        fake_home = Path(tempfile.mkdtemp(prefix="fake_home_"))
        fake_hermes = fake_home / ".local" / "bin" / "hermes"
        fake_hermes.parent.mkdir(parents=True)
        fake_hermes.write_text("#!/bin/sh\n", encoding="utf-8")
        missing = str(Path(tempfile.mkdtemp(prefix="gone_")) / "hermes.exe")
        import io
        fake_stderr = io.StringIO()
        with mock.patch.object(dispatch_domain, "WINDOWS_HERMES_INTEROP_PATH", missing), \
             mock.patch("shutil.which", return_value=None), \
             mock.patch("dispatch_domain.Path.home", return_value=fake_home), \
             mock.patch.object(dispatch_domain.sys, "stderr", fake_stderr):
            inv = dispatch_domain.resolve_hermes_invocation(None)
        self.assertEqual(inv.argv_prefix, [str(fake_hermes)])
        self.assertFalse(inv.windows_interop)
        self.assertIn("不在預期位置", inv.degradation_note)
        self.assertIn("無 lane profile", inv.degradation_note)
        self.assertIn("無 lane profile", fake_stderr.getvalue())

    def test_interop_missing_and_nothing_else_fails_visible_mentioning_interop_path(self):
        empty_home = Path(tempfile.mkdtemp(prefix="empty_home_"))
        missing = str(Path(tempfile.mkdtemp(prefix="gone_")) / "hermes.exe")
        with mock.patch.object(dispatch_domain, "WINDOWS_HERMES_INTEROP_PATH", missing), \
             mock.patch("shutil.which", return_value=None), \
             mock.patch("dispatch_domain.Path.home", return_value=empty_home):
            with self.assertRaises(dispatch_domain.DispatchError) as ctx:
                dispatch_domain.resolve_hermes_invocation(None)
        self.assertEqual(ctx.exception.exit_status, "hermes_not_found")
        # 錯誤訊息要把「查過哪裡」講全：interop 位置、PATH、~/.local/bin。
        self.assertIn(missing, ctx.exception.message)
        self.assertIn("PATH", ctx.exception.message)
        self.assertIn(str(empty_home), ctx.exception.message)


class WslpathTranslationTests(unittest.TestCase):
    """wslpath_to_windows() 本身：成功回傳 strip 後的輸出；任何失敗一律
    DispatchError("wslpath_error")，不靜默退回原路徑。"""

    def _completed(self, returncode=0, stdout="", stderr=""):
        return mock.Mock(returncode=returncode, stdout=stdout, stderr=stderr)

    def test_success_strips_trailing_newline(self):
        with mock.patch("subprocess.run",
                         return_value=self._completed(0, "C:\\Users\\razer\\x\n")) as m:
            self.assertEqual(
                dispatch_domain.wslpath_to_windows("/mnt/c/Users/razer/x"),
                "C:\\Users\\razer\\x",
            )
        argv = m.call_args[0][0]
        self.assertEqual(argv, ["wslpath", "-w", "/mnt/c/Users/razer/x"])

    def test_nonzero_exit_raises_wslpath_error(self):
        with mock.patch("subprocess.run",
                         return_value=self._completed(1, "", "wslpath: no such file")):
            with self.assertRaises(dispatch_domain.DispatchError) as ctx:
                dispatch_domain.wslpath_to_windows("/tmp/x")
        self.assertEqual(ctx.exception.exit_status, "wslpath_error")
        self.assertIn("no such file", ctx.exception.message)

    def test_empty_output_raises_wslpath_error(self):
        with mock.patch("subprocess.run", return_value=self._completed(0, "  \n")):
            with self.assertRaises(dispatch_domain.DispatchError) as ctx:
                dispatch_domain.wslpath_to_windows("/tmp/x")
        self.assertEqual(ctx.exception.exit_status, "wslpath_error")

    def test_wslpath_binary_missing_raises_wslpath_error(self):
        with mock.patch("subprocess.run", side_effect=FileNotFoundError("wslpath")):
            with self.assertRaises(dispatch_domain.DispatchError) as ctx:
                dispatch_domain.wslpath_to_windows("/tmp/x")
        self.assertEqual(ctx.exception.exit_status, "wslpath_error")


class RunSubprocessEncodingTests(unittest.TestCase):
    """回歸測試 2026-07-21 real-call incident 的根因本身：run_subprocess() 呼叫
    subprocess.run() 時必須明確帶 encoding="utf-8"，不能用 text=True（會退回
    系統 locale，Windows 中文環境常見 cp950/cp936，繁體中文子行程輸出會直接
    UnicodeDecodeError）。這裡直接斷言呼叫參數，不依賴當下機器的 locale 是
    什麼，也不依賴子行程真的輸出非 ASCII 內容——即使日後測試機換成 UTF-8
    locale、原本的「非 ASCII 輸出會不會崩潰」整合測試測不出regression，這裡
    仍然會抓到。"""

    def test_run_subprocess_pins_utf8_encoding_not_locale_dependent_text_mode(self):
        with mock.patch("subprocess.run") as mock_run:
            fake_completed = mock.Mock()
            mock_run.return_value = fake_completed
            result = dispatch_domain.run_subprocess(["echo", "hi"], cwd=ROOT, timeout=30)
        self.assertIs(result, fake_completed)
        _, kwargs = mock_run.call_args
        self.assertEqual(kwargs.get("encoding"), "utf-8")
        self.assertNotIn("text", kwargs, "不應再用 text=True 依賴系統 locale 解碼子行程輸出")
        self.assertFalse(kwargs.get("universal_newlines"), "不應透過 universal_newlines 間接退回 locale 解碼")


class NativeExecutionTests(unittest.TestCase):
    """真的呼叫 route_model.py，不 mock——native 本來就不對外呼叫，offline、
    deterministic，沒有理由 mock。（2026-07-20：原本這裡還有一個 OpenRouter
    缺 API key 的 fail-visible 測試；OpenRouter provider 路徑已整個移除，
    route_model.py 不再有 call_openrouter 邏輯，該測試一併移除。）"""

    def test_native_lane_succeeds_without_network(self):
        lane = {"provider": "anthropic", "model": None}
        prompt_path = ROOT / "scripts" / "requirements.txt"
        result = dispatch_domain.execute_native_or_openrouter(lane, "claude_native", prompt_path, timeout=30)
        self.assertEqual(result.exit_status, "success")
        self.assertIn("via=native", result.result_text if result.result_text else "")

    def test_timeout_is_fail_visible(self):
        hang_script = Path(tempfile.mkdtemp(prefix="hang_")) / "hang.py"
        hang_script.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
        lane = {"provider": "anthropic", "model": None}
        prompt_path = ROOT / "scripts" / "requirements.txt"
        with mock.patch.object(dispatch_domain, "ROUTE_MODEL_SCRIPT", hang_script):
            result = dispatch_domain.execute_native_or_openrouter(lane, "claude_native", prompt_path, timeout=1)
        self.assertEqual(result.exit_status, "timeout")


class HermesProfileExecutionTests(FakeHermesFixture):
    def setUp(self):
        super().setUp()
        self.lane = {"provider": "hermes", "hermes_profile": "nemocoding"}
        self.log_dir = Path(tempfile.mkdtemp(prefix="dispatch_logs_"))

    def test_success_reports_usage_and_model_and_isolates_cwd(self):
        marker = self.make_marker()
        lane = {"provider": "hermes", "hermes_profile": "nemocoding"}
        result = dispatch_domain.execute_hermes_profile(
            lane, "do the thing", "exec-1", 30, self.invocation, self.log_dir
        )
        self.assertEqual(result.exit_status, "success")
        self.assertEqual(result.result_text, "OK RESPONSE FROM nemocoding")
        self.assertEqual(result.usage["model"], "fake-nemo-model")
        self.assertEqual(result.model, "fake-nemo-model")

        marker_data = json.loads(marker.read_text(encoding="utf-8"))
        self.assertFalse(marker_data["agents_md_exists"], "中性 cwd 不該有 AGENTS.md")
        self.assertNotEqual(Path(marker_data["cwd"]).resolve(), ROOT.resolve(),
                             "hermes 執行 cwd 不該是 repo root（避免自動載入 AGENTS.md）")
        self.assertIn("PROJECT_ROOT = ", marker_data["prompt_head"],
                       "prompt 應該帶絕對路徑，讓中性 cwd 底下的 agent 仍能定位專案檔案")

    def test_profile_not_found_is_fail_visible(self):
        lane = {"provider": "hermes", "hermes_profile": "gptcoding"}
        result = dispatch_domain.execute_hermes_profile(
            lane, "do the thing", "exec-2", 30, self.invocation, self.log_dir
        )
        self.assertEqual(result.exit_status, "profile_not_found")
        self.assertIn("does not exist", result.detail)
        self.assertIsNone(result.result_text)

    def test_bad_usage_json_is_fail_visible(self):
        lane = {"provider": "hermes", "hermes_profile": "financialresearch"}
        result = dispatch_domain.execute_hermes_profile(
            lane, "do the thing", "exec-3", 30, self.invocation, self.log_dir
        )
        self.assertEqual(result.exit_status, "bad_usage_json")
        self.assertIsNone(result.usage)
        self.assertIsNotNone(result.detail)

    def test_empty_output_is_fail_visible(self):
        lane = {"provider": "hermes", "hermes_profile": "intelligence"}
        result = dispatch_domain.execute_hermes_profile(
            lane, "do the thing", "exec-4", 30, self.invocation, self.log_dir
        )
        self.assertEqual(result.exit_status, "empty_output")
        self.assertIsNone(result.result_text)

    def test_timeout_is_fail_visible(self):
        lane = {"provider": "hermes", "hermes_profile": "codereviewer"}
        result = dispatch_domain.execute_hermes_profile(
            lane, "do the thing", "exec-5", 1, self.invocation, self.log_dir
        )
        self.assertEqual(result.exit_status, "timeout")

    def test_non_ascii_stdout_is_decoded_without_crashing(self):
        # 回歸測試 2026-07-21 real-call incident：hermes -z 回傳的繁體中文
        # 研究內容過去會在 subprocess 讀取階段因為 UnicodeDecodeError 而整個
        # 崩潰（result 被弄丟、exit_status 誤判成 empty_output）。這裡驗證
        # execute_hermes_profile 現在能正確解碼、原樣帶回中文內容。
        lane = {"provider": "hermes", "hermes_profile": "unicodecheck"}
        result = dispatch_domain.execute_hermes_profile(
            lane, "do the thing", "exec-unicode", 30, self.invocation, self.log_dir
        )
        self.assertEqual(result.exit_status, "success")
        self.assertEqual(
            result.result_text,
            "研究結論：台股大盤觀察，關鍵字包含「風險」與「機會」。",
        )
        self.assertEqual(result.usage["model"], "fake-nemo-model")

    def test_prompt_too_long_fails_before_any_subprocess_call(self):
        lane = {"provider": "hermes", "hermes_profile": "nemocoding"}
        huge_prompt = "x" * (dispatch_domain.MAX_HERMES_PROMPT_CHARS + 1)
        # 故意帶一個不存在的執行檔前綴：如果程式碼真的嘗試呼叫 subprocess，
        # 這裡會因為 FileNotFoundError 而不是我們要驗證的 prompt_too_long。
        bad_invocation = dispatch_domain.HermesInvocation(
            argv_prefix=["this-binary-does-not-exist"], windows_interop=False,
            degradation_note=None,
        )
        result = dispatch_domain.execute_hermes_profile(
            lane, huge_prompt, "exec-6", 30, bad_invocation, self.log_dir
        )
        self.assertEqual(result.exit_status, "prompt_too_long")

    # --- windows_interop 情境（wslpath 轉譯經 mock，不執行真實 wslpath）-----

    def _interop_invocation(self):
        return dispatch_domain.HermesInvocation(
            argv_prefix=self.hermes_argv_prefix, windows_interop=True,
            degradation_note=None,
        )

    def test_interop_translates_usage_file_arg_and_project_root_line(self):
        # 轉譯規則：--usage-file 轉的是「已存在的父目錄」再接檔名（部分版本
        # wslpath 對不存在路徑會報錯）；PROJECT_ROOT 行整段轉。這裡用假譯法
        # 驗證兩種視角：傳給 hermes 的引數是「轉譯後」形式，dispatch 自己讀
        # usage file 仍用原路徑。父目錄假譯成自身（本機可寫，fake hermes 才
        # 寫得進去），ROOT 假譯成固定 Windows 字面以便斷言。
        marker = self.make_marker()
        lane = {"provider": "hermes", "hermes_profile": "nemocoding"}

        def fake_wslpath(path):
            if path == str(ROOT):
                return r"C:\FAKE\PROJECT\ROOT"
            return path  # usage-file 父目錄：identity（保持本機可寫）

        with mock.patch.object(dispatch_domain, "wslpath_to_windows",
                                side_effect=fake_wslpath) as m:
            result = dispatch_domain.execute_hermes_profile(
                lane, "do the thing", "exec-interop-1", 30,
                self._interop_invocation(), self.log_dir,
            )
        self.assertEqual(result.exit_status, "success")
        # usage file 由 dispatch 用原路徑讀回（同一檔案兩種視角）。
        self.assertEqual(result.usage["model"], "fake-nemo-model")
        # 兩個轉譯位點都被呼叫：usage-file 父目錄與 ROOT。
        called_paths = [c.args[0] for c in m.call_args_list]
        self.assertIn(str(Path(self.log_dir)), called_paths)
        self.assertIn(str(ROOT), called_paths)
        # PROJECT_ROOT 行對 Windows 側程序語意成立。
        marker_data = json.loads(marker.read_text(encoding="utf-8"))
        self.assertTrue(marker_data["prompt_head"].startswith(
            "PROJECT_ROOT = C:\\FAKE\\PROJECT\\ROOT"
        ), marker_data["prompt_head"])

    def test_interop_usage_file_translation_failure_is_fail_visible(self):
        # --usage-file 是功能性引數：wslpath 失敗必須明確報錯、不送出執行。
        lane = {"provider": "hermes", "hermes_profile": "nemocoding"}
        marker = self.make_marker()
        with mock.patch.object(
            dispatch_domain, "wslpath_to_windows",
            side_effect=dispatch_domain.DispatchError("wslpath_error", "wslpath 爆炸"),
        ):
            result = dispatch_domain.execute_hermes_profile(
                lane, "do the thing", "exec-interop-2", 30,
                self._interop_invocation(), self.log_dir,
            )
        self.assertEqual(result.exit_status, "wslpath_error")
        self.assertIn("--usage-file", result.detail)
        self.assertIn("wslpath 爆炸", result.detail)
        self.assertFalse(marker.exists(), "轉譯失敗不該真的 spawn hermes")

    def test_interop_project_root_translation_failure_is_lenient_with_note(self):
        # PROJECT_ROOT 行是資訊性文字：轉譯失敗保留原樣並註記，不擋執行。
        marker = self.make_marker()
        lane = {"provider": "hermes", "hermes_profile": "nemocoding"}

        def fake_wslpath(path):
            if path == str(ROOT):
                raise dispatch_domain.DispatchError("wslpath_error", "ROOT 轉譯失敗")
            return path

        with mock.patch.object(dispatch_domain, "wslpath_to_windows",
                                side_effect=fake_wslpath):
            result = dispatch_domain.execute_hermes_profile(
                lane, "do the thing", "exec-interop-3", 30,
                self._interop_invocation(), self.log_dir,
            )
        self.assertEqual(result.exit_status, "success")
        marker_data = json.loads(marker.read_text(encoding="utf-8"))
        self.assertIn(f"PROJECT_ROOT = {ROOT}", marker_data["prompt_head"])
        self.assertIn("wslpath 轉譯失敗", marker_data["prompt_head"])

    def test_non_interop_invocation_never_calls_wslpath(self):
        # windows_interop=False（非 WSL、或 WSL 落回本側 hermes）：完全不碰
        # wslpath，引數與 prompt 都維持原路徑——現行平台行為不變的回歸保證。
        lane = {"provider": "hermes", "hermes_profile": "nemocoding"}
        with mock.patch.object(dispatch_domain, "wslpath_to_windows") as m:
            result = dispatch_domain.execute_hermes_profile(
                lane, "do the thing", "exec-interop-4", 30,
                self.invocation, self.log_dir,
            )
        m.assert_not_called()
        self.assertEqual(result.exit_status, "success")


class DispatchEndToEndTests(FakeHermesFixture):
    """經 dispatch()／main() 的完整路徑，涵蓋 --hermes-bin 覆蓋、fallback、
    CLI JSON envelope 輸出。"""

    def setUp(self):
        super().setUp()
        self.log_dir = Path(tempfile.mkdtemp(prefix="dispatch_e2e_"))

    def _args(self, **overrides):
        from argparse import Namespace
        base = dict(
            owner="engineering", category="code_change",
            prompt_file=str(ROOT / "scripts" / "requirements.txt"),
            execution_id="e2e-test", capability=None, lane=None,
            timeout=30, hermes_bin=self.hermes_bin_command, log_dir=str(self.log_dir),
        )
        base.update(overrides)
        return Namespace(**base)

    def test_explicit_hermes_lane_success_envelope(self):
        # 2026-07-20 起 engineering 的 default_capability 已改成 claude_native
        # （不再是 complex_coding），--lane=hermes-nemocoding 的 capability 是
        # complex_coding，兩者不一致會被 select_lane 擋下，所以要明確帶
        # --capability=complex_coding 才能指到這條 lane（跟真實使用情境一致：
        # 想跳出預設 capability 用別條 lane 時，兩個旗標本來就要一起帶）。
        envelope, code = dispatch_domain.dispatch(
            self._args(lane="hermes-nemocoding", capability="complex_coding")
        )
        self.assertEqual(code, 0)
        self.assertEqual(envelope["exit_status"], "success")
        self.assertEqual(envelope["lane"], "hermes-nemocoding")
        self.assertEqual(envelope["profile"], "nemocoding")
        self.assertEqual(envelope["provider"], "hermes")
        self.assertEqual(envelope["model"], "fake-nemo-model")
        self.assertIsNone(envelope["fallback_reason"])
        self.assertEqual(envelope["result"], "OK RESPONSE FROM nemocoding")

    def test_explicit_hermes_lane_profile_not_found_has_no_fallback_when_registry_has_none(self):
        envelope, code = dispatch_domain.dispatch(
            self._args(lane="hermes-gptcoding", capability="complex_coding")
        )
        self.assertEqual(code, 1)
        self.assertEqual(envelope["exit_status"], "profile_not_found")
        self.assertEqual(envelope["lane"], "hermes-gptcoding")
        self.assertIsNone(envelope["fallback_reason"])

    def test_default_lane_for_engineering_resolves_directly_to_claude_native(self):
        # 2026-07-20 起 engineering 的 default_capability 已從 complex_coding
        # （原走 OpenRouter GPT-5.5）改成 claude_native——OPENROUTER_API_KEY 從未
        # 真正設定過，該路徑拍板整個移除。不帶 --lane、不帶 --capability 時，
        # 現在應該直接選到 claude-native 並成功，不需要任何 fallback。
        envelope, code = dispatch_domain.dispatch(self._args())
        self.assertEqual(code, 0)
        self.assertEqual(envelope["exit_status"], "success")
        self.assertEqual(envelope["lane"], "claude-native")
        self.assertIsNone(envelope["fallback_reason"])

    def test_lane_with_fallback_lane_falls_back_and_succeeds(self):
        # dispatch() 的 fallback 機制本身仍然是既有功能，但目前真實
        # registry/capability_lanes.yaml 已經沒有任何 lane 設定 fallback_lane
        # （原本三條 openrouter-* lane 是僅有的來源，2026-07-20 隨 OpenRouter
        # provider 路徑一併移除）。用合成 lane 直接測 dispatch() 的 fallback
        # 邏輯，不依賴目前 registry 是否剛好有可觸發 fallback 的資料。
        agents_doc, router_doc, lanes_doc = dispatch_domain.load_registries()
        synthetic_failing_lane = {
            "id": "synthetic-failing-lane", "capability": "complex_coding",
            "execution": "hermes_profile", "provider": "hermes", "model": None,
            "hermes_profile": "gptcoding",  # fake hermes 對這個 profile 回報 profile_not_found
            "status": "active", "cost_tier": "unknown", "risk_tier": "medium",
            "allowed_agents": ["engineering"], "intended_use": "test only",
            "guardrails": ["test only"], "fallback_lane": "claude-native",
        }
        fake_lanes_doc = {"lanes": [synthetic_failing_lane] + lanes_doc.get("lanes", [])}
        with mock.patch.object(
            dispatch_domain, "load_registries",
            return_value=(agents_doc, router_doc, fake_lanes_doc),
        ):
            envelope, code = dispatch_domain.dispatch(
                self._args(capability="complex_coding", lane="synthetic-failing-lane")
            )
        self.assertEqual(code, 0)
        self.assertEqual(envelope["exit_status"], "fallback_success")
        self.assertEqual(envelope["lane"], "claude-native")
        self.assertIsNotNone(envelope["fallback_reason"])

    def test_registry_error_before_execution_still_emits_stable_envelope(self):
        envelope, code = dispatch_domain.dispatch(self._args(owner="not_a_real_domain"))
        self.assertEqual(code, 1)
        self.assertEqual(envelope["exit_status"], "registry_error")
        self.assertIn("owner", envelope)
        self.assertIsNone(envelope["lane"])

    def test_prompt_path_outside_project_fails_visible(self):
        envelope, code = dispatch_domain.dispatch(self._args(prompt_file="C:/Windows/win.ini"))
        self.assertEqual(code, 1)
        self.assertEqual(envelope["exit_status"], "prompt_path_error")

    def test_cli_main_prints_json_envelope_to_stdout(self):
        import subprocess
        # dispatch_domain.py 明確把 stdout 鎖定 UTF-8（見 main()）——呼叫端要用
        # encoding="utf-8" 明確解碼，不能依賴 text=True 背後的系統 locale
        # （這台機器預設是 cp950，會在夾雜繁體中文訊息時解碼失敗）。這正是
        # 「輸出穩定 JSON envelope」的一部分：呼叫慣例要跟環境無關。
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "dispatch_domain.py"),
             "--owner", "automation", "--category", "recurring_workflow",
             "--prompt-file", str(ROOT / "scripts" / "requirements.txt"),
             "--execution-id", "cli-test-1",
             "--log-dir", str(self.log_dir)],
            cwd=ROOT, capture_output=True, encoding="utf-8", timeout=60,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        envelope = json.loads(proc.stdout)
        self.assertEqual(envelope["owner"], "automation")
        self.assertEqual(envelope["exit_status"], "success")
        self.assertEqual(envelope["lane"], "claude-native")


if __name__ == "__main__":
    unittest.main()
