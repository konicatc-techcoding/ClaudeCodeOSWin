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


# ====================================================================
# Stage 2.4d-2：episode 偵測（scan 擴充）＋ manual checkpoint
# （提案 docs/stage2.4d-episode-capture-proposal.md §5／§6；
#  矩陣 #1/#7/#8/#10/#15-17/#19/#20/#27/#28 的 scanner 相關部分）
# ====================================================================

_EP_SESSIONS_DDL = """
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    user_id TEXT,
    model TEXT,
    started_at REAL NOT NULL,
    ended_at REAL,
    end_reason TEXT,
    message_count INTEGER DEFAULT 0,
    title TEXT,
    session_key TEXT,
    chat_id TEXT,
    chat_type TEXT,
    thread_id TEXT,
    parent_session_id TEXT,
    archived INTEGER NOT NULL DEFAULT 0
);
"""

_EP_MESSAGES_DDL = """
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT,
    tool_call_id TEXT,
    tool_calls TEXT,
    tool_name TEXT,
    timestamp REAL NOT NULL,
    token_count INTEGER,
    finish_reason TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    compacted INTEGER NOT NULL DEFAULT 0
);
"""

# episode_cutover：測試固定值（不用 datetime.now()，避免 flaky）。
EP_CUTOVER_DT = datetime(2026, 7, 10, 0, 0, 0, tzinfo=timezone.utc)
EP_CUTOVER_ISO = EP_CUTOVER_DT.isoformat().replace("+00:00", "Z")
EP_CUTOVER_EPOCH = EP_CUTOVER_DT.timestamp()
HOUR = 3600.0

# scan 的 seen_at（同時也是 _detect_episodes 的「now」參照，決定 inactivity 是否
# 達門檻）——固定值，配合 EP_CUTOVER 與訊息時間戳推算 idle 小時數。
EP_NOW = "2026-07-11T00:00:00+00:00"  # cutover 後 24 小時


class EpisodeScannerTestBase(unittest.TestCase):
    """輕量、自建 schema 的 Hermes state.db（不依賴 seed_state_db.sql），
    專供 episode 偵測測試控制訊息時間戳與 archived/ended_at 欄位。"""

    INACTIVITY_HOURS = 2  # 測試用小門檻，不代表拍板值（拍板值 72 見 bridge.yaml）

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.hermes_db = self.tmp / "state.db"
        with contextlib.closing(sqlite3.connect(self.hermes_db)) as conn:
            conn.executescript(_EP_SESSIONS_DDL)
            conn.executescript(_EP_MESSAGES_DDL)
            conn.commit()
        self.bridge_db = self.tmp / "bridge_state.db"
        self.inbox = self.tmp / "inbox"
        self.processed = self.inbox / ".processed"
        self.failed = self.inbox / ".failed"
        for d in (self.inbox, self.processed, self.failed):
            d.mkdir(parents=True)
        self.config = self.tmp / "bridge.yaml"
        self._write_config(enabled=True, cutover=EP_CUTOVER_ISO,
                          inactivity_hours=self.INACTIVITY_HOURS)

    def tearDown(self):
        self._tmp.cleanup()

    def _write_config(self, *, enabled: bool, cutover, inactivity_hours,
                      legacy_cutover: str = "2020-01-01T00:00:00Z"):
        cutover_line = f'  episode_cutover: "{cutover}"\n' if cutover else "  episode_cutover:\n"
        self.config.write_text(
            f'cutover: "{legacy_cutover}"\n'
            "episodes:\n"
            f"  enabled: {'true' if enabled else 'false'}\n"
            f"{cutover_line}"
            f"  inactivity_hours: {inactivity_hours}\n",
            encoding="utf-8",
        )

    def add_session(self, sid: str, *, source: str = "desktop",
                    started_at: float = EP_CUTOVER_EPOCH - 10 * HOUR,
                    ended_at: float | None = None, archived: bool = False,
                    title: str | None = None):
        with contextlib.closing(sqlite3.connect(self.hermes_db)) as conn:
            conn.execute(
                "INSERT INTO sessions (id, source, started_at, ended_at, "
                "title, archived) VALUES (?, ?, ?, ?, ?, ?)",
                (sid, source, started_at, ended_at, title, int(archived)),
            )
            conn.commit()

    def add_message(self, mid: int, sid: str, role: str, content: str,
                    timestamp: float, active: int = 1):
        with contextlib.closing(sqlite3.connect(self.hermes_db)) as conn:
            conn.execute(
                "INSERT INTO messages (id, session_id, role, content, "
                "timestamp, active) VALUES (?, ?, ?, ?, ?, ?)",
                (mid, sid, role, content, timestamp, active),
            )
            conn.commit()

    def scan(self, **kwargs):
        kwargs.setdefault("state_db", self.hermes_db)
        kwargs.setdefault("bridge_db", self.bridge_db)
        kwargs.setdefault("config_path", self.config)
        kwargs.setdefault("inbox_dir", self.inbox)
        kwargs.setdefault("all_history", True)  # 略過 legacy cutover 計算，聚焦 episode 偵測
        kwargs.setdefault("seen_at", EP_NOW)
        return bridge_scanner.scan(**kwargs)

    def checkpoint(self, session_id: str, **kwargs):
        kwargs.setdefault("state_db", self.hermes_db)
        kwargs.setdefault("bridge_db", self.bridge_db)
        kwargs.setdefault("config_path", self.config)
        kwargs.setdefault("inbox_dir", self.inbox)
        return bridge_scanner.checkpoint(session_id, **kwargs)

    def rows(self) -> dict:
        if not self.bridge_db.exists():
            return {}
        with contextlib.closing(sqlite3.connect(self.bridge_db)) as conn:
            conn.row_factory = sqlite3.Row
            return {r["event_id"]: dict(r)
                    for r in conn.execute("SELECT * FROM bridge_sessions")}

    def episode_rows(self) -> dict:
        return {eid: row for eid, row in self.rows().items()
                if row.get("episode_seq") is not None}

    def cursor(self, sid: str):
        return bridge_state.get_cursor(sid, db_path=self.bridge_db)


class TestEpisodeTriggerEnded(EpisodeScannerTestBase):
    """矩陣 #15：trigger=ended——ended_at 已設（>= episode_cutover）即切，
    不等 inactivity；ended 但無新訊息不切空 episode。"""

    def test_ended_at_after_cutover_cuts_immediately_without_waiting_inactivity(self):
        sid = "sess-ended"
        self.add_session(sid, ended_at=EP_CUTOVER_EPOCH + HOUR)
        self.add_message(1, sid, "user", "hello", EP_CUTOVER_EPOCH + 0.5 * HOUR)
        self.add_message(2, sid, "assistant", "hi", EP_CUTOVER_EPOCH + 0.6 * HOUR)
        # seen_at 很接近訊息時間，inactivity 門檻不可能達到——確認切刀不靠 inactivity
        result = self.scan(seen_at=EP_CUTOVER_ISO)
        eps = self.episode_rows()
        self.assertEqual(len(eps), 1)
        row = next(iter(eps.values()))
        self.assertEqual(row["capture_trigger"], "ended")
        self.assertEqual(row["first_message_id"], 1)
        self.assertEqual(row["last_message_id"], 2)
        self.assertEqual(row["episode_seq"], 1)

    def test_ended_with_no_new_messages_does_not_cut_empty_episode(self):
        sid = "sess-ended-empty"
        self.add_session(sid, ended_at=EP_CUTOVER_EPOCH + HOUR)
        # 沒有任何訊息
        self.scan()
        self.assertEqual(self.episode_rows(), {})

    def test_ended_before_cutover_with_later_message_is_stale_and_uses_inactivity(self):
        """2.4d-2 複核 blocker 修正（原本這裡是「judgment call」，已撤銷）：
        ended_at < cutover 且 session 在 ended 後又有新訊息（cutover 後）——
        這個 ended_at 視為 stale，不得阻擋 inactivity 判斷；達 inactivity
        門檻仍應切出 episode（§0.1 拍板：ended 後復活不加特例、統一規則）。"""
        sid = "sess-ended-precutover"
        self.add_session(sid, ended_at=EP_CUTOVER_EPOCH - HOUR)
        self.add_message(1, sid, "user", "hello", EP_CUTOVER_EPOCH + HOUR)
        self.scan()  # 預設 seen_at=EP_NOW（cutover+24h），遠超 INACTIVITY_HOURS=2
        eps = self.episode_rows()
        self.assertEqual(len(eps), 1)
        row = next(iter(eps.values()))
        self.assertEqual(row["capture_trigger"], "inactivity")
        self.assertEqual(row["first_message_id"], 1)
        self.assertEqual(row["last_message_id"], 1)


