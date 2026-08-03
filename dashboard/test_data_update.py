#!/usr/bin/env python3
"""dashboard/test_data_update.py — 「Hermes 更新」唯讀升級預檢資料層測試。

設計正本 docs/webui-update-button-proposal.md §3(階段一)。核心測試:

- **白名單靜態鎖定**(比照 data_resident 的鎖定模式):subprocess.run 只有一個
  位點(_exec);無參數查詢限 FROZEN_GIT_TEMPLATES;帶 remote 名的查詢限
  REF_TEMPLATE_BUILDERS 且 remote 名須過 REMOTE_NAME_RE(擋旗標/路徑注入);
  原始碼無任何寫入 git 子指令的「命令字面」;ALLOWED 子指令集不含寫入子指令。
- **雙基準**(2026-07-24 防重演落地後的修正):origin(私有備份)與 upstream
  (官方)各一組 ahead/behind/ff;角色**依 remote URL 判定**(不依名稱——
  兩端 remote 命名不同:Windows 是 origin/upstream,WSL 是 origin(官方)/
  windows-side)。整體燈取兩組較嚴重者並標示 overall_driver。
  **關鍵回歸**:只比 origin 會把「origin 0/0 但官方落後 16」誤報為綠。
- **follow role**(2026-08-03,提案 §10.1):re-graft 後 WSL 的 `origin` 是
  **本機 Windows 路徑**,須被辨識為「應跟隨的權威基準」並**計入整體燈**;
  語意與 upstream 相反(落後=藍該同步、領先/分歧=橙異常)。含**否定案例**:
  本機路徑但不是 Windows hermes-agent 一律留在 peer(不得誤升級成權威基準)。
- **選項 b 拍板**(2026-08-03):follow 存在的 target 上 upstream 降為資訊性
  (不計入整體燈,照常輸出),整體燈由 follow 獨力驅動——WSL 卡片即 follow
  三態(綠/藍/橙);無 follow 的 target(Windows)upstream 照常計入,兩向皆測。
- **不喚醒**(硬性邊界):WSL distro 未 Running 時完全不下 `wsl -d` 指令。
- 45 秒 TTL 快取;全域容錯(未預期例外 → 兩端灰)。
- **live 版本字串**(2026-07-25 切片 1 補實作,提案 §3.1 第一項):
  `v0.19.0 upstream 3910ab28 + local 97011887 (+12 carried commits)`。
  版本來源限 `git show HEAD:pyproject.toml`(凍結字面)+ `merge-base`,
  **無寫入副作用**——`NoWriteSideEffectTests` 以行為測試逐一檢查探測期間
  實際跑過的每一條指令:必為 git(或 wsl 前綴的 git)、子指令在白名單內、
  且 args 必須是凍結模板或凍結建構器的產物,禁止任何非 git spawn。

fixture 一律假資料;不觸碰真實 git / wsl / %LOCALAPPDATA%。
執行:.venv/Scripts/python.exe dashboard/test_data_update.py
"""
import re
import subprocess as real_subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import data_resident  # noqa: E402
import data_update  # noqa: E402

DASHBOARD_DIR = Path(__file__).resolve().parent

T = data_update  # 短別名

OFFICIAL_URL = "https://github.com/NousResearch/hermes-agent.git"
PRIVATE_URL = "https://github.com/konicatc-techcoding/hermes-agent-private.git"
# WSL 端指向 Windows hermes-agent 的本機路徑(2026-07-25 re-graft 後 origin 的
# 實測值,以唯讀 `git remote -v` 確認過)→ 2026-08-03 起判為 follow role。
LOCAL_PATH_URL = "/mnt/c/Users/razer/AppData/Local/hermes/hermes-agent"
# 對應的 Windows 原生路徑(測試一律固定此值,不依賴執行機器的 LOCALAPPDATA)
WIN_REPO_PATH_STR = "C:/Users/razer/AppData/Local/hermes/hermes-agent"
# **本機路徑但不是 Windows hermes-agent**——follow 判準的否定案例,須留在 peer
OTHER_LOCAL_URL = "/home/razer/scratch/hermes-agent"
OTHER_MOUNT_URL = "/mnt/d/backup/hermes/hermes-agent"


class FakeGit:
    """記錄型 subprocess 替身。回應表以「cmd 去掉前綴後的 args tuple」為 key
    (win 前綴 3 個元素、wsl 前綴 7 個)。"""

    TimeoutExpired = real_subprocess.TimeoutExpired

    def __init__(self, win: dict | None = None, wsl: dict | None = None):
        self.win = win or {}
        self.wsl = wsl or {}
        self.calls: list[tuple] = []

    def run(self, cmd, **kwargs):
        cmd = tuple(cmd)
        self.calls.append(cmd)
        is_wsl = "wsl.exe" in cmd
        table = self.wsl if is_wsl else self.win
        args = cmd[7:] if is_wsl else cmd[3:]
        resp = table.get(args)
        if isinstance(resp, BaseException):
            raise resp
        if resp is None:
            return real_subprocess.CompletedProcess(cmd, 128, stdout="", stderr="")
        rc, out = resp
        return real_subprocess.CompletedProcess(cmd, rc, stdout=out, stderr="")

    @property
    def wsl_calls(self):
        return [c for c in self.calls if "wsl.exe" in c]


# 真實 pyproject.toml 的最小形態(hermes-agent 實測:`[project]` 段第 3 行、
# name 第 4 行、version 第 5 行)。刻意在 `[project]` 之外也放一個 version,
# 用來證明解析器是段落感知的、不會抓錯段。
PYPROJECT_OK = (
    '[build-system]\n'
    'requires = ["setuptools"]\n'
    'version = "9.9.9"\n'
    '\n'
    '[project]\n'
    'name = "hermes-agent"\n'
    'version = "0.19.0"\n'
    'requires-python = ">=3.11"\n'
    '\n'
    '[tool.pytest.ini_options]\n'
    'version = "0.0.1"\n'
)


def repo_responses(*, head="970118870", branch="main", describe="v1-g970118870",
                   porcelain="", remotes=(), refs="main 970118870 commit",
                   pyproject=PYPROJECT_OK, per_remote=None):
    """組出一個 repo 的完整回應表。per_remote = {name: dict(url=, tip=, behind=,
    ahead=, ancestor_rc=, log=, merge_base=)};tip=None 代表該 <remote>/main
    ref 不存在。pyproject=None 代表 `git show HEAD:pyproject.toml` 失敗。"""
    table = {
        T.GIT_HEAD_SHORT: (0, head),
        T.GIT_BRANCH: (0, branch),
        T.GIT_DESCRIBE: (0, describe),
        T.GIT_PORCELAIN: (0, porcelain),
        T.GIT_REFS: (0, refs),
        T.GIT_REMOTE_LIST: (0, "\n".join(remotes)),
        T.GIT_PYPROJECT: (128, "") if pyproject is None else (0, pyproject),
    }
    for name, spec in (per_remote or {}).items():
        table[T._t_remote_url(name)] = (0, spec.get("url", OFFICIAL_URL))
        tip = spec.get("tip")
        table[T._t_tip(name)] = (128, "") if tip is None else (0, tip)
        if tip is not None:
            table[T._t_behind(name)] = (0, str(spec.get("behind", 0)))
            table[T._t_ahead(name)] = (0, str(spec.get("ahead", 0)))
            table[T._t_ancestor(name)] = (spec.get("ancestor_rc", 1), "")
            table[T._t_diverge_log(name)] = (0, spec.get("log", ""))
            mb = spec.get("merge_base")
            table[T._t_merge_base(name)] = (128, "") if mb is None else (0, mb)
    return table


