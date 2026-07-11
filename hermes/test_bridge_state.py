#!/usr/bin/env python3
"""hermes/test_bridge_state.py — v0.2（Stage 2.4d-1）

hermes/bridge_state.py 的測試：init 冪等、schema 與 registry yaml 程式化對齊、
event_id upsert 去重、get/list、mark_failed 與 retry_count 語義、刪檔重建。
Stage 2.4d-1 新增：event_id 三層 namespace helpers（含 profile fail-closed）、
episode_content_hash 純函式、bridge_cursors（只前進不後退）、create_episode
（原子性＝矩陣 #3、冪等 conflict 路徑＝#23、boundary 一致性＝#24）、
v1→v2 migrate（冪等＝#13、舊 API 不回歸＝#14）、升級路徑（#2 的 cursor 語義）。

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
                         "SQLite 欄位集合必須等於 registry yaml 的 22 欄")
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


# ====================================================================
# Stage 2.4d-1：episode capture 的 repository 層
# （提案 docs/stage2.4d-episode-capture-proposal.md；矩陣 #2/#3/#13/#14/#23/#24）
# ====================================================================

def make_episode(**overrides) -> dict:
    """一筆合法的最小 episode（kwargs 形式，供 create_episode 用）。"""
    rec = dict(
        session_id="sess-ep",
        source_profile="default",
        session_source="desktop",
        episode_seq=1,
        capture_trigger="inactivity",
        first_message_id=100,
        last_message_id=120,
        source_content_hash="a" * 64,
        decision_reason="episode 偵測：inactivity 門檻到，切刀",
    )
    rec.update(overrides)
    return rec


class TestEpisodeEventIdHelpers(unittest.TestCase):
    """event_id 三層 namespace（提案 §2）：純函式，不碰 db。"""

    def test_episode_event_id_format(self):
        self.assertEqual(bridge_state.episode_event_id("sid-1", 3, 17),
                         "hermes:sid-1:3..17")
        self.assertEqual(bridge_state.episode_event_id("sid-1", 5, 5),
                         "hermes:sid-1:5..5", "單訊息 episode（first==last）合法")

    def test_episode_event_id_rejects_bad_boundary(self):
        for first, last in ((0, 5), (-1, 5), (5, 3)):
            with self.assertRaises(ValueError, msg=f"({first},{last}) 應被拒"):
                bridge_state.episode_event_id("sid", first, last)
        with self.assertRaises(ValueError):
            bridge_state.episode_event_id("sid", "3", 5)  # 非 int
        with self.assertRaises(ValueError):
            bridge_state.episode_event_id("sid", True, 5)  # bool 不是誠實的 rowid

    def test_episode_event_id_rejects_bad_session_id(self):
        for sid in ("", "a:b", "a/b"):
            with self.assertRaises(ValueError, msg=f"sid={sid!r} 應被拒"):
                bridge_state.episode_event_id(sid, 1, 2)

    def test_parse_session_level(self):
        parsed = bridge_state.parse_event_id("hermes:20260630_183709_063b4e40")
        self.assertEqual(parsed, {"kind": "session",
                                  "session_id": "20260630_183709_063b4e40"})

    def test_parse_message_level(self):
        parsed = bridge_state.parse_event_id("hermes:sess-1:42")
        self.assertEqual(parsed, {"kind": "message", "session_id": "sess-1",
                                  "rowid": 42})

    def test_parse_episode_level_roundtrip(self):
        eid = bridge_state.episode_event_id("sess-1", 100, 120)
        parsed = bridge_state.parse_event_id(eid)
        self.assertEqual(parsed, {"kind": "episode", "session_id": "sess-1",
                                  "first_message_id": 100, "last_message_id": 120})

    def test_parse_noninteger_colon_tail_is_session_level(self):
        """提案 §2 區分規則逐字：「含 ':' 但無 '..' 且冒號後是整數＝訊息；
        其餘＝session 層級」。"""
        parsed = bridge_state.parse_event_id("hermes:sess-1:abc")
        self.assertEqual(parsed["kind"], "session")
        self.assertEqual(parsed["session_id"], "sess-1:abc")

    def test_parse_rejects_profile_namespace_fail_closed(self):
        """"hermes/" 前綴（未來 profile namespace）一律拒絕——連
        hermes/default 也拒（不得默默視為 default，提案 §6.1）。"""
        for eid in ("hermes/nemocoding:sess-1:1..5", "hermes/default:sess-1",
                    "hermes/x:sess-1:42"):
            with self.assertRaises(ValueError, msg=f"{eid!r} 應被拒"):
                bridge_state.parse_event_id(eid)

    def test_parse_rejects_unknown_namespace_and_garbage(self):
        for eid in ("rss:foo", "hermes:", "", None, 42):
            with self.assertRaises(ValueError, msg=f"{eid!r} 應被拒"):
                bridge_state.parse_event_id(eid)

    def test_parse_rejects_malformed_episode(self):
        """含 '..' ＝ episode（機械規則）——但格式不合法時 fail loud，
        不退回其他層級默默解讀。"""
        for eid in ("hermes:sess:1..x", "hermes:sess:..5", "hermes:sess:5..3",
                    "hermes:sess:0..3", "hermes:1..5"):
            with self.assertRaises(ValueError, msg=f"{eid!r} 應被拒"):
                bridge_state.parse_event_id(eid)


class TestEpisodeContentHash(unittest.TestCase):
    """episode_content_hash：純函式（提案 §4.5），不碰 db、輸入順序無關、
    normalized 欄位固定。"""

    EVENTS = [
        {"rowid": 2, "role": "assistant", "type": "message",
         "content": "回答", "tool_calls": None, "timestamp": "2026-07-11T00:01:00Z"},
        {"rowid": 1, "role": "user", "type": "message",
         "content": "問題", "tool_calls": None, "timestamp": "2026-07-11T00:00:00Z"},
    ]

    def test_deterministic_and_pure(self):
        h1 = bridge_state.episode_content_hash(self.EVENTS)
        h2 = bridge_state.episode_content_hash(self.EVENTS)
        self.assertEqual(h1, h2, "同輸入恆得同 hash（矩陣 #25 的純函式前提）")
        self.assertTrue(re.fullmatch(r"[0-9a-f]{64}", h1), "SHA-256 hex digest")

    def test_input_order_does_not_matter(self):
        self.assertEqual(bridge_state.episode_content_hash(self.EVENTS),
                         bridge_state.episode_content_hash(self.EVENTS[::-1]),
                         "函式內依 rowid 升冪排序——輸入順序不影響結果")

    def test_content_change_changes_hash(self):
        tampered = [dict(self.EVENTS[0]), dict(self.EVENTS[1])]
        tampered[1]["content"] = "被 compaction 改寫的內容"
        self.assertNotEqual(bridge_state.episode_content_hash(self.EVENTS),
                            bridge_state.episode_content_hash(tampered))

    def test_only_normalized_fields_participate(self):
        """render 格式解耦：EPISODE_HASH_FIELDS 之外的欄位（例如 render 附帶的
        display 欄）不影響 hash。"""
        noisy = [dict(e, display="＊render 專用＊", extra=123) for e in self.EVENTS]
        self.assertEqual(bridge_state.episode_content_hash(self.EVENTS),
                         bridge_state.episode_content_hash(noisy))

    def test_missing_field_is_recorded_as_none_and_differs(self):
        without_ts = [{k: v for k, v in e.items() if k != "timestamp"}
                      for e in self.EVENTS]
        self.assertNotEqual(bridge_state.episode_content_hash(self.EVENTS),
                            bridge_state.episode_content_hash(without_ts),
                            "缺欄照實記 None——與帶值時必須不同（完整性檢查的意義）")

    def test_rowid_required_int(self):
        with self.assertRaises(ValueError):
            bridge_state.episode_content_hash([{"content": "x"}])
        with self.assertRaises(ValueError):
            bridge_state.episode_content_hash([{"rowid": "1", "content": "x"}])

    def test_contract_recipe(self):
        """契約 golden test：normalized（固定欄位、依 rowid 升冪）→ 固定鍵序
        JSON（sort_keys、緊湊分隔、非 ASCII 不轉義）→ SHA-256 hex。
        測試獨立重算配方，把關實作不悄悄改序列化細節（改了＝部署側全部
        假性 mismatch）。"""
        import hashlib as _hashlib
        import json as _json
        normalized = sorted(
            [{k: e.get(k) for k in bridge_state.EPISODE_HASH_FIELDS}
             for e in self.EVENTS],
            key=lambda item: item["rowid"])
        payload = _json.dumps(normalized, ensure_ascii=False, sort_keys=True,
                              separators=(",", ":"))
        expected = _hashlib.sha256(payload.encode("utf-8")).hexdigest()
        self.assertEqual(bridge_state.episode_content_hash(self.EVENTS), expected)


class TestCursorApi(BridgeStateTestBase):
    """bridge_cursors（提案 §1.2）：只前進不後退（矩陣 #2 的 repository 部分）、
    純讀取不建檔、複合主鍵隔離、舊 db 升級路徑。"""

    def test_init_creates_cursor_table_with_composite_pk(self):
        with _raw_conn(self.db_path) as conn:
            cols = {r[1]: {"notnull": bool(r[3]), "pk": r[5], "type": r[2]}
                    for r in conn.execute(
                        f"PRAGMA table_info({bridge_state.CURSOR_TABLE_NAME})")}
        cursor_schema = yaml.safe_load(
            (ROOT / "registry" / "bridge_state_schema.yaml").read_text(encoding="utf-8")
        )["bridge_cursors"]
        self.assertEqual(set(cols.keys()), set(cursor_schema["fields"].keys()),
                         "bridge_cursors 欄位集合必須等於 registry yaml")
        for name, spec in cursor_schema["fields"].items():
            self.assertEqual(cols[name]["type"],
                             bridge_state.SQL_TYPE_BY_SCHEMA_TYPE[spec["type"]])
            self.assertTrue(cols[name]["notnull"] or cols[name]["pk"],
                            f"{name} 必須 NOT NULL（PK 欄位隱含）")
        self.assertEqual(cols["source_profile"]["pk"], 1)
        self.assertEqual(cols["session_id"]["pk"], 2,
                         "複合主鍵順序必須是 (source_profile, session_id)")

    def test_get_cursor_none_initially(self):
        self.assertIsNone(bridge_state.get_cursor("sess-x", db_path=self.db_path))

    def test_get_cursor_on_missing_db_returns_none_without_creating_file(self):
        missing = Path(self._tmpdir.name) / "nope" / "bridge_state.db"
        self.assertIsNone(bridge_state.get_cursor("sess-x", db_path=missing))
        self.assertFalse(missing.exists(), "get_cursor 不得建立 db 檔")
        self.assertFalse(missing.parent.exists())

    def test_get_cursor_on_legacy_db_without_table_returns_none(self):
        legacy = Path(self._tmpdir.name) / "legacy.db"
        with _raw_conn(legacy) as conn:
            with conn:
                conn.execute(bridge_state.CREATE_TABLE_SQL)
        self.assertIsNone(bridge_state.get_cursor("sess-x", db_path=legacy),
                          "沒有 cursors table 的舊 db 要回 None，不得炸")

    def test_advance_creates_and_moves_forward_only(self):
        """矩陣 #2（repository 部分）：cursor 只前進不後退。"""
        r1 = bridge_state.advance_cursor("sess-x", 120, last_episode_seq=1,
                                         db_path=self.db_path)
        self.assertTrue(r1["advanced"])
        self.assertEqual(r1["cursor"]["last_captured_message_id"], 120)
        self.assertEqual(r1["cursor"]["last_episode_seq"], 1)

        older = bridge_state.advance_cursor("sess-x", 80, last_episode_seq=1,
                                            db_path=self.db_path)
        self.assertFalse(older["advanced"], "較小的值不得把 cursor 往回拉")
        self.assertEqual(older["cursor"]["last_captured_message_id"], 120)

        equal = bridge_state.advance_cursor("sess-x", 120, db_path=self.db_path)
        self.assertFalse(equal["advanced"], "相等的值同樣 no-op")

        newer = bridge_state.advance_cursor("sess-x", 150, last_episode_seq=2,
                                            db_path=self.db_path)
        self.assertTrue(newer["advanced"])
        cur = bridge_state.get_cursor("sess-x", db_path=self.db_path)
        self.assertEqual(cur["last_captured_message_id"], 150)
        self.assertEqual(cur["last_episode_seq"], 2)

    def test_advance_without_seq_preserves_existing_seq(self):
        bridge_state.advance_cursor("sess-x", 100, last_episode_seq=3,
                                    db_path=self.db_path)
        bridge_state.advance_cursor("sess-x", 200, db_path=self.db_path)
        cur = bridge_state.get_cursor("sess-x", db_path=self.db_path)
        self.assertEqual(cur["last_episode_seq"], 3,
                         "不帶 last_episode_seq 的 advance 不得動既有序號")

    def test_advance_rejects_invalid_values(self):
        for bad in (0, -1, "5", True, None):
            with self.assertRaises(ValueError, msg=f"{bad!r} 應被拒"):
                bridge_state.advance_cursor("sess-x", bad, db_path=self.db_path)
        with self.assertRaises(ValueError):
            bridge_state.advance_cursor("sess-x", 5, last_episode_seq=-1,
                                        db_path=self.db_path)

    def test_profiles_are_isolated_by_composite_key(self):
        """同名 session、不同 profile：cursor 各自獨立（提案 §6.1 的結構性保證；
        完整的 #27 fail-closed 行為屬 2.4d-2/3 的 scanner/importer 測試）。"""
        bridge_state.advance_cursor("sess-x", 100, source_profile="default",
                                    db_path=self.db_path)
        bridge_state.advance_cursor("sess-x", 7, source_profile="nemocoding",
                                    db_path=self.db_path)
        cur_default = bridge_state.get_cursor("sess-x", db_path=self.db_path)
        cur_other = bridge_state.get_cursor("sess-x", source_profile="nemocoding",
                                            db_path=self.db_path)
        self.assertEqual(cur_default["last_captured_message_id"], 100)
        self.assertEqual(cur_other["last_captured_message_id"], 7,
                         "不同 profile 的同名 session 絕不共用 cursor")

    def test_advance_on_legacy_db_creates_table(self):
        legacy = Path(self._tmpdir.name) / "legacy.db"
        with _raw_conn(legacy) as conn:
            with conn:
                conn.execute(bridge_state.CREATE_TABLE_SQL)
        result = bridge_state.advance_cursor("sess-x", 10, db_path=legacy)
        self.assertTrue(result["advanced"])
        self.assertEqual(
            bridge_state.get_cursor("sess-x", db_path=legacy)["last_captured_message_id"],
            10)


