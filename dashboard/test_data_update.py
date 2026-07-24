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
- **不喚醒**(硬性邊界):WSL distro 未 Running 時完全不下 `wsl -d` 指令。
- 45 秒 TTL 快取;全域容錯(未預期例外 → 兩端灰)。

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
LOCAL_PATH_URL = "/mnt/c/Users/razer/AppData/Local/hermes/hermes-agent"


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


def repo_responses(*, head="970118870", branch="main", describe="v1-g970118870",
                   porcelain="", remotes=(), refs="main 970118870 commit",
                   per_remote=None):
    """組出一個 repo 的完整回應表。per_remote = {name: dict(url=, tip=, behind=,
    ahead=, ancestor_rc=, log=)};tip=None 代表該 <remote>/main ref 不存在。"""
    table = {
        T.GIT_HEAD_SHORT: (0, head),
        T.GIT_BRANCH: (0, branch),
        T.GIT_DESCRIBE: (0, describe),
        T.GIT_PORCELAIN: (0, porcelain),
        T.GIT_REFS: (0, refs),
        T.GIT_REMOTE_LIST: (0, "\n".join(remotes)),
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
        "upstream": {"url": OFFICIAL_URL, "tip": "46c7a4076", "behind": 16, "ahead": 12,
                     "ancestor_rc": 1,
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
        "windows-side": {"url": LOCAL_PATH_URL, "tip": "c12c64f9e9", "behind": 0,
                         "ahead": 0, "ancestor_rc": 0},
    },
)


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
        T.WINDOWS_REPO_PATH = Path("C:/fake/hermes-agent")
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


# ---------------------------------------------------------------------------
# 角色判定(依 URL,不依名稱)
# ---------------------------------------------------------------------------

class RoleDetectionTests(unittest.TestCase):
    def test_official_url_is_upstream_role(self):
        self.assertEqual(T._role_for_url(OFFICIAL_URL), "upstream")
        self.assertEqual(T._role_for_url(OFFICIAL_URL.upper()), "upstream")

    def test_private_url_is_backup_role(self):
        self.assertEqual(T._role_for_url(PRIVATE_URL), "backup")

    def test_local_path_is_peer_role(self):
        self.assertEqual(T._role_for_url(LOCAL_PATH_URL), "peer")
        self.assertEqual(T._role_for_url(None), "peer")


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
        # windows-side = 本機路徑 → peer,不計入整體燈
        peer = self._group(end, "peer")
        self.assertEqual(peer["remote"], "windows-side")
        self.assertFalse(peer["counts_toward_overall"])
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
        wsl = repo_responses(
            head="abc", remotes=("windows-side",), refs="main abc commit",
            per_remote={"windows-side": {"url": LOCAL_PATH_URL, "tip": "abc",
                                         "behind": 0, "ahead": 0, "ancestor_rc": 0}})
        self._install(FakeGit(wsl=wsl))
        end = T._probe_wsl()
        self.assertEqual(end["light"], "gray")
        self.assertIn("無可用的比較基準", end["advice"])


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

    def test_unknown_counts_is_gray(self):
        self.assertEqual(T._classify_comparison("upstream", None, None)[0], "gray")

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


if __name__ == "__main__":
    unittest.main()
