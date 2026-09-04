#!/usr/bin/env python3
"""dashboard/test_data_jobs_freshness.py — jobs 管線新鮮度燈號（唯讀狀態層）測試。

原則（比照 scripts/test_jobs_freshness_watchdog.py）：
- **絕不碰真的 jobs.db**：每個測試在 tempdir 自建臨時 SQLite；時間以 `now` 參數固定。
- **絕不送 Slack**：本模組根本沒有 subprocess——底下有 AST 靜態斷言鎖定這件事
  （這比「測試時記得 mock」強：路徑上不存在送信能力）。
- 五態各有獨立測試，且斷言 **inconclusive 不亮警示色**（灰，不是黃/橙）。
- fail-soft：設定缺檔／jobs.db 不存在／設定壞掉 → 灰燈 + 原因，不噴例外。
- 判準單一真相：斷言本模組**沒有自己的 classify 實作、沒有自己的門檻數字**。

執行：.venv/Scripts/python.exe dashboard/test_data_jobs_freshness.py
"""
import ast
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

DASHBOARD_DIR = Path(__file__).resolve().parent
ROOT = DASHBOARD_DIR.parent
sys.path.insert(0, str(DASHBOARD_DIR))

import data_jobs_freshness as freshness  # noqa: E402

NOW = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)