class TestEpisodeTriggerInactivity(EpisodeScannerTestBase):
    """矩陣 #16：trigger=inactivity——未達門檻不切、達門檻切、要求 ended_at NULL。"""

    def test_below_threshold_does_not_cut(self):
        sid = "sess-idle-short"
        self.add_session(sid)  # ended_at=None
        last_ts = EP_CUTOVER_EPOCH + HOUR
        self.add_message(1, sid, "user", "hello", last_ts)
        # seen_at 只比最後訊息晚 1 小時（< INACTIVITY_HOURS=2）
        seen_at = datetime.fromtimestamp(last_ts + HOUR, tz=timezone.utc).isoformat()
        self.scan(seen_at=seen_at)
        self.assertEqual(self.episode_rows(), {})

    def test_at_or_above_threshold_cuts(self):
        sid = "sess-idle-long"
        self.add_session(sid)
        last_ts = EP_CUTOVER_EPOCH + HOUR
        self.add_message(1, sid, "user", "hello", last_ts)
        self.add_message(2, sid, "assistant", "hi", last_ts + 60)
        # seen_at 比最後訊息晚 3 小時（>= INACTIVITY_HOURS=2）
        seen_at = datetime.fromtimestamp(last_ts + 3 * HOUR, tz=timezone.utc).isoformat()
        self.scan(seen_at=seen_at)
        eps = self.episode_rows()
        self.assertEqual(len(eps), 1)
        row = next(iter(eps.values()))
        self.assertEqual(row["capture_trigger"], "inactivity")
        self.assertEqual(row["first_message_id"], 1)
        self.assertEqual(row["last_message_id"], 2)


class TestEpisodeTriggerArchived(EpisodeScannerTestBase):
    """矩陣 #28：trigger=archived——level-triggered，立即切、不等 72 小時；
    eligible 為空不切；Unarchive 後新訊息改由其他 trigger 另切一刀
    （fixture 驗證邏輯，非真實 Hermes UI 行為，見提案文首修訂記錄）。"""

    def test_archived_true_cuts_immediately_without_inactivity_wait(self):
        sid = "sess-archived"
        self.add_session(sid, archived=True)  # ended_at=None
        last_ts = EP_CUTOVER_EPOCH + HOUR
        self.add_message(1, sid, "user", "hello", last_ts)
        # seen_at 幾乎緊貼訊息時間——inactivity 條件不可能成立
        seen_at = datetime.fromtimestamp(last_ts + 60, tz=timezone.utc).isoformat()
        self.scan(seen_at=seen_at)
        eps = self.episode_rows()
        self.assertEqual(len(eps), 1)
        self.assertEqual(next(iter(eps.values()))["capture_trigger"], "archived")

    def test_archived_with_no_new_messages_does_not_cut(self):
        sid = "sess-archived-empty"
        self.add_session(sid, archived=True)
        self.scan()
        self.assertEqual(self.episode_rows(), {})

    def test_unarchive_then_new_message_cuts_via_other_trigger_not_archived(self):
        """fixture 模擬 Unarchive（archived 變回 false）之後新增訊息：
        不再滿足 archived 條件，改由 inactivity 另切下一刀；既有 episode 不變。"""
        sid = "sess-unarchive"
        self.add_session(sid, archived=True)
        last_ts = EP_CUTOVER_EPOCH + HOUR
        self.add_message(1, sid, "user", "hello", last_ts)
        seen_at1 = datetime.fromtimestamp(last_ts + 60, tz=timezone.utc).isoformat()
        self.scan(seen_at=seen_at1)
        ep1 = dict(self.episode_rows())
        self.assertEqual(len(ep1), 1)
        self.assertEqual(next(iter(ep1.values()))["capture_trigger"], "archived")

        # Unarchive：archived 變回 false；新增一則訊息
        with contextlib.closing(sqlite3.connect(self.hermes_db)) as conn:
            conn.execute("UPDATE sessions SET archived=0 WHERE id=?", (sid,))
            conn.commit()
        new_ts = last_ts + 2 * HOUR
        self.add_message(2, sid, "user", "回來了", new_ts)
        seen_at2 = datetime.fromtimestamp(new_ts + 3 * HOUR, tz=timezone.utc).isoformat()
        self.scan(seen_at=seen_at2)
        eps = self.episode_rows()
        self.assertEqual(len(eps), 2, "應多切出一個獨立 episode")
        triggers = sorted(row["capture_trigger"] for row in eps.values())
        self.assertEqual(triggers, ["archived", "inactivity"])
        # ep1 內容不變（immutability）
        ep1_row = next(iter(ep1.values()))
        ep1_after = self.rows()[ep1_row["event_id"]]
        self.assertEqual(ep1_after["decision_reason"], ep1_row["decision_reason"])
        self.assertEqual(ep1_after["last_message_id"], 1)