# --- 真實形態 fixture(2026-07-24 防重演落地後的兩端實況)------------------

WIN_REAL = repo_responses(
    head="970118870",
    describe="rescue/pre-remerge-20260724-14-g970118870",
    porcelain="",
    remotes=("origin", "upstream"),
    refs=("rescue/pre-updater-merge-20260724 c12c64f9e commit\n"
          "rescue/pre-remerge-20260724 df1464ef9 commit\n"
          "main 970118870 commit"),
    per_remote={
        # origin = 私有備份,本機已 push → 0/0(防重演基準有效)
        "origin": {"url": PRIVATE_URL, "tip": "970118870", "behind": 0, "ahead": 0,
                   "ancestor_rc": 0},
        # upstream = 官方,落後 16 / 領先 12(帶客製)→ diverged
        # merge_base = 已吸收到的官方最新一顆(live 版本字串的 `upstream <sha>`);
        # 實測值即 2026-07-24 受控 merge 進來的上游 tip 3910ab28c。
        "upstream": {"url": OFFICIAL_URL, "tip": "46c7a4076", "behind": 16, "ahead": 12,
                     "ancestor_rc": 1, "merge_base": "3910ab28c9e1f0a2b3c4d5e6f7",
                     "log": "970118870 fix(merge): slack ledger\n11f698fce merge(v0.19.0)"},
    },
)

WSL_REAL = repo_responses(
    head="c12c64f9e9",
    describe="pre-upstream-merge-20260717-653-gc12c64f9e9",
    porcelain="?? .install_method",
    remotes=("origin", "windows-side"),
    refs="main c12c64f9e9 commit",
    per_remote={
        # WSL 的 origin 就是官方(remote 命名與 Windows 不同!)
        "origin": {"url": OFFICIAL_URL, "tip": "c48d53413a", "behind": 160, "ahead": 11,
                   "ancestor_rc": 1, "log": "c12c64f9e custom merge"},
        # windows-side 指向 Windows repo → follow role(2026-08-03 起計入整體燈)
        "windows-side": {"url": LOCAL_PATH_URL, "tip": "c12c64f9e9", "behind": 0,
                         "ahead": 0, "ancestor_rc": 0},
    },
)

# 2026-07-25 re-graft 後 WSL 的**現況形態**:origin = 本機 Windows 路徑
# (follow)、upstream = 官方(diverged)。用於 §10.1 的 follow role 測試。
def wsl_post_regraft(*, follow_behind=0, follow_ahead=0, follow_ancestor_rc=0,
                     follow_url=LOCAL_PATH_URL, upstream_behind=295, upstream_ahead=12):
    return repo_responses(
        head="970118870", branch="main", porcelain="", remotes=("origin", "upstream"),
        refs="main 970118870 commit",
        per_remote={
            "origin": {"url": follow_url, "tip": "970118870",
                       "behind": follow_behind, "ahead": follow_ahead,
                       "ancestor_rc": follow_ancestor_rc,
                       "log": "abc123 wsl-only commit" if follow_ahead else ""},
            "upstream": {"url": OFFICIAL_URL, "tip": "760112adb",
                         "behind": upstream_behind, "ahead": upstream_ahead,
                         "ancestor_rc": 1, "merge_base": "3910ab28c",
                         "log": "970118870 custom"},
        })


class UpdateProbeTestCase(unittest.TestCase):
    def setUp(self):
        self._orig = {
            "subprocess": T.subprocess,
            "win_path": T.WINDOWS_REPO_PATH,
            "gateway_ready": data_resident._gateway_ready,
            "distro_state": data_resident._distro_state,
            "probe_units": data_resident._probe_units,
        }
        T._cache = None
        # 固定成真實形態的路徑(而非 C:/fake/…):follow role 的判準要拿它跟
        # remote URL 比對,測試不得依賴執行機器的 LOCALAPPDATA。
        T.WINDOWS_REPO_PATH = Path(WIN_REPO_PATH_STR)
        data_resident._gateway_ready = lambda: {
            "ready": True, "state": "running", "detail": "gateway 就緒"}
        data_resident._distro_state = lambda: (True, "distro 狀態:Running")
        data_resident._probe_units = lambda: {
            "hermes-worker.service": {"state": "active", "sub": "running", "resident": True},
            "hermes-telegram.service": {"state": "active", "sub": "running", "resident": True},
        }

    def tearDown(self):
        T.subprocess = self._orig["subprocess"]
        T.WINDOWS_REPO_PATH = self._orig["win_path"]
        data_resident._gateway_ready = self._orig["gateway_ready"]
        data_resident._distro_state = self._orig["distro_state"]
        data_resident._probe_units = self._orig["probe_units"]
        T._cache = None

    def _install(self, fake: FakeGit) -> FakeGit:
        T.subprocess = fake
        return fake

    @staticmethod
    def _group(end: dict, role: str) -> dict | None:
        for c in end.get("comparisons") or []:
            if c["role"] == role:
                return c
        return None


# ---------------------------------------------------------------------------
# 白名單鎖定
# ---------------------------------------------------------------------------

