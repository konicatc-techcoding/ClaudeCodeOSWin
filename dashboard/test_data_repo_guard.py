#!/usr/bin/env python3
"""dashboard/test_data_repo_guard.py — 未推送 commit 離線保險快照的唯讀狀態層測試。

核心測試(批次 1 止血,scripts/repo_guard_bundle.ps1 的觀測面):
- **零副作用鐵律**:原始碼不得出現任何 spawn 原語(subprocess/popen/exec…)
  ——這個端點被打開幾次都不能觸發 guard 執行。靜態鎖定。
- **不搶橙燈**:本模組任何路徑都不得回 "orange"(橙專屬升級預檢的 ahead>0
  「未 push」;兩層語意不得互相打架)。行為 + 靜態雙鎖。
- 三態新鮮度:24h 內 fresh(綠)／逾期 stale(黃)／manifest 不存在 never(灰)。
- fail-soft:JSON 損毀、頂層非物件、createdAt 缺漏/不可解析、讀取權限錯誤
  → 一律灰 + 明確說明,**不噴例外**(比照 data_stage3 的 unknown 慣例)。
- 語意誠實:欄位名為 covered_*(當時保全)而非「目前暴露」,且說明文字必須
  講明「快照不代表目前暴露狀態」。

fixture 一律 tempfile 隔離的假 manifest,不觸碰真實 %LOCALAPPDATA%\\AgentOS。
執行:.venv/Scripts/python.exe dashboard/test_data_repo_guard.py
"""
import ast
import json
import re
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import data_repo_guard  # noqa: E402

DASHBOARD_DIR = Path(__file__).resolve().parent


def _manifest(created: datetime, exposed: int = 3, dirty: int = 0) -> dict:
    return {
        "id": "FAKE-repo",
        "repoPath": "C:/FAKE/repo",
        "createdAt": created.strftime("%Y-%m-%dT%H:%M:%S"),
        "fingerprint": "f" * 64,
        "bundle": "C:/FAKE/store/FAKE-repo-20260101-000000.bundle",
        "bundleBytes": 4096,
        "exposedCommits": exposed,
        "exposedRefs": ["refs/heads/main (+2)", "refs/stash (+1)"],
        "dirtyFiles": dirty,
        "localRefs": ["refs/heads/main abc123"],
        "restore": ["# ..."],
    }


class RepoGuardStatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="repo-guard-test-"))
        self._orig_store = data_repo_guard.GUARD_STORE
        self._orig_targets = data_repo_guard.GUARD_TARGETS
        data_repo_guard.GUARD_STORE = self.tmp
        data_repo_guard.GUARD_TARGETS = (("FAKE-repo", "假 repo（測試用）"),)

    def tearDown(self):
        data_repo_guard.GUARD_STORE = self._orig_store
        data_repo_guard.GUARD_TARGETS = self._orig_targets
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, payload) -> None:
        target_dir = self.tmp / "FAKE-repo"
        target_dir.mkdir(parents=True, exist_ok=True)
        text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        (target_dir / data_repo_guard.MANIFEST_NAME).write_text(text, encoding="utf-8")

    def _only_target(self) -> dict:
        payload = data_repo_guard.get_repo_guard_status()
        self.assertEqual(len(payload["targets"]), 1)
        return payload["targets"][0]

    def test_fresh_within_24h(self):
        self._write(_manifest(datetime.now().astimezone() - timedelta(hours=2)))
        t = self._only_target()
        self.assertEqual(t["status"], "fresh")
        self.assertEqual(t["light"], "green")
        self.assertEqual(t["covered_commits"], 3)
        self.assertEqual(t["covered_refs"], ["refs/heads/main (+2)", "refs/stash (+1)"])
        self.assertLess(t["age_hours"], 24)
        # 語意誠實:必須講明這是快照,不是目前暴露狀態
        self.assertIn("不代表目前暴露狀態", t["summary"])

    def test_stale_beyond_24h(self):
        self._write(_manifest(datetime.now().astimezone() - timedelta(days=9)))
        t = self._only_target()
        self.assertEqual(t["status"], "stale")
        self.assertEqual(t["light"], "yellow")
        self.assertGreater(t["age_hours"], 24)
        self.assertIn("天前", t["age_text"])
        self.assertIn("repo_guard_bundle.ps1", t["summary"])  # 手動重跑指引

    def test_never_run(self):
        t = self._only_target()  # 完全沒寫 manifest
        self.assertEqual(t["status"], "never")
        self.assertEqual(t["light"], "gray")
        self.assertIsNone(t["created_at"])
        self.assertIsNone(t["age_hours"])
        self.assertEqual(t["covered_refs"], [])

    def test_broken_json_fails_soft(self):
        self._write("{ 這不是 JSON")
        t = self._only_target()
        self.assertEqual(t["status"], "error")
        self.assertEqual(t["light"], "gray")
        self.assertIn("JSON", t["summary"])

    def test_non_object_manifest_fails_soft(self):
        self._write("[1, 2, 3]")
        t = self._only_target()
        self.assertEqual(t["status"], "error")
        self.assertEqual(t["light"], "gray")

    def test_unparsable_created_at_fails_soft(self):
        bad = _manifest(datetime.now().astimezone())
        bad["createdAt"] = "not-a-timestamp"
        self._write(bad)
        t = self._only_target()
        self.assertEqual(t["status"], "error")
        self.assertEqual(t["light"], "gray")
        self.assertIsNone(t["age_hours"], "無法判斷新鮮度時不得臆測年齡")

    def test_missing_created_at_fails_soft(self):
        bad = _manifest(datetime.now().astimezone())
        del bad["createdAt"]
        self._write(bad)
        t = self._only_target()
        self.assertEqual(t["status"], "error")
        self.assertEqual(t["light"], "gray")

    def test_dirty_files_noted(self):
        self._write(_manifest(datetime.now().astimezone(), dirty=4))
        t = self._only_target()
        self.assertIn("未提交", t["summary"], "bundle 不涵蓋未提交內容必須講出來")

    def test_no_localappdata_degrades_gray(self):
        data_repo_guard.GUARD_STORE = None
        payload = data_repo_guard.get_repo_guard_status()
        self.assertIsNone(payload["store_root"])
        self.assertEqual(payload["targets"][0]["light"], "gray")
        self.assertEqual(payload["targets"][0]["status"], "error")

    def test_overall_light_takes_worst(self):
        data_repo_guard.GUARD_TARGETS = (("FAKE-repo", "a"), ("FAKE-missing", "b"))
        self._write(_manifest(datetime.now().astimezone()))  # 一綠一灰(never)
        payload = data_repo_guard.get_repo_guard_status()
        self.assertEqual(payload["overall_light"], "gray", "整體燈取最嚴重者")

    def test_payload_shape(self):
        self._write(_manifest(datetime.now().astimezone()))
        payload = data_repo_guard.get_repo_guard_status()
        for key in ("checked_at", "store_root", "fresh_hours", "scheduled",
                    "note", "overall_light", "targets"):
            self.assertIn(key, payload)
        self.assertFalse(payload["scheduled"], "拍板不建排程 → 必須誠實標示無排程")
        self.assertIn("不會觸發 guard 執行", payload["note"])

    def test_never_returns_orange(self):
        """行為鎖:所有情境的燈都不得是 orange(橙專屬預檢的 ahead>0)。"""
        cases = [
            _manifest(datetime.now().astimezone()),
            _manifest(datetime.now().astimezone() - timedelta(days=30)),
            "{壞掉",
        ]
        for case in cases:
            self._write(case)
            payload = data_repo_guard.get_repo_guard_status()
            lights = {payload["overall_light"], *(t["light"] for t in payload["targets"])}
            self.assertNotIn("orange", lights)
            self.assertTrue(lights <= {"green", "yellow", "gray"}, f"未預期的燈色: {lights}")
        shutil.rmtree(self.tmp / "FAKE-repo", ignore_errors=True)
        payload = data_repo_guard.get_repo_guard_status()
        self.assertNotIn("orange", {t["light"] for t in payload["targets"]})


class RepoGuardSourceGuardTests(unittest.TestCase):
    """靜態鎖定:零 spawn 原語、零寫入呼叫、原始碼不含 orange。"""

    _RAW = (DASHBOARD_DIR / "data_repo_guard.py").read_text(encoding="utf-8")
    # 掃描對象排除模組 docstring——說明文字本來就會提到「零 subprocess」這類詞,
    # 掃 docstring 會讓靜態鎖定變成假陽性;要鎖的是**程式碼**沒有 spawn 原語。
    SOURCE = _RAW.replace(ast.get_docstring(ast.parse(_RAW)) or "", "")

    def test_no_spawn_primitives(self):
        for needle in ("subprocess", "os.system", "popen", "Popen", "os.exec",
                       "spawn", "importlib"):
            self.assertNotIn(needle, self.SOURCE,
                             f"data_repo_guard.py 不得出現 spawn 原語:{needle}"
                             "(本端點不可觸發 guard 執行)")

    def test_no_write_calls(self):
        for needle in ("write_text(", "unlink(", "rmtree(", "mkdir(",
                       "touch(", "rename(", "replace("):
            self.assertNotIn(needle, self.SOURCE,
                             f"data_repo_guard.py 出現寫入型呼叫:{needle}(唯讀鐵律)")

    def test_source_has_no_orange(self):
        self.assertFalse(re.search(r'"orange"', self.SOURCE),
                         "橙專屬升級預檢的『未 push』,本模組不得使用")

    def test_reads_only_latest_manifest(self):
        """唯一的檔案讀取路徑必須是 _latest.json(不遞迴掃描 store 目錄)。"""
        self.assertIn("MANIFEST_NAME", self.SOURCE)
        for needle in ("glob(", "rglob(", "iterdir(", "walk("):
            self.assertNotIn(needle, self.SOURCE,
                             f"不得對 store 目錄做掃描:{needle}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
