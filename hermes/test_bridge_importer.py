#!/usr/bin/env python3
"""hermes/test_bridge_importer.py — v0.1（Stage 2.4c）

hermes/bridge_importer.py 的測試，覆蓋使用者完成定義 1–7 全部：

1. 正常 session：discovered → to_inbox，恰好一個檔案。
2. 敏感 session：被阻擋（needs_review），標記內容零外洩——bridge db 全 dump、
   importer stdout/stderr、inbox 目錄全樹都不得出現命中原文（只能有類別標籤）；
   且敏感判定用完整內容（標記藏在截斷摘錄之外的訊息仍要命中）。
3. 不明或錯誤：fail-closed（failed＋可讀 error_reason，不落地、不含內容）。
4. 重跑不重複寫檔、不重設 first_seen_at。
5. DB 更新失敗後檔案已落地 → bridge_scanner reconcile 回填 to_inbox（真的走通）。
6. Hermes state.db／jobs.db／telegram.json 零修改（fingerprint 把關）。
7. 尚未 enqueue、尚未 headless CoS、尚未 importer timer（靜態把關）。

另涵蓋：dry-run 零寫入（含 db 不建檔、retry_count 不遞增）、政策檔缺失
fail loud 整批不跑、retry 上限轉 needs_review、--limit、CLI exit code。

隔離保證（沿用 test_bridge_scanner.py 的 fingerprint 慣例）：全程只用 temp
目錄的 db 與 inbox；setUpModule/tearDownModule 對受保護檔案與目錄做前後比對。

執行：.venv/Scripts/python.exe hermes/test_bridge_importer.py
"""
import ast
import contextlib
import hashlib
import io
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bridge_importer  # noqa: E402（import 時會把 session_adapter 加進 sys.path）
import bridge_scanner  # noqa: E402
import bridge_state  # noqa: E402
import adapter as adapter_module  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / "hermes" / "state"
REAL_INBOX = ROOT / "memory" / "inbox"
SYSTEMD_DIR = ROOT / "hermes" / "systemd"
SEED_SQL = (ROOT / "hermes" / "session_adapter" / "tests" / "fixtures"
            / "seed_state_db.sql")

T1 = "2026-07-10T01:00:00+00:00"
T2 = "2026-07-10T02:00:00+00:00"
T3 = "2026-07-10T03:00:00+00:00"

# 敏感 fixture 的標記字串——測試斷言它們不得外洩到任何地方。
# （假 API key 命中 api_tokens 的 sk- pattern；"血壓 999/888" 命中 health_data）
FAKE_API_KEY = "sk-FAKE_TEST_1234567890abcdef"
FAKE_HEALTH_VALUE = "999/888"
SECRET_MARKERS = (FAKE_API_KEY, FAKE_HEALTH_VALUE)

# 正常 session：5 則 message、總字元 > 200、無敏感、無測試標記
OK_MESSAGES = [
    ("user", "我們來決定 bridge importer 的落地格式：檔名必須是 deterministic，"
             "重跑不能產生重複檔案，這是既有 adapter 慣例的延續。"),
    ("assistant", "同意。沿用 hermes_session 檔名慣例即可，exclusive create "
                  "天然擋掉重複落地，不需要另外設計鎖。"),
    ("user", "另外判定結果要寫回 bridge_state，先有檔案再記狀態，順序不能反過來。"),
    ("assistant", "了解，落地成功才更新狀態；失敗就留給 reconcile 依目錄位置回填。"),
    ("user", "就這麼定案。這段對話刻意寫得夠長，能通過結構性門檻的檢查。"),
]

# 敏感 session：標記只在第 1 則訊息，後面 40 則填充——把標記推出預設
# 「尾端 30 則」截斷摘錄窗口之外，驗證偵測用的是完整內容不是截斷摘錄。
SECRET_MESSAGES = [
    ("user", f"先記一下我的 API 金鑰 {FAKE_API_KEY} 之後要用；"
             f"另外回報健康數值：血壓 {FAKE_HEALTH_VALUE}，有點高。"),
] + [
    ("assistant" if i % 2 else "user",
     f"後續討論第 {i} 段：這一段沒有機密，只是把摘錄窗口往後推。")
    for i in range(1, 41)
]