class WhitelistLockTests(unittest.TestCase):
    SOURCE = (DASHBOARD_DIR / "data_update.py").read_text(encoding="utf-8")

    def test_run_git_rejects_non_frozen_template(self):
        for bad in [("fetch", "origin"), ("reset", "--hard", "origin/main"),
                    ("merge", "--ff-only", "origin/main"), ("pull",), ("checkout", "main")]:
            with self.assertRaises(ValueError):
                T._run_git(("git", "-C", "x"), bad)

    def test_run_git_remote_rejects_unknown_builder(self):
        with self.assertRaises(ValueError):
            T._run_git_remote(("git", "-C", "x"), lambda r: ("fetch", r), "origin")

    def test_run_git_remote_rejects_illegal_remote_names(self):
        # 擋旗標注入、路徑、空白、空字串、非字串、過長
        for bad in ["--upgrade", "-f", "a/b", "a b", "", "..", "o;rm", None, 5, "x" * 40]:
            with self.assertRaises(ValueError):
                T._run_git_remote(("git", "-C", "x"), T._t_tip, bad)

    def test_remote_name_re_accepts_real_names(self):
        for good in ["origin", "upstream", "windows-side", "my.remote_1"]:
            self.assertTrue(T.REMOTE_NAME_RE.match(good), good)

    def test_all_builders_produce_allowed_subcommands(self):
        for builder in T.REF_TEMPLATE_BUILDERS:
            args = builder("origin")
            self.assertIn(args[0], T.ALLOWED_GIT_SUBCOMMANDS, f"{builder} → {args}")

    def test_all_frozen_templates_subcommand_in_allowed(self):
        for tmpl in T.FROZEN_GIT_TEMPLATES:
            self.assertIn(tmpl[0], T.ALLOWED_GIT_SUBCOMMANDS, f"模板 {tmpl} 子指令不在白名單")

    def test_allowed_subcommands_contain_no_write_verb(self):
        write_verbs = {"fetch", "pull", "merge", "reset", "checkout", "clone",
                       "push", "commit", "rebase", "stash", "apply", "cherry-pick"}
        self.assertEqual(T.ALLOWED_GIT_SUBCOMMANDS & write_verbs, set())

    def test_single_subprocess_call_site(self):
        self.assertEqual(len(re.findall(r"subprocess\.run\(", self.SOURCE)), 1,
                         "subprocess.run 位點必須恰為一處(_exec)")

    def test_source_has_no_write_git_command_literals(self):
        # 寫入 git 子指令的「命令字面」(quoted token)不得出現。
        # 註:"merge-base" 是唯讀模板,字面不含 '"merge"'。
        for forbidden in ['"fetch"', '"pull"', '"merge"', '"reset"', '"checkout"',
                          '"clone"', '"push"', '"commit"', '"rebase"', '"stash"',
                          '"apply"', '"cherry-pick"']:
            self.assertNotIn(forbidden, self.SOURCE, f"不得出現寫入 git 命令字面:{forbidden}")

    def test_frozen_templates_are_readonly_forms(self):
        self.assertEqual(T.GIT_PORCELAIN, ("status", "--porcelain"))
        self.assertEqual(T.GIT_REMOTE_LIST, ("remote",))
        self.assertEqual(T._t_ancestor("origin"),
                         ("merge-base", "--is-ancestor", "HEAD", "origin/main"))
        self.assertEqual(T._t_remote_url("upstream"), ("remote", "get-url", "upstream"))

    # --- 切片 1 新增的版本來源:必須是凍結字面,且不得擴出寫入面 ---------------

    def test_version_source_is_frozen_zero_parameter_template(self):
        """版本來源只能是這一條凍結字面——沒有任何參數化入口可被注入。"""
        self.assertEqual(T.GIT_PYPROJECT, ("show", "HEAD:pyproject.toml"))
        self.assertIn(T.GIT_PYPROJECT, T.FROZEN_GIT_TEMPLATES)
        self.assertIn("show", T.ALLOWED_GIT_SUBCOMMANDS)

    def test_version_source_is_not_parameterised_anywhere(self):
        """`show` 只准出現在那條凍結字面裡:不得有任何建構器產生 show。"""
        for builder in T.REF_TEMPLATE_BUILDERS:
            self.assertNotEqual(builder("origin")[0], "show",
                                f"{builder} 不得產生可參數化的 show")

    def test_merge_base_builder_reuses_existing_readonly_subcommand(self):
        self.assertEqual(T._t_merge_base("upstream"),
                         ("merge-base", "upstream/main", "HEAD"))
        self.assertIn(T._t_merge_base, T.REF_TEMPLATE_BUILDERS)
        # 沒有因此新增任何子指令:merge-base 本來就在(--is-ancestor 用的同一個)
        self.assertIn("merge-base", T.ALLOWED_GIT_SUBCOMMANDS)

    def test_only_two_executables_are_known_to_the_module(self):
        """版本**不得**靠跑 hermes CLI 取得(會 spawn process、載入整包 hermes、
        且 banner 會寫 ~/.hermes/.update_check ⇒「看狀態」變「動狀態」)。

        鎖定方式是「可執行檔常數」而非掃 `hermes` 這個字——模組 docstring 本來
        就會談到 hermes(以及為何不讀 dist-info),掃字會誤傷說明文字。真正的
        保證在:模組只認得 git / wsl 兩個 binary,加上 NoWriteSideEffectTests
        的行為測試逐條檢查實際跑過的指令。"""
        self.assertEqual(T.GIT_BIN, "git")
        self.assertEqual(T.WSL_BIN, "wsl.exe")
        # 模組認得的可執行檔常數恰為這兩個——擋掉未來有人加 HERMES_BIN 之類的。
        bins = {name for name in dir(T) if name.endswith("_BIN")}
        self.assertEqual(bins, {"GIT_BIN", "WSL_BIN"}, f"多了未預期的可執行檔常數:{bins}")
        # 註:此處刻意**不掃** '"hermes"' / "hermes_cli" 等字面——`WINDOWS_REPO_PATH`
        # 的路徑組裝(`/ "hermes" / "hermes-agent"`)與 docstring 的排除理由
        # (引用上游 `hermes_cli/banner.py` 說明為何不跑 CLI)本來就會出現這些字,
        # 掃了會誤傷說明文字。取版本不得引入的是「查已安裝套件」的 API:
        for forbidden in ["importlib", "pkg_resources", "shutil.which"]:
            self.assertNotIn(forbidden, self.SOURCE,
                             f"版本來源不得涉及 {forbidden}(見模組 docstring 的排除理由)")

    def test_no_non_git_spawn_primitives_in_source(self):
        """除了唯一的 subprocess.run,不得出現任何其他 spawn 原語。"""
        for forbidden in ["Popen", "os.system", "os.popen", "subprocess.call",
                          "check_output", "os.exec", "os.spawn"]:
            self.assertNotIn(forbidden, self.SOURCE, f"不得出現 spawn 原語:{forbidden}")


# ---------------------------------------------------------------------------
# 角色判定(依 URL,不依名稱)
# ---------------------------------------------------------------------------