class TestEpisodeStaleEndedRevival(EpisodeScannerTestBase):
    """2.4d-2 複核 blocker 修正：`ended_at` 有值但 session 在 ended 後又有
    更新的訊息（`ended_at < last_message_at`）——這個 ended_at 視為 stale，
    不得阻擋 inactivity／archived 判斷（§0.1 拍板：ended 後復活不加特例、
    統一規則）。舊版只要 ended_at 有值就永遠不再檢查 inactivity，會讓復活
    後的新訊息永久漏擷取；本類別驗證修正後的行為。"""

    def test_ended_after_cutover_but_stale_relative_to_later_message_uses_inactivity(self):
        """ended_at 本身 >= episode_cutover，但比該 session 最後一則訊息舊
        （復活）——仍視為 stale，達 inactivity 門檻照樣切出 inactivity
        episode，boundary 涵蓋 ended 前後所有 eligible 訊息。"""
        sid = "sess-ended-then-revived"
        ended_at = EP_CUTOVER_EPOCH + HOUR
        self.add_session(sid, ended_at=ended_at)
        self.add_message(1, sid, "user", "ended 前", EP_CUTOVER_EPOCH + 0.5 * HOUR)
        self.add_message(2, sid, "user", "復活後的新訊息", ended_at + 2 * HOUR)
        seen_at = datetime.fromtimestamp(
            ended_at + 5 * HOUR, tz=timezone.utc).isoformat()  # 距最後訊息 3h >= 門檻 2h
        self.scan(seen_at=seen_at)
        eps = self.episode_rows()
        self.assertEqual(len(eps), 1)
        row = next(iter(eps.values()))
        self.assertEqual(row["capture_trigger"], "inactivity")
        self.assertEqual(row["first_message_id"], 1)
        self.assertEqual(row["last_message_id"], 2)

    def test_stale_ended_below_inactivity_threshold_does_not_cut(self):
        """stale ended 但尚未達 inactivity 門檻：不切（eligible 存在但還沒
        到 inactivity 時間，不是「有 ended_at 就不切」的舊邏輯）。"""
        sid = "sess-ended-then-revived-recent"
        ended_at = EP_CUTOVER_EPOCH + HOUR
        self.add_session(sid, ended_at=ended_at)
        self.add_message(1, sid, "user", "復活後的新訊息", ended_at + HOUR)
        seen_at = datetime.fromtimestamp(
            ended_at + 1.5 * HOUR, tz=timezone.utc).isoformat()  # 距最後訊息僅 0.5h < 門檻 2h
        self.scan(seen_at=seen_at)
        self.assertEqual(self.episode_rows(), {})

    def test_stale_ended_with_archived_true_uses_archived_trigger_immediately(self):
        """stale-ended + archived=true：archived 優先序不受 ended 是否
        stale 影響，立即切 archived，不等 inactivity 門檻。"""
        sid = "sess-ended-then-revived-archived"
        ended_at = EP_CUTOVER_EPOCH + HOUR
        self.add_session(sid, ended_at=ended_at, archived=True)
        self.add_message(1, sid, "user", "復活後的新訊息", ended_at + HOUR)
        seen_at = datetime.fromtimestamp(
            ended_at + 1.1 * HOUR, tz=timezone.utc).isoformat()  # 遠低於 inactivity 門檻
        self.scan(seen_at=seen_at)
        eps = self.episode_rows()
        self.assertEqual(len(eps), 1)
        self.assertEqual(next(iter(eps.values()))["capture_trigger"], "archived")


class TestEpisodeMultipleAndRerun(EpisodeScannerTestBase):
    """矩陣 #1：多 episode 生成；矩陣 #10：同窗口重跑不重複 create_episode。"""

    def test_two_rounds_produce_adjacent_non_overlapping_episodes(self):
        sid = "sess-multi"
        self.add_session(sid, archived=True)
        self.add_message(1, sid, "user", "第一輪-1", EP_CUTOVER_EPOCH + HOUR)
        self.add_message(2, sid, "assistant", "第一輪-2", EP_CUTOVER_EPOCH + 1.1 * HOUR)
        seen_at1 = datetime.fromtimestamp(
            EP_CUTOVER_EPOCH + 1.2 * HOUR, tz=timezone.utc).isoformat()
        self.scan(seen_at=seen_at1)
        eps1 = self.episode_rows()
        self.assertEqual(len(eps1), 1)
        row1 = next(iter(eps1.values()))
        self.assertEqual((row1["first_message_id"], row1["last_message_id"]), (1, 2))
        self.assertEqual(row1["episode_seq"], 1)

        self.add_message(3, sid, "user", "第二輪-1", EP_CUTOVER_EPOCH + 2 * HOUR)
        self.add_message(4, sid, "assistant", "第二輪-2", EP_CUTOVER_EPOCH + 2.1 * HOUR)
        seen_at2 = datetime.fromtimestamp(
            EP_CUTOVER_EPOCH + 2.2 * HOUR, tz=timezone.utc).isoformat()
        self.scan(seen_at=seen_at2)
        eps2 = self.episode_rows()
        self.assertEqual(len(eps2), 2)
        seqs = sorted(r["episode_seq"] for r in eps2.values())
        self.assertEqual(seqs, [1, 2])
        row2 = next(r for r in eps2.values() if r["episode_seq"] == 2)
        self.assertEqual((row2["first_message_id"], row2["last_message_id"]), (3, 4))
        # boundary 相接不重疊不跳漏
        self.assertEqual(row2["first_message_id"], row1["last_message_id"] + 1)

    def test_rerun_same_window_does_not_duplicate_create_episode(self):
        sid = "sess-rerun"
        self.add_session(sid, archived=True)
        self.add_message(1, sid, "user", "hello", EP_CUTOVER_EPOCH + HOUR)
        seen_at = datetime.fromtimestamp(
            EP_CUTOVER_EPOCH + 1.1 * HOUR, tz=timezone.utc).isoformat()
        self.scan(seen_at=seen_at)
        before = self.episode_rows()
        self.assertEqual(len(before), 1)
        # archived 仍是 true（level-triggered 仍會成立），但 cursor 已推進、
        # 沒有新訊息可切——不得重複 create_episode
        self.scan(seen_at=seen_at)
        after = self.episode_rows()
        self.assertEqual(len(after), 1, "重跑不得產生第二筆 episode 列")
        before_row = next(iter(before.values()))
        after_row = next(iter(after.values()))
        self.assertEqual(before_row["decision_reason"], after_row["decision_reason"])
        self.assertEqual(before_row["updated_at"], after_row["updated_at"])


class TestEpisodePreCutoverGuard(EpisodeScannerTestBase):
    """矩陣 #7：pre-cutover 不湧入。"""

    def test_all_messages_before_cutover_never_scanned(self):
        sid = "sess-precutover"
        self.add_session(sid, archived=True)
        self.add_message(1, sid, "user", "old-1", EP_CUTOVER_EPOCH - 3 * HOUR)
        self.add_message(2, sid, "assistant", "old-2", EP_CUTOVER_EPOCH - 2 * HOUR)
        self.scan()
        self.assertEqual(self.episode_rows(), {},
                         "cutover 前的活動不得被候選過濾撿到（§5.3）")

    def test_mixed_before_and_after_cutover_only_captures_after(self):
        sid = "sess-mixed"
        self.add_session(sid, archived=True)
        self.add_message(1, sid, "user", "old", EP_CUTOVER_EPOCH - HOUR)
        self.add_message(2, sid, "user", "new", EP_CUTOVER_EPOCH + HOUR)
        self.scan()
        eps = self.episode_rows()
        self.assertEqual(len(eps), 1)
        row = next(iter(eps.values()))
        self.assertEqual(row["first_message_id"], 2, "cutover 前的訊息不進 boundary")
        self.assertEqual(row["last_message_id"], 2)


class TestEpisodeCursorMissingGuard(EpisodeScannerTestBase):
    """矩陣 #8：cursor 遺失的 fail-closed 防護。"""

    def test_landed_episode_file_without_cursor_blocks_cut(self):
        sid = "sess-lost-cursor"
        self.add_session(sid, archived=True)
        self.add_message(1, sid, "user", "hello", EP_CUTOVER_EPOCH + HOUR)
        # 模擬「已有 episode 落地檔，但 bridge db（含 cursor）已消失」：
        # 只放一個檔名帶 _ep 段的檔案，不建立任何 cursor 記錄
        (self.inbox / f"hermes_session_{sid}_ep1-1.md").write_text(
            "已落地內容", encoding="utf-8")
        result = self.scan()
        self.assertEqual(self.episode_rows(), {}, "拒切：不得切出與既有 episode 重疊的 boundary")
        blocked = [a for a in result["actions"]
                  if a["action"] == "cursor_missing_needs_reconcile"]
        self.assertEqual(len(blocked), 1)
        self.assertEqual(blocked[0]["session_id"], sid)
        self.assertIsNone(self.cursor(sid), "拒切路徑不得建立 cursor")

    def test_no_cursor_and_no_landed_file_cuts_normally_from_cutover(self):
        sid = "sess-fresh"
        self.add_session(sid, archived=True)
        self.add_message(1, sid, "user", "hello", EP_CUTOVER_EPOCH + HOUR)
        self.scan()
        eps = self.episode_rows()
        self.assertEqual(len(eps), 1, "無落地檔、無 cursor 時應依 cutover 正常首切")