MARKER_MESSAGES = [
    ("user", "_hermes_bridge_test"),
    ("assistant", "STAGE0-OK：管線自檢通過，本 session 僅為排程健檢訊號。"),
]

SHORT_MESSAGES = [("user", "hi"), ("assistant", "hello")]

# 不可觸碰的真實檔案（存在與否、mtime、大小都不得被測試改變）
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


class ImporterTestBase(unittest.TestCase):
    """temp Hermes db（seed schema＋fixture sessions）＋ temp bridge db＋temp inbox。
    政策檔預設用 repo 正本 registry/consolidation_policy.yaml——順便把
    「repo 政策真的能偵測 canonical 標記」當成整合事實來測。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.hermes_db = self.tmp / "state.db"
        with contextlib.closing(sqlite3.connect(self.hermes_db)) as conn:
            conn.executescript(SEED_SQL.read_text(encoding="utf-8"))
            self._add_session(conn, "sess_ok", OK_MESSAGES, source="telegram")
            self._add_session(conn, "sess_secret", SECRET_MESSAGES)
            self._add_session(conn, "sess_marker", MARKER_MESSAGES,
                              title="排程自檢")
            self._add_session(conn, "_probe_test_naming", SHORT_MESSAGES,
                              title="_probe_test")
            self._add_session(conn, "sess_short", SHORT_MESSAGES)
            conn.commit()
        self.bridge_db = self.tmp / "bridge_state.db"
        self.inbox = self.tmp / "inbox"
        self.inbox.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    @staticmethod
    def _add_session(conn, sid, messages, *, source="cli", title=None,
                     ended=1783600000.0):
        conn.execute(
            "INSERT INTO sessions (id, source, model, started_at, ended_at, "
            "end_reason, message_count, title) VALUES (?,?,?,?,?,?,?,?)",
            (sid, source, "test-model", ended - 100, ended, "stop",
             len(messages), title))
        ts = ended - 90
        for role, content in messages:
            conn.execute(
                "INSERT INTO messages (session_id, role, content, timestamp, "
                "active, compacted) VALUES (?,?,?,?,1,0)", (sid, role, content, ts))
            ts += 1

    def seed_discovered(self, sid, *, session_source="cli", seen_at=T1):
        bridge_state.init_db(self.bridge_db)
        return bridge_state.upsert_session_state(
            session_id=sid, source_profile="default",
            session_source=session_source, import_status="discovered",
            memory_type="none", useful_chat=False,
            decision_reason="bridge scan 首次發現（測試種子）",
            seen_at=seen_at, db_path=self.bridge_db)

    def seed_failed(self, sid, *, retries=0, seen_at=T1):
        bridge_state.init_db(self.bridge_db)
        rec = bridge_state.upsert_session_state(
            session_id=sid, source_profile="default", session_source="cli",
            import_status="failed", memory_type="none", useful_chat=False,
            decision_reason="前次匯入失敗（測試種子）",
            error_reason="匯入失敗（階段：export／讀取完整內容；例外類別：KeyError）"
                         "——fail-closed，不落地",
            seen_at=seen_at, db_path=self.bridge_db)
        for _ in range(retries):
            bridge_state.increment_retry_count(rec["event_id"],
                                               db_path=self.bridge_db)
        return bridge_state.get_session_state(rec["event_id"],
                                              db_path=self.bridge_db)

    def run_import(self, **kwargs):
        """跑 import 並捕捉 stdout/stderr（零外洩斷言用）。"""
        kwargs.setdefault("state_db", self.hermes_db)
        kwargs.setdefault("bridge_db", self.bridge_db)
        kwargs.setdefault("inbox_dir", self.inbox)
        kwargs.setdefault("seen_at", T2)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            result = bridge_importer.import_discovered(**kwargs)
            bridge_importer._print_result(result)
        self.last_stdout = out.getvalue()
        self.last_stderr = err.getvalue()
        return result

    # ---- 檢視工具 ----

    def rec(self, sid) -> dict | None:
        return bridge_state.get_session_state(f"hermes:{sid}",
                                              db_path=self.bridge_db)

    def all_rows(self) -> dict:
        if not self.bridge_db.exists():
            return {}
        with contextlib.closing(sqlite3.connect(self.bridge_db)) as conn:
            conn.row_factory = sqlite3.Row
            return {r["event_id"]: dict(r)
                    for r in conn.execute("SELECT * FROM bridge_sessions")}

    def db_dump(self) -> str:
        """bridge db 全部 table 的完整 SQL dump（零外洩斷言用）。"""
        with contextlib.closing(sqlite3.connect(self.bridge_db)) as conn:
            return "\n".join(conn.iterdump())

    def inbox_files(self) -> list[Path]:
        return sorted(p for p in self.inbox.rglob("*") if p.is_file())

    def inbox_text(self) -> str:
        return "\n".join(p.read_text(encoding="utf-8")
                         for p in self.inbox_files())

    def action_for(self, result, sid) -> dict:
        return next(a for a in result["actions"] if a["session_id"] == sid)


class TestPolicyLoading(ImporterTestBase):
    def _policy_doc(self):
        return {
            "usefulness": {"min_content_chars": 200, "min_session_messages": 4},
            "guardrails": {
                "test_session": {
                    "detect": ["underscore_prefix_with_test",
                               {"known_markers": ["STAGE0-OK",
                                                  "_hermes_bridge_test"]}],
                },
                "sensitive": {
                    "categories": ["credentials", "api_tokens"],
                    "detection": {
                        "credentials": [r"-----BEGIN [A-Z ]*PRIVATE KEY-----"],
                        "api_tokens": [r"\bsk-[A-Za-z0-9_-]{16,}\b"],
                    },
                },
            },
        }

    def _write_policy(self, doc) -> Path:
        import yaml
        path = self.tmp / "policy.yaml"
        path.write_text(yaml.safe_dump(doc, allow_unicode=True),
                        encoding="utf-8")
        return path

    def test_repo_policy_loads_and_covers_all_categories(self):
        """守住版控正本：registry/consolidation_policy.yaml 必須能載入，
        且 categories 的每個成員都有可編譯的 pattern。"""
        policy = bridge_importer.load_guardrail_policy()
        self.assertEqual(
            set(policy["sensitive_patterns"]),
            {"credentials", "api_tokens", "passwords", "health_data",
             "financial_pii"})
        self.assertTrue(all(policy["sensitive_patterns"].values()))
        self.assertIn("STAGE0-OK", policy["test_markers"])
        self.assertIn("_hermes_bridge_test", policy["test_markers"])
        self.assertTrue(policy["underscore_rule"])
        self.assertEqual(policy["min_content_chars"], 200)
        self.assertEqual(policy["min_session_messages"], 4)

    def test_missing_policy_file_fails_loud_and_nothing_runs(self):
        """政策檔讀不到 → fail loud 整批不跑：不落地、不動 db。"""
        self.seed_discovered("sess_ok")
        rows_before = self.all_rows()
        with self.assertRaises(FileNotFoundError):
            bridge_importer.import_discovered(
                state_db=self.hermes_db, bridge_db=self.bridge_db,
                inbox_dir=self.inbox,
                policy_path=self.tmp / "no_such_policy.yaml")
        self.assertEqual(self.inbox_files(), [], "整批不跑：不得落地任何檔案")
        self.assertEqual(self.all_rows(), rows_before, "整批不跑：db 不得變動")

    def test_policy_without_detection_section_fails_loud(self):
        doc = self._policy_doc()
        del doc["guardrails"]["sensitive"]["detection"]
        with self.assertRaises(ValueError):
            bridge_importer.load_guardrail_policy(self._write_policy(doc))

    def test_policy_missing_category_patterns_fails_loud(self):
        doc = self._policy_doc()
        del doc["guardrails"]["sensitive"]["detection"]["api_tokens"]
        with self.assertRaises(ValueError):
            bridge_importer.load_guardrail_policy(self._write_policy(doc))

    def test_policy_bad_regex_fails_loud(self):
        doc = self._policy_doc()
        doc["guardrails"]["sensitive"]["detection"]["api_tokens"] = ["([unclosed"]
        with self.assertRaises(ValueError):
            bridge_importer.load_guardrail_policy(self._write_policy(doc))

    def test_max_import_retries_loading(self):
        # repo 正本：欄位存在且為非負整數
        value = bridge_importer.load_max_import_retries()
        self.assertIsInstance(value, int)
        self.assertGreaterEqual(value, 0)
        # 設定檔不存在 → fail loud
        with self.assertRaises(FileNotFoundError):
            bridge_importer.load_max_import_retries(self.tmp / "no.yaml")
        # 欄位缺失 → 預設 3
        cfg = self.tmp / "cfg.yaml"
        cfg.write_text('cutover: "2026-07-10T00:00:00Z"\n', encoding="utf-8")
        self.assertEqual(bridge_importer.load_max_import_retries(cfg), 3)
        # 欄位型別不對 → ValueError
        cfg.write_text('max_import_retries: "three"\n', encoding="utf-8")
        with self.assertRaises(ValueError):
            bridge_importer.load_max_import_retries(cfg)


class TestImportFlow(ImporterTestBase):
    def test_normal_session_lands_exactly_one_file(self):
        """完成定義 1：discovered → to_inbox，恰好一個檔案；
        完成定義 6：來源 Hermes state.db 一個 byte 都不能動。"""
        self.seed_discovered("sess_ok", session_source="telegram")
        source_before = _sha256(self.hermes_db)
        result = self.run_import()

        files = self.inbox_files()
        self.assertEqual(len(files), 1, "只能產生恰好一個檔案")
        self.assertEqual(files[0].name, "hermes_session_sess_ok.md")
        body = files[0].read_text(encoding="utf-8")
        self.assertIn("session_id: sess_ok", body)

        rec = self.rec("sess_ok")
        self.assertEqual(rec["import_status"], "to_inbox")
        self.assertTrue(rec["imported_inbox_path"].endswith(
            "hermes_session_sess_ok.md"))
        self.assertIs(rec["useful_chat"], True)
        self.assertEqual(rec["memory_type"], "episodic")
        self.assertTrue(rec["event_id_range"].startswith("hermes:sess_ok:"))
        self.assertEqual(rec["retry_count"], 0)
        self.assertEqual(self.action_for(result, "sess_ok")["action"], "to_inbox")

        self.assertEqual(_sha256(self.hermes_db), source_before,
                         "importer 只能經 snapshot 讀取，來源 db 零修改")

    def test_rerun_no_duplicates_no_first_seen_reset(self):
        """完成定義 4：重跑不重複寫檔、不重設 first_seen_at。"""
        self.seed_discovered("sess_ok", seen_at=T1)
        self.run_import(seen_at=T2)
        first = self.rec("sess_ok")
        result2 = self.run_import(seen_at=T3)
        self.assertEqual(result2["queued"], 0,
                         "to_inbox 不在佇列，重跑沒有東西可處理")
        self.assertEqual(len(self.inbox_files()), 1, "重跑不得產生第二個檔案")
        rec = self.rec("sess_ok")
        self.assertEqual(rec["first_seen_at"], T1, "first_seen_at 不得被重跑洗掉")
        self.assertEqual(rec, first, "重跑不得改動任何欄位")

    def test_sensitive_blocked_zero_leak(self):
        """完成定義 2：敏感 session 被阻擋（needs_review），標記內容零外洩——
        bridge db 全 dump、stdout/stderr、inbox 全樹都不得出現命中原文。
        標記藏在第 1 則訊息（在預設尾端 30 則截斷摘錄之外）：驗證偵測用
        完整內容，不是截斷摘錄。"""
        # fixture 形狀自檢：41 則訊息、標記只在第 1 則（截斷窗口之外）
        self.assertGreater(len(SECRET_MESSAGES), 30)
        self.assertIn(FAKE_API_KEY, SECRET_MESSAGES[0][1])
        self.assertTrue(all(FAKE_API_KEY not in c for _, c in SECRET_MESSAGES[1:]))

        self.seed_discovered("sess_secret")
        result = self.run_import()

        rec = self.rec("sess_secret")
        self.assertEqual(rec["import_status"], "needs_review")
        self.assertIn("sensitive:api_tokens", rec["decision_reason"])
        self.assertIn("sensitive:health_data", rec["decision_reason"])
        self.assertIs(rec["useful_chat"], False)
        self.assertEqual(self.inbox_files(), [], "敏感 session 決不落地")
        action = self.action_for(result, "sess_secret")
        self.assertEqual(action["action"], "blocked_sensitive")

        dump = self.db_dump()
        printed = self.last_stdout + self.last_stderr
        tree = self.inbox_text()
        for marker in SECRET_MARKERS:
            self.assertNotIn(marker, dump, "命中原文不得進 bridge db")
            self.assertNotIn(marker, printed, "命中原文不得出現在 stdout/stderr")
            self.assertNotIn(marker, tree, "命中原文不得落在 inbox 任何檔案")

    def test_test_marker_session_skipped(self):
        self.seed_discovered("sess_marker")
        self.run_import()
        rec = self.rec("sess_marker")
        self.assertEqual(rec["import_status"], "skipped")
        self.assertIn("exclusion:test_session", rec["decision_reason"])
        self.assertEqual(self.inbox_files(), [])

    def test_underscore_test_naming_skipped(self):
        self.seed_discovered("_probe_test_naming")
        self.run_import()
        rec = self.rec("_probe_test_naming")
        self.assertEqual(rec["import_status"], "skipped")
        self.assertIn("exclusion:test_session", rec["decision_reason"])

    def test_short_session_skipped(self):
        self.seed_discovered("sess_short")
        self.run_import()
        rec = self.rec("sess_short")
        self.assertEqual(rec["import_status"], "skipped")
        self.assertIn("exclusion:too_short", rec["decision_reason"])
        self.assertEqual(self.inbox_files(), [])

    def test_mixed_batch_single_pass(self):
        """一次跑完整批：各判定互不干擾，仍恰好只落地一個檔案。"""
        for sid in ("sess_ok", "sess_secret", "sess_marker", "sess_short",
                    "sess_gone"):  # sess_gone 不存在於 Hermes db → failed
            self.seed_discovered(sid)
        result = self.run_import()
        self.assertEqual(result["counts"], {
            "to_inbox": 1, "blocked_sensitive": 1, "skipped_exclusion": 2,
            "failed": 1})
        self.assertEqual(len(self.inbox_files()), 1)
        self.assertEqual(self.rec("sess_ok")["import_status"], "to_inbox")
        self.assertEqual(self.rec("sess_secret")["import_status"], "needs_review")
        self.assertEqual(self.rec("sess_marker")["import_status"], "skipped")
        self.assertEqual(self.rec("sess_short")["import_status"], "skipped")
        self.assertEqual(self.rec("sess_gone")["import_status"], "failed")


class TestErrorsAndRetry(ImporterTestBase):
    def test_missing_session_fail_closed(self):
        """完成定義 3：判不出來＝不匯入——session 不在 Hermes db 裡 →
        failed＋可讀 error_reason（只含階段與例外類別，不含內容）。"""
        self.seed_discovered("sess_gone")
        self.run_import()
        rec = self.rec("sess_gone")
        self.assertEqual(rec["import_status"], "failed")
        self.assertIn("export／讀取完整內容", rec["error_reason"])
        self.assertIn("KeyError", rec["error_reason"])
        self.assertIn("fail-closed", rec["error_reason"])
        self.assertEqual(self.inbox_files(), [], "fail-closed 決不落地")

    def test_failed_retry_increments_then_succeeds(self):
        """前次 failed 的 session 重新嘗試：retry_count +1，成功後 to_inbox。"""
        self.seed_failed("sess_ok", retries=0)
        result = self.run_import()
        rec = self.rec("sess_ok")
        self.assertEqual(rec["import_status"], "to_inbox")
        self.assertEqual(rec["retry_count"], 1, "re-attempt 當下要遞增")
        self.assertEqual(len(self.inbox_files()), 1)
        self.assertEqual(self.action_for(result, "sess_ok")["action"], "to_inbox")

    def test_failed_retry_fails_again_updates_error(self):
        self.seed_failed("sess_gone", retries=1)
        self.run_import()
        rec = self.rec("sess_gone")
        self.assertEqual(rec["import_status"], "failed")
        self.assertEqual(rec["retry_count"], 2)
        self.assertIn("KeyError", rec["error_reason"])

    def test_retry_limit_transitions_needs_review(self):
        """retry_count 達上限：不再自動嘗試，轉 needs_review。"""
        before = self.seed_failed("sess_gone", retries=3)
        result = self.run_import(max_retries=3)
        rec = self.rec("sess_gone")
        self.assertEqual(rec["import_status"], "needs_review")
        self.assertEqual(rec["retry_count"], 3, "達上限後不得再遞增")
        self.assertIn("重試已達上限", rec["decision_reason"])
        self.assertEqual(rec["error_reason"], before["error_reason"],
                         "原 error_reason 保留供人工檢視")
        self.assertEqual(self.action_for(result, "sess_gone")["action"],
                         "retry_exhausted")
        # 再跑一次：needs_review 不在佇列，狀態穩定
        result2 = self.run_import(max_retries=3)
        self.assertEqual(result2["queued"], 0)


class TestReconcileRecovery(ImporterTestBase):
    def test_db_update_failure_recovered_by_reconcile(self):
        """完成定義 5：檔案已落地但狀態更新失敗 → importer 不中斷、db 維持
        原狀態；下次 bridge_scanner reconcile 依目錄位置回填 to_inbox。"""
        self.seed_discovered("sess_ok")
        original = bridge_state.upsert_session_state

        def boom(**kwargs):
            if kwargs.get("import_status") == "to_inbox":
                raise sqlite3.OperationalError("disk I/O error（模擬）")
            return original(**kwargs)

        bridge_importer.bridge_state.upsert_session_state = boom
        try:
            result = self.run_import()
        finally:
            bridge_importer.bridge_state.upsert_session_state = original

        action = self.action_for(result, "sess_ok")
        self.assertEqual(action["action"], "db_update_failed_recoverable")
        self.assertEqual(len(self.inbox_files()), 1, "檔案已落地")
        self.assertEqual(self.rec("sess_ok")["import_status"], "discovered",
                         "狀態未更新（模擬 DB 失敗）")

        # reconcile 恢復路徑（真的走通既有實作，不是模擬）
        bridge_scanner.reconcile(inbox_dir=self.inbox, bridge_db=self.bridge_db,
                                 seen_at=T3)
        rec = self.rec("sess_ok")
        self.assertEqual(rec["import_status"], "to_inbox",
                         "reconcile 依目錄位置回填 to_inbox")
        self.assertTrue(rec["imported_inbox_path"])
        # 回填後 importer 重跑：佇列為空，不重複落地
        result2 = self.run_import(seen_at=T3)
        self.assertEqual(result2["queued"], 0)
        self.assertEqual(len(self.inbox_files()), 1)

    def test_already_imported_defers_to_reconcile(self):
        """inbox 已有同 session 檔案（前次 DB 更新失敗或人工匯過）：不是錯誤
        ——記 log、db 不動，留給 reconcile 回填（回填邏輯只有一份實作）。"""
        (self.inbox / "hermes_session_sess_ok.md").write_text(
            "---\nschema: claudecodeos.inbox.v1\nsource: hermes-session\n"
            "session_id: sess_ok\n---\n\n# Hermes session 匯入 — sess_ok\n"
            "\n- 來源：hermes/cli\n\n先前人工匯入的內容\n", encoding="utf-8")
        self.seed_discovered("sess_ok")
        result = self.run_import()
        action = self.action_for(result, "sess_ok")
        self.assertEqual(action["action"], "already_imported_defer_reconcile")
        self.assertEqual(len(self.inbox_files()), 1, "不得重複落地")
        self.assertEqual(self.rec("sess_ok")["import_status"], "discovered",
                         "importer 不當場對帳，db 維持原狀態")
        bridge_scanner.reconcile(inbox_dir=self.inbox, bridge_db=self.bridge_db,
                                 seen_at=T3)
        self.assertEqual(self.rec("sess_ok")["import_status"], "to_inbox")


class TestDryRunAndLimit(ImporterTestBase):
    def test_dry_run_zero_writes(self):
        """dry-run 零寫入：不落地、db 列零變動、retry_count 不遞增；
        回報的預測動作同樣只含類別標籤。"""
        self.seed_discovered("sess_ok")
        self.seed_discovered("sess_secret")
        self.seed_failed("sess_gone", retries=1)
        rows_before = self.all_rows()
        result = self.run_import(dry_run=True)

        self.assertTrue(result["dry_run"])
        self.assertEqual(self.inbox_files(), [], "dry-run 不得落地")
        self.assertEqual(self.all_rows(), rows_before,
                         "dry-run 不得改動任何列（含 retry_count）")
        self.assertEqual(self.action_for(result, "sess_ok")["action"], "to_inbox")
        self.assertEqual(self.action_for(result, "sess_secret")["action"],
                         "blocked_sensitive")
        printed = self.last_stdout + self.last_stderr
        for marker in SECRET_MARKERS:
            self.assertNotIn(marker, printed, "dry-run 輸出同樣零外洩")

    def test_missing_bridge_db_creates_nothing(self):
        """bridge db 不存在＝沒有 scanner 產出：dry-run 與真跑都不建立 db 檔。"""
        absent = self.tmp / "absent" / "bridge_state.db"
        for dry in (True, False):
            result = bridge_importer.import_discovered(
                dry_run=dry, state_db=self.hermes_db, bridge_db=absent,
                inbox_dir=self.inbox)
            self.assertFalse(result["bridge_db_exists"])
            self.assertEqual(result["actions"], [])
            self.assertFalse(absent.exists(), "不得建立 bridge db 檔案")

    def test_limit_caps_processing(self):
        self.seed_discovered("sess_ok", seen_at=T1)
        self.seed_discovered("sess_short", seen_at=T2)
        result = self.run_import(limit=1)
        self.assertEqual(result["queued"], 1)
        self.assertEqual([a["session_id"] for a in result["actions"]],
                         ["sess_ok"], "--limit 依 first_seen_at 順序截斷")
        self.assertEqual(self.rec("sess_short")["import_status"], "discovered",
                         "超出 limit 的留在 discovered，下次再處理")
        with self.assertRaises(ValueError):
            self.run_import(limit=0)


class TestStaticAndCli(ImporterTestBase):
    """完成定義 7：尚未 enqueue、尚未 headless CoS、尚未 importer timer；
    另守住 scanner 同款的讀取邊界。"""

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

    def test_importer_static_guarantees(self):
        code = self._code_only_source(
            Path(__file__).resolve().parent / "bridge_importer.py")
        self.assertNotIn("sqlite3", code,
                         "importer 不得自己開 SQLite——一律經 adapter 與 bridge_state")
        self.assertNotIn("immutable", code)
        self.assertIn("snapshot=True", code,
                      "讀 Hermes state.db 必須走 snapshot 模式")
        self.assertNotIn("snapshot=False", code)
        for forbidden in ("enqueue", "jobs.db", "telegram", "LOCALAPPDATA",
                          "claude -p", "invoke_cos", "systemd"):
            self.assertNotIn(forbidden, code,
                             f"2.4c 邊界：importer 不得出現 {forbidden!r}")

    def test_no_importer_scheduler_units(self):
        """importer 尚未排程化：hermes/systemd 不得有 importer 的 unit 檔。"""
        offenders = [p.name for p in SYSTEMD_DIR.iterdir()
                     if "import" in p.name.lower()]
        self.assertEqual(offenders, [])

    def test_cli_exit_codes(self):
        cfg = self.tmp / "bridge.yaml"
        cfg.write_text('cutover: "2026-07-10T00:00:00Z"\nmax_import_retries: 3\n',
                       encoding="utf-8")
        self.seed_discovered("sess_ok")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            # 政策檔缺失 → exit 1（fail loud）
            code_err = bridge_importer._cli([
                "--bridge-db", str(self.bridge_db), "import",
                "--state-db", str(self.hermes_db), "--inbox", str(self.inbox),
                "--policy", str(self.tmp / "no_policy.yaml"),
                "--config", str(cfg)])
            # dry-run 正常路徑 → exit 0
            code_ok = bridge_importer._cli([
                "--bridge-db", str(self.bridge_db), "import", "--dry-run",
                "--state-db", str(self.hermes_db), "--inbox", str(self.inbox),
                "--config", str(cfg)])
        self.assertEqual(code_err, 1)
        self.assertEqual(code_ok, 0)
        self.assertEqual(self.inbox_files(), [], "dry-run 不得落地")
        # 參數用法錯誤 → argparse exit 2
        with contextlib.redirect_stderr(io.StringIO()), \
                self.assertRaises(SystemExit) as ctx:
            bridge_importer._build_parser().parse_args([])
        self.assertEqual(ctx.exception.code, 2)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    unittest.main(verbosity=2)
