#!/usr/bin/env python3
"""dashboard/test_api.py — P1 唯讀 API 的測試(提案 §4.2 DoD 第 2/3/4 條)。

覆蓋:
- §3.1:CORS——偽造 Origin: http://evil.example → 403;白名單 origin 拿到
  Access-Control-Allow-Origin。
- §3.2:任何非 GET(POST/PUT/DELETE/PATCH/OPTIONS)→ 405,全域攔截。
- import guard:AST 白名單鎖定 api.py/redact.py 的 import 集合,不含任何
  已知寫入模組(hermes/db.py、bridge_dispatch、cron.jobs、subprocess…)。
- bind 檢核:server 只 bind 127.0.0.1;create_server 沒有 host 參數;
  原始碼無 0.0.0.0。
- bot_token 不外洩(DoD 第 3 條):adapter config endpoint 回應全文不含
  假 token fixture 值(一律 FAKE_ 前綴,tempfile 隔離)。
- endpoint 行為:與 data.py 一對一的十個 endpoint + 參數驗證 + traversal 拒絕。
- redact 第三道掃描:eyJ/高熵長字串被置換,一般內容不誤傷。

fixture 說明:跟 test_data.py 同款——用暫存目錄與 hermes/db.py 建測試資料
(只在測試裡 import db.py;api.py/data.py 本身不 import)。

執行:.venv/Scripts/python.exe dashboard/test_api.py
"""
import ast
import inspect
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hermes"))
import api  # noqa: E402
import data  # noqa: E402
import data_resident  # noqa: E402
import data_stage3  # noqa: E402
import db  # noqa: E402
import redact  # noqa: E402

DASHBOARD_DIR = Path(__file__).resolve().parent

# 假密鑰 fixture:一律 FAKE_ 前綴,絕不使用真實值(提案 §3.4)
FAKE_BOT_TOKEN = "FAKE_1234567890_AbCdEfGhIjKlMnOpQrStUvWxYz_TEST_ONLY"
FAKE_P2_ACCESS_TOKEN = "FAKE_SECRET_api_access_abc123456"
FAKE_P2_REFRESH_TOKEN = "FAKE_SECRET_api_refresh_zyx98765"


class ApiServerTestCase(unittest.TestCase):
    """共用的 server fixture:port 0 起在 thread 裡,資料路徑指向 tempdir。"""

    @classmethod
    def setUpClass(cls):
        cls.server = api.create_server(port=0)
        cls.port = cls.server.server_address[1]
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def setUp(self):
        self._tmpdir = Path(tempfile.mkdtemp())
        self._orig = {
            "jobs_db": data.JOBS_DB_PATH,
            "db_path": db.DB_PATH,
            "log_dir": data.LOG_DIR,
            "memory_dir": data.MEMORY_DIR,
            "registry_dir": data.REGISTRY_DIR,
            "config_dir": data.CONFIG_DIR,
        }
        db_path = self._tmpdir / "jobs.db"
        data.JOBS_DB_PATH = db_path
        db.DB_PATH = db_path
        db.init_db()
        data.LOG_DIR = self._tmpdir / "logs"
        data.LOG_DIR.mkdir()
        data.MEMORY_DIR = self._tmpdir / "memory"
        data.MEMORY_DIR.mkdir()
        data.REGISTRY_DIR = self._tmpdir / "registry"
        data.REGISTRY_DIR.mkdir()
        data.CONFIG_DIR = self._tmpdir / "config"
        data.CONFIG_DIR.mkdir()

    def tearDown(self):
        data.JOBS_DB_PATH = self._orig["jobs_db"]
        db.DB_PATH = self._orig["db_path"]
        data.LOG_DIR = self._orig["log_dir"]
        data.MEMORY_DIR = self._orig["memory_dir"]
        data.REGISTRY_DIR = self._orig["registry_dir"]
        data.CONFIG_DIR = self._orig["config_dir"]
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    # --- HTTP helpers(不吞例外,回傳 (status, headers, body_text)) ---

    def _request(self, path: str, method: str = "GET", origin: str | None = None):
        req = urllib.request.Request(f"{self.base}{path}", method=method)
        if origin is not None:
            req.add_header("Origin", origin)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, dict(resp.headers), resp.read().decode("utf-8")
        except urllib.error.HTTPError as err:
            return err.code, dict(err.headers), err.read().decode("utf-8")

    def _get_json(self, path: str, origin: str | None = None):
        status, _, body = self._request(path, origin=origin)
        self.assertEqual(status, 200, body)
        return json.loads(body)


