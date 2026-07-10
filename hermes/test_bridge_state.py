#!/usr/bin/env python3
"""hermes/test_bridge_state.py — v0.1（Stage 2.2）

hermes/bridge_state.py 的測試：init 冪等、schema 與 registry yaml 程式化對齊、
event_id upsert 去重、get/list、mark_failed 與 retry_count 語義、刪檔重建。

隔離保證（本檔自我驗證，見 setUpModule/tearDownModule 與靜態檢查測試）：
- 全程只用 temp 目錄的 db，絕不觸碰 Hermes state.db、hermes/config/telegram.json、
  hermes/jobs.db。
- 測試套件執行本身不建立、不改動真實的 hermes/state/bridge_state.db：
  Windows 開發側它本來就不存在（依決策 db 只存在 WSL 部署側），測試後仍不存在；
  WSL 部署側它是預期終態，測試前後 fingerprint 必須一致。兩側跑同一套測試。

執行：.venv/Scripts/python.exe hermes/test_bridge_state.py
"""
import ast
import contextlib
import re
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bridge_state  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / "hermes" / "state"

# 不可觸碰的真實檔案（存在與否、mtime 都不得被測試改變）
_PROTECTED = [
    bridge_state.DEFAULT_DB_PATH,
    ROOT / "hermes" / "jobs.db",
    ROOT / "hermes" / "config" / "telegram.json",
]
_snapshot: dict = {}


def _fingerprint(path: Path):
    return (path.stat().st_mtime_ns, path.stat().st_size) if path.exists() else None


def setUpModule():
    _snapshot["protected"] = {p: _fingerprint(p) for p in _PROTECTED}
    _snapshot["state_dir_listing"] = (
        sorted(p.name for p in STATE_DIR.iterdir()) if STATE_DIR.exists() else None
    )


def tearDownModule():
    for p, before in _snapshot["protected"].items():
        after = _fingerprint(p)
        assert after == before, f"測試不得觸碰 {p}（before={before}, after={after}）"
    listing = sorted(p.name for p in STATE_DIR.iterdir()) if STATE_DIR.exists() else None
    assert listing == _snapshot["state_dir_listing"], (
        f"測試不得在 hermes/state/ 留下任何檔案：before={_snapshot['state_dir_listing']} "
        f"after={listing}"
    )


def make_record(**overrides) -> dict:
    """一筆合法的最小記錄（kwargs 形式，供 upsert_session_state 用）。"""
    rec = dict(
        session_id="sess-001",
        source_profile="default",
        session_source="telegram",
        import_status="discovered",
        memory_type="none",
        useful_chat=False,
        decision_reason="新完結 session，尚未判定",
    )
    rec.update(overrides)
    return rec


def _raw_conn(db_path: Path):
    """測試直查用的 sqlite 連線；contextlib.closing 確保連線關閉，
    Windows 上才不會把 temp db 檔鎖住導致 TemporaryDirectory 清不掉。"""
    return contextlib.closing(sqlite3.connect(db_path))


class BridgeStateTestBase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "bridge_state.db"
        bridge_state.init_db(self.db_path)

    def tearDown(self):
        self._tmpdir.cleanup()


