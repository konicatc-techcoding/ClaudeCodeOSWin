#!/usr/bin/env python3
"""dashboard/test_data_jobs_snapshot.py — 快照讀端（資料來源與資料年齡）測試。

核心命題只有一個：**讀到快照時，使用者絕不可以被誤導成「這是即時資料」**。
因此本檔的每一組測試都在問「這種情況下，我們有沒有誠實地說出資料多舊／不可用」：

- runtime db 存在 → live（無年齡問題），且**不會**去看快照。
- 快照新鮮 / 偏舊 / 過期 → 三態各自可辨，年齡文字必定出現。
- 快照**不存在** → never（灰、明說沒資料），不是「沒事」。
- 快照**損壞**（截斷／非 db／沒有 jobs 表） → error，`usable=False`
  ——**不讓壞檔一路噴到 /api/jobs 變成 500**。
- manifest 壞掉／缺 captured_at／檔案與 manifest 對不上 → 一律 error，不臆測年齡。
- 燈號層：偏舊時綠降黃、過期時整體轉灰（不拿舊資料算綠燈）。

一律在 tempdir 造假快照，**絕不碰真實的 %LOCALAPPDATA%\\AgentOS\\jobs-snapshot**。

執行：.venv/Scripts/python.exe dashboard/test_data_jobs_snapshot.py
"""
import ast
import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

DASHBOARD_DIR = Path(__file__).resolve().parent
ROOT = DASHBOARD_DIR.parent
sys.path.insert(0, str(DASHBOARD_DIR))

import data  # noqa: E402
import data_jobs_freshness as freshness  # noqa: E402
import data_jobs_snapshot as snap  # noqa: E402

NOW = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)

SCHEMA = """
CREATE TABLE jobs (
    id TEXT PRIMARY KEY, source TEXT NOT NULL, status TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, completed_at TEXT,
    cost_usd REAL, thread_id TEXT, attempts INTEGER, max_attempts INTEGER
);
"""

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
alert:
  channel: CTEST00000
  send_cli: hermes
  message_key_prefix: agentos-watchdog-test
snapshot:
  fresh_hours: 1.5
  expire_hours: 6