class TestCreateEpisode(BridgeStateTestBase):
    """create_episode：原子 transaction（矩陣 #3）、UNIQUE conflict 冪等路徑
    （#23）、boundary 顯式欄位與 event_id 一致（#24）、fail-closed 驗證。"""

    def test_creates_row_and_cursor_together(self):
        result = bridge_state.create_episode(**make_episode(), db_path=self.db_path)
        self.assertTrue(result["created"])
        row = result["row"]
        self.assertEqual(row["event_id"], "hermes:sess-ep:100..120")
        self.assertEqual(row["event_id_range"], row["event_id"],
                         "episode 列的 event_id_range＝episode event_id 本身")
        self.assertEqual(row["import_status"], "discovered")
        self.assertEqual(row["episode_seq"], 1)
        self.assertEqual(row["capture_trigger"], "inactivity")
        self.assertEqual(row["first_message_id"], 100)
        self.assertEqual(row["last_message_id"], 120)
        self.assertEqual(row["source_content_hash"], "a" * 64)
        self.assertEqual(row["retry_count"], 0)
        cur = bridge_state.get_cursor("sess-ep", db_path=self.db_path)
        self.assertEqual(cur["last_captured_message_id"], 120,
                         "cursor 必須在同一呼叫內推進到 boundary 的 last")
        self.assertEqual(cur["last_episode_seq"], 1)

    def test_boundary_explicit_fields_equal_event_id(self):
        """矩陣 #24：解析 event_id 所得 first/last ＝ 顯式欄位 ＝ event_id_range
        ——由同一組值產生，機械一致。"""
        row = bridge_state.create_episode(**make_episode(), db_path=self.db_path)["row"]
        parsed = bridge_state.parse_event_id(row["event_id"])
        self.assertEqual(parsed["kind"], "episode")
        self.assertEqual(parsed["session_id"], row["session_id"])
        self.assertEqual(parsed["first_message_id"], row["first_message_id"])
        self.assertEqual(parsed["last_message_id"], row["last_message_id"])
        self.assertEqual(row["event_id_range"], row["event_id"])

    def test_rejects_mismatched_event_id_range(self):
        """矩陣 #24：repository 拒絕不一致寫入——event_id_range 與 boundary
        衍生的 event_id 不同即拒。"""
        with self.assertRaises(ValueError):
            bridge_state.create_episode(
                **make_episode(event_id_range="hermes:sess-ep:100..999"),
                db_path=self.db_path)
        self.assertIsNone(bridge_state.get_session_state(
            "hermes:sess-ep:100..120", db_path=self.db_path), "拒絕時零寫入")
        self.assertIsNone(bridge_state.get_cursor("sess-ep", db_path=self.db_path))

    def test_atomicity_injected_failure_rolls_back_row_and_cursor(self):
        """矩陣 #3：create_episode 中途失敗（注入例外於 cursor 前進步驟）→
        無 episode 列且 cursor 未動——同一 transaction，要嘛全有要嘛全無。"""
        original = bridge_state._advance_cursor_locked

        def boom(*args, **kwargs):
            raise RuntimeError("injected mid-transaction failure")

        bridge_state._advance_cursor_locked = boom
        try:
            with self.assertRaises(RuntimeError):
                bridge_state.create_episode(**make_episode(), db_path=self.db_path)
        finally:
            bridge_state._advance_cursor_locked = original
        self.assertIsNone(
            bridge_state.get_session_state("hermes:sess-ep:100..120",
                                           db_path=self.db_path),
            "episode 列必須被 rollback")
        self.assertIsNone(bridge_state.get_cursor("sess-ep", db_path=self.db_path),
                          "cursor 必須未動")
        # 事故後重跑要能成功（transaction 乾淨收場，沒有殘留半套狀態）
        result = bridge_state.create_episode(**make_episode(), db_path=self.db_path)
        self.assertTrue(result["created"])

    def test_same_boundary_conflict_is_idempotent_noop(self):
        """矩陣 #23：撞既有 event_id → 既有列不動、cursor 推進到該 boundary
        的 last、不新增列。"""
        first = bridge_state.create_episode(**make_episode(), db_path=self.db_path)
        again = bridge_state.create_episode(
            **make_episode(decision_reason="重掃後重切同一刀",
                           capture_trigger="manual", episode_seq=9,
                           source_content_hash="b" * 64),
            db_path=self.db_path)
        self.assertFalse(again["created"])
        self.assertEqual(again["row"], first["row"],
                         "既有列必須一個欄位都不動（含 decision_reason／trigger／hash）")
        with _raw_conn(self.db_path) as conn:
            count = conn.execute(
                f"SELECT COUNT(*) FROM {bridge_state.TABLE_NAME}").fetchone()[0]
        self.assertEqual(count, 1, "不新增列")
        cur = bridge_state.get_cursor("sess-ep", db_path=self.db_path)
        self.assertEqual(cur["last_captured_message_id"], 120)
        self.assertEqual(cur["last_episode_seq"], 1,
                         "冪等路徑用既有列的 episode_seq，不採信呼叫端的新值")

    def test_conflict_path_never_pulls_cursor_back(self):
        """矩陣 #2＋#23 交界：cursor 已在更前面（例如後續 episode 已切）時，
        重切舊 boundary 連 cursor 都不動。"""
        bridge_state.create_episode(**make_episode(), db_path=self.db_path)
        bridge_state.create_episode(
            **make_episode(episode_seq=2, first_message_id=121,
                           last_message_id=200), db_path=self.db_path)
        bridge_state.create_episode(**make_episode(), db_path=self.db_path)  # 重切 ep1
        cur = bridge_state.get_cursor("sess-ep", db_path=self.db_path)
        self.assertEqual(cur["last_captured_message_id"], 200)
        self.assertEqual(cur["last_episode_seq"], 2)

    def test_sequential_episodes_share_session(self):
        """同 session 多列合法（方案 A 的核心）：ep1、ep2 各自一列、
        cursor 跟到最新。"""
        bridge_state.create_episode(**make_episode(), db_path=self.db_path)
        bridge_state.create_episode(
            **make_episode(episode_seq=2, first_message_id=121,
                           last_message_id=200, capture_trigger="ended"),
            db_path=self.db_path)
        with _raw_conn(self.db_path) as conn:
            rows = conn.execute(
                f"SELECT event_id FROM {bridge_state.TABLE_NAME} "
                "WHERE session_id='sess-ep' ORDER BY episode_seq").fetchall()
        self.assertEqual([r[0] for r in rows],
                         ["hermes:sess-ep:100..120", "hermes:sess-ep:121..200"])

    def test_rejects_non_default_profile_fail_closed(self):
        """提案 §6.1：非 default profile 的 episode 會污染 event_id namespace
        ——repository 層直接拒絕。"""
        with self.assertRaises(ValueError):
            bridge_state.create_episode(
                **make_episode(source_profile="nemocoding"), db_path=self.db_path)
        self.assertIsNone(bridge_state.get_cursor(
            "sess-ep", source_profile="nemocoding", db_path=self.db_path))

    def test_rejects_legacy_trigger_and_invalid_enum(self):
        with self.assertRaises(ValueError):
            bridge_state.create_episode(
                **make_episode(capture_trigger="legacy"), db_path=self.db_path)
        with self.assertRaises(ValueError):
            bridge_state.create_episode(
                **make_episode(capture_trigger="idle"), db_path=self.db_path)

    def test_rejects_missing_hash_and_bad_seq(self):
        """條件必填（提案 §1.2）：episode 列的五個新欄全必填。"""
        with self.assertRaises(ValueError):
            bridge_state.create_episode(
                **make_episode(source_content_hash=""), db_path=self.db_path)
        with self.assertRaises(ValueError):
            bridge_state.create_episode(
                **make_episode(source_content_hash=None), db_path=self.db_path)
        for bad_seq in (0, -1, None, "1"):
            with self.assertRaises(ValueError, msg=f"episode_seq={bad_seq!r}"):
                bridge_state.create_episode(
                    **make_episode(episode_seq=bad_seq), db_path=self.db_path)

    def test_rejects_bad_boundary(self):
        with self.assertRaises(ValueError):
            bridge_state.create_episode(
                **make_episode(first_message_id=121, last_message_id=120),
                db_path=self.db_path)

    def test_conditional_required_for_status(self):
        with self.assertRaises(ValueError):
            bridge_state.create_episode(
                **make_episode(import_status="failed"), db_path=self.db_path)
        with self.assertRaises(ValueError):
            bridge_state.create_episode(
                **make_episode(import_status="to_inbox"), db_path=self.db_path)

    def test_works_on_v1_db_via_upgrade_path(self):
        """升級路徑：對 17 欄舊 db 直接 create_episode——冪等補欄＋建表後成功
        （比照 advance_scan_watermark 對舊 db 的先例）。"""
        v1 = Path(self._tmpdir.name) / "v1.db"
        with _raw_conn(v1) as conn:
            with conn:
                conn.execute(_V1_CREATE_TABLE_SQL)
        result = bridge_state.create_episode(**make_episode(), db_path=v1)
        self.assertTrue(result["created"])
        self.assertEqual(
            bridge_state.get_cursor("sess-ep", db_path=v1)["last_captured_message_id"],
            120)