class TestInitAndSchema(BridgeStateTestBase):
    def test_first_init_creates_db_and_table(self):
        self.assertTrue(self.db_path.exists())
        with _raw_conn(self.db_path) as conn:
            tables = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        self.assertIn(bridge_state.TABLE_NAME, tables)

    def test_repeated_init_is_idempotent_and_preserves_rows(self):
        bridge_state.upsert_session_state(**make_record(), db_path=self.db_path)
        bridge_state.init_db(self.db_path)
        bridge_state.ensure_schema(self.db_path)  # 別名同樣冪等
        rec = bridge_state.get_session_state("hermes:sess-001", db_path=self.db_path)
        self.assertIsNotNone(rec, "重複 init 不得清掉既有資料")

    def test_db_can_be_rebuilt_after_delete(self):
        """disposable 保證：整個 db 檔刪掉後，init 可從零重建、寫入正常。"""
        bridge_state.upsert_session_state(**make_record(), db_path=self.db_path)
        # 關閉 WAL 殘留後刪檔
        for suffix in ("", "-wal", "-shm"):
            Path(str(self.db_path) + suffix).unlink(missing_ok=True)
        self.assertFalse(self.db_path.exists())
        bridge_state.init_db(self.db_path)
        rec = bridge_state.upsert_session_state(**make_record(), db_path=self.db_path)
        self.assertEqual(rec["event_id"], "hermes:sess-001")

    def test_table_columns_align_with_registry_yaml(self):
        """程式化對齊比對：欄位名集合、NOT NULL、型別對映
        （int→INTEGER、bool→INTEGER、其餘 TEXT）都必須跟 registry yaml 一致。"""
        fields = yaml.safe_load(
            (ROOT / "registry" / "bridge_state_schema.yaml").read_text(encoding="utf-8")
        )["fields"]
        with _raw_conn(self.db_path) as conn:
            cols = {
                r[1]: {"type": r[2], "notnull": bool(r[3])}
                for r in conn.execute(
                    f"PRAGMA table_info({bridge_state.TABLE_NAME})"
                )
            }
        self.assertEqual(set(cols.keys()), set(fields.keys()),
                         "SQLite 欄位集合必須等於 registry yaml 的 17 欄")
        for name, spec in fields.items():
            expected_type = bridge_state.SQL_TYPE_BY_SCHEMA_TYPE[spec["type"]]
            self.assertEqual(cols[name]["type"], expected_type,
                             f"欄位 {name} 型別對映錯誤")
            self.assertEqual(cols[name]["notnull"], spec["required"],
                             f"欄位 {name} 的 NOT NULL 必須等於 yaml required")

    def test_event_id_has_unique_constraint(self):
        """event_id 是去重 key：要有 UNIQUE 約束，繞過 API 直接 INSERT 重複值也會被擋。"""
        with _raw_conn(self.db_path) as conn:
            unique_cols = set()
            for idx in conn.execute(
                f"PRAGMA index_list({bridge_state.TABLE_NAME})"
            ).fetchall():
                if idx[2]:  # unique flag
                    for info in conn.execute(f"PRAGMA index_info({idx[1]})"):
                        unique_cols.add(info[2])
        self.assertIn("event_id", unique_cols)

        bridge_state.upsert_session_state(**make_record(), db_path=self.db_path)
        with _raw_conn(self.db_path) as conn, self.assertRaises(sqlite3.IntegrityError):
            with conn:
                conn.execute(
                    f"INSERT INTO {bridge_state.TABLE_NAME} ("
                    "session_id, source_profile, session_source, import_status, "
                    "memory_type, useful_chat, decision_reason, first_seen_at, "
                    "last_seen_at, updated_at, event_id) "
                    "VALUES ('x','default','cli','discovered','none',0,'dup','t','t','t',"
                    "'hermes:sess-001')"
                )