class RoleDetectionTests(unittest.TestCase):
    def setUp(self):
        # 判定會用到 WINDOWS_REPO_PATH;固定住,不依賴執行機器的 LOCALAPPDATA
        self._orig_path = T.WINDOWS_REPO_PATH
        T.WINDOWS_REPO_PATH = Path(WIN_REPO_PATH_STR)

    def tearDown(self):
        T.WINDOWS_REPO_PATH = self._orig_path

    def test_official_url_is_upstream_role(self):
        self.assertEqual(T._role_for_url(OFFICIAL_URL), "upstream")
        self.assertEqual(T._role_for_url(OFFICIAL_URL.upper()), "upstream")

    def test_private_url_is_backup_role(self):
        self.assertEqual(T._role_for_url(PRIVATE_URL), "backup")

    def test_unknown_or_missing_url_is_peer_role(self):
        self.assertEqual(T._role_for_url(None), "peer")
        self.assertEqual(T._role_for_url(""), "peer")
        self.assertEqual(T._role_for_url("https://example.com/someone/hermes-agent.git"),
                         "peer")

    # --- follow role(2026-08-03,提案 §10.1)---------------------------------

    def test_wsl_mount_path_to_windows_repo_is_follow_role(self):
        """WSL 的 /mnt/c/… 指向 Windows hermes-agent → follow(不是 peer)。"""
        self.assertEqual(T._role_for_url(LOCAL_PATH_URL), "follow")

    def test_follow_accepts_equivalent_path_spellings(self):
        for variant in [
            LOCAL_PATH_URL,
            LOCAL_PATH_URL + "/",
            LOCAL_PATH_URL + "/.git",
            LOCAL_PATH_URL.upper(),
            "/mnt/c//Users/razer/AppData/Local/hermes/hermes-agent",
            "C:\\Users\\razer\\AppData\\Local\\hermes\\hermes-agent",
            "C:/Users/razer/AppData/Local/hermes/hermes-agent",
            "file:///C:/Users/razer/AppData/Local/hermes/hermes-agent",
        ]:
            self.assertEqual(T._role_for_url(variant), "follow", variant)

    def test_other_local_paths_are_not_promoted_to_follow(self):
        """**核心否定案例**:是本機路徑,但不是 Windows hermes-agent → 仍是 peer
        (否則任何本機 remote 都會被誤升級成權威基準,§10.1 明列的約束)。"""
        for other in [
            OTHER_LOCAL_URL,
            OTHER_MOUNT_URL,
            "/mnt/c/Users/razer/AppData/Local/hermes/hermes-agent-old",
            "/mnt/c/Users/razer/AppData/Local/hermes",
            "/mnt/c/Users/other/AppData/Local/hermes/hermes-agent",
            "../hermes-agent",
            "/mnt/c/Users/razer/AppData/Local/hermes/hermes-agent/subdir",
        ]:
            self.assertEqual(T._role_for_url(other), "peer", other)

    def test_network_urls_are_never_follow(self):
        """網路型 URL(含 scp 形式 SSH 簡寫)一律不是本機路徑。"""
        for url in [
            "https://github.com/NousResearch/hermes-agent.git",
            "git@github.com:razer/hermes-agent.git",
            "ssh://host/mnt/c/Users/razer/AppData/Local/hermes/hermes-agent",
            "https://host/mnt/c/Users/razer/AppData/Local/hermes/hermes-agent",
        ]:
            self.assertFalse(T._is_windows_repo_url(url), url)

    def test_follow_impossible_when_localappdata_unset(self):
        T.WINDOWS_REPO_PATH = None
        self.assertFalse(T._is_windows_repo_url(LOCAL_PATH_URL))
        self.assertEqual(T._role_for_url(LOCAL_PATH_URL), "peer")

    def test_official_and_backup_markers_win_over_path_matching(self):
        """順序固定:URL 標記優先,follow 不會蓋掉 upstream/backup 判定。"""
        self.assertEqual(T._role_for_url(OFFICIAL_URL), "upstream")
        self.assertEqual(T._role_for_url(PRIVATE_URL), "backup")

    def test_canonical_path_key_never_raises_on_junk(self):
        for junk in [None, "", "   ", "\\\\wsl.localhost\\Ubuntu\\home\\x", "::::",
                     "/mnt/", "/mnt/c", "c:", "\x00", "file://", "a@b:"]:
            T._canonical_path_key(junk)  # 只要不噴例外即可
            self.assertIsInstance(T._role_for_url(junk if isinstance(junk, str) else None), str)

    def test_mount_root_maps_to_drive_root(self):
        self.assertEqual(T._canonical_path_key("/mnt/c/x/y"), "c:/x/y")
        self.assertEqual(T._canonical_path_key("D:\\x\\y"), "d:/x/y")
        # 非 /mnt/ 開頭的 posix 路徑維持原樣(不會被誤當成磁碟機)
        self.assertEqual(T._canonical_path_key("/home/razer/x"), "/home/razer/x")


# ---------------------------------------------------------------------------
# 雙基準:Windows(關鍵回歸——只比 origin 會誤報綠)
# ---------------------------------------------------------------------------

class WindowsDualBasisTests(UpdateProbeTestCase):
    def test_windows_real_state_backup_green_upstream_orange_overall_orange(self):
        self._install(FakeGit(win=WIN_REAL))
        end = T._probe_windows()
        backup = self._group(end, "backup")
        upstream = self._group(end, "upstream")

        # origin(私有備份)= 0/0 → 綠(防重演基準有效)
        self.assertEqual(backup["remote"], "origin")
        self.assertEqual((backup["behind"], backup["ahead"]), (0, 0))
        self.assertEqual(backup["light"], "green")

        # upstream(官方)= 落後 16 / 領先 12 → 橙 diverged
        self.assertEqual(upstream["remote"], "upstream")
        self.assertEqual((upstream["behind"], upstream["ahead"]), (16, 12))
        self.assertEqual(upstream["light"], "orange")
        self.assertIn("16", upstream["summary"])
        self.assertIn("12", upstream["summary"])
        self.assertIn("不可自動", upstream["summary"])

        # 整體 = 較嚴重者 = 橙,且標示由 upstream 組造成
        self.assertEqual(end["light"], "orange")
        self.assertEqual(end["overall_driver"], "upstream")
        # 建議文字兩組併陳(備份健康 + 官方有新版)
        self.assertIn("備份同步", end["advice"])
        self.assertIn("官方有 16 個新 commit", end["advice"])

    def test_regression_origin_only_would_misreport_green(self):
        """關鍵回歸鎖定:origin 0/0 但官方落後 16 時,整體**不得**為綠。"""
        self._install(FakeGit(win=WIN_REAL))
        end = T._probe_windows()
        self.assertNotEqual(end["light"], "green",
                            "只比 origin 會誤報『已最新』——必須由 upstream 組拉成橙")

    def test_both_bases_synced_is_green(self):
        win = repo_responses(
            remotes=("origin", "upstream"), refs="rescue/x c1 commit",
            per_remote={
                "origin": {"url": PRIVATE_URL, "tip": "a1", "behind": 0, "ahead": 0,
                           "ancestor_rc": 0},
                "upstream": {"url": OFFICIAL_URL, "tip": "a1", "behind": 0, "ahead": 3,
                             "ancestor_rc": 1, "log": "c1 custom"},
            })
        self._install(FakeGit(win=win))
        end = T._probe_windows()
        self.assertEqual(self._group(end, "upstream")["light"], "green")
        self.assertEqual(end["light"], "green")
        self.assertIn("已吸收官方最新", end["advice"])

    def test_upstream_behind_without_custom_is_blue_but_custom_loss_wins(self):
        win = repo_responses(
            remotes=("origin", "upstream"), refs="rescue/x c1 commit",
            per_remote={
                "origin": {"url": PRIVATE_URL, "tip": "a1", "behind": 0, "ahead": 0,
                           "ancestor_rc": 0},
                "upstream": {"url": OFFICIAL_URL, "tip": "b2", "behind": 7, "ahead": 0,
                             "ancestor_rc": 0},
            })
        self._install(FakeGit(win=win))
        end = T._probe_windows()
        up = self._group(end, "upstream")
        self.assertEqual(up["light"], "blue")
        self.assertTrue(up["can_ff"])
        self.assertIn("ff-only", up["summary"])
        # expect_custom=True 且 upstream ahead==0 → 客製遺失偵測 → 紅(優先於 blue)
        self.assertEqual(end["light"], "red")
        self.assertTrue(any("純上游" in r for r in end["blocking_reasons"]))

    def test_upstream_remote_missing_group_not_applicable(self):
        win = repo_responses(
            remotes=("origin",), refs="rescue/x c1 commit",
            per_remote={"origin": {"url": PRIVATE_URL, "tip": "a1", "behind": 0,
                                   "ahead": 0, "ancestor_rc": 0}})
        self._install(FakeGit(win=win))
        end = T._probe_windows()
        self.assertIsNone(self._group(end, "upstream"), "無官方 remote → 無 upstream 組")
        self.assertEqual(end["light"], "green")  # 只剩 backup 組
        self.assertEqual(end["overall_driver"], "backup")

    def test_upstream_ref_missing_is_not_applicable_gray(self):
        win = repo_responses(
            remotes=("origin", "upstream"), refs="rescue/x c1 commit",
            per_remote={
                "origin": {"url": PRIVATE_URL, "tip": "a1", "behind": 0, "ahead": 0,
                           "ancestor_rc": 0},
                "upstream": {"url": OFFICIAL_URL, "tip": None},  # 本地無 upstream/main
            })
        self._install(FakeGit(win=win))
        end = T._probe_windows()
        up = self._group(end, "upstream")
        self.assertFalse(up["applicable"])
        self.assertEqual(up["light"], "gray")
        self.assertIn("不適用", up["summary"])
        self.assertEqual(end["light"], "gray")  # gray 比 green 嚴重

    def test_backup_behind_local_is_orange(self):
        win = repo_responses(
            remotes=("origin", "upstream"), refs="rescue/x c1 commit",
            per_remote={
                "origin": {"url": PRIVATE_URL, "tip": "old", "behind": 0, "ahead": 4,
                           "ancestor_rc": 1, "log": "c1 x"},
                "upstream": {"url": OFFICIAL_URL, "tip": "u1", "behind": 0, "ahead": 9,
                             "ancestor_rc": 1, "log": "c1 x"},
            })
        self._install(FakeGit(win=win))
        end = T._probe_windows()
        backup = self._group(end, "backup")
        self.assertEqual(backup["light"], "orange")
        self.assertIn("尚未推送", backup["summary"])
        self.assertEqual(end["overall_driver"], "backup")

    def test_dirty_tree_is_red_regardless_of_bases(self):
        win = dict(WIN_REAL)
        win[T.GIT_PORCELAIN] = (0, " M hermes_cli/main.py")
        self._install(FakeGit(win=win))
        end = T._probe_windows()
        self.assertEqual(end["light"], "red")
        self.assertEqual(end["overall_driver"], "target")

    def test_rescue_refs_lost_is_red(self):
        win = dict(WIN_REAL)
        win[T.GIT_REFS] = (0, "main 970118870 commit")
        self._install(FakeGit(win=win))
        end = T._probe_windows()
        self.assertEqual(end["light"], "red")
        self.assertTrue(any("rescue" in r for r in end["blocking_reasons"]))

    def test_repo_missing_is_gray(self):
        self._install(FakeGit(win={}))
        end = T._probe_windows()
        self.assertEqual(end["light"], "gray")
        self.assertFalse(end["queryable"])