class TestArchivedTrigger(BridgeStateTestBase):
    """矩陣 #28（2026-07-12 前置任務 2）：capture_trigger='archived' 的
    repository 層契約。**邏輯已測（fixture/mock），非真實 Hermes UI 行為
    驗證**——Unarchive（1→0）的 live 驗證本次跳過，是明確標注的非阻塞
    test gap（見提案 §6）；這裡只用假資料驗證「archived true→false 後
    新增訊息形成下一個獨立 episode」的邏輯本身。"""

    def test_create_episode_accepts_archived_trigger(self):
        result = bridge_state.create_episode(
            **make_episode(capture_trigger="archived"), db_path=self.db_path)
        self.assertTrue(result["created"])
        row = result["row"]
        self.assertEqual(row["capture_trigger"], "archived")
        self.assertEqual(row["event_id"], "hermes:sess-ep:100..120")
        # 矩陣 #24 一致性檢查對 archived 一樣適用。
        parsed = bridge_state.parse_event_id(row["event_id"])
        self.assertEqual(parsed["first_message_id"], row["first_message_id"])
        self.assertEqual(parsed["last_message_id"], row["last_message_id"])

    def test_archived_does_not_bypass_empty_eligible_rule(self):
        """archived 但無新訊息時不建立空 episode——與其他 trigger 共用同一條
        「eligible 為空不切刀」通用規則，這裡驗證 repository 層沒有為
        archived 開特例後門：boundary 不合法（無 eligible 訊息可言）一樣
        fail loud，不會因為 trigger='archived' 就放行。"""
        with self.assertRaises(ValueError):
            bridge_state.create_episode(
                **make_episode(capture_trigger="archived",
                               first_message_id=101, last_message_id=100),
                db_path=self.db_path)

    def test_archived_true_to_false_then_new_messages_forms_next_episode(self):
        """fixture 模擬「archived true→false（Unarchive）後新增訊息」：
        ep1（trigger=archived）先切；假設 session 之後 Unarchive 並累積
        新訊息，下一刀（無論是哪個 trigger）產生 ep2，boundary 相接不重疊，
        ep1 列與內容完全不變（immutability，與 ended／inactivity 同一
        規則）。這只驗證邏輯，不是對 Hermes UI Unarchive 行為的 live 驗證
        （test gap，見提案 §6／類別 docstring）。"""
        first = bridge_state.create_episode(
            **make_episode(capture_trigger="archived"), db_path=self.db_path)
        ep1_row_before = dict(first["row"])
        second = bridge_state.create_episode(
            **make_episode(episode_seq=2, first_message_id=121,
                           last_message_id=150, capture_trigger="manual"),
            db_path=self.db_path)
        self.assertTrue(second["created"])
        ep1_row_after = bridge_state.get_session_state(
            "hermes:sess-ep:100..120", db_path=self.db_path)
        self.assertEqual(ep1_row_after, ep1_row_before,
                         "Unarchive 後新增訊息不得改動已切的 archived episode")
        self.assertEqual(second["row"]["first_message_id"], 121,
                         "新訊息（Unarchive 後累積）形成下一個獨立 episode，"
                         "不併入 ep1")
        cur = bridge_state.get_cursor("sess-ep", db_path=self.db_path)
        self.assertEqual(cur["last_captured_message_id"], 150)
        self.assertEqual(cur["last_episode_seq"], 2)