class TestEpisodeConfigGate(EpisodeScannerTestBase):
    """矩陣 #19：config gate——enabled=false/區塊缺失與 2.4c 位元級一致；
    enabled=true 但 episode_cutover 缺失 → fail loud。"""

    def test_enabled_false_produces_zero_episodes_even_with_eligible_activity(self):
        self._write_config(enabled=False, cutover=EP_CUTOVER_ISO, inactivity_hours=2)
        sid = "sess-gate-off"
        self.add_session(sid, archived=True)
        self.add_message(1, sid, "user", "hello", EP_CUTOVER_EPOCH + HOUR)
        result = self.scan()
        self.assertEqual(self.episode_rows(), {})
        self.assertFalse(result["episodes_enabled"])
        self.assertEqual(result["episode_actions"], 0)

    def test_missing_episodes_block_behaves_like_2_4c(self):
        self.config.write_text('cutover: "2020-01-01T00:00:00Z"\n', encoding="utf-8")
        sid = "sess-no-block"
        self.add_session(sid, archived=True)
        self.add_message(1, sid, "user", "hello", EP_CUTOVER_EPOCH + HOUR)
        result = self.scan()
        self.assertEqual(self.episode_rows(), {})
        self.assertFalse(result["episodes_enabled"])

    def test_enabled_true_missing_cutover_fails_loud(self):
        self._write_config(enabled=True, cutover=None, inactivity_hours=2)
        with self.assertRaises(ValueError):
            self.scan()
        self.assertFalse(self.bridge_db.exists(),
                         "fail loud 路徑不得建立 bridge db")

    def test_load_episodes_config_directly(self):
        cfg = bridge_scanner.load_episodes_config(self.config)
        self.assertTrue(cfg["enabled"])
        self.assertEqual(cfg["episode_cutover"], EP_CUTOVER_ISO)
        self.assertEqual(cfg["inactivity_hours"], self.INACTIVITY_HOURS)


class TestEpisodeProfileFailClosed(EpisodeScannerTestBase):
    """矩陣 #27：非 default profile fail-closed（scanner 半部）。"""

    def test_non_default_profile_with_episodes_enabled_fails_loud(self):
        with self.assertRaises(ValueError):
            self.scan(source_profile="nemocoding")
        self.assertFalse(self.bridge_db.exists())

    def test_non_default_profile_with_episodes_disabled_is_allowed(self):
        self._write_config(enabled=False, cutover=EP_CUTOVER_ISO, inactivity_hours=2)
        # 不該 raise——episodes 關閉時非 default profile 不受這條限制
        result = self.scan(source_profile="nemocoding", all_history=True)
        self.assertEqual(result["episode_actions"], 0)


class TestCheckpointManualTrigger(EpisodeScannerTestBase):
    """矩陣 #17：trigger=manual——立即切、無視門檻；無新訊息 exit 0 不切；
    --dry-run 零寫入；不觸發 importer（本函式只建立 discovered episode 列）。"""

    def test_manual_cuts_immediately_ignoring_all_thresholds(self):
        sid = "sess-manual"
        self.add_session(sid)  # 無 ended_at、無 archived，且剛剛才有新訊息
        self.add_message(1, sid, "user", "現在就切這段", EP_CUTOVER_EPOCH + HOUR)
        result = self.checkpoint(sid)
        self.assertEqual(result["action"], "create_episode")
        self.assertTrue(result["created"])
        row = self.episode_rows()[result["event_id"]]
        self.assertEqual(row["capture_trigger"], "manual")
        self.assertEqual(row["import_status"], "discovered",
                         "manual 只建立 discovered，不觸發 importer")

    def test_no_new_messages_reports_not_error(self):
        sid = "sess-manual-empty"
        self.add_session(sid)
        result = self.checkpoint(sid)
        self.assertEqual(result["action"], "no_new_messages")
        self.assertFalse(result["created"])

    def test_dry_run_writes_nothing(self):
        sid = "sess-manual-dry"
        self.add_session(sid)
        self.add_message(1, sid, "user", "hello", EP_CUTOVER_EPOCH + HOUR)
        result = self.checkpoint(sid, dry_run=True)
        self.assertEqual(result["action"], "create_episode")
        self.assertFalse(self.bridge_db.exists(), "dry-run 不得建立 bridge db")

    def test_second_checkpoint_only_captures_new_messages(self):
        sid = "sess-manual-twice"
        self.add_session(sid)
        self.add_message(1, sid, "user", "第一段", EP_CUTOVER_EPOCH + HOUR)
        first = self.checkpoint(sid)
        self.assertEqual(first["action"], "create_episode")
        self.add_message(2, sid, "user", "第二段", EP_CUTOVER_EPOCH + 2 * HOUR)
        second = self.checkpoint(sid)
        self.assertEqual(second["action"], "create_episode")
        self.assertEqual(second["first_message_id"], 2)
        self.assertEqual(second["last_message_id"], 2)
        self.assertEqual(second["episode_seq"], 2)

    def test_missing_cutover_without_cursor_fails_loud(self):
        self._write_config(enabled=False, cutover=None, inactivity_hours=2)
        sid = "sess-manual-nocutover"
        self.add_session(sid)
        self.add_message(1, sid, "user", "hello", EP_CUTOVER_EPOCH + HOUR)
        with self.assertRaises(ValueError):
            self.checkpoint(sid)

    def test_cursor_missing_guard_applies_to_checkpoint_too(self):
        sid = "sess-manual-lostcursor"
        self.add_session(sid)
        self.add_message(1, sid, "user", "hello", EP_CUTOVER_EPOCH + HOUR)
        (self.inbox / f"hermes_session_{sid}_ep1-1.md").write_text(
            "已落地內容", encoding="utf-8")
        result = self.checkpoint(sid)
        self.assertEqual(result["action"], "cursor_missing_needs_reconcile")

    def test_unknown_session_raises(self):
        with self.assertRaises(ValueError):
            self.checkpoint("no-such-session")


class TestCheckpointCli(EpisodeScannerTestBase):
    """checkpoint CLI 子指令：exit code 與 --dry-run。"""

    def _run_cli(self, *extra_args):
        argv = ["--bridge-db", str(self.bridge_db), "checkpoint",
               *extra_args, "--state-db", str(self.hermes_db),
               "--config", str(self.config), "--inbox", str(self.inbox)]
        return bridge_scanner._cli(argv)

    def test_cli_success_exit_zero(self):
        sid = "sess-cli-manual"
        self.add_session(sid)
        self.add_message(1, sid, "user", "hello", EP_CUTOVER_EPOCH + HOUR)
        self.assertEqual(self._run_cli(sid), 0)

    def test_cli_no_new_messages_exit_zero(self):
        sid = "sess-cli-empty"
        self.add_session(sid)
        self.assertEqual(self._run_cli(sid), 0)

    def test_cli_cursor_missing_guard_exit_nonzero(self):
        sid = "sess-cli-lostcursor"
        self.add_session(sid)
        self.add_message(1, sid, "user", "hello", EP_CUTOVER_EPOCH + HOUR)
        (self.inbox / f"hermes_session_{sid}_ep1-1.md").write_text(
            "已落地內容", encoding="utf-8")
        self.assertEqual(self._run_cli(sid), 1)

    def test_cli_dry_run_flag(self):
        sid = "sess-cli-dry"
        self.add_session(sid)
        self.add_message(1, sid, "user", "hello", EP_CUTOVER_EPOCH + HOUR)
        self.assertEqual(self._run_cli(sid, "--dry-run"), 0)
        self.assertFalse(self.bridge_db.exists())