# ---------------------------------------------------------------------------
# 雙基準:WSL(remote 結構與 Windows 不同;含不喚醒鐵律)
# ---------------------------------------------------------------------------

class WslDualBasisTests(UpdateProbeTestCase):
    def test_wsl_origin_is_recognised_as_official_upstream_by_url(self):
        self._install(FakeGit(wsl=WSL_REAL))
        end = T._probe_wsl()
        up = self._group(end, "upstream")
        self.assertIsNotNone(up, "WSL 的 origin 指向官方 → 角色須判為 upstream")
        self.assertEqual(up["remote"], "origin")
        self.assertEqual((up["behind"], up["ahead"]), (160, 11))
        self.assertEqual(up["light"], "orange")
        # windows-side 指向 Windows hermes-agent → follow(2026-08-03 起計入整體燈)
        follow = self._group(end, "follow")
        self.assertEqual(follow["remote"], "windows-side")
        self.assertTrue(follow["counts_toward_overall"])
        # 選項 b:follow 存在 → upstream 降為資訊性(照常輸出,不計入整體燈)
        self.assertFalse(up["counts_toward_overall"])
        self.assertIsNone(self._group(end, "peer"), "已被辨識的基準不應留在 peer")
        # 無 backup 角色(WSL 沒有私有備份 remote)
        self.assertIsNone(self._group(end, "backup"))

    def test_wsl_real_state_is_red_due_to_dirty_tree(self):
        self._install(FakeGit(wsl=WSL_REAL))
        end = T._probe_wsl()
        self.assertEqual(end["light"], "red")
        self.assertTrue(end["dirty"])

    def test_wsl_clean_behind_upstream_no_custom_is_blue(self):
        wsl = repo_responses(
            head="abc", porcelain="", remotes=("origin",), refs="main abc commit",
            per_remote={"origin": {"url": OFFICIAL_URL, "tip": "def", "behind": 5,
                                   "ahead": 0, "ancestor_rc": 0}})
        self._install(FakeGit(wsl=wsl))
        end = T._probe_wsl()
        self.assertEqual(end["light"], "blue")
        self.assertEqual(end["overall_driver"], "upstream")
        self.assertIn("ff-only", end["advice"])

    def test_wsl_distro_stopped_never_runs_wsl_dash_d(self):
        data_resident._distro_state = lambda: (False, "distro 狀態:Stopped")
        fake = self._install(FakeGit(wsl=WSL_REAL))
        end = T._probe_wsl()
        self.assertEqual(end["light"], "gray")
        self.assertEqual(fake.wsl_calls, [], "distro Stopped 時不得執行任何 wsl -d git 指令")
        self.assertTrue(any("避免喚醒" in r for r in end["blocking_reasons"]))

    def test_wsl_distro_unknown_is_gray_no_wsl_dash_d(self):
        data_resident._distro_state = lambda: (None, "wsl 指令不存在或逾時")
        fake = self._install(FakeGit(wsl=WSL_REAL))
        self.assertEqual(T._probe_wsl()["light"], "gray")
        self.assertEqual(fake.wsl_calls, [])

    def test_wsl_repo_missing_is_gray(self):
        self._install(FakeGit(wsl={}))
        end = T._probe_wsl()
        self.assertEqual(end["light"], "gray")
        self.assertFalse(end["queryable"])

    def test_no_recognisable_basis_is_gray(self):
        # 唯一的 remote 是「本機路徑但不是 Windows hermes-agent」→ peer → 無基準
        wsl = repo_responses(
            head="abc", remotes=("some-peer",), refs="main abc commit",
            per_remote={"some-peer": {"url": OTHER_LOCAL_URL, "tip": "abc",
                                      "behind": 0, "ahead": 0, "ancestor_rc": 0}})
        self._install(FakeGit(wsl=wsl))
        end = T._probe_wsl()
        self.assertEqual(self._group(end, "peer")["remote"], "some-peer")
        self.assertEqual(end["light"], "gray")
        self.assertIn("無可用的比較基準", end["advice"])


# ---------------------------------------------------------------------------
# follow role:WSL 有沒有跟上 Windows 整合 tip(提案 §10.1,2026-08-03)
# ---------------------------------------------------------------------------