class TestUpsertSemantics(BridgeStateTestBase):
    def test_upsert_inserts_with_defaults(self):
        rec = bridge_state.upsert_session_state(**make_record(), db_path=self.db_path)
        self.assertEqual(rec["event_id"], "hermes:sess-001",
                         "event_id 省略時依慣例取 hermes:<session_id>")
        self.assertEqual(rec["retry_count"], 0)
        self.assertIs(rec["useful_chat"], False)
        self.assertEqual(rec["first_seen_at"], rec["last_seen_at"])

    def test_upsert_same_event_id_updates_not_duplicates(self):
        bridge_state.upsert_session_state(
            **make_record(), seen_at="2026-07-10T00:00:00+00:00", db_path=self.db_path)
        updated = bridge_state.upsert_session_state(
            **make_record(import_status="to_inbox",
                          imported_inbox_path="memory/inbox/x.md",
                          decision_reason="含正面訊號，落地 inbox",
                          useful_chat=True),
            seen_at="2026-07-10T01:00:00+00:00", db_path=self.db_path)

        with _raw_conn(self.db_path) as conn:
            count = conn.execute(
                f"SELECT COUNT(*) FROM {bridge_state.TABLE_NAME}"
            ).fetchone()[0]
        self.assertEqual(count, 1, "同 event_id 重跑必須是更新既有列，不是新增")
        self.assertEqual(updated["import_status"], "to_inbox")
        self.assertIs(updated["useful_chat"], True)
        self.assertEqual(updated["first_seen_at"], "2026-07-10T00:00:00+00:00",
                         "first_seen_at 必須保持首次值")
        self.assertEqual(updated["last_seen_at"], "2026-07-10T01:00:00+00:00")
        self.assertEqual(updated["updated_at"], "2026-07-10T01:00:00+00:00")

    def test_upsert_does_not_reset_retry_count(self):
        bridge_state.upsert_session_state(**make_record(), db_path=self.db_path)
        bridge_state.increment_retry_count("hermes:sess-001", db_path=self.db_path)
        rec = bridge_state.upsert_session_state(**make_record(), db_path=self.db_path)
        self.assertEqual(rec["retry_count"], 1,
                         "upsert 不得動 retry_count（只由 increment_retry_count 控制）")

    def test_upsert_rejects_invalid_enum_values(self):
        with self.assertRaises(ValueError):
            bridge_state.upsert_session_state(
                **make_record(import_status="done"), db_path=self.db_path)
        with self.assertRaises(ValueError):
            bridge_state.upsert_session_state(
                **make_record(memory_type="working"), db_path=self.db_path)

    def test_enum_validation_sources_from_registry_yaml(self):
        """enum 合法值單一來源：模組讀到的值必須恰等於 registry yaml 的 values，
        且 yaml 中每個 import_status 值都能通過 upsert。"""
        fields = yaml.safe_load(
            (ROOT / "registry" / "bridge_state_schema.yaml").read_text(encoding="utf-8")
        )["fields"]
        self.assertEqual(bridge_state.schema_enum_values("import_status"),
                         set(fields["import_status"]["values"]))
        self.assertEqual(bridge_state.schema_enum_values("memory_type"),
                         set(fields["memory_type"]["values"]))
        for i, status in enumerate(fields["import_status"]["values"]):
            extra = {}
            if status == "failed":
                extra["error_reason"] = "測試用錯誤摘要"
            if status in ("to_inbox", "imported"):
                extra["imported_inbox_path"] = "memory/inbox/x.md"
            rec = bridge_state.upsert_session_state(
                **make_record(session_id=f"sess-enum-{i}", import_status=status, **extra),
                db_path=self.db_path)
            self.assertEqual(rec["import_status"], status)

    def test_upsert_enforces_conditional_required_fields(self):
        with self.assertRaises(ValueError):
            bridge_state.upsert_session_state(
                **make_record(import_status="failed"), db_path=self.db_path)
        with self.assertRaises(ValueError):
            bridge_state.upsert_session_state(
                **make_record(import_status="to_inbox"), db_path=self.db_path)
        with self.assertRaises(ValueError):
            bridge_state.upsert_session_state(
                **make_record(import_status="imported"), db_path=self.db_path)


class TestReadBack(BridgeStateTestBase):
    def test_get_session_state_roundtrip(self):
        bridge_state.upsert_session_state(
            **make_record(useful_chat=True, selected_capability_lane="hermes-gptcoding",
                          event_id_range="hermes:sess-001:1..42"),
            db_path=self.db_path)
        rec = bridge_state.get_session_state("hermes:sess-001", db_path=self.db_path)
        self.assertEqual(rec["session_id"], "sess-001")
        self.assertEqual(rec["source_profile"], "default")
        self.assertIs(rec["useful_chat"], True, "useful_chat 讀回時要還原成 bool")
        self.assertEqual(rec["selected_capability_lane"], "hermes-gptcoding")
        self.assertEqual(rec["event_id_range"], "hermes:sess-001:1..42")

    def test_get_unknown_event_id_returns_none(self):
        self.assertIsNone(
            bridge_state.get_session_state("hermes:nope", db_path=self.db_path))

    def test_list_by_import_status_filters(self):
        bridge_state.upsert_session_state(
            **make_record(session_id="s1"), db_path=self.db_path)
        bridge_state.upsert_session_state(
            **make_record(session_id="s2"), db_path=self.db_path)
        bridge_state.upsert_session_state(
            **make_record(session_id="s3", import_status="skipped",
                          decision_reason="命中排除訊號"),
            db_path=self.db_path)
        discovered = bridge_state.list_by_import_status(
            "discovered", db_path=self.db_path)
        skipped = bridge_state.list_by_import_status("skipped", db_path=self.db_path)
        self.assertEqual({r["session_id"] for r in discovered}, {"s1", "s2"})
        self.assertEqual({r["session_id"] for r in skipped}, {"s3"})
        self.assertEqual(bridge_state.list_by_import_status(
            "failed", db_path=self.db_path), [])

    def test_list_rejects_invalid_status(self):
        with self.assertRaises(ValueError):
            bridge_state.list_by_import_status("bogus", db_path=self.db_path)