# ====================================================================
# Stage 2.4d-3：reconcile 的 episode-aware 回填＋cursor recovery
# （提案 docs/stage2.4d-episode-capture-proposal.md §2／§3.2；
#  矩陣 #18/#21/#22/#27（reconcile 側 profile fail-closed））
# ====================================================================

def _episode_inbox_text(sid: str, first: int, last: int, episode_seq: int,
                        capture_trigger: str | None, session_source: str = "cli") -> str:
    """手寫的 episode inbox 檔內容（frontmatter 格式對齊
    adapter._render_frontmatter 的實際輸出，不經 adapter 產生——保持測試
    自成一體、不依賴 Hermes db fixture）。

    capture_trigger=None：完全省略該 frontmatter 行（模擬 2.4d-3 複核修正
    測試情境 A「episode 檔缺 capture_trigger」）；帶值時原樣寫入（包含
    合法值以外的字串，模擬情境 B「非法值」，不做任何驗證——reconcile 才是
    受測對象）。"""
    event_id_range = f"hermes:{sid}:{first}..{last}"
    trigger_line = f"capture_trigger: {capture_trigger}\n" if capture_trigger is not None else ""
    return ("---\n"
            "schema: claudecodeos.inbox.v1\n"
            "source: hermes-session\n"
            f"session_id: {sid}\n"
            f'event_id_range: "{event_id_range}"\n'
            f"episode: {episode_seq}\n"
            f"{trigger_line}"
            "created_at: 2026-07-09T00:00:00Z\n"
            "usefulness: pending\n"
            "sensitivity: pending\n"
            "---\n\n"
            f"# Hermes session 匯入 — {sid}\n\n"
            f"- 來源：hermes/{session_source}\n\n內容\n")


class ReconcileEpisodeTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.inbox = self.tmp / "inbox"
        self.processed = self.inbox / ".processed"
        self.failed = self.inbox / ".failed"
        for d in (self.inbox, self.processed, self.failed):
            d.mkdir(parents=True)
        self.bridge_db = self.tmp / "bridge_state.db"

    def tearDown(self):
        self._tmp.cleanup()

    def _write_episode_file(self, directory: Path, sid: str, first: int, last: int,
                            episode_seq: int, capture_trigger: str | None = "inactivity",
                            session_source: str = "cli") -> Path:
        name = adapter_module.HermesSessionAdapter.episode_inbox_filename(
            sid, first, last)
        path = directory / name
        path.write_text(
            _episode_inbox_text(sid, first, last, episode_seq, capture_trigger,
                                session_source),
            encoding="utf-8")
        return path

    def reconcile(self, **kwargs):
        kwargs.setdefault("inbox_dir", self.inbox)
        kwargs.setdefault("bridge_db", self.bridge_db)
        return bridge_scanner.reconcile(**kwargs)

    def rows(self) -> dict:
        if not self.bridge_db.exists():
            return {}
        with contextlib.closing(sqlite3.connect(self.bridge_db)) as conn:
            conn.row_factory = sqlite3.Row
            return {r["event_id"]: dict(r)
                    for r in conn.execute("SELECT * FROM bridge_sessions")}

    def cursor(self, sid: str):
        return bridge_state.get_cursor(sid, db_path=self.bridge_db)


class TestReconcileEpisodeAware(ReconcileEpisodeTestBase):
    """矩陣 #18：reconcile episode-aware 回填。"""

    def test_processed_episode_file_backfills_imported_episode_row(self):
        self._write_episode_file(self.processed, "sess-a", 1, 5, episode_seq=1,
                                 capture_trigger="archived")
        result = self.reconcile(seen_at=T1)
        eid = "hermes:sess-a:1..5"
        rows = self.rows()
        self.assertIn(eid, rows)
        row = rows[eid]
        self.assertEqual(row["import_status"], "imported")
        self.assertEqual(row["episode_seq"], 1)
        self.assertEqual(row["capture_trigger"], "archived")
        self.assertEqual(row["first_message_id"], 1)
        self.assertEqual(row["last_message_id"], 5)
        self.assertIsNone(row["source_content_hash"],
                          "hash 無法自檔案還原，回填 NULL（提案 §3.2）")
        action = next(a for a in result["actions"] if a.get("event_id") == eid)
        self.assertEqual(action["action"], "insert_episode_imported")

    def test_to_inbox_episode_file_backfills_to_inbox(self):
        self._write_episode_file(self.inbox, "sess-b", 10, 20, episode_seq=2,
                                 capture_trigger="inactivity")
        self.reconcile()
        row = self.rows()["hermes:sess-b:10..20"]
        self.assertEqual(row["import_status"], "to_inbox")

    def test_failed_episode_file_backfills_failed(self):
        self._write_episode_file(self.failed, "sess-c", 1, 3, episode_seq=1,
                                 capture_trigger="manual")
        self.reconcile()
        row = self.rows()["hermes:sess-c:1..3"]
        self.assertEqual(row["import_status"], "failed")
        self.assertTrue(row["error_reason"])

    def test_filename_ep_group_used_when_frontmatter_missing(self):
        """檔名 ep 捕獲組仍可正確判定 identity／boundary（無 frontmatter
        時）——但 2.4d-3 複核修正後，無 frontmatter 必然也缺
        `capture_trigger`，所以正確結果是 fail-closed（`episode_metadata_
        incomplete`），不再像舊版那樣用 'manual' 佔位建立列（見
        TestReconcileEpisodeMetadataFailClosed 情境 A）。"""
        sid = "sess-d"
        name = adapter_module.HermesSessionAdapter.episode_inbox_filename(sid, 7, 9)
        (self.processed / name).write_text("無 frontmatter 的殘骸\n",
                                           encoding="utf-8")
        result = self.reconcile()
        eid = "hermes:sess-d:7..9"
        action = next(a for a in result["actions"] if a.get("event_id") == eid)
        self.assertEqual(action["action"], "episode_metadata_incomplete")
        self.assertEqual(action["category"], "metadata:capture_trigger_missing")
        self.assertNotIn(eid, self.rows())

    def test_legacy_file_still_backfills_legacy_row_unaffected(self):
        """episode 檔與 legacy 檔同時存在時互不干擾（零回歸）。"""
        (self.inbox / "hermes_session_sess-legacy.md").write_text(
            "---\nschema: claudecodeos.inbox.v1\nsource: hermes-session\n"
            "session_id: sess-legacy\n---\n\n內容\n", encoding="utf-8")
        self._write_episode_file(self.inbox, "sess-ep", 1, 2, episode_seq=1)
        self.reconcile()
        rows = self.rows()
        self.assertIn("hermes:sess-legacy", rows)
        self.assertIsNone(rows["hermes:sess-legacy"]["episode_seq"])
        self.assertIn("hermes:sess-ep:1..2", rows)

    def test_existing_discovered_episode_row_advances_to_directory_truth(self):
        """既有 discovered episode 列（scanner 已建立）在檔案落地後回填
        imported——與 legacy 的既有語義一致（矩陣 #18 的「已存在列」分支）。"""
        bridge_state.create_episode(
            session_id="sess-e", source_profile="default", session_source="cli",
            episode_seq=1, capture_trigger="inactivity",
            first_message_id=1, last_message_id=5,
            source_content_hash="a" * 64,
            decision_reason="scan 建立", seen_at=T1, db_path=self.bridge_db)
        self._write_episode_file(self.processed, "sess-e", 1, 5, episode_seq=1)
        self.reconcile(seen_at=T2)
        row = self.rows()["hermes:sess-e:1..5"]
        self.assertEqual(row["import_status"], "imported")
        # hash 是既有列的（scanner 算的），不被回填的 NULL 洗掉
        self.assertEqual(row["source_content_hash"], "a" * 64)

    def test_dry_run_writes_nothing(self):
        self._write_episode_file(self.processed, "sess-f", 1, 5, episode_seq=1)
        result = self.reconcile(dry_run=True)
        self.assertFalse(self.bridge_db.exists())
        names = {a["action"] for a in result["actions"]}
        self.assertIn("insert_episode_imported", names)