class FollowRoleTests(UpdateProbeTestCase):
    """re-graft 後 WSL 的 origin = 本機 Windows 路徑。三種狀態 + 否定案例。"""

    def _wsl(self, **kw) -> dict:
        self._install(FakeGit(wsl=wsl_post_regraft(**kw)))
        return T._probe_wsl()

    def test_in_sync_with_windows_tip_is_green_and_counts(self):
        end = self._wsl()
        follow = self._group(end, "follow")
        self.assertEqual(follow["remote"], "origin")
        self.assertEqual(follow["role_label"], T.ROLE_LABEL["follow"])
        self.assertTrue(follow["counts_toward_overall"], "follow 組必須計入整體燈")
        self.assertEqual(follow["light"], "green")
        self.assertIn("已跟上", follow["summary"])
        # 選項 b:upstream 降為資訊性 → WSL 整體燈 = follow 燈 = **綠**
        # (官方組雖為橙,但那是跟隨者的預期常態,不再驅動整體燈)
        self.assertEqual(self._group(end, "upstream")["light"], "orange")
        self.assertEqual(end["light"], "green")
        self.assertEqual(end["overall_driver"], "follow")
        self.assertTrue(end["advice"].startswith("已跟上 Windows 整合 tip"))

    def test_behind_windows_tip_is_blue_should_sync(self):
        end = self._wsl(follow_behind=5, follow_ancestor_rc=0)
        follow = self._group(end, "follow")
        self.assertEqual(follow["light"], "blue", "落後 = 該同步了(資訊態,非異常)")
        self.assertTrue(follow["can_ff"])
        self.assertIn("落後 Windows 整合 tip 5 個 commit", follow["summary"])
        self.assertIn("該同步了", follow["summary"])
        # 選項 b:整體燈隨 follow 三態 → 藍
        self.assertEqual(end["light"], "blue")
        self.assertEqual(end["overall_driver"], "follow")

    def test_ahead_of_windows_tip_is_orange_anomaly(self):
        """語意與 upstream 組相反:領先 = 異常(WSL 不該有 Windows 沒有的 commit)。"""
        end = self._wsl(follow_ahead=2, follow_ancestor_rc=1)
        follow = self._group(end, "follow")
        self.assertEqual(follow["light"], "orange")
        self.assertIn("領先 Windows 整合 tip 2 個 commit", follow["summary"])
        self.assertIn("不該有 Windows 沒有的 commit", follow["summary"])
        self.assertEqual(follow["diverge_commits"], ["abc123 wsl-only commit"])
        # 選項 b:整體燈隨 follow 三態 → 橙
        self.assertEqual(end["light"], "orange")
        self.assertEqual(end["overall_driver"], "follow")

    def test_diverged_from_windows_tip_is_orange_anomaly(self):
        end = self._wsl(follow_behind=3, follow_ahead=2, follow_ancestor_rc=1)
        follow = self._group(end, "follow")
        self.assertEqual(follow["light"], "orange")
        self.assertIn("雙向分歧", follow["summary"])
        self.assertIn("不該有 Windows 沒有的 commit", follow["summary"])
        self.assertEqual(end["light"], "orange")
        self.assertEqual(end["overall_driver"], "follow")

    # --- 選項 b(2026-08-03 拍板):follow 存在 → upstream 降為資訊性 ----------

    def test_option_b_upstream_demoted_when_follow_present(self):
        """有 follow 組的 target:upstream 不計入整體燈,但資訊照常輸出。"""
        end = self._wsl()
        up = self._group(end, "upstream")
        self.assertFalse(up["counts_toward_overall"], "follow 存在 → upstream 降為資訊性")
        # 資訊不打折:數字/summary 照常輸出(UI 標「僅供參考」)
        self.assertEqual((up["behind"], up["ahead"]), (295, 12))
        self.assertEqual(up["light"], "orange")
        self.assertIn("不可自動", up["summary"])
        # advice 只併陳計入組 → 不含官方組的常態橙訊息
        self.assertNotIn("官方有", end["advice"])

    def test_option_b_upstream_counts_when_no_follow_group(self):
        """反向:無 follow 組的 target(拓撲回退)→ upstream 照常計入。
        規則跟著 remote 拓撲走,不是硬編 target id——WSL 若改指雲端即自動回退。"""
        wsl = repo_responses(
            head="abc", porcelain="", remotes=("origin",), refs="main abc commit",
            per_remote={"origin": {"url": OFFICIAL_URL, "tip": "def", "behind": 5,
                                   "ahead": 0, "ancestor_rc": 0}})
        self._install(FakeGit(wsl=wsl))
        end = T._probe_wsl()
        up = self._group(end, "upstream")
        self.assertTrue(up["counts_toward_overall"], "無 follow → upstream 照常計入")
        self.assertEqual(end["light"], "blue")
        self.assertEqual(end["overall_driver"], "upstream")

    def test_option_b_windows_end_upstream_still_counts(self):
        """Windows 端無 follow remote(全網路 URL)→ 完全不受選項 b 影響。"""
        self._install(FakeGit(win=WIN_REAL))
        end = T._probe_windows()
        up = self._group(end, "upstream")
        self.assertTrue(up["counts_toward_overall"])
        self.assertEqual(end["light"], "orange")
        self.assertEqual(end["overall_driver"], "upstream")

    def test_option_b_backup_not_demoted_by_follow(self):
        """backup 是防重演基準,任何 target 都計入——不因 follow 存在而降級。
        (WSL 理論上不會有 backup remote,此為防禦性案例。)"""
        wsl = repo_responses(
            head="abc", porcelain="", remotes=("origin", "bak"), refs="main abc commit",
            per_remote={
                "origin": {"url": LOCAL_PATH_URL, "tip": "abc", "behind": 0,
                           "ahead": 0, "ancestor_rc": 0},
                "bak": {"url": PRIVATE_URL, "tip": "old", "behind": 0, "ahead": 4,
                        "ancestor_rc": 1, "log": "c1 x"},
            })
        self._install(FakeGit(wsl=wsl))
        end = T._probe_wsl()
        backup = self._group(end, "backup")
        self.assertTrue(backup["counts_toward_overall"], "backup 不受 follow 降級影響")
        self.assertEqual(end["light"], "orange")  # backup 橙 > follow 綠
        self.assertEqual(end["overall_driver"], "backup")

    def test_option_b_demotion_applies_even_when_follow_not_applicable(self):
        """邊角:follow 組存在但 ref 不存在(灰)→ upstream 仍降級,整體 = 灰。
        「最該監控的一條查不到」須以灰示警,不得讓 upstream 的常態橙蓋過去。"""
        wsl = repo_responses(
            head="abc", porcelain="", remotes=("origin", "upstream"), refs="main abc commit",
            per_remote={
                "origin": {"url": LOCAL_PATH_URL, "tip": None},  # follow ref 不存在
                "upstream": {"url": OFFICIAL_URL, "tip": "def", "behind": 295,
                             "ahead": 12, "ancestor_rc": 1, "log": "abc custom"},
            })
        self._install(FakeGit(wsl=wsl))
        end = T._probe_wsl()
        self.assertFalse(self._group(end, "upstream")["counts_toward_overall"])
        self.assertEqual(self._group(end, "follow")["light"], "gray")
        self.assertEqual(end["light"], "gray")
        self.assertEqual(end["overall_driver"], "follow")

    # --- §10.1 的核心回歸:這條以前完全不亮燈 --------------------------------

    def test_regression_follow_anomaly_now_drives_overall_light(self):
        """官方組健康(綠)時,WSL 領先 Windows 整合 tip 必須把整體拉成橙。

        修正前 follow 被判 peer(counts_toward_overall=False)→ 整體會是**綠**,
        「WSL 有沒有跟上 Windows」完全不亮燈(提案 §10.1 描述的盲點)。"""
        end = self._wsl(follow_ahead=2, follow_ancestor_rc=1,
                        upstream_behind=0, upstream_ahead=12)
        self.assertEqual(self._group(end, "upstream")["light"], "green")
        self.assertEqual(end["light"], "orange", "follow 組的異常必須計入整體燈")
        self.assertEqual(end["overall_driver"], "follow")
        self.assertIn("領先 Windows 整合 tip", end["advice"])

    def test_negative_other_local_path_stays_peer_and_does_not_light_up(self):
        """**否定案例**:本機路徑但不是 Windows hermes-agent → 仍是 peer、不計入
        整體燈。證明 follow 的升級是綁定身分判定,不是「只要是本機路徑」。"""
        end = self._wsl(follow_url=OTHER_LOCAL_URL, follow_ahead=2,
                        follow_ancestor_rc=1, upstream_behind=0, upstream_ahead=12)
        self.assertIsNone(self._group(end, "follow"), "非 Windows repo 不得升級為 follow")
        peer = self._group(end, "peer")
        self.assertEqual(peer["remote"], "origin")
        self.assertFalse(peer["counts_toward_overall"])
        self.assertEqual(end["light"], "green", "peer 不參與燈號合成(既有行為不變)")
        self.assertEqual(end["overall_driver"], "upstream")

    def test_follow_ref_missing_is_gray_not_applicable(self):
        self._install(FakeGit(wsl=repo_responses(
            head="970118870", porcelain="", remotes=("origin",), refs="main x commit",
            per_remote={"origin": {"url": LOCAL_PATH_URL, "tip": None}})))
        end = T._probe_wsl()
        follow = self._group(end, "follow")
        self.assertFalse(follow["applicable"])
        self.assertEqual(follow["light"], "gray")
        self.assertEqual(end["light"], "gray")

    def test_follow_does_not_change_windows_end_behaviour(self):
        """Windows 端的 remote 都是網路 URL → 不受 follow 判定影響(回歸)。"""
        self._install(FakeGit(win=WIN_REAL))
        end = T._probe_windows()
        self.assertIsNone(self._group(end, "follow"))
        self.assertEqual(end["light"], "orange")
        self.assertEqual(end["overall_driver"], "upstream")