class TestFailureAndRetrySemantics(BridgeStateTestBase):
    def test_mark_failed_sets_error_reason_without_touching_retry_count(self):
        bridge_state.upsert_session_state(**make_record(), db_path=self.db_path)
        rec = bridge_state.mark_failed(
            "hermes:sess-001", "session_adapter 逾時", db_path=self.db_path)
        self.assertEqual(rec["import_status"], "failed")
        self.assertEqual(rec["error_reason"], "session_adapter 逾時")
        self.assertEqual(rec["retry_count"], 0,
                         "失敗當下不遞增——retry_count 記的是重新嘗試次數")

    def test_mark_failed_requires_error_reason(self):
        bridge_state.upsert_session_state(**make_record(), db_path=self.db_path)
        with self.assertRaises(ValueError):
            bridge_state.mark_failed("hermes:sess-001", "", db_path=self.db_path)

    def test_mark_failed_unknown_event_id_returns_none(self):
        self.assertIsNone(bridge_state.mark_failed(
            "hermes:nope", "x", db_path=self.db_path))

    def test_increment_retry_count_on_reattempt(self):
        """遞增時機：bridge 對同一 session 重新嘗試匯入的當下呼叫。
        典型序列：discovered → failed（retry_count 仍 0）→ 重跑開始時 +1。"""
        bridge_state.upsert_session_state(**make_record(), db_path=self.db_path)
        bridge_state.mark_failed("hermes:sess-001", "boom", db_path=self.db_path)
        new_count = bridge_state.increment_retry_count(
            "hermes:sess-001", db_path=self.db_path)
        self.assertEqual(new_count, 1)
        self.assertEqual(bridge_state.increment_retry_count(
            "hermes:sess-001", db_path=self.db_path), 2)
        rec = bridge_state.get_session_state("hermes:sess-001", db_path=self.db_path)
        self.assertEqual(rec["retry_count"], 2)

    def test_increment_retry_count_unknown_event_id_returns_none(self):
        self.assertIsNone(bridge_state.increment_retry_count(
            "hermes:nope", db_path=self.db_path))


class TestTouchLastSeen(BridgeStateTestBase):
    """touch_last_seen：scanner 對既有記錄的安全路徑——只動 last_seen_at。"""

    def test_touch_updates_only_last_seen_at(self):
        bridge_state.upsert_session_state(
            **make_record(), seen_at="2026-07-10T00:00:00+00:00",
            db_path=self.db_path)
        rec = bridge_state.touch_last_seen(
            "hermes:sess-001", seen_at="2026-07-10T02:00:00+00:00",
            db_path=self.db_path)
        self.assertEqual(rec["last_seen_at"], "2026-07-10T02:00:00+00:00")
        self.assertEqual(rec["first_seen_at"], "2026-07-10T00:00:00+00:00",
                         "touch 不得動 first_seen_at")
        self.assertEqual(rec["updated_at"], "2026-07-10T00:00:00+00:00",
                         "touch 不是狀態變更，不得動 updated_at")
        self.assertEqual(rec["import_status"], "discovered")
        self.assertEqual(rec["retry_count"], 0)

    def test_touch_never_resets_terminal_status(self):
        """硬條件：已是 imported/failed 的記錄被重掃 touch，狀態原封不動。"""
        bridge_state.upsert_session_state(
            **make_record(import_status="imported",
                          imported_inbox_path="memory/inbox/x.md",
                          decision_reason="已整併", useful_chat=True,
                          memory_type="semantic"),
            db_path=self.db_path)
        rec = bridge_state.touch_last_seen("hermes:sess-001", db_path=self.db_path)
        self.assertEqual(rec["import_status"], "imported")
        self.assertEqual(rec["memory_type"], "semantic")
        self.assertIs(rec["useful_chat"], True)
        self.assertEqual(rec["decision_reason"], "已整併")
        self.assertEqual(rec["imported_inbox_path"], "memory/inbox/x.md")

    def test_touch_unknown_event_id_returns_none(self):
        self.assertIsNone(bridge_state.touch_last_seen(
            "hermes:nope", db_path=self.db_path))