class TestUpsertEpisodeGuard(BridgeStateTestBase):
    """upsert_session_state 的 v2 邊界：episode 新列只能走 create_episode；
    更新既有 episode 列時五個 episode 欄不可能被洗掉（immutability，§4.4）。"""

    def test_upsert_cannot_create_episode_row(self):
        with self.assertRaises(ValueError):
            bridge_state.upsert_session_state(
                **make_record(session_id="sess-ep"),
                event_id="hermes:sess-ep:100..120", db_path=self.db_path)
        self.assertIsNone(bridge_state.get_session_state(
            "hermes:sess-ep:100..120", db_path=self.db_path))

    def test_upsert_updates_existing_episode_row_preserving_episode_fields(self):
        """importer 的狀態轉換路徑（2.4d-3 會用）：episode 列可被 upsert 更新
        狀態，但 boundary／trigger／hash／seq 原封不動。"""
        bridge_state.create_episode(**make_episode(), db_path=self.db_path)
        updated = bridge_state.upsert_session_state(
            session_id="sess-ep",
            source_profile="default",
            session_source="desktop",
            import_status="to_inbox",
            memory_type="episodic",
            useful_chat=True,
            decision_reason="importer：無敏感命中，落地",
            imported_inbox_path="memory/inbox/hermes_session_sess-ep_ep100-120.md",
            event_id="hermes:sess-ep:100..120",
            event_id_range="hermes:sess-ep:100..120",
            db_path=self.db_path)
        self.assertEqual(updated["import_status"], "to_inbox")
        self.assertEqual(updated["episode_seq"], 1, "upsert 不得洗掉 episode 欄")
        self.assertEqual(updated["capture_trigger"], "inactivity")
        self.assertEqual(updated["first_message_id"], 100)
        self.assertEqual(updated["last_message_id"], 120)
        self.assertEqual(updated["source_content_hash"], "a" * 64)

    def test_upsert_rejects_profile_namespace_event_id(self):
        with self.assertRaises(ValueError):
            bridge_state.upsert_session_state(
                **make_record(), event_id="hermes/nemocoding:sess-001",
                db_path=self.db_path)

    def test_upsert_rejects_episode_event_id_with_mismatched_session(self):
        bridge_state.create_episode(**make_episode(), db_path=self.db_path)
        with self.assertRaises(ValueError):
            bridge_state.upsert_session_state(
                **make_record(session_id="sess-other"),
                event_id="hermes:sess-ep:100..120", db_path=self.db_path)

    def test_touch_and_mark_failed_work_on_episode_rows(self):
        """既有逐列 API 對 episode 列原樣可用（方案 A 的「repository 改動近零」）。"""
        bridge_state.create_episode(**make_episode(), db_path=self.db_path)
        eid = "hermes:sess-ep:100..120"
        touched = bridge_state.touch_last_seen(eid, db_path=self.db_path)
        self.assertEqual(touched["import_status"], "discovered")
        failed = bridge_state.mark_failed(eid, "export 逾時", db_path=self.db_path)
        self.assertEqual(failed["import_status"], "failed")
        self.assertEqual(failed["episode_seq"], 1)
        self.assertEqual(bridge_state.increment_retry_count(eid, db_path=self.db_path), 1)