# ---------------------------------------------------------------------------
# _classify_comparison 純函式(每組語意)
# ---------------------------------------------------------------------------

class ClassifyComparisonTests(unittest.TestCase):
    def test_upstream_semantics(self):
        self.assertEqual(T._classify_comparison("upstream", 0, 0)[0], "green")
        self.assertEqual(T._classify_comparison("upstream", 12, 0)[0], "green")
        self.assertEqual(T._classify_comparison("upstream", 0, 16)[0], "blue")
        self.assertEqual(T._classify_comparison("upstream", 12, 16)[0], "orange")

    def test_backup_semantics(self):
        self.assertEqual(T._classify_comparison("backup", 0, 0)[0], "green")
        self.assertEqual(T._classify_comparison("backup", 3, 0)[0], "orange")
        self.assertEqual(T._classify_comparison("backup", 0, 3)[0], "blue")
        self.assertEqual(T._classify_comparison("backup", 3, 3)[0], "orange")

    def test_follow_semantics_are_the_mirror_image_of_upstream(self):
        # (ahead, behind) → light。落後 = 藍(該同步);領先/分歧 = 橙(異常)
        self.assertEqual(T._classify_comparison("follow", 0, 0)[0], "green")
        self.assertEqual(T._classify_comparison("follow", 0, 5)[0], "blue")
        self.assertEqual(T._classify_comparison("follow", 2, 0)[0], "orange")
        self.assertEqual(T._classify_comparison("follow", 2, 3)[0], "orange")
        # 對照:同樣「領先 2、無落後」在 upstream 組是綠(帶客製屬健康)
        self.assertEqual(T._classify_comparison("upstream", 2, 0)[0], "green")

    def test_unknown_counts_is_gray(self):
        self.assertEqual(T._classify_comparison("upstream", None, None)[0], "gray")
        self.assertEqual(T._classify_comparison("follow", None, None)[0], "gray")

    def test_counted_roles_and_driver_priority(self):
        self.assertEqual(set(T.COUNTED_ROLES), {"upstream", "backup", "follow"})
        self.assertNotIn("peer", T.COUNTED_ROLES)
        # 打平時 follow 優先被指認為主導組;backup > upstream 維持既有行為
        self.assertGreater(T.DRIVER_PRIORITY["follow"], T.DRIVER_PRIORITY["backup"])
        self.assertGreater(T.DRIVER_PRIORITY["backup"], T.DRIVER_PRIORITY["upstream"])
        self.assertEqual(set(T.ROLE_LABEL), {"upstream", "backup", "follow", "peer"})

    def test_severity_order_used_for_overall(self):
        self.assertLess(T.LIGHT_SEVERITY["green"], T.LIGHT_SEVERITY["blue"])
        self.assertLess(T.LIGHT_SEVERITY["blue"], T.LIGHT_SEVERITY["gray"])
        self.assertLess(T.LIGHT_SEVERITY["gray"], T.LIGHT_SEVERITY["orange"])
        self.assertLess(T.LIGHT_SEVERITY["orange"], T.LIGHT_SEVERITY["red"])


# ---------------------------------------------------------------------------
# 整合、快取、容錯
# ---------------------------------------------------------------------------

class ProbeAndCacheTests(UpdateProbeTestCase):
    def test_probe_lists_two_targets_with_remote_note(self):
        self._install(FakeGit(win=WIN_REAL, wsl=WSL_REAL))
        payload = T._probe()
        self.assertEqual([t["id"] for t in payload["targets"]], ["windows", "wsl"])
        self.assertIn("未執行 fetch", payload["remote_note"])

    def test_get_update_precheck_caches_within_ttl(self):
        count = {"n": 0}
        orig = T._probe

        def counting():
            count["n"] += 1
            return {"checked_at": "now", "remote_note": "x", "stage": "s", "targets": []}

        T._probe = counting
        try:
            T.get_update_precheck()
            T.get_update_precheck()
            self.assertEqual(count["n"], 1, "TTL 內第二次應命中快取")
            ts, payload = T._cache
            T._cache = (ts - T.CACHE_TTL_SECONDS - 1, payload)
            T.get_update_precheck()
            self.assertEqual(count["n"], 2)
        finally:
            T._probe = orig

    def test_unexpected_exception_degrades_to_gray(self):
        orig = T._probe
        T._probe = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            payload = T.get_update_precheck()
        finally:
            T._probe = orig
        self.assertTrue(all(t["light"] == "gray" for t in payload["targets"]))
        self.assertEqual(len(payload["targets"]), 2)


# ---------------------------------------------------------------------------
# live 版本字串(切片 1;提案 §3.1 第一項)——純函式
# ---------------------------------------------------------------------------

class ParseProjectVersionTests(unittest.TestCase):
    def test_reads_version_from_project_section_only(self):
        # PYPROJECT_OK 在 [build-system] 與 [tool.pytest.ini_options] 也各放了
        # 一個 version;只有 [project] 那個才算數。
        self.assertEqual(T._parse_project_version(PYPROJECT_OK), "0.19.0")

    def test_ignores_comments_and_blank_lines(self):
        text = '# comment\n\n[project]\n# version = "9.9.9"\nversion = "1.2.3"\n'
        self.assertEqual(T._parse_project_version(text), "1.2.3")

    def test_single_quotes_accepted(self):
        self.assertEqual(T._parse_project_version("[project]\nversion = '2.0.0'\n"), "2.0.0")

    def test_missing_version_returns_none(self):
        self.assertIsNone(T._parse_project_version('[project]\nname = "x"\n'))

    def test_no_project_section_returns_none(self):
        self.assertIsNone(T._parse_project_version('[tool.poetry]\nversion = "1.0"\n'))

    def test_empty_or_none_returns_none(self):
        self.assertIsNone(T._parse_project_version(None))
        self.assertIsNone(T._parse_project_version(""))

    def test_malformed_input_never_raises(self):
        for junk in ["[[[[", "\x00\x01binary", "version=", "[project]\nversion =\n",
                     "[project]\nversion = \"\"\n", "no newlines at all"]:
            self.assertIsNone(T._parse_project_version(junk), junk)


