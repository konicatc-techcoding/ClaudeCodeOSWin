#!/usr/bin/env python3
"""hermes/test_bridge_scanner.py — v0.1（Stage 2.3；2.4a 加入 cutover 設定化與 watermark）

hermes/bridge_scanner.py 的測試：scan 的安全預設（無參數時 effective since ＝
max(config cutover, watermark)，設定檔缺失 fail loud、絕不默認全掃）、
--since 過濾（含端點）、--all-history 明確全掃、未完結不撈、discovered upsert、
重跑冪等、既有狀態不被重設（硬條件 5）、真實 scan 推進 watermark／dry-run 絕不
推進、dry-run 零寫入、reconcile 目錄回填（frontmatter 與檔名退回兩種對帳路徑）、
processed_path 修正、靜態隔離保證。

隔離保證（沿用 test_bridge_state.py 的 fingerprint 慣例）：
- 全程只用 temp 目錄的 db 與 inbox，絕不觸碰 Hermes 真實 state.db、
  hermes/config/telegram.json、hermes/jobs.db、真實 memory/inbox/。
- setUpModule/tearDownModule 對受保護檔案與目錄做前後 fingerprint 比對。

執行：.venv/Scripts/python.exe hermes/test_bridge_scanner.py
"""
import ast
import contextlib
import hashlib
import io
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bridge_scanner  # noqa: E402（import 時會把 session_adapter 加進 sys.path）
import bridge_state  # noqa: E402
import adapter as adapter_module  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / "hermes" / "state"
REAL_INBOX = ROOT / "memory" / "inbox"
SEED_SQL = (ROOT / "hermes" / "session_adapter" / "tests" / "fixtures"
            / "seed_state_db.sql")

# seed 之外補兩個「完結」session：一個在 cutover 前、一個在 cutover 後。
# epoch 對照：1751000500 ≈ 2025-06-27；1783600000 ≈ 2026-07-09。
EXTRA_SESSIONS_SQL = """
INSERT INTO sessions (id, source, model, started_at, ended_at, end_reason,
                      message_count, title)
VALUES ('sess_old_ended', 'cli', 'test-model',
        1751000000.0, 1751000500.0, 'stop', 1, 'cutover 前完結'),
       ('sess_new_ended', 'telegram', 'test-model',
        1783599000.0, 1783600000.0, 'stop', 1, 'cutover 後完結');
"""

# seed 裡唯一原生完結的 session（ended_at 1783324702.95 ≈ 2026-07-06T15:58Z）
SEED_ENDED_SID = "20260706_155721_18145a"
# seed 裡未完結的兩個 session（ended_at NULL）
UNENDED_SIDS = ("20260630_183709_063b4e40", "20260707_000000_baddata")

CUTOVER = "2026-07-08T00:00:00Z"  # 介於 seed 完結 (07-06) 與 sess_new_ended (07-09)
T1 = "2026-07-10T01:00:00+00:00"
T2 = "2026-07-10T02:00:00+00:00"

# 不可觸碰的真實檔案／目錄（存在與否、mtime、內容都不得被測試改變）
try:
    _REAL_HERMES_STATE = adapter_module.default_state_db_path()
except FileNotFoundError:
    _REAL_HERMES_STATE = None
_PROTECTED = [p for p in [
    bridge_state.DEFAULT_DB_PATH,
    ROOT / "hermes" / "jobs.db",
    ROOT / "hermes" / "config" / "telegram.json",
    _REAL_HERMES_STATE,
] if p is not None]
_snapshot: dict = {}


def _fingerprint(path: Path):
    return (path.stat().st_mtime_ns, path.stat().st_size) if path.exists() else None