class TestReconcileCursorRecovery(ReconcileEpisodeTestBase):
    """矩陣 #21：cursor 重建（db 全空的 recovery 情境）。"""

    def test_rebuilds_cursor_from_landed_files_when_db_absent(self):
        self._write_episode_file(self.processed, "sess-x", 1, 5, episode_seq=1)
        self._write_episode_file(self.inbox, "sess-x", 6, 10, episode_seq=2)
        self.assertFalse(self.bridge_db.exists())
        self.reconcile(seen_at=T1)
        cur = self.cursor("sess-x")
        self.assertIsNotNone(cur)
        self.assertEqual(cur["last_captured_message_id"], 10)
        self.assertEqual(cur["last_episode_seq"], 2)

    def test_rerun_on_healthy_db_only_advances_never_retreats(self):
        self._write_episode_file(self.processed, "sess-y", 1, 5, episode_seq=1)
        self.reconcile(seen_at=T1)
        before = self.cursor("sess-y")
        # 再跑一次（健康 db）：cursor 不動（只前進不後退，天然冪等）
        self.reconcile(seen_at=T2)
        after = self.cursor("sess-y")
        self.assertEqual(before, after)

    def test_multiple_sessions_each_get_independent_cursor(self):
        self._write_episode_file(self.processed, "sess-a", 1, 5, episode_seq=1)
        self._write_episode_file(self.processed, "sess-b", 100, 120, episode_seq=1)
        self.reconcile()
        self.assertEqual(self.cursor("sess-a")["last_captured_message_id"], 5)
        self.assertEqual(self.cursor("sess-b")["last_captured_message_id"], 120)

    def test_episode_seq_falls_back_to_positional_estimate_without_frontmatter(self):
        """缺 `episode:` 序號欄位時取檔案數保守估計（提案 §3.2：只影響序號
        可讀性，不影響 boundary 正確性）——但仍必須帶合法 `capture_trigger`：
        2.4d-3 複核修正後，「無 frontmatter」不再是可以連 capture_trigger
        都一併佔位補值的理由（那個情境已改由 fail-closed 涵蓋，見情境 A／
        `test_filename_ep_group_used_when_frontmatter_missing`），這裡改成
        只缺 `episode:` 欄、其餘 frontmatter（含合法 capture_trigger）齊全，
        測試序號 fallback 這個獨立於 capture_trigger 誠實性之外的機制。"""
        sid = "sess-z"
        name1 = adapter_module.HermesSessionAdapter.episode_inbox_filename(sid, 1, 5)
        name2 = adapter_module.HermesSessionAdapter.episode_inbox_filename(sid, 6, 10)
        minimal_fm = ("---\nschema: claudecodeos.inbox.v1\nsource: hermes-session\n"
                     f"session_id: {sid}\ncapture_trigger: inactivity\n---\n\n殘骸\n")
        (self.processed / name1).write_text(minimal_fm, encoding="utf-8")
        (self.processed / name2).write_text(minimal_fm, encoding="utf-8")
        self.reconcile()
        cur = self.cursor(sid)
        self.assertEqual(cur["last_captured_message_id"], 10)
        self.assertEqual(cur["last_episode_seq"], 2, "兩個落地檔的保守估計")

    def test_dry_run_does_not_create_cursor(self):
        self._write_episode_file(self.processed, "sess-w", 1, 5, episode_seq=1)
        self.reconcile(dry_run=True)
        self.assertFalse(self.bridge_db.exists())


class TestReconcileProfileFailClosed(ReconcileEpisodeTestBase):
    """矩陣 #27：reconcile 側非 default profile fail-closed（§6.1）。"""

    def test_non_default_profile_skips_episode_row_and_cursor(self):
        self._write_episode_file(self.processed, "sess-p", 1, 5, episode_seq=1)
        result = self.reconcile(source_profile="nemocoding")
        self.assertEqual(self.rows(), {}, "非 default profile 不建立 episode 列")
        self.assertIsNone(self.cursor("sess-p"), "非 default profile 不動 cursor")
        actions = [a["action"] for a in result["actions"]]
        self.assertIn("unsupported_profile_fail_closed", actions)