class LocalhostOnlyTests(ApiServerTestCase):
    """§3.1 + DoD 第 4 條:CORS/403 與 bind 檢核。"""

    def test_evil_origin_rejected_with_403(self):
        status, headers, _ = self._request("/api/health", origin="http://evil.example")
        self.assertEqual(status, 403)
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_non_localhost_port_origin_rejected(self):
        for origin in ["https://localhost:5173", "http://localhost.evil.example:5173",
                       "http://192.168.1.10:5173", "null"]:
            status, _, _ = self._request("/api/health", origin=origin)
            self.assertEqual(status, 403, f"origin {origin} 應被拒絕")

    def test_whitelisted_origins_get_cors_header(self):
        for origin in ["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:8501"]:
            status, headers, _ = self._request("/api/health", origin=origin)
            self.assertEqual(status, 200)
            self.assertEqual(headers.get("Access-Control-Allow-Origin"), origin)

    def test_no_origin_header_allowed(self):
        # 同機 curl/腳本(無 Origin header)不受 CORS 限制——CORS 是瀏覽器機制
        status, headers, _ = self._request("/api/health")
        self.assertEqual(status, 200)
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_server_binds_only_loopback(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")

    def test_create_server_has_no_host_parameter(self):
        params = list(inspect.signature(api.create_server).parameters)
        self.assertEqual(params, ["port"])

    def test_source_has_no_wildcard_bind(self):
        source = (DASHBOARD_DIR / "api.py").read_text(encoding="utf-8")
        self.assertNotIn("0.0.0.0", source)
        self.assertIn('API_HOST = "127.0.0.1"', source)


class ReadOnlyEnforcementTests(ApiServerTestCase):
    """§3.2 + DoD 第 2 條:非 GET 全域 405。"""

    def test_write_methods_rejected_with_405(self):
        for method in ["POST", "PUT", "DELETE", "PATCH", "OPTIONS", "TRACE"]:
            status, headers, _ = self._request("/api/health", method=method)
            self.assertEqual(status, 405, f"{method} 應回 405")
            self.assertEqual(headers.get("Allow"), "GET")

    def test_post_rejected_on_every_route_shape(self):
        # 全域攔截:不是逐 endpoint 擋——未知路徑的 POST 也一樣 405(不是 404)
        for path in ["/api/jobs", "/api/logs/worker.log", "/api/does-not-exist", "/"]:
            status, _, _ = self._request(path, method="POST")
            self.assertEqual(status, 405, f"POST {path} 應回 405")


class ImportGuardTests(unittest.TestCase):
    """§3.2 import guard:AST 白名單鎖定,寫入模組 import 直接紅燈。"""

    # api.py/redact.py 唯一被允許的 import 集合(stdlib + data + redact)。
    # 想加東西?先想清楚它有沒有寫入能力,再來改這份白名單。
    ALLOWED_IMPORTS = {
        "argparse", "json", "re", "sys", "urllib.parse",
        "http.server", "pathlib", "data", "data_resident", "data_stage3", "redact",
    }
    # 已知寫入模組(防守性斷言;白名單本來就擋掉它們,雙保險)
    FORBIDDEN = {
        "db", "hermes.db", "hermes", "bridge_dispatch", "hermes.bridge_dispatch",
        "cron", "cron.jobs", "subprocess", "shutil", "os",
    }

    @staticmethod
    def _collect_imports(path: Path) -> set[str]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    found.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if node.level:
                    module = "." * node.level + module
                for alias in node.names:
                    found.add(f"{module}.{alias.name}" if module else alias.name)
        return found

    def test_api_imports_are_whitelisted(self):
        found = self._collect_imports(DASHBOARD_DIR / "api.py")
        # from http.server import X → "http.server.X",取 module 名比對
        normalized = set()
        for name in found:
            if name.startswith("http.server."):
                normalized.add("http.server")
            elif name.startswith("pathlib."):
                normalized.add("pathlib")
            else:
                normalized.add(name)
        unexpected = normalized - self.ALLOWED_IMPORTS
        self.assertEqual(unexpected, set(), f"api.py 出現白名單外 import:{unexpected}")
        self.assertEqual(normalized & self.FORBIDDEN, set())

    def test_redact_imports_are_whitelisted(self):
        found = self._collect_imports(DASHBOARD_DIR / "redact.py")
        self.assertEqual(found - {"re"}, set(), "redact.py 只准 import re")

    def test_no_write_modules_loaded_at_runtime(self):
        # api 模組 import 後,不得帶進 hermes 寫入模組。
        # 注意:本測試檔自己 import 了 hermes/db.py 當 fixture,所以這裡改用
        # 「api.py 模組屬性」檢查——api 名稱空間裡不能掛任何 db/enqueue 相關物件。
        self.assertFalse(hasattr(api, "db"))
        self.assertFalse(hasattr(api, "enqueue"))
        self.assertFalse(hasattr(api.data, "enqueue"))


class SecretNonLeakTests(ApiServerTestCase):
    """DoD 第 3 條:bot_token 不外洩測試移植到 API 層。"""

    def test_adapter_config_response_never_contains_fake_token(self):
        (data.CONFIG_DIR / "telegram.json").write_text(
            json.dumps({"bot_token": FAKE_BOT_TOKEN, "allowed_chat_ids": [1, 2, 3]}),
            encoding="utf-8",
        )
        status, _, body = self._request("/api/adapter-config")
        self.assertEqual(status, 200)
        self.assertNotIn(FAKE_BOT_TOKEN, body)  # 回應「全文」斷言
        payload = json.loads(body)
        self.assertTrue(payload["telegram"]["configured"])
        self.assertEqual(payload["telegram"]["allowed_chat_ids_count"], 3)

    def test_third_line_redaction_on_any_endpoint(self):
        # 第三道防線:就算未來某個 endpoint 意外含 JWT-like 值,序列化前會被遮蔽。
        # 用 log endpoint 模擬:log 檔內容混入假 JWT 與高熵長字串。
        fake_jwt = "eyJhbGciOiJIUzI1NiJ9.eyJGQUtFIjoidGVzdCJ9.FAKE_sig_abc123"
        fake_long = "FAKE" + "Ab3" * 20  # 64 字元混合類別
        log = data.LOG_DIR / "worker.log"
        log.write_text(f"normal line\ntoken leak: {fake_jwt}\nblob: {fake_long}\n", encoding="utf-8")
        body = json.dumps(self._get_json("/api/logs/worker.log"))
        self.assertNotIn(fake_jwt, body)
        self.assertNotIn(fake_long, body)
        self.assertIn("normal line", body)
        self.assertIn(redact.REDACTED, body)


class RedactUnitTests(unittest.TestCase):
    def test_jwt_like_redacted(self):
        text = "before eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload_x.sig_y after"
        result = redact.scan_text(text)
        self.assertNotIn("eyJ", result)
        self.assertIn("before", result)
        self.assertIn("after", result)

    def test_high_entropy_long_token_redacted(self):
        token = "FAKE_" + "aB9" * 20
        self.assertEqual(redact.scan_text(f"x {token} y"), f"x {redact.REDACTED} y")

    def test_normal_content_untouched(self):
        for text in [
            "worker completed job 3f2a1c9e-1234-5678-9abc-def012345678",  # UUID(36)不誤殺
            "一般中文敘述,含 log 行與 https://example.com 連結",
            "0" * 100,  # 純數字長串(單一字元類別)不誤殺
            "a" * 100,  # 純小寫長串不誤殺
        ]:
            self.assertEqual(redact.scan_text(text), text)

    def test_suspect_key_names_redacted(self):
        result = redact.scan_structure({"bot_token": "short", "count": 3, "name": "ok"})
        self.assertEqual(result["bot_token"], redact.REDACTED)
        self.assertEqual(result["count"], 3)
        self.assertEqual(result["name"], "ok")


class EndpointBehaviorTests(ApiServerTestCase):
    """endpoint 一對一對應 data.py(提案 §4.2 第 2 項)。"""

    def test_health_reports_jobs_db_state(self):
        payload = self._get_json("/api/health")
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["readonly"])
        self.assertTrue(payload["jobs_db_exists"])
        data.JOBS_DB_PATH = self._tmpdir / "missing.db"
        self.assertFalse(self._get_json("/api/health")["jobs_db_exists"])

    def test_status_counts(self):
        job_id = db.enqueue("manual", "hello")
        db.claim_next_job("worker-1")
        db.mark_completed(job_id, "done", cost_usd=0.1)
        self.assertEqual(self._get_json("/api/status-counts"), {"completed": 1})

    def test_jobs_list_with_filters(self):
        db.enqueue("telegram", "a")
        job_b = db.enqueue("cron", "b")
        db.claim_next_job("worker-1")
        db.mark_completed(job_b, "done")
        self.assertEqual(len(self._get_json("/api/jobs?limit=10")), 2)
        completed = self._get_json("/api/jobs?status=completed")
        self.assertEqual([j["id"] for j in completed], [job_b])
        self.assertEqual(len(self._get_json("/api/jobs?source=cron")), 1)

    def test_jobs_invalid_params_rejected(self):
        for path in ["/api/jobs?limit=0", "/api/jobs?limit=abc", "/api/jobs?limit=9999",
                     "/api/jobs?status=hacked", "/api/jobs?source=a%20b"]:
            status, _, _ = self._request(path)
            self.assertEqual(status, 400, path)

    def test_job_detail_found_and_not_found(self):
        job_id = db.enqueue("manual", "hello")
        payload = self._get_json(f"/api/jobs/{job_id}")
        self.assertEqual(payload["id"], job_id)
        status, _, _ = self._request("/api/jobs/does-not-exist")
        self.assertEqual(status, 404)

    def test_cost_summary(self):
        j1 = db.enqueue("telegram", "a")
        db.claim_next_job("worker-1")
        db.mark_completed(j1, "done", cost_usd=0.25)
        payload = self._get_json("/api/cost-summary")
        self.assertAlmostEqual(payload["total"], 0.25)
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["by_source"][0]["source"], "telegram")

    def test_memory_endpoints(self):
        inbox = data.MEMORY_DIR / "inbox"
        (inbox / ".processed").mkdir(parents=True)
        (inbox / "a.md").write_text("x")
        (inbox / ".processed" / "b.md").write_text("x")
        (data.MEMORY_DIR / "MEMORY.md").write_text("x")
        counts = self._get_json("/api/memory/inbox-counts")
        self.assertEqual(counts, {"pending": 1, "processed": 1, "failed": 0})
        self.assertEqual(self._get_json("/api/memory/files"), ["MEMORY.md"])

    def test_domains(self):
        (data.REGISTRY_DIR / "agents.yaml").write_text(
            "agents:\n  - id: engineering\n    status: active\n", encoding="utf-8"
        )
        self.assertEqual(self._get_json("/api/domains"), [{"id": "engineering", "status": "active"}])

    def test_systemd_status_endpoint_returns_dict(self):
        # Windows 上 systemctl 不存在 → data.get_systemd_status() 回 {},API 照回
        payload = self._get_json("/api/systemd-status")
        self.assertIsInstance(payload, dict)

    def test_log_tail(self):
        (data.LOG_DIR / "worker.log").write_text(
            "\n".join(f"line {i}" for i in range(10)), encoding="utf-8"
        )
        payload = self._get_json("/api/logs/worker.log?lines=3")
        self.assertEqual(payload["content"], "line 7\nline 8\nline 9")

    def test_log_missing_file_is_safe_message(self):
        payload = self._get_json("/api/logs/nope.log")
        self.assertIn("找不到", payload["content"])

    def test_log_path_traversal_rejected(self):
        for path in [
            "/api/logs/..%2F..%2Fsecret.log",
            "/api/logs/..%5C..%5Csecret.log",
            "/api/logs/%2e%2e%2fsecret.log",
            "/api/logs/.hidden.log",
            "/api/logs/worker.txt",
            "/api/logs/",
        ]:
            status, _, _ = self._request(path)
            self.assertIn(status, (400, 404), f"{path} 應被拒絕")

    def test_unknown_route_404(self):
        status, _, _ = self._request("/api/nope")
        self.assertEqual(status, 404)
        status, _, _ = self._request("/")
        self.assertEqual(status, 404)