# 17 欄的 v1 DDL（2.4c 部署側的既成事實，供 migration 測試建 fixture——
# 刻意寫死副本，不引用 bridge_state.CREATE_TABLE_SQL：升級測試的起點必須是
# 歷史 schema，不能跟著現行常數飄）。
_V1_CREATE_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS bridge_sessions (
        session_id                TEXT    NOT NULL,
        source_profile            TEXT    NOT NULL,
        session_source            TEXT    NOT NULL,
        import_status             TEXT    NOT NULL,
        memory_type               TEXT    NOT NULL,
        useful_chat               INTEGER NOT NULL,
        selected_capability_lane  TEXT,
        decision_reason           TEXT    NOT NULL,
        imported_inbox_path       TEXT,
        processed_path            TEXT,
        first_seen_at             TEXT    NOT NULL,
        last_seen_at              TEXT    NOT NULL,
        updated_at                TEXT    NOT NULL,
        retry_count               INTEGER NOT NULL DEFAULT 0,
        error_reason              TEXT,
        event_id                  TEXT    NOT NULL UNIQUE,
        event_id_range            TEXT
    )
"""


class TestMigrate(unittest.TestCase):
    """migrate CLI（提案 §3.1）：冪等（矩陣 #13）、舊 API 不回歸（#14）、
    fail loud、種子的 fail-closed 邊界。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "bridge_state.db"

    def tearDown(self):
        self._tmpdir.cleanup()

    def _insert_v1_row(self, conn, *, sid, status, event_id_range=None,
                       source_profile="default", inbox_path=None,
                       processed_path=None, error_reason=None):
        conn.execute(
            "INSERT INTO bridge_sessions (session_id, source_profile, "
            "session_source, import_status, memory_type, useful_chat, "
            "decision_reason, imported_inbox_path, processed_path, "
            "first_seen_at, last_seen_at, updated_at, error_reason, "
            "event_id, event_id_range) "
            "VALUES (?, ?, 'desktop', ?, 'none', 0, '2.4c 既有記錄', ?, ?, "
            "'2026-07-10T08:00:00+00:00', '2026-07-10T08:00:00+00:00', "
            "'2026-07-10T08:00:00+00:00', ?, ?, ?)",
            (sid, source_profile, status, inbox_path, processed_path,
             error_reason, f"hermes:{sid}", event_id_range))

    def _make_v1_db(self):
        """模擬 2.4c 部署側現況：17 欄＋bridge_meta（含 watermark）＋3 筆
        legacy 記錄（imported／skipped／needs_review 各一）。"""
        with _raw_conn(self.db_path) as conn:
            with conn:
                conn.execute(_V1_CREATE_TABLE_SQL)
                conn.execute(bridge_state.CREATE_META_TABLE_SQL)
                conn.execute(
                    "INSERT INTO bridge_meta (key, value) VALUES "
                    "('scan_watermark', '2026-07-10T08:05:00+00:00')")
                self._insert_v1_row(
                    conn, sid="sess-imp", status="imported",
                    event_id_range="hermes:sess-imp:5..40",
                    inbox_path="memory/inbox/hermes_session_sess-imp.md",
                    processed_path="memory/inbox/.processed/hermes_session_sess-imp.md")
                self._insert_v1_row(conn, sid="sess-skip", status="skipped")
                self._insert_v1_row(
                    conn, sid="sess-rev", status="needs_review",
                    event_id_range="hermes:sess-rev:1..9")

    def test_migrate_adds_columns_backfills_and_seeds_cursor(self):
        """矩陣 #13 前半：5 欄補齊、legacy 回填 capture_trigger='legacy'
        （boundary／hash 欄維持 NULL）、imported 筆 cursor 種子正確、
        skipped／needs_review 不種。"""
        self._make_v1_db()
        summary = bridge_state.migrate(self.db_path)
        self.assertEqual(summary["columns_added"],
                         [name for name, _ in bridge_state.EPISODE_COLUMNS])
        self.assertTrue(summary["cursor_table_created"])
        self.assertEqual(summary["legacy_backfilled"], 3)
        self.assertEqual(summary["cursors_seeded"], 1)

        with _raw_conn(self.db_path) as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(bridge_sessions)")}
        self.assertEqual(len(cols), 22)

        for sid in ("sess-imp", "sess-skip", "sess-rev"):
            rec = bridge_state.get_session_state(f"hermes:{sid}", db_path=self.db_path)
            self.assertEqual(rec["capture_trigger"], "legacy", sid)
            self.assertIsNone(rec["episode_seq"], "legacy 列不捏造 episode 序號")
            self.assertIsNone(rec["first_message_id"])
            self.assertIsNone(rec["last_message_id"])
            self.assertIsNone(rec["source_content_hash"])

        cur = bridge_state.get_cursor("sess-imp", db_path=self.db_path)
        self.assertEqual(cur["last_captured_message_id"], 40,
                         "imported 筆從 event_id_range 的 <last> 種 cursor")
        self.assertEqual(cur["last_episode_seq"], 0)
        self.assertIsNone(bridge_state.get_cursor("sess-skip", db_path=self.db_path))
        self.assertIsNone(
            bridge_state.get_cursor("sess-rev", db_path=self.db_path),
            "needs_review 即使 event_id_range 可解析也不種（提案 §3.1 步驟 4）")
        # watermark 原樣保留
        self.assertEqual(bridge_state.get_scan_watermark(self.db_path),
                         "2026-07-10T08:05:00+00:00")

    def test_migrate_is_idempotent_second_run_is_noop(self):
        """矩陣 #13 後半：第二次 migrate 全程 no-op、資料位元級不變。"""
        self._make_v1_db()
        bridge_state.migrate(self.db_path)
        with _raw_conn(self.db_path) as conn:
            rows_before = conn.execute(
                "SELECT * FROM bridge_sessions ORDER BY event_id").fetchall()
            cursors_before = conn.execute(
                "SELECT * FROM bridge_cursors ORDER BY session_id").fetchall()
        summary = bridge_state.migrate(self.db_path)
        self.assertEqual(summary["columns_added"], [])
        self.assertFalse(summary["cursor_table_created"])
        self.assertEqual(summary["legacy_backfilled"], 0)
        self.assertEqual(summary["cursors_seeded"], 0)
        with _raw_conn(self.db_path) as conn:
            rows_after = conn.execute(
                "SELECT * FROM bridge_sessions ORDER BY event_id").fetchall()
            cursors_after = conn.execute(
                "SELECT * FROM bridge_cursors ORDER BY session_id").fetchall()
        self.assertEqual(rows_before, rows_after)
        self.assertEqual(cursors_before, cursors_after)

    def test_migrate_missing_db_fails_loud(self):
        with self.assertRaises(FileNotFoundError):
            bridge_state.migrate(Path(self._tmpdir.name) / "nope.db")
        self.assertFalse((Path(self._tmpdir.name) / "nope.db").exists(),
                         "fail loud 不得默默建空 db")

    def test_migrate_seed_skips_unparseable_and_non_episode_ranges(self):
        with _raw_conn(self.db_path) as conn:
            with conn:
                conn.execute(_V1_CREATE_TABLE_SQL)
                # event_id_range 是訊息層級（無 '..'）——不種
                self._insert_v1_row(
                    conn, sid="sess-msg", status="imported",
                    event_id_range="hermes:sess-msg:42",
                    inbox_path="memory/inbox/x.md")
                # event_id_range 完全不合法——不種、不炸
                self._insert_v1_row(
                    conn, sid="sess-bad", status="imported",
                    event_id_range="not-an-event-id",
                    inbox_path="memory/inbox/y.md")
        summary = bridge_state.migrate(self.db_path)
        self.assertEqual(summary["cursors_seeded"], 0)
        self.assertEqual(summary["seed_skipped_unparseable"], 2)
        self.assertIsNone(bridge_state.get_cursor("sess-msg", db_path=self.db_path))
        self.assertIsNone(bridge_state.get_cursor("sess-bad", db_path=self.db_path))

    def test_migrate_seed_skips_non_default_profile_fail_closed(self):
        """提案 §6.1：非 default 的列不種 cursor（記錄並跳過，不默默處理）。"""
        with _raw_conn(self.db_path) as conn:
            with conn:
                conn.execute(_V1_CREATE_TABLE_SQL)
                self._insert_v1_row(
                    conn, sid="sess-np", status="imported",
                    source_profile="nemocoding",
                    event_id_range="hermes:sess-np:1..8",
                    inbox_path="memory/inbox/z.md")
        summary = bridge_state.migrate(self.db_path)
        self.assertEqual(summary["cursors_seeded"], 0)
        self.assertEqual(summary["seed_skipped_non_default"], 1)
        self.assertIsNone(bridge_state.get_cursor(
            "sess-np", source_profile="nemocoding", db_path=self.db_path))

    def test_legacy_apis_unregressed_after_migrate(self):
        """矩陣 #14：migrate 後既有 API 全套語義不變（legacy 列照舊可
        upsert 更新、touch、mark_failed、retry、list；schema 對齊 yaml）。"""
        self._make_v1_db()
        bridge_state.migrate(self.db_path)

        # upsert 更新既有 legacy 列：不新增列、first_seen_at 保持
        updated = bridge_state.upsert_session_state(
            session_id="sess-skip", source_profile="default",
            session_source="desktop", import_status="discovered",
            memory_type="none", useful_chat=False,
            decision_reason="人工重啟判定", db_path=self.db_path)
        self.assertEqual(updated["first_seen_at"], "2026-07-10T08:00:00+00:00")
        self.assertEqual(updated["capture_trigger"], "legacy",
                         "upsert 不得洗掉 migrate 回填的 capture_trigger")

        # touch / mark_failed / retry / list 照舊
        touched = bridge_state.touch_last_seen("hermes:sess-imp",
                                               db_path=self.db_path)
        self.assertEqual(touched["import_status"], "imported")
        failed = bridge_state.mark_failed("hermes:sess-rev", "人工標記",
                                          db_path=self.db_path)
        self.assertEqual(failed["import_status"], "failed")
        self.assertEqual(bridge_state.increment_retry_count(
            "hermes:sess-rev", db_path=self.db_path), 1)
        self.assertEqual(
            {r["session_id"] for r in bridge_state.list_by_import_status(
                "imported", db_path=self.db_path)},
            {"sess-imp"})

        # 新增全新 legacy…不對——2.4d 起不再新增 session 層級記錄是 scanner
        # 的政策；repository 的 upsert 本身仍允許（reconcile 對無 ep 段檔案
        # 的回填路徑，提案 §2），語義照舊可用：
        fresh = bridge_state.upsert_session_state(
            **make_record(session_id="sess-new"), db_path=self.db_path)
        self.assertEqual(fresh["event_id"], "hermes:sess-new")
        self.assertIsNone(fresh["capture_trigger"],
                          "migrate 之後新建的 legacy 列 capture_trigger 為 NULL"
                          "（NULL 視同 legacy，schema 定義）")

        # migrate 後的 db 欄位集合對齊 v2 yaml（#13 步驟 5 的 marker 檢查）
        fields = yaml.safe_load(
            (ROOT / "registry" / "bridge_state_schema.yaml").read_text(encoding="utf-8")
        )["fields"]
        with _raw_conn(self.db_path) as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(bridge_sessions)")}
        self.assertEqual(cols, set(fields.keys()))

    def test_ensure_schema_upgrades_v1_db_without_backfill(self):
        """init/ensure_schema 只補結構不回填（回填屬 migrate）——兩者分工
        清楚，ensure_schema 對 v1 db 冪等補欄後 capture_trigger 仍 NULL。"""
        self._make_v1_db()
        bridge_state.ensure_schema(self.db_path)
        rec = bridge_state.get_session_state("hermes:sess-imp", db_path=self.db_path)
        self.assertIsNone(rec["capture_trigger"],
                          "ensure_schema 不做資料回填")
        with _raw_conn(self.db_path) as conn:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn(bridge_state.CURSOR_TABLE_NAME, tables)


if __name__ == "__main__":
    unittest.main()