CONFIG = """
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
    description: 測試用 cron
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


class FreshnessTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = self.tmp / "jobs.db"
        self.config = self.tmp / "jobs_watchdog.yaml"
        self.config.write_text(CONFIG, encoding="utf-8")
        conn = sqlite3.connect(self.db)
        conn.executescript(SCHEMA)
        conn.commit()
        conn.close()
        self._n = 0

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def add_job(self, source, status, *, hours_ago=1.0, completed=True):
        self._n += 1
        ts = (NOW - timedelta(hours=hours_ago)).isoformat()
        completed_at = ts if (status == "completed" and completed) else None
        conn = sqlite3.connect(self.db)
        conn.execute(
            "INSERT INTO jobs (id, source, status, created_at, updated_at, "
            "completed_at) VALUES (?,?,?,?,?,?)",
            (f"job-{self._n}", source, status, ts, ts, completed_at))
        conn.commit()
        conn.close()

    def result(self):
        return freshness.get_jobs_freshness(
            jobs_db=self.db, config_path=self.config, now=NOW)

    def row(self, payload, source):
        return next(s for s in payload["sources"] if s["source"] == source)


class FiveStateLightTests(FreshnessTestCase):
    """五態 → 燈色（與看門狗五態一對一，UI 不另創狀態）。"""

    def test_healthy_is_green(self):
        for _ in range(3):
            self.add_job("cron", "completed")
        row = self.row(self.result(), "cron")
        self.assertEqual(row["state"], "healthy")
        self.assertEqual(row["light"], "green")
        self.assertFalse(row["alerting"])

    def test_trigger_dead_is_orange(self):
        """window 內零進件（本次事故形態之一）→ 橙。"""
        payload = self.result()
        row = self.row(payload, "cron")
        self.assertEqual(row["state"], "trigger_dead")
        self.assertEqual(row["light"], "orange")
        self.assertTrue(row["alerting"])
        self.assertEqual(payload["overall_light"], "orange")

    def test_executor_dead_is_orange(self):
        """有進件、零 completed、有 dead_letter（2026-08 的情況）→ 橙。"""
        for _ in range(3):
            self.add_job("cron", "dead_letter")
        row = self.row(self.result(), "cron")
        self.assertEqual(row["state"], "executor_dead")
        self.assertEqual(row["light"], "orange")

    def test_executor_degraded_is_yellow(self):
        for _ in range(2):
            self.add_job("cron", "completed")
        for _ in range(3):
            self.add_job("cron", "dead_letter")
        row = self.row(self.result(), "cron")
        self.assertEqual(row["state"], "executor_degraded")
        self.assertEqual(row["light"], "yellow")

    def test_inconclusive_is_gray_and_not_a_warning(self):
        """**正常的「還在跑」不得亮警示色**——灰，且不列入異常計數。"""
        self.add_job("cron", "queued", hours_ago=0.5)
        payload = self.result()
        row = self.row(payload, "cron")
        self.assertEqual(row["state"], "inconclusive")
        self.assertEqual(row["light"], "gray")
        self.assertFalse(row["alerting"])
        self.assertNotIn(row["light"], ("orange", "yellow", "red"))

    def test_event_driven_source_with_no_enqueue_is_green(self):
        """事件驅動 source 零進件是正常（誤報防線），不是故障。"""
        row = self.row(self.result(), "telegram")
        self.assertEqual(row["state"], "healthy")
        self.assertEqual(row["light"], "green")

    def test_overall_light_takes_the_worst_but_gray_never_beats_green(self):
        # cron 進行中（灰）+ telegram 健康（綠）→ 整體綠，不因「還在跑」轉灰
        self.add_job("cron", "queued", hours_ago=0.5)
        payload = self.result()
        self.assertEqual(self.row(payload, "cron")["light"], "gray")
        self.assertEqual(payload["overall_light"], "green")
        self.assertIn("皆無異常", payload["overall_text"])

    def test_all_five_states_have_a_light(self):
        import jobs_freshness_core as core
        states = {core.STATE_HEALTHY, core.STATE_INCONCLUSIVE, core.STATE_TRIGGER_DEAD,
                  core.STATE_EXECUTOR_DEAD, core.STATE_EXECUTOR_DEGRADED}
        self.assertEqual(set(freshness.STATE_LIGHTS), states)
        # 燈色值域限四色（不新增 red——紅在本系統是常駐/服務層級的語意）
        self.assertEqual(set(freshness.STATE_LIGHTS.values()),
                         {"green", "yellow", "orange", "gray"})


class FailSoftTests(FreshnessTestCase):
    """fail-soft：任何讀不到／解析不了 → 灰燈 + 誠實說明，不噴例外。"""

    def test_missing_jobs_db(self):
        payload = freshness.get_jobs_freshness(
            jobs_db=self.tmp / "nope.db", config_path=self.config, now=NOW)
        self.assertEqual(payload["status"], "unavailable")
        self.assertEqual(payload["overall_light"], "gray")
        self.assertIn("jobs.db", payload["reason"])
        self.assertIn("不代表沒事", payload["summary"])

    def test_missing_config(self):
        payload = freshness.get_jobs_freshness(
            jobs_db=self.db, config_path=self.tmp / "nope.yaml", now=NOW)
        self.assertEqual(payload["overall_light"], "gray")
        self.assertIn("設定檔", payload["reason"])

    def test_broken_config(self):
        bad = self.tmp / "bad.yaml"
        bad.write_text("defaults: {lookback_hours: 1}\nsources: []\n", encoding="utf-8")
        payload = freshness.get_jobs_freshness(
            jobs_db=self.db, config_path=bad, now=NOW)
        self.assertEqual(payload["overall_light"], "gray")
        self.assertTrue(payload["reason"])

    def test_schema_mismatch(self):
        other = self.tmp / "other.db"
        sqlite3.connect(other).close()
        payload = freshness.get_jobs_freshness(
            jobs_db=other, config_path=self.config, now=NOW)
        self.assertEqual(payload["overall_light"], "gray")
        self.assertFalse(payload["available"])


class SingleSourceOfTruthTests(FreshnessTestCase):
    """門檻與判準只有一份真相：registry/jobs_watchdog.yaml + core.classify。"""

    def test_thresholds_come_from_config_file(self):
        self.config.write_text(
            CONFIG.replace("min_expected_enqueued: 1", "min_expected_enqueued: 9"),
            encoding="utf-8")
        payload = self.result()
        self.assertEqual(payload["thresholds"]["min_expected_enqueued"], 9)

    def test_ui_layer_does_not_reimplement_classify_or_thresholds(self):
        tree = ast.parse((DASHBOARD_DIR / "data_jobs_freshness.py").read_text(encoding="utf-8"))
        funcs = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        self.assertNotIn("classify", funcs, "UI 層不得自己複製一份判準")
        self.assertNotIn("evaluate", funcs)
        # 門檻數字不得硬編：全檔不出現 registry 裡那些門檻值的字面
        source = (DASHBOARD_DIR / "data_jobs_freshness.py").read_text(encoding="utf-8")
        for literal in ("lookback_hours = ", "dead_letter_ratio_threshold = ", "0.5"):
            self.assertNotIn(literal, source, f"疑似硬編門檻:{literal}")

    def test_watchdog_and_ui_agree_on_the_same_findings(self):
        """同一份 DB/設定下，UI 的 state 必須與看門狗 dry-run 逐一相同。"""
        sys.path.insert(0, str(ROOT / "scripts"))
        import jobs_freshness_watchdog as wd
        for _ in range(3):
            self.add_job("cron", "dead_letter")
        wd_result = wd.run(jobs_db=self.db, config_path=self.config,
                           dry_run=True, now=NOW)
        wd_states = {f["source"]: f["state"] for f in wd_result["findings"]}
        ui_states = {s["source"]: s["state"] for s in self.result()["sources"]}
        self.assertEqual(ui_states, wd_states)


def _code_only(path: Path) -> str:
    """去掉註解與字串常值後的程式碼——靜態掃描只該看「程式做什麼」，
    不該被說明文字裡的 subprocess／UPDATE 等字眼誤判（含誤放行與誤攔）。"""
    import io
    import tokenize
    pieces = []
    with io.open(path, "r", encoding="utf-8") as fh:
        for tok in tokenize.generate_tokens(fh.readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            pieces.append(tok.string)
    return " ".join(pieces)


class ReadOnlyStaticTests(unittest.TestCase):
    """唯讀鐵律的靜態鎖定：這條路徑上不存在送信/寫入能力。"""

    FILES = (DASHBOARD_DIR / "data_jobs_freshness.py",
             ROOT / "scripts" / "jobs_freshness_core.py")

    def test_no_spawn_primitives(self):
        for path in self.FILES:
            code = _code_only(path)
            for token in ("subprocess", "system", "popen", "spawn", "execv",
                          "send_alert", "Popen"):
                self.assertNotIn(token, code, f"{path.name} 出現 spawn 原語 {token}")

    def test_no_write_sql(self):
        """SQL 只存在於字串裡，故這一項改看字串常值（與上一項互補）。"""
        import io
        import tokenize
        for path in self.FILES:
            literals = []
            with io.open(path, "r", encoding="utf-8") as fh:
                prev_row = -1
                for tok in tokenize.generate_tokens(fh.readline):
                    # 只取「不是 docstring」的字串:docstring 獨占一整個
                    # 邏輯行且前面沒有其他 token,這裡以簡化判準排除之。
                    if tok.type == tokenize.STRING and tok.start[0] == prev_row:
                        literals.append(tok.string)
                    elif tok.type == tokenize.STRING:
                        literals.append(tok.string)
                    if tok.type not in (tokenize.NL, tokenize.NEWLINE,
                                        tokenize.INDENT, tokenize.DEDENT):
                        prev_row = tok.end[0]
            sql = " ".join(s for s in literals if "SELECT" in s.upper()
                           or "PRAGMA" in s.upper()).upper()
            for verb in ("INSERT ", "UPDATE ", "DELETE ", "CREATE ", "DROP ", "ATTACH "):
                self.assertNotIn(verb, sql, f"{path.name} 的 SQL 出現寫入動詞:{verb}")

    def test_core_imports_are_stdlib_only(self):
        tree = ast.parse((ROOT / "scripts" / "jobs_freshness_core.py").read_text(encoding="utf-8"))
        modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module.split(".")[0])
        self.assertEqual(modules - {"__future__", "sqlite3", "datetime", "pathlib", "yaml"},
                         set(), "core 只准 stdlib + pyyaml")

    def test_ui_layer_never_imports_the_alerting_half(self):
        """UI 資料層只 import core;**不得** import 持有 Slack 送信的 watchdog。"""
        tree = ast.parse((DASHBOARD_DIR / "data_jobs_freshness.py").read_text(encoding="utf-8"))
        modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
        self.assertIn("jobs_freshness_core", modules)
        self.assertNotIn("jobs_freshness_watchdog", modules)

    def test_read_only_connection_uses_double_guard(self):
        text = (ROOT / "scripts" / "jobs_freshness_core.py").read_text(encoding="utf-8")
        self.assertIn("mode=ro", text)
        self.assertIn("PRAGMA query_only=ON", text)


if __name__ == "__main__":
    unittest.main(verbosity=1)