class Stage3EndpointTests(ApiServerTestCase):
    """P2:Stage 3 四個新 endpoint(webui-migration §4.3)。

    三層假密鑰斷言的「API 回應全文」層在這裡——data 層在
    test_data_stage3.py、UI 渲染層在 webui/tests/stage3-render.test.mjs。
    fixture 全部 FAKE_/TEST_ 前綴+tempfile 隔離,不觸碰真實
    %LOCALAPPDATA%\\hermes\\。"""

    def setUp(self):
        super().setUp()
        self._orig_stage3 = {
            "hermes_home": data_stage3.HERMES_HOME,
            "lanes": data_stage3.CAPABILITY_LANES_PATH,
            "systemd_dir": data_stage3.SYSTEMD_UNIT_DIR,
            "state_db": data_stage3.HERMES_STATE_DB,
        }
        self.hermes_home = self._tmpdir / "fake_hermes_home"
        self.hermes_home.mkdir()
        data_stage3.HERMES_HOME = self.hermes_home
        data_stage3.CAPABILITY_LANES_PATH = self._tmpdir / "capability_lanes.yaml"
        data_stage3.SYSTEMD_UNIT_DIR = self._tmpdir / "systemd_units"
        data_stage3.HERMES_STATE_DB = self._tmpdir / "state.db"

    def tearDown(self):
        data_stage3.HERMES_HOME = self._orig_stage3["hermes_home"]
        data_stage3.CAPABILITY_LANES_PATH = self._orig_stage3["lanes"]
        data_stage3.SYSTEMD_UNIT_DIR = self._orig_stage3["systemd_dir"]
        data_stage3.HERMES_STATE_DB = self._orig_stage3["state_db"]
        super().tearDown()

    def _write_fake_auth(self):
        (self.hermes_home / "auth.json").write_text(json.dumps({
            "version": 1,
            "providers": {"fake-provider": {"access_token": FAKE_P2_ACCESS_TOKEN}},
            "credential_pool": {"fake-provider": [{
                "id": "FAKE-entry-1", "label": "TEST_label", "priority": 1,
                "source": "TEST_source", "last_status": "ok",
                "last_refresh": "2026-07-23T00:00:00Z",
                "access_token": FAKE_P2_ACCESS_TOKEN,
                "refresh_token": FAKE_P2_REFRESH_TOKEN,
            }]},
        }), encoding="utf-8")

    def test_capability_lanes_endpoint(self):
        data_stage3.CAPABILITY_LANES_PATH.write_text(
            "lanes:\n  - id: test-lane\n    capability: c\n    status: active\n",
            encoding="utf-8",
        )
        payload = self._get_json("/api/capability-lanes")
        # registry 欄位原樣+資料層標注的實際生效模型欄位(無 hermes_profile → native)
        self.assertEqual(payload, [{
            "id": "test-lane", "capability": "c", "status": "active",
            "effective_model": "(native session)",
            "effective_model_source": "native",
            "effective_provider": None,
        }])

    def test_capability_lanes_effective_model_from_profile_config(self):
        data_stage3.CAPABILITY_LANES_PATH.write_text(
            "lanes:\n  - id: lane-p\n    capability: c\n    model: null\n"
            "    hermes_profile: fakeprof\n",
            encoding="utf-8",
        )
        config_path = self.hermes_home / "profiles" / "fakeprof" / "config.yaml"
        config_path.parent.mkdir(parents=True)
        config_path.write_text(
            "model:\n  default: FAKE-model-api\n  provider: FAKE-prov\n"
            "slack:\n  bot_token: FAKE_SECRET_cfg_token_111222\n",
            encoding="utf-8",
        )
        status, _, body = self._request("/api/capability-lanes")
        self.assertEqual(status, 200)
        self.assertNotIn("FAKE_SECRET_cfg_token_111222", body)  # 回應全文不含非白名單值
        payload = json.loads(body)
        self.assertEqual(payload[0]["effective_model"], "FAKE-model-api")
        self.assertEqual(payload[0]["effective_model_source"], "profile")

    def test_credential_status_full_response_never_contains_fake_secrets(self):
        """功能二 DoD 第 2 項的 API 層版本:回應「全文」不含假秘密值子字串。"""
        self._write_fake_auth()
        status, _, body = self._request("/api/credential-status")
        self.assertEqual(status, 200)
        self.assertNotIn(FAKE_P2_ACCESS_TOKEN, body)
        self.assertNotIn(FAKE_P2_REFRESH_TOKEN, body)
        payload = json.loads(body)
        self.assertTrue(payload["available"])
        entry = payload["profiles"]["(global-root)"]["credential_pool"]["fake-provider"]["entries"][0]
        self.assertEqual(entry["label"], "TEST_label")
        self.assertNotIn("access_token", entry)

    def test_schedule_table_endpoint(self):
        data_stage3.SYSTEMD_UNIT_DIR.mkdir()
        (data_stage3.SYSTEMD_UNIT_DIR / "hermes-test.timer").write_text(
            "[Timer]\nOnCalendar=*-*-* 08:10:00\nUnit=hermes-test.service\n",
            encoding="utf-8",
        )
        (self.hermes_home / "cron").mkdir()
        (self.hermes_home / "cron" / "jobs.json").write_text(json.dumps({"jobs": [{
            "id": "FAKE-j1", "name": "TEST_job", "no_agent": False,
            "model": None, "model_snapshot": None, "enabled": True,
            "schedule": {"kind": "cron", "expr": "0 8 * * *"},
            "prompt": "FAKE_PROMPT_MUST_NOT_LEAK",
        }]}), encoding="utf-8")
        status, _, body = self._request("/api/schedule-table")
        self.assertEqual(status, 200)
        self.assertNotIn("FAKE_PROMPT_MUST_NOT_LEAK", body)
        rows = json.loads(body)
        sources = {row["source"] for row in rows}
        self.assertEqual(sources, {"systemd", "hermes-native"})
        for row in rows:
            for field in ["job_name", "schedule_expr", "deployed", "timer_active",
                          "last_result", "next_trigger", "last_trigger",
                          "model_drift", "drift_cost_direction"]:
                self.assertIn(field, row)

    def test_hermes_sessions_endpoint_no_content_leak(self):
        """功能一:API 回應全文不含訊息內容/路由中繼資料。"""
        conn = sqlite3.connect(data_stage3.HERMES_STATE_DB)
        try:
            conn.executescript(
                "CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT NOT NULL,"
                " user_id TEXT, model TEXT, started_at REAL NOT NULL, ended_at REAL,"
                " end_reason TEXT, message_count INTEGER DEFAULT 0, title TEXT,"
                " session_key TEXT, chat_id TEXT, chat_type TEXT, thread_id TEXT,"
                " parent_session_id TEXT, archived INTEGER NOT NULL DEFAULT 0);"
                "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " session_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT,"
                " tool_call_id TEXT, tool_calls TEXT, tool_name TEXT,"
                " timestamp REAL NOT NULL, finish_reason TEXT, token_count INTEGER,"
                " active INTEGER DEFAULT 1, compacted INTEGER DEFAULT 0);"
            )
            conn.execute(
                "INSERT INTO sessions (id, source, started_at, message_count, title,"
                " session_key, chat_id) VALUES ('TEST_s1', 'cli', 1783500000.0, 1,"
                " 'TEST 標題', 'FAKE_skey_1', 'FAKE_chat_1')")
            conn.execute(
                "INSERT INTO messages (session_id, role, content, timestamp) VALUES"
                " ('TEST_s1', 'user', 'FAKE_MESSAGE_BODY_MUST_NOT_LEAK', 1783500001.0)")
            conn.commit()
        finally:
            conn.close()
        status, _, body = self._request("/api/hermes/sessions")
        self.assertEqual(status, 200)
        self.assertNotIn("FAKE_MESSAGE_BODY_MUST_NOT_LEAK", body)
        self.assertNotIn("FAKE_skey_1", body)
        self.assertNotIn("FAKE_chat_1", body)
        sessions = json.loads(body)
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["session_id"], "TEST_s1")
        self.assertEqual(sessions[0]["title"], "TEST 標題")
        # source 篩選與參數驗證
        self.assertEqual(self._get_json("/api/hermes/sessions?source=telegram"), [])
        status, _, _ = self._request("/api/hermes/sessions?source=a%20b")
        self.assertEqual(status, 400)
        status, _, _ = self._request("/api/hermes/sessions?limit=0")
        self.assertEqual(status, 400)

    def test_hermes_sessions_missing_db_returns_empty(self):
        data_stage3.HERMES_STATE_DB = self._tmpdir / "missing" / "state.db"
        self.assertEqual(self._get_json("/api/hermes/sessions"), [])

    def test_stage3_endpoints_reject_non_get(self):
        for path in ["/api/capability-lanes", "/api/credential-status",
                     "/api/schedule-table", "/api/hermes/sessions"]:
            status, _, _ = self._request(path, method="POST")
            self.assertEqual(status, 405, path)