class TestReconcileEpisodeMetadataFailClosed(ReconcileEpisodeTestBase):
    """Stage 2.4d-3 複核修正：episode 檔缺／非法 capture_trigger 時 reconcile
    必須 fail-closed（不猜 trigger、不佔位、不寫 DB、不動 cursor），且同一
    session 的 cursor recovery 因此整體停止；其他 session 不受影響。
    情境 A-G（對應任務規格）。"""

    # ---- A：episode 檔缺 capture_trigger ----

    def test_a_missing_capture_trigger_is_metadata_incomplete_no_writes(self):
        path = self._write_episode_file(self.processed, "sess-miss", 1, 5,
                                         episode_seq=1, capture_trigger=None)
        before_bytes = path.read_bytes()
        result = self.reconcile(seen_at=T1)
        eid = "hermes:sess-miss:1..5"
        action = next(a for a in result["actions"] if a.get("event_id") == eid)
        self.assertEqual(action["action"], "episode_metadata_incomplete")
        self.assertEqual(action["category"], "metadata:capture_trigger_missing")
        self.assertNotIn(eid, self.rows(), "缺 capture_trigger 不得建立 episode DB 列")
        self.assertIsNone(self.cursor("sess-miss"), "缺 capture_trigger 不得推進/建立 cursor")
        self.assertEqual(path.read_bytes(), before_bytes, "不得改動 inbox 實體檔案")

    # ---- B：episode capture_trigger 為非法值 ----

    def test_b_invalid_capture_trigger_is_metadata_incomplete_no_exception(self):
        self._write_episode_file(self.processed, "sess-bad", 1, 5, episode_seq=1,
                                 capture_trigger="recovered")
        result = self.reconcile(seen_at=T1)  # 不得拋未處理例外（本行本身就是斷言）
        eid = "hermes:sess-bad:1..5"
        action = next(a for a in result["actions"] if a.get("event_id") == eid)
        self.assertEqual(action["action"], "episode_metadata_incomplete")
        self.assertEqual(action["category"], "metadata:capture_trigger_invalid")
        self.assertNotIn(eid, self.rows())
        self.assertIsNone(self.cursor("sess-bad"))

    def test_b_legacy_value_in_episode_file_also_rejected(self):
        """capture_trigger='legacy' 是 migration 回填 session-level 列專用，
        episode 檔帶這個值一樣視為非法（不得被 create_episode 的
        ValueError 中斷整批 reconcile）。"""
        self._write_episode_file(self.processed, "sess-legacyval", 1, 2,
                                 episode_seq=1, capture_trigger="legacy")
        result = self.reconcile()
        eid = "hermes:sess-legacyval:1..2"
        action = next(a for a in result["actions"] if a.get("event_id") == eid)
        self.assertEqual(action["action"], "episode_metadata_incomplete")
        self.assertEqual(action["category"], "metadata:capture_trigger_invalid")
        self.assertNotIn(eid, self.rows())

    # ---- C：同 session 一個合法 episode ＋ 一個 metadata 不完整 episode ----

    def test_c_mixed_session_cursor_recovery_fails_closed_entirely(self):
        self._write_episode_file(self.processed, "sess-mixed", 1, 5,
                                 episode_seq=1, capture_trigger="archived")
        self._write_episode_file(self.inbox, "sess-mixed", 6, 10,
                                 episode_seq=2, capture_trigger=None)
        result = self.reconcile(seen_at=T1)
        rows = self.rows()
        # 合法的那一檔仍正常回填（fail-closed 範圍是「cursor recovery 重建
        # 步驟」，不是「同 session 其他合法檔各自的 create_episode() 呼叫」——
        # 兩者是不同機制，見 reconcile() docstring）
        self.assertIn("hermes:sess-mixed:1..5", rows)
        self.assertEqual(rows["hermes:sess-mixed:1..5"]["capture_trigger"], "archived")
        # 不完整的那一檔不建立列
        self.assertNotIn("hermes:sess-mixed:6..10", rows)
        # ep1 自己的 create_episode() 呼叫（獨立的 atomic 列＋cursor 不變量，
        # 提案 §1.2）本來就會把 cursor 推進到它自己的 boundary（5）——這只
        # 反映「訊息 1..5 確實已被合法 trigger 擷取」的事實，不是猜測。真正
        # 被 fail-closed 擋下的是 reconcile 的「cursor 重建」步驟：不得因為
        # 壞檔（6..10）在同一組裡，就把 cursor 推到 10——那會把未經驗證的
        # boundary 當成已擷取事實。
        cursor = self.cursor("sess-mixed")
        self.assertIsNotNone(cursor)
        self.assertEqual(cursor["last_captured_message_id"], 5,
                         "cursor 不得因同 session 的壞檔而被推到 10")
        actions = [a for a in result["actions"]
                  if a["action"] == "cursor_recovery_fail_closed"
                  and a["session_id"] == "sess-mixed"]
        self.assertEqual(len(actions), 1)
        self.assertIn("metadata:capture_trigger_missing_or_invalid", actions[0]["category"])
        # 明確指出是哪個 path 不完整
        self.assertTrue(any("sess-mixed" in p for p in actions[0]["incomplete_paths"]))
        # 且沒有一般的 cursor_recovery action 混進來（該 session 的重建步驟
        # 確實被整條跳過，不是重建到不同值）
        self.assertFalse(any(a["action"] == "cursor_recovery"
                             and a["session_id"] == "sess-mixed"
                             for a in result["actions"]))

    # ---- D：另一個完全合法 session 同批存在 ----

    def test_d_unrelated_healthy_session_in_same_batch_unaffected(self):
        self._write_episode_file(self.processed, "sess-mixed", 1, 5,
                                 episode_seq=1, capture_trigger="archived")
        self._write_episode_file(self.inbox, "sess-mixed", 6, 10,
                                 episode_seq=2, capture_trigger=None)
        self._write_episode_file(self.processed, "sess-clean", 1, 8,
                                 episode_seq=1, capture_trigger="inactivity")
        result = self.reconcile(seen_at=T1)
        # 壞 session 依然 fail-closed：cursor 只到合法檔自己的 boundary（5，
        # 由 ep1 的 create_episode() 本身推進），不會被壞檔（10）拖寬，
        # 也不會被 reconcile 的 cursor 重建步驟另外推進
        mixed_cursor = self.cursor("sess-mixed")
        self.assertIsNotNone(mixed_cursor)
        self.assertEqual(mixed_cursor["last_captured_message_id"], 5)
        # 乾淨 session 正常 recovery，不被拖垮
        clean_cursor = self.cursor("sess-clean")
        self.assertIsNotNone(clean_cursor)
        self.assertEqual(clean_cursor["last_captured_message_id"], 8)
        rows = self.rows()
        self.assertIn("hermes:sess-clean:1..8", rows)
        self.assertEqual(rows["hermes:sess-clean:1..8"]["import_status"], "imported")
        recovery_actions = {a["session_id"]: a["action"] for a in result["actions"]
                           if a["action"] in ("cursor_recovery", "cursor_recovery_fail_closed")}
        self.assertEqual(recovery_actions["sess-clean"], "cursor_recovery")
        self.assertEqual(recovery_actions["sess-mixed"], "cursor_recovery_fail_closed")

    # ---- E：合法 episode（既有行為回歸確認） ----

    def test_e_valid_episode_keeps_existing_trigger_and_cursor_advances(self):
        self._write_episode_file(self.processed, "sess-ok", 1, 5, episode_seq=1,
                                 capture_trigger="manual")
        result = self.reconcile(seen_at=T1)
        eid = "hermes:sess-ok:1..5"
        row = self.rows()[eid]
        self.assertEqual(row["capture_trigger"], "manual")
        self.assertEqual(row["import_status"], "imported")
        action = next(a for a in result["actions"] if a.get("event_id") == eid)
        self.assertEqual(action["action"], "insert_episode_imported")
        cursor = self.cursor("sess-ok")
        self.assertEqual(cursor["last_captured_message_id"], 5)
        recovery = next(a for a in result["actions"]
                        if a["action"] == "cursor_recovery" and a["session_id"] == "sess-ok")
        self.assertEqual(recovery["last_captured_message_id"], 5)

    # ---- F：legacy 無 capture_trigger（不被誤標 metadata incomplete） ----

    def test_f_legacy_file_without_capture_trigger_not_flagged(self):
        (self.inbox / "hermes_session_sess-legacy2.md").write_text(
            "---\nschema: claudecodeos.inbox.v1\nsource: hermes-session\n"
            "session_id: sess-legacy2\n---\n\n內容\n", encoding="utf-8")
        result = self.reconcile()
        eid = "hermes:sess-legacy2"
        action = next(a for a in result["actions"] if a.get("event_id") == eid)
        self.assertNotEqual(action["action"], "episode_metadata_incomplete")
        self.assertEqual(action["action"], "insert_to_inbox")
        row = self.rows()[eid]
        self.assertIsNone(row["episode_seq"])
        self.assertIsNone(row["capture_trigger"])

    # ---- G：dry-run 與真實模式一致，dry-run 零寫入 ----

    def test_g_dry_run_matches_real_mode_action_zero_writes(self):
        self._write_episode_file(self.processed, "sess-dry", 1, 5, episode_seq=1,
                                 capture_trigger=None)
        eid = "hermes:sess-dry:1..5"

        dry_result = self.reconcile(dry_run=True)
        self.assertFalse(self.bridge_db.exists(), "dry-run 零寫入：連 db 檔都不建立")
        dry_action = next(a for a in dry_result["actions"] if a.get("event_id") == eid)
        self.assertEqual(dry_action["action"], "episode_metadata_incomplete")
        self.assertEqual(dry_action["category"], "metadata:capture_trigger_missing")

        real_result = self.reconcile(seen_at=T1)
        real_action = next(a for a in real_result["actions"] if a.get("event_id") == eid)
        self.assertEqual(real_action["action"], dry_action["action"])
        self.assertEqual(real_action["category"], dry_action["category"])
        self.assertNotIn(eid, self.rows())
        self.assertIsNone(self.cursor("sess-dry"))