class TestScanWatermark(BridgeStateTestBase):
    """bridge_meta / scan_watermark（Stage 2.4a）：只前進不後退、純讀取不建檔、
    舊 db 升級路徑、可拋棄重建語義。"""

    WM1 = "2026-07-10T06:00:00+00:00"
    WM2 = "2026-07-10T07:00:00+00:00"

    def test_init_creates_meta_table(self):
        with _raw_conn(self.db_path) as conn:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn(bridge_state.META_TABLE_NAME, tables)

    def test_watermark_none_initially(self):
        self.assertIsNone(bridge_state.get_scan_watermark(self.db_path))

    def test_get_watermark_on_missing_db_returns_none_without_creating_file(self):
        """純讀取硬條件：db 檔不存在 → None，且**不建立 db 檔**
        （scanner 的 dry-run 與 effective since 判斷靠這個保證）。"""
        missing = Path(self._tmpdir.name) / "nope" / "bridge_state.db"
        self.assertIsNone(bridge_state.get_scan_watermark(missing))
        self.assertFalse(missing.exists(), "get_scan_watermark 不得建立 db 檔")
        self.assertFalse(missing.parent.exists(),
                         "get_scan_watermark 連父目錄都不得建立")

    def test_advance_sets_and_normalizes_value(self):
        result = bridge_state.advance_scan_watermark(
            "2026-07-10T06:00:00Z", db_path=self.db_path)
        self.assertTrue(result["advanced"])
        self.assertEqual(result["watermark"], self.WM1,
                         "寫入值要正規化為 UTC isoformat（+00:00 形式）")
        self.assertEqual(bridge_state.get_scan_watermark(self.db_path), self.WM1)

    def test_advance_only_moves_forward(self):
        """只前進不後退：new <= current 時 no-op 並回報現值。"""
        bridge_state.advance_scan_watermark(self.WM2, db_path=self.db_path)
        older = bridge_state.advance_scan_watermark(self.WM1, db_path=self.db_path)
        self.assertFalse(older["advanced"], "較舊的值不得把 watermark 往回拉")
        self.assertEqual(older["watermark"], self.WM2)
        equal = bridge_state.advance_scan_watermark(self.WM2, db_path=self.db_path)
        self.assertFalse(equal["advanced"], "相等的值同樣 no-op")
        self.assertEqual(bridge_state.get_scan_watermark(self.db_path), self.WM2)

    def test_advance_rejects_unparseable_value(self):
        with self.assertRaises(ValueError):
            bridge_state.advance_scan_watermark(
                "not-a-timestamp", db_path=self.db_path)
        self.assertIsNone(bridge_state.get_scan_watermark(self.db_path),
                          "解析失敗不得寫入任何值")

    def _make_legacy_db(self) -> Path:
        """模擬 Stage 2.4a 之前的既有 db：只有 bridge_sessions、沒有 bridge_meta。"""
        legacy = Path(self._tmpdir.name) / "legacy_bridge_state.db"
        with _raw_conn(legacy) as conn:
            with conn:
                conn.execute(bridge_state.CREATE_TABLE_SQL)
        bridge_state.upsert_session_state(
            **make_record(session_id="sess-legacy"), db_path=legacy)
        with _raw_conn(legacy) as conn:
            with conn:
                conn.execute(f"DROP TABLE IF EXISTS {bridge_state.META_TABLE_NAME}")
        return legacy

    def test_get_watermark_on_legacy_db_returns_none(self):
        legacy = self._make_legacy_db()
        self.assertIsNone(bridge_state.get_scan_watermark(legacy),
                          "沒有 meta table 的舊 db 讀 watermark 要回 None，不得炸")

    def test_ensure_schema_upgrades_legacy_db_and_preserves_rows(self):
        """升級路徑：對既有 db 呼叫 ensure_schema 冪等補建 meta table，
        既有 bridge_sessions 資料原封不動。"""
        legacy = self._make_legacy_db()
        bridge_state.ensure_schema(legacy)
        with _raw_conn(legacy) as conn:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn(bridge_state.META_TABLE_NAME, tables)
        rec = bridge_state.get_session_state("hermes:sess-legacy", db_path=legacy)
        self.assertIsNotNone(rec, "升級不得動既有資料")
        self.assertIsNone(bridge_state.get_scan_watermark(legacy))

    def test_advance_on_legacy_db_creates_meta_table(self):
        legacy = self._make_legacy_db()
        result = bridge_state.advance_scan_watermark(self.WM1, db_path=legacy)
        self.assertTrue(result["advanced"])
        self.assertEqual(bridge_state.get_scan_watermark(legacy), self.WM1)

    def test_watermark_is_disposable_with_db(self):
        """可拋棄語義：db 重建後 watermark 消失（scanner 退回 cutover 底線）。"""
        bridge_state.advance_scan_watermark(self.WM1, db_path=self.db_path)
        for suffix in ("", "-wal", "-shm"):
            Path(str(self.db_path) + suffix).unlink(missing_ok=True)
        bridge_state.init_db(self.db_path)
        self.assertIsNone(bridge_state.get_scan_watermark(self.db_path))