class ResidentStatusEndpointTests(ApiServerTestCase):
    """背景常駐狀態燈號 endpoint(webui-service-control §1.3,唯讀)。

    探測本體的三層/五態邏輯在 test_data_resident.py;這裡鎖 API 層:
    endpoint 曝露、非 GET 拒絕、探測失敗優雅退化(不噴 500)。
    以替換 data_resident._probe 隔離,不觸碰真實 wsl。"""

    def setUp(self):
        super().setUp()
        self._orig_probe = data_resident._probe
        data_resident._cache = None

    def tearDown(self):
        data_resident._probe = self._orig_probe
        data_resident._cache = None
        super().tearDown()

    def test_resident_status_returns_probe_payload(self):
        data_resident._probe = lambda: {
            "light": "green", "text": "背景常駐中", "detail": "常駐單元全部 active,gateway 就緒",
            "checked_at": "2026-07-24T00:00:00+00:00",
            "distro": {"name": "Ubuntu", "running": True, "detail": "distro 狀態:Running"},
            "resident_units": ["hermes-worker.service", "hermes-telegram.service"],
            "units": {"hermes-worker.service": {"state": "active", "sub": "running",
                                                "load": "loaded", "resident": True}},
            "gateway": {"ready": True, "state": "running", "detail": "gateway 就緒"},
        }
        payload = self._get_json("/api/resident-status")
        self.assertEqual(payload["light"], "green")
        self.assertEqual(payload["text"], "背景常駐中")
        self.assertEqual(payload["distro"]["running"], True)

    def test_resident_status_probe_failure_degrades_to_gray_not_500(self):
        data_resident._probe = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        status, _, body = self._request("/api/resident-status")
        self.assertEqual(status, 200, "探測失敗必須優雅退化,不得 500")
        payload = json.loads(body)
        self.assertEqual(payload["light"], "gray")
        self.assertEqual(payload["text"], "無法查詢")

    def test_resident_status_rejects_non_get(self):
        status, _, _ = self._request("/api/resident-status", method="POST")
        self.assertEqual(status, 405)


if __name__ == "__main__":
    unittest.main()