class LiveVersionStringTests(unittest.TestCase):
    @staticmethod
    def _upstream(**kw):
        base = {"role": "upstream", "merge_base": "3910ab28c9e1", "ahead": 12}
        base.update(kw)
        return [base]

    def test_full_form_matches_proposal_example(self):
        # 提案 §3.1 的範例字串,逐字比對(實測真實 repo 也產出同一句)
        out = T._live_version("970118870", "0.19.0", self._upstream())
        self.assertEqual(out["text"],
                         "v0.19.0 upstream 3910ab28 + local 97011887 (+12 carried commits)")
        self.assertEqual(out["package"], "0.19.0")
        self.assertEqual(out["upstream_base"], "3910ab28")
        self.assertEqual(out["local"], "97011887")
        self.assertEqual(out["carried"], 12)

    def test_sha_truncated_to_eight(self):
        out = T._live_version("c12c64f9e9", "0.18.2", self._upstream(merge_base="abcdef1234567890"))
        self.assertEqual(out["local"], "c12c64f9")
        self.assertEqual(out["upstream_base"], "abcdef12")

    def test_no_carried_commits_omits_suffix(self):
        out = T._live_version("970118870", "0.19.0", self._upstream(ahead=0))
        self.assertEqual(out["text"], "v0.19.0 upstream 3910ab28 + local 97011887")
        self.assertNotIn("carried", out["text"])

    def test_missing_package_version_degrades_gracefully(self):
        out = T._live_version("970118870", None, self._upstream())
        self.assertEqual(out["text"], "upstream 3910ab28 + local 97011887 (+12 carried commits)")
        self.assertIsNone(out["package"])

    def test_no_upstream_group_falls_back_to_local_only(self):
        out = T._live_version("970118870", None, [{"role": "backup", "ahead": 0}])
        self.assertEqual(out["text"], "local 97011887")

    def test_everything_missing_is_none_not_exception(self):
        out = T._live_version(None, None, [])
        self.assertIsNone(out["text"])
        self.assertIsNone(out["local"])

    def test_probe_surfaces_version_on_real_shaped_fixture(self):
        # 端到端:走 _facts_from_prefix,證明 GIT_PYPROJECT + merge-base 有接上
        fake = FakeGit(win=WIN_REAL)
        orig = T.subprocess
        T.subprocess = fake
        try:
            facts = T._facts_from_prefix(("git", "-C", "x"), "x")
        finally:
            T.subprocess = orig
        self.assertEqual(facts["live_version"]["text"],
                         "v0.19.0 upstream 3910ab28 + local 97011887 (+12 carried commits)")

    def test_pyproject_unreadable_degrades_to_no_package(self):
        win = repo_responses(
            head="970118870", remotes=("upstream",), pyproject=None,
            per_remote={"upstream": {"url": OFFICIAL_URL, "tip": "46c7a4076",
                                     "behind": 16, "ahead": 12, "ancestor_rc": 1,
                                     "merge_base": "3910ab28c"}})
        fake = FakeGit(win=win)
        orig = T.subprocess
        T.subprocess = fake
        try:
            facts = T._facts_from_prefix(("git", "-C", "x"), "x")
        finally:
            T.subprocess = orig
        self.assertIsNone(facts["live_version"]["package"])
        self.assertEqual(facts["live_version"]["text"],
                         "upstream 3910ab28 + local 97011887 (+12 carried commits)")


# ---------------------------------------------------------------------------
# 無寫入副作用(行為測試;切片 1 的硬性驗收條件)
# ---------------------------------------------------------------------------

class NoWriteSideEffectTests(UpdateProbeTestCase):
    """不是看原始碼字面,而是**實際跑一輪完整探測**,再逐一檢查跑過的每一條
    指令。新增版本欄位後這條防線特別重要:任何未來「順手」加進來的非白名單
    指令(尤其非 git 的 spawn)都會在這裡當場被抓到。"""

    WRITE_VERBS = {"fetch", "pull", "merge", "reset", "checkout", "clone", "push",
                   "commit", "rebase", "stash", "apply", "cherry-pick", "gc",
                   "prune", "remote-add", "tag", "am", "revert", "restore", "switch"}

    def _all_calls(self) -> list[tuple]:
        fake = self._install(FakeGit(win=WIN_REAL, wsl=WSL_REAL))
        T._probe()
        return fake.calls

    @staticmethod
    def _split(cmd: tuple) -> tuple[tuple, tuple]:
        """回傳 (prefix, git args)。wsl 前綴 7 個元素、Windows 前綴 3 個。"""
        return (cmd[:7], cmd[7:]) if "wsl.exe" in cmd else (cmd[:3], cmd[3:])

    def test_probe_actually_runs_commands(self):
        # 防呆:若探測根本沒跑指令,底下的斷言會空轉而假綠
        self.assertGreater(len(self._all_calls()), 10)

    def test_every_command_is_git_only_no_foreign_spawn(self):
        for cmd in self._all_calls():
            if "wsl.exe" in cmd:
                self.assertEqual(cmd[:5], (T.WSL_BIN, "-d", T.WSL_DISTRO, "--exec", T.GIT_BIN),
                                 f"WSL 呼叫必須是 `wsl -d <distro> --exec git`:{cmd}")
            else:
                self.assertEqual(cmd[0], T.GIT_BIN, f"非 git 的 spawn:{cmd}")

    def test_every_subcommand_is_whitelisted_and_not_a_write_verb(self):
        for cmd in self._all_calls():
            _, args = self._split(cmd)
            self.assertIn(args[0], T.ALLOWED_GIT_SUBCOMMANDS, f"非白名單子指令:{cmd}")
            self.assertNotIn(args[0], self.WRITE_VERBS, f"出現寫入子指令:{cmd}")

    def test_every_args_tuple_traces_back_to_a_frozen_source(self):
        """每一條實際跑過的 args,都必須能對應到凍結模板或凍結建構器的產物
        ——不存在「臨時組出來」的指令。"""
        remotes = set(WIN_REAL[T.GIT_REMOTE_LIST][1].split()) | \
            set(WSL_REAL[T.GIT_REMOTE_LIST][1].split())
        allowed = set(T.FROZEN_GIT_TEMPLATES)
        for builder in T.REF_TEMPLATE_BUILDERS:
            for remote in remotes:
                allowed.add(builder(remote))
        for cmd in self._all_calls():
            _, args = self._split(cmd)
            self.assertIn(args, allowed, f"args 無法對應到凍結來源:{cmd}")

    def test_no_command_touches_the_working_tree_or_network(self):
        """額外的字面防線:跑過的指令裡不得出現任何會改工作區或觸網的旗標。"""
        forbidden = {"--hard", "--autostash", "--force", "-f", "--prune",
                     "--set-upstream", "--rebase", "--write-tree"}
        for cmd in self._all_calls():
            self.assertEqual(set(cmd) & forbidden, set(), f"出現危險旗標:{cmd}")

    def test_version_probe_adds_exactly_one_readonly_call_per_repo(self):
        """版本欄位帶進來的新指令面就是這一條凍結字面,且每個 repo 恰跑一次。"""
        calls = self._all_calls()
        shows = [c for c in calls if self._split(c)[1] == T.GIT_PYPROJECT]
        self.assertEqual(len(shows), 2, "Windows/WSL 各一次 git show HEAD:pyproject.toml")
        for cmd in calls:
            _, args = self._split(cmd)
            if args[0] == "show":
                self.assertEqual(args, T.GIT_PYPROJECT, "show 只能是那條凍結字面")


if __name__ == "__main__":
    unittest.main()