"""


class SnapshotFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.snapshot_dir = self.tmp / "jobs-snapshot"
        self.snapshot_dir.mkdir()
        self.runtime = self.tmp / "runtime-jobs.db"  # 預設不存在（＝Windows 現況）
        self._orig_dir = snap.SNAPSHOT_DIR
        self._orig_config = snap._CONFIG_PATH
        snap.SNAPSHOT_DIR = self.snapshot_dir
        self.config = self.tmp / "jobs_watchdog.yaml"
        self.config.write_text(CONFIG, encoding="utf-8")
        snap._CONFIG_PATH = self.config

    def tearDown(self):
        snap.SNAPSHOT_DIR = self._orig_dir
        snap._CONFIG_PATH = self._orig_config
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    # --- helpers ---

    def make_db(self, path: Path, *, jobs=(("cron", "completed", 1.0),)):
        if path.exists():
            path.unlink()
        conn = sqlite3.connect(path)
        conn.executescript(SCHEMA)
        for i, (source, status, hours_ago) in enumerate(jobs):
            ts = (NOW - timedelta(hours=hours_ago)).isoformat()
            conn.execute(
                "INSERT INTO jobs (id, source, status, created_at, updated_at, "
                "completed_at, cost_usd) VALUES (?,?,?,?,?,?,?)",
                (f"job-{i}", source, status, ts, ts,
                 ts if status == "completed" else None, 0.25))
        conn.commit()
        conn.close()
        return path

    def write_snapshot(self, *, age_hours: float, jobs=(("cron", "completed", 1.0),),
                       manifest_extra: dict | None = None, db: bool = True,
                       base: datetime | None = None):
        db_path = self.snapshot_dir / snap.SNAPSHOT_NAME
        if db:
            self.make_db(db_path, jobs=jobs)
        captured = ((base or NOW) - timedelta(hours=age_hours))
        manifest = {
            "schema": "agentos.jobs-snapshot/1",
            "captured_at": captured.isoformat(timespec="seconds"),
            "source_db": "/home/razer/dev/ClaudeCodeOSWin/hermes/jobs.db",
            "snapshot_file": snap.SNAPSHOT_NAME,
            "jobs_count": len(jobs),
        }
        if manifest_extra:
            manifest.update(manifest_extra)
        (self.snapshot_dir / snap.MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        return db_path

    def resolve(self):
        return snap.resolve_jobs_source(runtime_db=self.runtime, now=NOW)


class SourceKindTests(SnapshotFixture):
    def test_runtime_db_wins_and_has_no_age(self):
        """本機真的有 runtime db（WSL 側）→ live，且不去讀快照。"""
        self.make_db(self.runtime)
        self.write_snapshot(age_hours=99)
        info = self.resolve()
        self.assertEqual(info["kind"], "runtime")
        self.assertEqual(info["status"], "live")
        self.assertTrue(info["trusted_for_verdict"])
        self.assertEqual(info["age_hours"], 0.0)
        self.assertEqual(info["db_path"], str(self.runtime))

    def test_fresh_snapshot(self):
        self.write_snapshot(age_hours=0.5)
        info = self.resolve()
        self.assertEqual(info["kind"], "snapshot")
        self.assertEqual(info["status"], "fresh")
        self.assertTrue(info["usable"])
        self.assertTrue(info["trusted_for_verdict"])
        self.assertEqual(info["age_text"], "30 分鐘前")
        self.assertIn("30 分鐘前", info["age_label"])

    def test_stale_snapshot_is_not_trusted_for_verdict(self):
        self.write_snapshot(age_hours=3)
        info = self.resolve()
        self.assertEqual(info["status"], "stale")
        self.assertTrue(info["usable"], "偏舊仍可查詢（Jobs 頁照樣要有資料）")
        self.assertFalse(info["trusted_for_verdict"])
        self.assertIn("3.0 小時前", info["summary"])

    def test_expired_snapshot(self):
        self.write_snapshot(age_hours=30)
        info = self.resolve()
        self.assertEqual(info["status"], "expired")
        self.assertTrue(info["usable"])
        self.assertFalse(info["trusted_for_verdict"])
        self.assertEqual(info["age_text"], "30.0 小時前")

    def test_thresholds_come_from_registry_file(self):
        self.config.write_text(
            CONFIG.replace("fresh_hours: 1.5", "fresh_hours: 9")
                  .replace("expire_hours: 6", "expire_hours: 12"),
            encoding="utf-8")
        self.write_snapshot(age_hours=3)
        self.assertEqual(self.resolve()["status"], "fresh")

    def test_broken_threshold_config_falls_back_to_conservative_defaults(self):
        self.config.write_text("not: [a valid mapping for us", encoding="utf-8")
        self.write_snapshot(age_hours=3)
        info = self.resolve()
        self.assertEqual(info["fresh_hours"], snap.DEFAULT_FRESH_HOURS)
        self.assertEqual(info["status"], "stale")


class MissingAndBrokenTests(SnapshotFixture):
    """快照不存在／損壞——兩者都必須是「明說不可用」，不是靜默的空白。"""

    def test_never_when_nothing_exists(self):
        info = self.resolve()
        self.assertEqual(info["status"], "never")
        self.assertFalse(info["usable"])
        self.assertIsNone(info["captured_at"])
        self.assertIn("找不到", info["summary"])

    def test_db_without_manifest_is_error_not_guessed_age(self):
        self.make_db(self.snapshot_dir / snap.SNAPSHOT_NAME)
        info = self.resolve()
        self.assertEqual(info["status"], "error")
        self.assertFalse(info["usable"])
        self.assertIn("manifest", info["reason"])

    def test_manifest_without_db_file(self):
        self.write_snapshot(age_hours=1, db=False)
        info = self.resolve()
        self.assertEqual(info["status"], "error")
        self.assertFalse(info["usable"])

    def test_corrupt_manifest_json(self):
        self.make_db(self.snapshot_dir / snap.SNAPSHOT_NAME)
        (self.snapshot_dir / snap.MANIFEST_NAME).write_text("{ not json",
                                                            encoding="utf-8")
        info = self.resolve()
        self.assertEqual(info["status"], "error")
        self.assertIn("JSON", info["reason"])

    def test_manifest_without_parsable_captured_at(self):
        self.write_snapshot(age_hours=1, manifest_extra={"captured_at": "昨天下午"})
        info = self.resolve()
        self.assertEqual(info["status"], "error")
        self.assertIn("captured_at", info["reason"])

    def test_corrupt_snapshot_db_is_detected_before_any_query(self):
        """快照檔本身壞掉（截斷／不是 SQLite）→ error + usable=False。

        沒有這一關，壞檔會讓 /api/jobs 在查詢途中噴 DatabaseError → 500。
        """
        self.write_snapshot(age_hours=0.2)
        (self.snapshot_dir / snap.SNAPSHOT_NAME).write_bytes(b"NOT A SQLITE FILE" * 40)
        info = self.resolve()
        self.assertEqual(info["status"], "error")
        self.assertFalse(info["usable"])
        self.assertFalse(info["trusted_for_verdict"])
        self.assertIn("12 分鐘前", info["summary"], "壞掉也要說出它是何時的")

    def test_snapshot_without_jobs_table_is_rejected(self):
        self.write_snapshot(age_hours=0.2)
        target = self.snapshot_dir / snap.SNAPSHOT_NAME
        target.unlink()
        sqlite3.connect(target).close()  # 合法但空的 db
        info = self.resolve()
        self.assertEqual(info["status"], "error")
        self.assertIn("jobs 表", info["reason"])


class DataLayerWiringTests(SnapshotFixture):
    """data.py（Jobs 頁／成本頁／status-counts）確實吃得到快照——這三處
    在 Windows 觀測面本來就一直是空的，快照修好它們是本次改動的附帶目的。"""

    def setUp(self):
        super().setUp()
        self._orig_jobs_db = data.JOBS_DB_PATH
        data.JOBS_DB_PATH = self.runtime  # 不存在＝Windows 現況

    def tearDown(self):
        data.JOBS_DB_PATH = self._orig_jobs_db
        super().tearDown()

    def test_jobs_and_cost_read_the_snapshot(self):
        self.write_snapshot(age_hours=0.5,
                            jobs=(("cron", "completed", 1.0), ("rss", "dead_letter", 2.0)))
        self.assertTrue(data.jobs_db_exists())
        self.assertEqual(data.get_status_counts(), {"completed": 1, "dead_letter": 1})
        self.assertEqual(len(data.get_recent_jobs()), 2)
        self.assertEqual(data.get_cost_summary()["count"], 2)

    def test_corrupt_snapshot_degrades_to_empty_not_exception(self):
        self.write_snapshot(age_hours=0.5)
        (self.snapshot_dir / snap.SNAPSHOT_NAME).write_bytes(b"broken" * 100)
        self.assertFalse(data.jobs_db_exists())
        self.assertEqual(data.get_status_counts(), {})
        self.assertEqual(data.get_recent_jobs(), [])
        self.assertEqual(data.get_cost_summary()["count"], 0)

    def test_jobs_source_exposes_age_for_the_ui(self):
        # data.jobs_source() 不吃注入的 now（正式路徑用實際時間），
        # 所以這裡以「實際現在」為基準造一份 2 小時前的快照。
        self.write_snapshot(age_hours=2, base=datetime.now(timezone.utc))
        info = data.jobs_source()
        self.assertEqual(info["kind"], "snapshot")
        self.assertEqual(info["status"], "stale")
        self.assertEqual(info["age_text"], "2.0 小時前")


class FreshnessVerdictWithDataAgeTests(SnapshotFixture):
    """★ 本次改動的重點：**快照年齡必須進入新鮮度判準**。"""

    def result(self):
        return freshness.get_jobs_freshness(runtime_db_for_test=None,
                                            config_path=self.config, now=NOW) \
            if False else freshness.get_jobs_freshness(
                jobs_db=self.runtime, config_path=self.config, now=NOW)

    def test_fresh_snapshot_keeps_green(self):
        self.write_snapshot(age_hours=0.5, jobs=(("cron", "completed", 1.0),))
        payload = self.result()
        self.assertEqual(payload["data_status"], "fresh")
        self.assertEqual(payload["overall_light"], "green")
        self.assertIn("快照", payload["summary"])

    def test_stale_snapshot_downgrades_green_to_yellow(self):
        """『rss 9 分鐘前成功』若算自 3 小時前的快照，那個結論就是假的。"""
        self.write_snapshot(age_hours=3, jobs=(("cron", "completed", 1.0),))
        payload = self.result()
        self.assertEqual(payload["data_status"], "stale")
        self.assertEqual(payload["overall_light"], "yellow",
                         "偏舊資料不得產生綠燈")
        row = payload["sources"][0]
        self.assertEqual(row["state"], "healthy", "判準本身不變（單一真相）")
        self.assertEqual(row["light"], "yellow")
        self.assertEqual(row["light_before_data_age"], "green")
        self.assertTrue(row["data_stale"])
        self.assertIn("僅供參考", row["state_short"])

    def test_stale_snapshot_keeps_bad_news(self):
        """壞消息不會因為資料舊而失效——橙燈維持（那件事確實發生過）。"""
        self.write_snapshot(age_hours=3, jobs=(("cron", "dead_letter", 1.0),))
        payload = self.result()
        self.assertEqual(payload["overall_light"], "orange")
        self.assertEqual(payload["sources"][0]["state"], "executor_dead")

    def test_expired_snapshot_turns_everything_gray(self):
        self.write_snapshot(age_hours=30, jobs=(("cron", "completed", 1.0),))
        payload = self.result()
        self.assertEqual(payload["data_status"], "expired")
        self.assertEqual(payload["overall_light"], "gray")
        self.assertEqual(payload["overall_text"], "資料過期，無法判斷")
        self.assertEqual(payload["sources"][0]["light"], "gray")
        self.assertEqual(payload["sources"][0]["light_before_data_age"], "green")
        self.assertIn("過期", payload["summary"])

    def test_expired_snapshot_still_names_what_it_saw(self):
        """轉灰不等於閉嘴：當時看到的異常要寫在文字裡（不靠顏色）。"""
        self.write_snapshot(age_hours=30, jobs=(("cron", "dead_letter", 1.0),))
        payload = self.result()
        self.assertEqual(payload["overall_light"], "gray")
        self.assertIn("執行端死了", payload["summary"])

    def test_missing_snapshot_is_unavailable_gray(self):
        payload = self.result()
        self.assertEqual(payload["status"], "unavailable")
        self.assertEqual(payload["overall_light"], "gray")
        self.assertEqual(payload["data_status"], "never")
        self.assertIn("不代表沒事", payload["summary"])

    def test_corrupt_snapshot_is_unavailable_gray(self):
        self.write_snapshot(age_hours=0.5)
        (self.snapshot_dir / snap.SNAPSHOT_NAME).write_bytes(b"x" * 500)
        payload = self.result()
        self.assertEqual(payload["status"], "unavailable")
        self.assertEqual(payload["overall_light"], "gray")
        self.assertEqual(payload["data_status"], "error")

    def test_payload_always_carries_data_age_fields(self):
        """呈現層要能無條件顯示資料年齡——欄位不得在某些分支消失。"""
        cases = [None, 0.5, 3, 30]
        for age in cases:
            if age is not None:
                self.write_snapshot(age_hours=age)
            payload = self.result()
            for key in ("data_source", "data_status", "data_age_text",
                        "data_age_label", "data_captured_at", "data_trusted",
                        "data_summary"):
                self.assertIn(key, payload, f"age={age} 缺少 {key}")


class ReadOnlyStaticTests(unittest.TestCase):
    """唯讀鐵律：讀端不得有任何 spawn 原語或寫入 SQL（它只會讀那份快照）。"""

    def test_no_spawn_primitives(self):
        source = (DASHBOARD_DIR / "data_jobs_snapshot.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module.split(".")[0])
        self.assertEqual(modules - {"json", "os", "sqlite3", "datetime", "pathlib", "yaml"},
                         set(), "讀端只准這幾個模組（不得有 subprocess/shutil）")

    def test_no_write_sql(self):
        source = (DASHBOARD_DIR / "data_jobs_snapshot.py").read_text(encoding="utf-8")
        upper = source.upper()
        for verb in ("INSERT ", "UPDATE ", "DELETE ", "DROP ", "ATTACH ", "VACUUM"):
            self.assertNotIn(verb, upper, f"讀端出現寫入動詞：{verb}")

    def test_read_only_connection_is_double_guarded(self):
        source = (DASHBOARD_DIR / "data_jobs_snapshot.py").read_text(encoding="utf-8")
        self.assertIn("mode=ro", source)
        self.assertIn("PRAGMA query_only=ON", source)


if __name__ == "__main__":
    unittest.main(verbosity=1)