class TestReconcileCursorRecoveryStability(EpisodeScannerTestBase):
    """矩陣 #22：cursor 重建後切刀穩定性（雙 case，提案 §3.2 的核心推演）。
    用完整 scanner+state pipeline（真的呼叫 scan()／reconcile()，不只是
    手寫檔案）驗證 recovery 後下一刀的 boundary。"""

    def test_tail_episode_has_landed_file_boundary_stable_after_recovery(self):
        """尾端 episode 有落地檔：recovery 後下一刀 boundary 與未重建時完全
        相同。"""
        sid = "sess-stable"
        self.add_session(sid, archived=True)
        self.add_message(1, sid, "user", "ep1-a", EP_CUTOVER_EPOCH + HOUR)
        self.add_message(2, sid, "user", "ep1-b", EP_CUTOVER_EPOCH + 1.1 * HOUR)
        seen1 = datetime.fromtimestamp(
            EP_CUTOVER_EPOCH + 1.2 * HOUR, tz=timezone.utc).isoformat()
        self.scan(seen_at=seen1)
        ep1 = next(iter(self.episode_rows().values()))
        self.assertEqual((ep1["first_message_id"], ep1["last_message_id"]), (1, 2))

        # ep1 落地（模擬 importer 已成功落地到 .processed/）
        self._write_episode_file(
            self.processed, sid, 1, 2, episode_seq=1, capture_trigger="archived")

        # 模擬 bridge_state.db 整個消失（recovery 情境）
        for p in self.tmp.glob("bridge_state.db*"):
            p.unlink()
        self.assertFalse(self.bridge_db.exists())

        # recovery：只跑 reconcile，cursor 從落地檔重建
        bridge_scanner.reconcile(inbox_dir=self.inbox, bridge_db=self.bridge_db,
                                 seen_at=seen1)
        self.assertEqual(self.cursor(sid)["last_captured_message_id"], 2)

        # 新訊息 + 下一刀：boundary 應與「未重建」時完全相同（[3..4]）
        self.add_message(3, sid, "user", "ep2-a", EP_CUTOVER_EPOCH + 2 * HOUR)
        self.add_message(4, sid, "user", "ep2-b", EP_CUTOVER_EPOCH + 2.1 * HOUR)
        seen2 = datetime.fromtimestamp(
            EP_CUTOVER_EPOCH + 2.2 * HOUR, tz=timezone.utc).isoformat()
        self.scan(seen_at=seen2)
        eps = self.episode_rows()
        ep2 = next(r for r in eps.values() if r["episode_seq"] == 2)
        self.assertEqual((ep2["first_message_id"], ep2["last_message_id"]), (3, 4),
                         "尾端有落地檔：recovery 後下一刀 boundary 與未重建時完全相同")

    def test_tail_needs_review_without_file_widens_next_cut_no_duplicate_landing(self):
        """尾端是無檔判定（needs_review，模擬「內容被判定但從未落地」）：
        recovery 後下一刀吸收該區段＋新訊息（boundary 變寬），且新 boundary
        與 ep1 已落地內容不重疊（inbox 零重複落地的機械保證：不同 boundary
        →不同 deterministic 檔名）。"""
        sid = "sess-widen"
        self.add_session(sid, archived=True)
        self.add_message(1, sid, "user", "ep1-a", EP_CUTOVER_EPOCH + HOUR)
        seen1 = datetime.fromtimestamp(
            EP_CUTOVER_EPOCH + 1.2 * HOUR, tz=timezone.utc).isoformat()
        self.scan(seen_at=seen1)
        ep1 = next(iter(self.episode_rows().values()))
        self.assertEqual((ep1["first_message_id"], ep1["last_message_id"]), (1, 1))
        self._write_episode_file(
            self.processed, sid, 1, 1, episode_seq=1, capture_trigger="archived")

        # ep2：切出但從未落地（模擬 importer 判定 needs_review、不落地）——
        # 用新訊息觸發第二刀，cursor 因此在「重建前」會前進到 3（吸收這段），
        # 但這段從未寫成檔案。
        self.add_message(2, sid, "user", "ep2-sensitive", EP_CUTOVER_EPOCH + 2 * HOUR)
        self.add_message(3, sid, "user", "ep2-sensitive-2",
                         EP_CUTOVER_EPOCH + 2.05 * HOUR)
        seen2 = datetime.fromtimestamp(
            EP_CUTOVER_EPOCH + 2.1 * HOUR, tz=timezone.utc).isoformat()
        self.scan(seen_at=seen2)
        eps_before_wipe = self.episode_rows()
        self.assertEqual(len(eps_before_wipe), 2)
        ep2 = next(r for r in eps_before_wipe.values() if r["episode_seq"] == 2)
        self.assertEqual((ep2["first_message_id"], ep2["last_message_id"]), (2, 3))
        # ep2 從未落地（沒有對應 inbox 檔），只有 ep1 的檔案存在

        # 模擬 db 整個消失：recovery 只能看見 ep1 的落地檔
        for p in self.tmp.glob("bridge_state.db*"):
            p.unlink()
        self.assertFalse(self.bridge_db.exists())
        bridge_scanner.reconcile(inbox_dir=self.inbox, bridge_db=self.bridge_db,
                                 seen_at=seen2)
        # recovery 後 cursor 只到 1（ep1 的 last）——比重建前的 3 更小
        self.assertEqual(self.cursor(sid)["last_captured_message_id"], 1)

        # 新訊息 + 下一刀：吸收「無檔區段（2..3）＋新訊息（4）」，boundary 變寬
        self.add_message(4, sid, "user", "ep3-new", EP_CUTOVER_EPOCH + 3 * HOUR)
        seen3 = datetime.fromtimestamp(
            EP_CUTOVER_EPOCH + 3.1 * HOUR, tz=timezone.utc).isoformat()
        self.scan(seen_at=seen3)
        eps_after = self.episode_rows()
        widened = max(eps_after.values(), key=lambda r: r["last_message_id"])
        self.assertEqual((widened["first_message_id"], widened["last_message_id"]),
                         (2, 4), "重建後下一刀吸收無檔區段＋新訊息，boundary 變寬")
        # inbox 零重複落地：widened 的 deterministic 檔名與 ep1 的檔名不同
        # （不同 boundary），不會撞名、不會覆寫既有內容
        ep1_name = adapter_module.HermesSessionAdapter.episode_inbox_filename(sid, 1, 1)
        widened_name = adapter_module.HermesSessionAdapter.episode_inbox_filename(
            sid, widened["first_message_id"], widened["last_message_id"])
        self.assertNotEqual(ep1_name, widened_name)
        self.assertEqual(len(list(self.inbox.rglob("*.md"))), 1,
                         "此時只有 ep1 落地過；widened episode 尚未匯入（scan 不落地）")

    def _write_episode_file(self, directory: Path, sid: str, first: int, last: int,
                            episode_seq: int, capture_trigger: str | None = "inactivity",
                            session_source: str = "cli") -> Path:
        name = adapter_module.HermesSessionAdapter.episode_inbox_filename(
            sid, first, last)
        path = directory / name
        path.write_text(
            _episode_inbox_text(sid, first, last, episode_seq, capture_trigger,
                                session_source),
            encoding="utf-8")
        return path


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    unittest.main(verbosity=2)