class TestIsolationGuarantees(unittest.TestCase):
    """證明模組/測試不觸碰 Hermes 真實資料（靜態＋常數層面；
    行為層面由 setUpModule/tearDownModule 的 fingerprint 比對把關）。"""

    @staticmethod
    def _code_only_source(path: Path) -> str:
        """回傳去掉 docstring 與註解後的程式碼——docstring 依規格「明文化 DB 性質」
        本來就要提到 Hermes state.db（rebuild 來源），靜態檢查只針對實際 code。"""
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

    def test_module_source_never_references_hermes_state_db(self):
        """靜態檢查：bridge_state.py 的 code（不含 docstring/註解）只有一個
        sqlite3.connect 入口，且沒有任何指向 Hermes state.db 的路徑
        （"state.db" 只允許出現在 "bridge_state.db" 這個檔名裡）。"""
        code = self._code_only_source(
            Path(__file__).resolve().parent / "bridge_state.py")
        self.assertEqual(code.count("sqlite3.connect"), 1,
                         "bridge_state.py 應該只有一個 sqlite 連線入口")
        self.assertIsNone(re.search(r"(?<!bridge_)state\.db", code),
                          "code 不得引用 Hermes state.db")
        for forbidden in ("jobs.db", "telegram.json", "LOCALAPPDATA", "mode=rw"):
            self.assertNotIn(forbidden, code)

    def test_default_db_path_points_at_hermes_state_dir(self):
        self.assertEqual(bridge_state.DEFAULT_DB_PATH,
                         ROOT / "hermes" / "state" / "bridge_state.db")

    def test_suite_does_not_touch_real_bridge_state_db(self):
        """隔離保證的正確語義：測試套件執行本身**不建立、不改動** DEFAULT_DB_PATH
        ——而非「該路徑絕對不存在」。Windows 開發側它本來就不存在，測試後必須仍
        不存在；WSL 部署側它是 CLI init 建立的預期終態，測試後 fingerprint
        （mtime/size）必須與 setUpModule 快照一致。同一套測試因此在兩側都能
        誠實通過，隔離保證不打折。"""
        before = _snapshot["protected"][bridge_state.DEFAULT_DB_PATH]
        after = _fingerprint(bridge_state.DEFAULT_DB_PATH)
        self.assertEqual(
            after, before,
            "DEFAULT_DB_PATH 測試前後必須一致（原本不存在→仍不存在；"
            "原本存在→mtime/size 未被改動）",
        )


if __name__ == "__main__":
    unittest.main()