def _tree_fingerprint(root: Path):
    if not root.exists():
        return None
    return sorted(
        (str(p.relative_to(root)), p.stat().st_mtime_ns, p.stat().st_size)
        for p in root.rglob("*") if p.is_file()
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def setUpModule():
    _snapshot["protected"] = {p: _fingerprint(p) for p in _PROTECTED}
    _snapshot["state_dir_listing"] = (
        sorted(p.name for p in STATE_DIR.iterdir()) if STATE_DIR.exists() else None
    )
    _snapshot["inbox_tree"] = _tree_fingerprint(REAL_INBOX)


def tearDownModule():
    for p, before in _snapshot["protected"].items():
        assert _fingerprint(p) == before, f"測試不得觸碰 {p}"
    listing = (sorted(p.name for p in STATE_DIR.iterdir())
               if STATE_DIR.exists() else None)
    assert listing == _snapshot["state_dir_listing"], \
        "測試不得在 hermes/state/ 留下任何檔案"
    assert _tree_fingerprint(REAL_INBOX) == _snapshot["inbox_tree"], \
        "測試不得觸碰真實 memory/inbox/"


class ScannerTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.hermes_db = self.tmp / "state.db"
        with contextlib.closing(sqlite3.connect(self.hermes_db)) as conn:
            conn.executescript(SEED_SQL.read_text(encoding="utf-8"))
            conn.executescript(EXTRA_SESSIONS_SQL)
            conn.commit()
        self.bridge_db = self.tmp / "bridge_state.db"
        # 測試自己的政策設定檔（cutover 底線），不依賴 repo 的真實 bridge.yaml
        self.config = self.tmp / "bridge.yaml"
        self.config.write_text(f'cutover: "{CUTOVER}"\n', encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def scan(self, **kwargs):
        kwargs.setdefault("state_db", self.hermes_db)
        kwargs.setdefault("bridge_db", self.bridge_db)
        kwargs.setdefault("config_path", self.config)
        return bridge_scanner.scan(**kwargs)

    def rows(self) -> dict:
        """bridge db 目前所有列（event_id → dict）；db 不存在回空 dict。"""
        if not self.bridge_db.exists():
            return {}
        with contextlib.closing(sqlite3.connect(self.bridge_db)) as conn:
            conn.row_factory = sqlite3.Row
            return {r["event_id"]: dict(r)
                    for r in conn.execute("SELECT * FROM bridge_sessions")}


class TestScanSafetyAndFilters(ScannerTestBase):
    def test_missing_config_fails_loud_never_full_scan(self):
        """硬條件 1（2.4a 形式）：無參數時靠 config cutover 底線；設定檔讀不到
        必須 fail loud，絕不默認全掃。"""
        with self.assertRaises(FileNotFoundError):
            self.scan(config_path=self.tmp / "no_such_bridge.yaml")
        self.assertFalse(self.bridge_db.exists(), "報錯路徑不得建立 bridge db")

    def test_config_without_cutover_field_fails_loud(self):
        bad = self.tmp / "bad.yaml"
        bad.write_text("some_other_key: 1\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.scan(config_path=bad)
        self.assertFalse(self.bridge_db.exists(), "報錯路徑不得建立 bridge db")

    def test_config_with_unparseable_cutover_fails_loud(self):
        bad = self.tmp / "bad.yaml"
        bad.write_text('cutover: "not-a-timestamp"\n', encoding="utf-8")
        with self.assertRaises(ValueError):
            self.scan(config_path=bad)

    def test_repo_bridge_config_is_valid(self):
        """守住版控正本：hermes/config/bridge.yaml 必須存在且 cutover 可解析。"""
        cutover = bridge_scanner.load_cutover(bridge_scanner.DEFAULT_BRIDGE_CONFIG)
        parsed = bridge_scanner._parse_iso_utc(cutover)
        self.assertIsNotNone(parsed.tzinfo,
                             "cutover 必須可解析為 aware UTC 時間"
                             "（值本身是政策層決策，測試不寫死）")

    def test_api_rejects_since_plus_all_history(self):
        with self.assertRaises(ValueError):
            self.scan(since=CUTOVER, all_history=True)

    def test_cli_scan_without_range_flag_is_accepted(self):
        """2.4a 行為變更：無 --since/--all-history 不再是 usage error（exit 2），
        改走 max(config cutover, watermark) 安全預設。"""
        parser = bridge_scanner._build_parser()
        args = parser.parse_args(["scan"])
        self.assertIsNone(args.since)
        self.assertFalse(args.all_history)

    def test_cli_scan_rejects_both_flags(self):
        parser = bridge_scanner._build_parser()
        with contextlib.redirect_stderr(io.StringIO()), \
                self.assertRaises(SystemExit) as ctx:
            parser.parse_args(["scan", "--since", CUTOVER, "--all-history"])
        self.assertEqual(ctx.exception.code, 2)

    def test_since_excludes_sessions_ended_before_cutover(self):
        result = self.scan(since=CUTOVER)
        self.assertEqual(set(self.rows()), {"hermes:sess_new_ended"},
                         "cutover 前完結的 session 不得被撈進來")
        self.assertEqual(result["candidates"], 1)

    def test_since_is_inclusive_at_cutover_instant(self):
        exact = datetime.fromtimestamp(
            1783600000.0, tz=timezone.utc).isoformat()  # sess_new_ended 的 ended_at
        self.scan(since=exact)
        self.assertIn("hermes:sess_new_ended", self.rows(),
                      "--since 是含端點的 cutover（ended_at >= since）")

    def test_all_history_scans_all_ended_sessions(self):
        self.scan(all_history=True)
        self.assertEqual(set(self.rows()), {
            f"hermes:{SEED_ENDED_SID}",
            "hermes:sess_old_ended",
            "hermes:sess_new_ended",
        })

    def test_unended_sessions_never_scanned(self):
        self.scan(all_history=True)
        rows = self.rows()
        for sid in UNENDED_SIDS:
            self.assertNotIn(f"hermes:{sid}", rows,
                             "ended_at NULL（未完結）的 session 不得被撈")

    def test_bad_since_value_raises(self):
        with self.assertRaises(ValueError):
            self.scan(since="not-a-timestamp")


class TestScanWrites(ScannerTestBase):
    def test_new_session_recorded_as_discovered(self):
        self.scan(since=CUTOVER, seen_at=T1)
        rec = bridge_state.get_session_state(
            "hermes:sess_new_ended", db_path=self.bridge_db)
        self.assertEqual(rec["import_status"], "discovered")
        self.assertEqual(rec["session_id"], "sess_new_ended")
        self.assertEqual(rec["source_profile"], "default")
        self.assertEqual(rec["session_source"], "telegram")
        self.assertEqual(rec["memory_type"], "none")
        self.assertIs(rec["useful_chat"], False)
        self.assertEqual(rec["retry_count"], 0)
        self.assertEqual(rec["first_seen_at"], T1)
        self.assertEqual(rec["last_seen_at"], T1)
        self.assertIn("尚未判定", rec["decision_reason"])

    def test_rerun_is_idempotent_and_touches_last_seen(self):
        self.scan(all_history=True, seen_at=T1)
        result2 = self.scan(all_history=True, seen_at=T2)
        rows = self.rows()
        self.assertEqual(len(rows), 3, "重跑不得產生重複記錄")
        rec = rows["hermes:sess_new_ended"]
        self.assertEqual(rec["first_seen_at"], T1, "first_seen_at 不得被重跑洗掉")
        self.assertEqual(rec["last_seen_at"], T2)
        self.assertEqual(rec["import_status"], "discovered")
        self.assertTrue(all(a["action"] == "touch_last_seen"
                            for a in result2["actions"]))

    def test_existing_terminal_statuses_never_reset(self):
        """硬條件 5 直接測試：imported/failed 既有狀態不得被 scan 重設。"""
        bridge_state.init_db(self.bridge_db)
        bridge_state.upsert_session_state(
            session_id=SEED_ENDED_SID, source_profile="default",
            session_source="cli", import_status="imported",
            memory_type="semantic", useful_chat=True,
            decision_reason="已由 consolidation 整併",
            imported_inbox_path="memory/inbox/x.md",
            processed_path="memory/inbox/.processed/x.md",
            seen_at=T1, db_path=self.bridge_db)
        bridge_state.upsert_session_state(
            session_id="sess_new_ended", source_profile="default",
            session_source="telegram", import_status="failed",
            memory_type="none", useful_chat=False,
            decision_reason="前次匯入失敗", error_reason="adapter 逾時",
            seen_at=T1, db_path=self.bridge_db)

        self.scan(all_history=True, seen_at=T2)

        imported = bridge_state.get_session_state(
            f"hermes:{SEED_ENDED_SID}", db_path=self.bridge_db)
        self.assertEqual(imported["import_status"], "imported",
                         "scan 不得把 imported 重設回 discovered")
        self.assertEqual(imported["memory_type"], "semantic")
        self.assertIs(imported["useful_chat"], True)
        self.assertEqual(imported["decision_reason"], "已由 consolidation 整併")
        self.assertEqual(imported["processed_path"],
                         "memory/inbox/.processed/x.md")
        self.assertEqual(imported["first_seen_at"], T1)
        self.assertEqual(imported["last_seen_at"], T2)
        self.assertEqual(imported["updated_at"], T1,
                         "touch 不是狀態變更，updated_at 不得動")

        failed = bridge_state.get_session_state(
            "hermes:sess_new_ended", db_path=self.bridge_db)
        self.assertEqual(failed["import_status"], "failed",
                         "scan 不得把 failed 重設回 discovered")
        self.assertEqual(failed["error_reason"], "adapter 逾時")
        self.assertEqual(failed["retry_count"], 0,
                         "scan 不是 re-attempt，不得動 retry_count")
        self.assertEqual(failed["last_seen_at"], T2)

    def test_dry_run_creates_nothing_when_db_absent(self):
        result = self.scan(since=CUTOVER, dry_run=True)
        self.assertFalse(self.bridge_db.exists(),
                         "dry-run 不得建立 bridge db 檔案")
        self.assertTrue(result["dry_run"])
        self.assertEqual([a["action"] for a in result["actions"]],
                         ["insert_discovered"])

    def test_dry_run_on_existing_db_changes_no_rows(self):
        self.scan(all_history=True, seen_at=T1)
        before = self.rows()
        result = self.scan(all_history=True, dry_run=True, seen_at=T2)
        self.assertEqual(self.rows(), before, "dry-run 不得改動任何列")
        self.assertTrue(all(a["action"] == "touch_last_seen"
                            for a in result["actions"]))
        self.assertEqual(len(result["actions"]), 3)

    def test_source_state_db_bytes_unchanged_after_scan(self):
        before = _sha256(self.hermes_db)
        self.scan(all_history=True)
        self.assertEqual(_sha256(self.hermes_db), before,
                         "scan 只能經 snapshot 讀取，來源 db 一個 byte 都不能動")


class TestScanEffectiveSinceAndWatermark(ScannerTestBase):
    """Stage 2.4a：無參數時 effective since ＝ max(config cutover, watermark)；
    真實 scan 推進 watermark（只前進）、dry-run 絕不推進。"""

    WM_NEWER = "2026-07-09T18:00:00+00:00"  # > CUTOVER 也 > 所有 ended_at
    WM_OLDER = "2026-07-01T00:00:00+00:00"  # < CUTOVER

    def _set_watermark(self, value: str):
        bridge_state.init_db(self.bridge_db)
        bridge_state.advance_scan_watermark(value, db_path=self.bridge_db)

    def test_no_args_uses_config_cutover_when_no_watermark(self):
        """情境 1：只有 cutover（無 watermark）→ 下界＝config cutover。"""
        result = self.scan(seen_at=T1)
        self.assertEqual(result["since_source"], "config cutover")
        self.assertEqual(result["effective_since"], "2026-07-08T00:00:00+00:00")
        self.assertEqual(set(self.rows()), {"hermes:sess_new_ended"},
                         "cutover 前完結的 session 不得被撈進來")

    def test_no_args_watermark_newer_than_cutover_wins(self):
        """情境 2：watermark 較新 → 下界＝watermark（增量掃描）。"""
        self._set_watermark(self.WM_NEWER)
        result = self.scan(seen_at=T1)
        self.assertEqual(result["since_source"], "bridge_meta watermark")
        self.assertEqual(result["effective_since"], self.WM_NEWER)
        self.assertEqual(result["candidates"], 0,
                         "watermark 之前完結的 session 都不該再撈")

    def test_no_args_cutover_newer_than_watermark_wins(self):
        """情境 3：cutover 較新（例如 db 重建後又人工掃過舊區間）→ 下界＝cutover
        ——cutover 是絕對底線，自動掃描絕不越過它往前掃 pre-bridge 歷史。"""
        self._set_watermark(self.WM_OLDER)
        result = self.scan(seen_at=T1)
        self.assertEqual(result["since_source"], "config cutover")
        self.assertEqual(result["effective_since"], "2026-07-08T00:00:00+00:00")
        self.assertEqual(set(self.rows()), {"hermes:sess_new_ended"})

    def test_real_scan_advances_watermark_to_window_upper_bound(self):
        before_scan = datetime.now(timezone.utc)
        result = self.scan(since=CUTOVER)
        wm = bridge_state.get_scan_watermark(self.bridge_db)
        self.assertIsNotNone(wm, "真實 scan 成功必須推進 watermark")
        self.assertEqual(result["watermark_after"], wm)
        wm_dt = bridge_scanner._parse_iso_utc(wm)
        self.assertGreaterEqual(wm_dt, before_scan.replace(microsecond=0),
                                "watermark ＝ 本次掃描窗口上界（snapshot 前時間戳）")
        self.assertLessEqual(wm_dt, datetime.now(timezone.utc))

    def test_dry_run_never_advances_watermark(self):
        # db 不存在：dry-run 連 db 檔都不建立，也就沒有 watermark
        result = self.scan(dry_run=True)
        self.assertFalse(self.bridge_db.exists())
        self.assertIsNone(result["watermark_after"])
        # db 已存在且有 watermark：dry-run 後 watermark 原封不動
        self._set_watermark(self.WM_OLDER)
        result = self.scan(dry_run=True)
        self.assertEqual(result["watermark_before"], self.WM_OLDER)
        self.assertEqual(result["watermark_after"], self.WM_OLDER)
        self.assertEqual(bridge_state.get_scan_watermark(self.bridge_db),
                         self.WM_OLDER, "dry-run 絕不推進 watermark")

    def test_manual_since_over_old_range_never_moves_watermark_backwards(self):
        """真實 scan 一律嘗試 advance；只前進語義保證人工掃舊區間
        不會把 watermark 往回拉。"""
        self.scan(since=CUTOVER)
        wm1 = bridge_state.get_scan_watermark(self.bridge_db)
        self.scan(since="2020-01-01T00:00:00Z")  # 人工覆蓋掃很舊的區間
        wm2 = bridge_state.get_scan_watermark(self.bridge_db)
        self.assertGreaterEqual(bridge_scanner._parse_iso_utc(wm2),
                                bridge_scanner._parse_iso_utc(wm1))

    def test_no_args_rescan_is_incremental_and_idempotent(self):
        """重疊/後續窗口重掃冪等：第一次 no-args 掃到新 session 並推進
        watermark；第二次 no-args 用 watermark 當下界（增量），不重複記錄、
        不重設既有狀態、first_seen_at 不被洗掉。"""
        self.scan(seen_at=T1)
        first = self.rows()
        self.assertEqual(set(first), {"hermes:sess_new_ended"})
        result2 = self.scan(seen_at=T2)
        self.assertEqual(result2["since_source"], "bridge_meta watermark")
        self.assertEqual(result2["candidates"], 0,
                         "watermark 已越過所有 ended_at，重掃是增量、零候選")
        rows = self.rows()
        self.assertEqual(len(rows), 1, "重掃不得產生重複記錄")
        rec = rows["hermes:sess_new_ended"]
        self.assertEqual(rec["import_status"], "discovered")
        self.assertEqual(rec["first_seen_at"], T1, "first_seen_at 不得被重掃洗掉")


def _frontmatter_file(sid: str, event_id_range: str, session_source: str) -> str:
    return ("---\n"
            "schema: claudecodeos.inbox.v1\n"
            "source: hermes-session\n"
            f"session_id: {sid}\n"
            f'event_id_range: "{event_id_range}"\n'
            "created_at: 2026-07-09T00:00:00Z\n"
            "usefulness: pending\n"
            "sensitivity: pending\n"
            "---\n\n"
            f"# Hermes session 匯入 — {sid}\n\n"
            f"- 來源：hermes/{session_source}\n\n內容\n")


class ReconcileTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.inbox = self.tmp / "inbox"
        self.processed = self.inbox / ".processed"
        self.failed = self.inbox / ".failed"
        for d in (self.inbox, self.processed, self.failed):
            d.mkdir(parents=True)
        self.bridge_db = self.tmp / "bridge_state.db"

        # A：inbox 本層、有 frontmatter → to_inbox
        self.fa = self.inbox / "hermes_session_sessA.md"
        self.fa.write_text(_frontmatter_file("sessA", "hermes:sessA:1..9",
                                             "telegram"), encoding="utf-8")
        # B：.processed/、有 frontmatter → imported（依據 frontmatter）
        self.fb = self.processed / "hermes_session_sessB.md"
        self.fb.write_text(_frontmatter_file("sessB", "hermes:sessB:10..20",
                                             "cli"), encoding="utf-8")
        # C：.processed/、舊時間戳檔名、無 frontmatter → imported（退回檔名比對）
        self.fc = self.processed / "20260101T000000Z_hermes_session_sessC.md"
        self.fc.write_text("# Hermes session 匯入 — sessC\n\n- 來源：hermes/tui\n",
                           encoding="utf-8")
        # D：.failed/、無 frontmatter → failed
        self.fd = self.failed / "hermes_session_sessD.md"
        self.fd.write_text("匯入失敗的殘骸\n", encoding="utf-8")
        # E：非 hermes session 的 inbox 檔案 → 不對帳、不寫記錄
        self.fe = self.inbox / "2026-07-03T00-00-00Z-project-status.md"
        self.fe.write_text("來源：手動測試種子\n內容：專案狀態\n", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def reconcile(self, **kwargs):
        kwargs.setdefault("inbox_dir", self.inbox)
        kwargs.setdefault("bridge_db", self.bridge_db)
        return bridge_scanner.reconcile(**kwargs)


class TestReconcile(ReconcileTestBase):
    def test_backfills_statuses_from_directory_truth(self):
        result = self.reconcile(seen_at=T1)

        rec_a = bridge_state.get_session_state("hermes:sessA",
                                               db_path=self.bridge_db)
        self.assertEqual(rec_a["import_status"], "to_inbox")
        self.assertEqual(rec_a["imported_inbox_path"], str(self.fa.resolve()))
        self.assertEqual(rec_a["event_id_range"], "hermes:sessA:1..9")
        self.assertEqual(rec_a["session_source"], "telegram")
        self.assertIn("frontmatter", rec_a["decision_reason"])
        self.assertIs(rec_a["useful_chat"], False)

        rec_b = bridge_state.get_session_state("hermes:sessB",
                                               db_path=self.bridge_db)
        self.assertEqual(rec_b["import_status"], "imported")
        self.assertEqual(rec_b["processed_path"], str(self.fb.resolve()))
        self.assertEqual(rec_b["imported_inbox_path"],
                         str((self.inbox / self.fb.name).resolve()))
        self.assertEqual(rec_b["event_id_range"], "hermes:sessB:10..20")
        self.assertIs(rec_b["useful_chat"], True,
                      "已被 consolidation 整併＝事實上有用")
        self.assertIn("frontmatter", rec_b["decision_reason"])

        rec_c = bridge_state.get_session_state("hermes:sessC",
                                               db_path=self.bridge_db)
        self.assertEqual(rec_c["import_status"], "imported")
        self.assertEqual(rec_c["processed_path"], str(self.fc.resolve()))
        self.assertEqual(rec_c["session_source"], "tui")
        self.assertIn("檔名", rec_c["decision_reason"],
                      "無 frontmatter 時退回檔名比對，依據要記錄在 decision_reason")

        rec_d = bridge_state.get_session_state("hermes:sessD",
                                               db_path=self.bridge_db)
        self.assertEqual(rec_d["import_status"], "failed")
        self.assertTrue(rec_d["error_reason"])
        self.assertEqual(rec_d["session_source"], "unknown")

        self.assertIsNone(bridge_state.get_session_state(
            "hermes:sessE", db_path=self.bridge_db))
        skipped = [a for a in result["actions"]
                   if a["action"] == "skip_unrecognized"]
        self.assertEqual([Path(a["path"]).name for a in skipped],
                         [self.fe.name], "認不出的檔案只記 skip，不寫記錄")
        self.assertTrue(all(a["action"] != "insert_discovered"
                            for a in result["actions"]),
                        "reconcile 永不產生 discovered")

    def test_consistent_existing_row_only_touched(self):
        bridge_state.init_db(self.bridge_db)
        bridge_state.upsert_session_state(
            session_id="sessB", source_profile="default", session_source="cli",
            import_status="imported", memory_type="semantic", useful_chat=True,
            decision_reason="既有判定（不得被 reconcile 洗掉）",
            imported_inbox_path=str((self.inbox / self.fb.name).resolve()),
            processed_path=bridge_scanner._rel_to_root(self.fb),
            event_id_range="hermes:sessB:10..20",
            seen_at=T1, db_path=self.bridge_db)
        result = self.reconcile(seen_at=T2)
        rec = bridge_state.get_session_state("hermes:sessB",
                                             db_path=self.bridge_db)
        self.assertEqual(rec["import_status"], "imported")
        self.assertEqual(rec["decision_reason"], "既有判定（不得被 reconcile 洗掉）")
        self.assertEqual(rec["memory_type"], "semantic")
        self.assertEqual(rec["first_seen_at"], T1)
        self.assertEqual(rec["last_seen_at"], T2)
        action = next(a for a in result["actions"]
                      if a.get("event_id") == "hermes:sessB")
        self.assertEqual(action["action"], "touch_last_seen")

    def test_processed_path_mismatch_corrected_to_actual(self):
        """既定規則：processed_path 不一致時以 .processed/ 實際位置為準回寫。"""
        bridge_state.init_db(self.bridge_db)
        bridge_state.upsert_session_state(
            session_id="sessB", source_profile="default", session_source="cli",
            import_status="imported", memory_type="semantic", useful_chat=True,
            decision_reason="既有判定",
            imported_inbox_path="memory/inbox/hermes_session_sessB.md",
            processed_path="memory/inbox/.processed/舊的錯誤路徑.md",
            seen_at=T1, db_path=self.bridge_db)
        result = self.reconcile(seen_at=T2)
        rec = bridge_state.get_session_state("hermes:sessB",
                                             db_path=self.bridge_db)
        self.assertEqual(rec["processed_path"],
                         bridge_scanner._rel_to_root(self.fb))
        self.assertEqual(rec["import_status"], "imported")
        self.assertEqual(rec["decision_reason"], "既有判定",
                         "只修 processed_path，不動既有判定")
        self.assertIs(rec["useful_chat"], True)
        self.assertEqual(rec["first_seen_at"], T1)
        action = next(a for a in result["actions"]
                      if a.get("event_id") == "hermes:sessB")
        self.assertEqual(action["action"], "fix_processed_path")

    def test_discovered_row_advances_to_directory_truth(self):
        bridge_state.init_db(self.bridge_db)
        bridge_state.upsert_session_state(
            session_id="sessA", source_profile="default",
            session_source="telegram", import_status="discovered",
            memory_type="none", useful_chat=False,
            decision_reason="scan 發現，尚未判定",
            seen_at=T1, db_path=self.bridge_db)
        self.reconcile(seen_at=T2)
        rec = bridge_state.get_session_state("hermes:sessA",
                                             db_path=self.bridge_db)
        self.assertEqual(rec["import_status"], "to_inbox",
                         "檔案在 inbox 本層＝目錄真相是 to_inbox")
        self.assertEqual(rec["first_seen_at"], T1, "first_seen_at 保持首次值")
        self.assertIn("reconcile", rec["decision_reason"])

    def test_dry_run_writes_nothing(self):
        result = self.reconcile(dry_run=True)
        self.assertFalse(self.bridge_db.exists(),
                         "reconcile dry-run 不得建立 bridge db")
        names = {a["action"] for a in result["actions"]}
        self.assertEqual(names, {"insert_to_inbox", "insert_imported",
                                 "insert_failed", "skip_unrecognized"})

    def test_missing_inbox_dir_raises(self):
        with self.assertRaises(FileNotFoundError):
            self.reconcile(inbox_dir=self.tmp / "no_inbox")


class TestStaticGuarantees(unittest.TestCase):
    """靜態把關：scanner 沒有任何直開 SQLite／immutable 的 code path，
    讀 Hermes db 只能走 adapter 的 snapshot 模式。"""

    @staticmethod
    def _code_only_source(path: Path) -> str:
        src = path.read_text(encoding="utf-8")
        skip_lines: set[int] = set()
        for node in ast.walk(ast.parse(src)):
            if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)):
                skip_lines.update(range(node.lineno, node.end_lineno + 1))
        kept = [
            line.split("#", 1)[0]
            for i, line in enumerate(src.splitlines(), 1)
            if i not in skip_lines
        ]
        return "\n".join(kept)

    def test_scanner_has_no_sqlite_no_immutable_and_forces_snapshot(self):
        code = self._code_only_source(
            Path(__file__).resolve().parent / "bridge_scanner.py")
        self.assertNotIn("sqlite3", code,
                         "scanner 不得自己開 SQLite——一律經 adapter 與 bridge_state")
        self.assertNotIn("immutable", code,
                         "硬條件：immutable 連線的 code path 不可存在")
        self.assertIn("snapshot=True", code,
                      "讀 Hermes state.db 必須走 snapshot 模式")
        self.assertNotIn("snapshot=False", code)
        for forbidden in ("jobs.db", "telegram.json", "LOCALAPPDATA"):
            self.assertNotIn(forbidden, code)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    unittest.main(verbosity=2)
