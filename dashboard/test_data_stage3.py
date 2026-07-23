#!/usr/bin/env python3
"""dashboard/test_data_stage3.py — P2 Stage 3 三項功能的資料層測試。

對照 DoD(驗收即對照 stage3 提案 v2 原文,不另立標準):

- 功能二 §3.5:第 1 項(兩函式單元測試、假 fixture)、第 2 項(假秘密值
  遞迴不外洩,優先級最高)、第 3 項(allowlist 語意:未知欄位被擋)、
  第 4 項(輸出前掃描防呆:白名單欄位塞假 JWT 仍被攔)、第 5 項
  (LOCALAPPDATA 未設/profile 目錄不存在/壞 JSON 三情境不噴例外)。
  第 6 項(渲染輸出掃描)在新載體由 test_api.py(API 回應全文)與
  webui/tests/stage3-render.test.mjs(UI 渲染)接手。
- 功能三 §4.4:第 1 項(涵蓋 6 個 systemd timer+原生 cron job、欄位齊全)、
  第 2 項(兩路徑獨立退化)、第 3 項(list-timers mock 解析)、第 4 項
  (jobs.json fixture 解析)、第 5 項(漂移三情境)、第 6 項(路徑 A 複用
  get_systemd_status,不另兜平行邏輯)。
- 功能一 §2.4:正常情境 normalized 欄位+遞迴不含 content、state.db 不存在
  回 []、source 篩選正確。

fixture 一律 FAKE_/TEST_ 前綴假值、tempfile.mkdtemp() 隔離(§3.6)——
不使用、不衍生自任何真實憑證片段,不放在易與真實 %LOCALAPPDATA%\\hermes\\
混淆的路徑。

執行:.venv/Scripts/python.exe dashboard/test_data_stage3.py
"""
import ast
import json
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import data  # noqa: E402
import data_stage3  # noqa: E402
import redact  # noqa: E402

DASHBOARD_DIR = Path(__file__).resolve().parent
REPO_SYSTEMD_DIR = data_stage3.ROOT / "hermes" / "systemd"

# 假秘密值 fixture(§3.5 DoD 第 2 項;一律 FAKE_ 前綴)
FAKE_ACCESS_TOKEN = "FAKE_SECRET_access_abcdef123456"
FAKE_REFRESH_TOKEN = "FAKE_SECRET_refresh_zyxwvu987654"
FAKE_AGENT_KEY = "FAKE_SECRET_agentkey_qqppoo112233"
FAKE_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJGQUtFX1RFU1QiOjF9.FAKE_signature_abc"
FAKE_UNKNOWN_VALUE = "FAKE_UNKNOWN_FIELD_VALUE_555"

FAKE_SECRETS = [FAKE_ACCESS_TOKEN, FAKE_REFRESH_TOKEN, FAKE_AGENT_KEY]


def _walk_strings(obj):
    """遞迴走訪結構裡的每一個字串值與 key(§3.6:遞迴掃描是主要斷言工具)。"""
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield str(key)
            yield from _walk_strings(value)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            yield from _walk_strings(item)
    elif isinstance(obj, str):
        yield obj


def _walk_keys(obj):
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield str(key)
            yield from _walk_keys(value)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            yield from _walk_keys(item)


class Stage3TestBase(unittest.TestCase):
    """共用 fixture:把 data_stage3 的路徑常數指向 tempdir(隔離真實環境)。"""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="stage3_test_"))
        self._orig = {
            "hermes_home": data_stage3.HERMES_HOME,
            "lanes": data_stage3.CAPABILITY_LANES_PATH,
            "systemd_dir": data_stage3.SYSTEMD_UNIT_DIR,
            "state_db": data_stage3.HERMES_STATE_DB,
        }
        self.hermes_home = self.tmpdir / "fake_hermes_home"
        self.hermes_home.mkdir()
        data_stage3.HERMES_HOME = self.hermes_home
        data_stage3.CAPABILITY_LANES_PATH = self.tmpdir / "capability_lanes.yaml"
        data_stage3.SYSTEMD_UNIT_DIR = self.tmpdir / "systemd_units"
        data_stage3.HERMES_STATE_DB = self.tmpdir / "state.db"

    def tearDown(self):
        data_stage3.HERMES_HOME = self._orig["hermes_home"]
        data_stage3.CAPABILITY_LANES_PATH = self._orig["lanes"]
        data_stage3.SYSTEMD_UNIT_DIR = self._orig["systemd_dir"]
        data_stage3.HERMES_STATE_DB = self._orig["state_db"]
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # --- fixture builders ---

    def write_auth_json(self, path: Path, extra_entry_fields: dict | None = None):
        """假 auth.json:結構比照安全 schema 驗證結果(只有欄位「名稱」來自
        驗證;所有「值」都是 FAKE_ 假造)。"""
        entry = {
            "id": "FAKE-uuid-entry-1",
            "label": "TEST_label_a",
            "auth_type": "oauth",
            "priority": 100,
            "source": "TEST_source",
            "last_status": "ok",
            "last_refresh": "2026-07-23T00:00:00Z",
            "access_token": FAKE_ACCESS_TOKEN,
            "refresh_token": FAKE_REFRESH_TOKEN,
            "agent_key": FAKE_AGENT_KEY,
        }
        if extra_entry_fields:
            entry.update(extra_entry_fields)
        payload = {
            "version": 1,
            "providers": {
                "fake-provider": {
                    "access_token": FAKE_ACCESS_TOKEN,
                    "refresh_token": FAKE_REFRESH_TOKEN,
                }
            },
            "credential_pool": {"fake-provider": [entry]},
            "updated_at": "2026-07-23T00:00:00Z",
            "active_provider": "fake-provider",
            "suppressed_sources": {"fake-provider": ["TEST_suppressed_marker"]},
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")

    def write_jobs_json(self, path: Path, jobs: list):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"jobs": jobs, "updated_at": "2026-07-23T00:00:00Z"}),
                        encoding="utf-8")

    def write_global_config(self, default_model: str):
        (self.hermes_home / "config.yaml").write_text(
            f"model:\n  default: {default_model}\n  provider: FAKE_provider\n",
            encoding="utf-8",
        )

    def make_native_job(self, **overrides) -> dict:
        """假原生 cron job(欄位名稱依安全 schema 驗證結果,值全為假)。"""
        job = {
            "id": "FAKE-job-id-1",
            "name": "TEST_ai_news",
            "prompt": "FAKE_PROMPT_CONTENT_should_not_leak",
            "model": None,
            "provider": None,
            "model_snapshot": None,
            "script": None,
            "no_agent": False,
            "schedule": {"kind": "cron", "expr": "0 8 * * *", "display": "每天 08:00"},
            "schedule_display": "每天 08:00",
            "enabled": True,
            "state": "scheduled",
            "created_at": "2026-07-01T00:00:00Z",
            "next_run_at": "2026-07-24T08:00:00Z",
            "last_run_at": "2026-07-23T08:00:00Z",
            "last_status": "ok",
            "last_error": None,
        }
        job.update(overrides)
        return job

    def write_state_db(self, path: Path):
        """假 Hermes state.db(sessions/messages 兩張表,adapter 讀的欄位齊全;
        messages.content 塞 FAKE 憑證字串,驗證列表層天然不外洩)。"""
        conn = sqlite3.connect(path)
        try:
            conn.executescript(
                """
                CREATE TABLE sessions (
                    id TEXT PRIMARY KEY, source TEXT NOT NULL, user_id TEXT,
                    model TEXT, started_at REAL NOT NULL, ended_at REAL,
                    end_reason TEXT, message_count INTEGER DEFAULT 0,
                    title TEXT, session_key TEXT, chat_id TEXT, chat_type TEXT,
                    thread_id TEXT, parent_session_id TEXT,
                    archived INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT,
                    tool_call_id TEXT, tool_calls TEXT, tool_name TEXT,
                    timestamp REAL NOT NULL, finish_reason TEXT,
                    token_count INTEGER, active INTEGER DEFAULT 1,
                    compacted INTEGER DEFAULT 0
                );
                """
            )
            conn.execute(
                "INSERT INTO sessions (id, source, model, started_at, ended_at, "
                "message_count, title, session_key, chat_id) VALUES "
                "('TEST_session_cli', 'cli', 'FAKE-model-a', 1783500000.0, NULL, "
                "2, 'TEST 標題一', 'FAKE_session_key_123', 'FAKE_chat_id_999')"
            )
            conn.execute(
                "INSERT INTO sessions (id, source, model, started_at, ended_at, "
                "message_count, title, session_key, chat_id) VALUES "
                "('TEST_session_tg', 'telegram', 'FAKE-model-b', 1783600000.0, "
                "1783600100.0, 1, 'TEST 標題二', 'FAKE_session_key_456', 'FAKE_chat_id_888')"
            )
            conn.execute(
                "INSERT INTO messages (session_id, role, content, timestamp) VALUES "
                f"('TEST_session_cli', 'user', 'FAKE_MESSAGE_CONTENT {FAKE_ACCESS_TOKEN}', 1783500001.0)"
            )
            conn.commit()
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# 功能二 — 憑證/Lane 狀態
# ---------------------------------------------------------------------------

class CapabilityLaneTests(Stage3TestBase):
    def test_lanes_returned_in_full(self):
        data_stage3.CAPABILITY_LANES_PATH.write_text(
            "lanes:\n"
            "  - id: test-lane\n    capability: complex_coding\n"
            "    execution: hermes_profile\n    provider: FAKE_provider\n"
            "    model: FAKE-model-x\n    status: active\n    cost_tier: free\n"
            "    risk_tier: low\n    allowed_agents: [engineering]\n",
            encoding="utf-8",
        )
        lanes = data_stage3.get_capability_lane_status()
        self.assertEqual(len(lanes), 1)
        self.assertEqual(lanes[0]["id"], "test-lane")
        self.assertEqual(lanes[0]["cost_tier"], "free")

    def test_missing_or_bad_file_returns_empty(self):
        self.assertEqual(data_stage3.get_capability_lane_status(), [])
        data_stage3.CAPABILITY_LANES_PATH.write_text("lanes: [::bad yaml", encoding="utf-8")
        self.assertEqual(data_stage3.get_capability_lane_status(), [])

    def test_real_repo_lanes_file_parses(self):
        """對 repo 內真實 capability_lanes.yaml(公開治理資料)跑一次。"""
        data_stage3.CAPABILITY_LANES_PATH = self._orig["lanes"]
        lanes = data_stage3.get_capability_lane_status()
        self.assertGreater(len(lanes), 0)
        for lane in lanes:
            self.assertIn("id", lane)
            self.assertIn("capability", lane)


class EffectiveModelTests(Stage3TestBase):
    """lane 表「實際生效模型」欄位:profile config → 全域 fallback → 退化,
    以及 config.yaml 白名單讀取(只抽 model.default/provider)不外洩其他區塊。"""

    FAKE_CONFIG_SECRET = "FAKE_SECRET_config_slack_token_998877"

    def _write_lanes(self):
        data_stage3.CAPABILITY_LANES_PATH.write_text(
            "lanes:\n"
            "  - id: native-lane\n    capability: c\n    execution: claude_native\n"
            "  - id: lane-alpha\n    capability: c\n    execution: hermes_profile\n"
            "    model: null\n    hermes_profile: alpha\n"
            "  - id: lane-beta\n    capability: c\n    execution: hermes_profile\n"
            "    model: null\n    hermes_profile: beta\n",
            encoding="utf-8",
        )

    def _write_profile_config(self, profile: str, default: str, provider: str):
        path = self.hermes_home / "profiles" / profile / "config.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        # 混入假敏感欄位(model 區塊內外都放):白名單只抽 default/provider
        path.write_text(
            "slack:\n  bot_token: %s\n"
            "model:\n  default: %s\n  provider: %s\n  base_url: http://127.0.0.1:9\n"
            "  secret_key: %s\n" % (
                self.FAKE_CONFIG_SECRET, default, provider, self.FAKE_CONFIG_SECRET),
            encoding="utf-8",
        )

    def _lanes_by_id(self):
        return {lane["id"]: lane for lane in data_stage3.get_capability_lane_status()}

    def test_profile_value_wins_and_global_fallback(self):
        """有 profile 值顯示 profile 值;無 profile config → fallback 全域。"""
        self._write_lanes()
        self._write_profile_config("alpha", "FAKE-model-alpha", "FAKE-prov-alpha")
        self.write_global_config("FAKE-model-global")
        lanes = self._lanes_by_id()
        self.assertEqual(lanes["lane-alpha"]["effective_model"], "FAKE-model-alpha")
        self.assertEqual(lanes["lane-alpha"]["effective_model_source"], "profile")
        self.assertEqual(lanes["lane-alpha"]["effective_provider"], "FAKE-prov-alpha")
        # beta 無 config.yaml → 繼承全域
        self.assertEqual(lanes["lane-beta"]["effective_model"], "FAKE-model-global")
        self.assertEqual(lanes["lane-beta"]["effective_model_source"], "global")
        # native lane 標示 native,不查 config
        self.assertEqual(lanes["native-lane"]["effective_model"], "(native session)")
        self.assertEqual(lanes["native-lane"]["effective_model_source"], "native")
        self.assertIsNone(lanes["native-lane"]["effective_provider"])
        # registry 原欄位不被覆寫(model 仍為 null)
        self.assertIsNone(lanes["lane-alpha"]["model"])

    def test_profile_config_without_model_block_inherits_global(self):
        """profile config 存在但無 model 區塊 → 繼承全域。"""
        self._write_lanes()
        path = self.hermes_home / "profiles" / "alpha" / "config.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("agent:\n  name: TEST\n", encoding="utf-8")
        self.write_global_config("FAKE-model-global")
        lanes = self._lanes_by_id()
        self.assertEqual(lanes["lane-alpha"]["effective_model"], "FAKE-model-global")
        self.assertEqual(lanes["lane-alpha"]["effective_model_source"], "global")

    def test_degrades_when_no_configs_or_no_localappdata(self):
        """config 全缺 → 無法查詢/unknown;LOCALAPPDATA 未設同樣不噴例外。"""
        self._write_lanes()
        lanes = self._lanes_by_id()
        self.assertEqual(lanes["lane-alpha"]["effective_model"], "無法查詢")
        self.assertEqual(lanes["lane-alpha"]["effective_model_source"], "unknown")
        data_stage3.HERMES_HOME = None
        lanes = self._lanes_by_id()
        self.assertEqual(lanes["lane-beta"]["effective_model_source"], "unknown")
        self.assertEqual(lanes["native-lane"]["effective_model"], "(native session)")

    def test_bad_profile_config_yaml_falls_back(self):
        self._write_lanes()
        path = self.hermes_home / "profiles" / "alpha" / "config.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("model: [::broken", encoding="utf-8")
        self.write_global_config("FAKE-model-global")
        lanes = self._lanes_by_id()
        self.assertEqual(lanes["lane-alpha"]["effective_model"], "FAKE-model-global")

    def test_config_sensitive_fields_never_leak(self):
        """安全斷言:config.yaml 的非白名單內容(slack token、model 區塊內的
        未知欄位)不得出現在回傳結構任何字串值。"""
        self._write_lanes()
        self._write_profile_config("alpha", "FAKE-model-alpha", "FAKE-prov-alpha")
        self.write_global_config("FAKE-model-global")
        lanes = data_stage3.get_capability_lane_status()
        body = json.dumps(lanes, ensure_ascii=False)
        self.assertNotIn(self.FAKE_CONFIG_SECRET, body)
        keys = set(_walk_keys(lanes))
        self.assertNotIn("secret_key", keys)
        self.assertNotIn("slack", keys)
        self.assertNotIn("base_url", keys)  # model 區塊白名單只有 default/provider


class CredentialStatusTests(Stage3TestBase):
    """§3.5 DoD 第 1/2/3/4/5 項——本檔安全性最關鍵的測試群。"""

    def test_whitelist_fields_extracted(self):
        self.write_auth_json(self.hermes_home / "auth.json")
        result = data_stage3.get_hermes_credential_status()
        self.assertTrue(result["available"])
        root = result["profiles"]["(global-root)"]
        self.assertTrue(root["auth_json_exists"])
        pool = root["credential_pool"]["fake-provider"]
        self.assertEqual(pool["entry_count"], 1)
        entry = pool["entries"][0]
        self.assertEqual(entry["id"], "FAKE-uuid-entry-1")
        self.assertEqual(entry["label"], "TEST_label_a")
        self.assertEqual(entry["priority"], 100)
        self.assertEqual(entry["last_status"], "ok")
        self.assertEqual(entry["source"], "TEST_source")
        # 頂層 providers 只有名稱清單(實測 schema 是 dict,只取 key 名稱)
        self.assertEqual(root["providers"], ["fake-provider"])

    def test_fake_secrets_never_appear_anywhere_in_structure(self):
        """DoD 第 2 項(優先級最高):整個回傳結構遞迴搜尋,任何字串值都
        不包含假秘密值的任何子字串。"""
        self.write_auth_json(self.hermes_home / "auth.json")
        (self.hermes_home / "profiles" / "fakeprofile").mkdir(parents=True)
        self.write_auth_json(self.hermes_home / "profiles" / "fakeprofile" / "auth.json")
        result = data_stage3.get_hermes_credential_status()
        for text in _walk_strings(result):
            for secret in FAKE_SECRETS:
                self.assertNotIn(secret, text)
        # 序列化全文再驗一次(等價於 API 回應全文的資料層版本)
        body = json.dumps(result, ensure_ascii=False)
        for secret in FAKE_SECRETS:
            self.assertNotIn(secret, body)

    def test_unknown_fields_are_dropped_allowlist_semantics(self):
        """DoD 第 3 項:模擬未來 schema 新增未知欄位——未知欄位(不論名稱)
        不出現在輸出,驗證的是 allowlist 語意,不只是已知敏感欄位被擋。"""
        self.write_auth_json(
            self.hermes_home / "auth.json",
            extra_entry_fields={"mystery_future_field": FAKE_UNKNOWN_VALUE},
        )
        result = data_stage3.get_hermes_credential_status()
        keys = set(_walk_keys(result))
        self.assertNotIn("mystery_future_field", keys)
        self.assertNotIn("access_token", keys)
        self.assertNotIn("refresh_token", keys)
        self.assertNotIn("agent_key", keys)
        self.assertNotIn("suppressed_sources", keys)  # §3.2 保守立場:不在白名單
        for text in _walk_strings(result):
            self.assertNotIn(FAKE_UNKNOWN_VALUE, text)
        # 白名單欄位齊全(正向斷言也要有,§3.6)
        entry = result["profiles"]["(global-root)"]["credential_pool"]["fake-provider"]["entries"][0]
        self.assertEqual(set(entry.keys()), set(data_stage3.CREDENTIAL_ENTRY_ALLOWLIST))

    def test_output_scan_catches_jwt_in_whitelisted_field(self):
        """DoD 第 4 項:故意在白名單欄位(label)塞假 JWT——模擬白名單寫錯
        的情境,輸出前掃描仍要攔下換成 REDACTED 佔位。"""
        self.write_auth_json(
            self.hermes_home / "auth.json",
            extra_entry_fields={"label": FAKE_JWT},
        )
        result = data_stage3.get_hermes_credential_status()
        entry = result["profiles"]["(global-root)"]["credential_pool"]["fake-provider"]["entries"][0]
        self.assertEqual(entry["label"], redact.REDACTED)
        body = json.dumps(result)
        self.assertNotIn("eyJ", body)

    def test_no_localappdata_no_profiles_dir_bad_json_all_safe(self):
        """DoD 第 5 項:三種退化情境皆不噴例外。"""
        # (a) LOCALAPPDATA 未設
        data_stage3.HERMES_HOME = None
        result = data_stage3.get_hermes_credential_status()
        self.assertFalse(result["available"])
        self.assertEqual(result["profiles"], {})
        # (b) profiles 目錄不存在(只有 global-root 缺檔)
        data_stage3.HERMES_HOME = self.hermes_home
        result = data_stage3.get_hermes_credential_status()
        self.assertTrue(result["available"])
        self.assertEqual(result["profiles"]["(global-root)"], {"auth_json_exists": False})
        # (c) 壞 JSON
        (self.hermes_home / "auth.json").write_text("{not valid json", encoding="utf-8")
        result = data_stage3.get_hermes_credential_status()
        self.assertEqual(
            result["profiles"]["(global-root)"],
            {"auth_json_exists": True, "error": "設定檔格式錯誤"},
        )

    def test_profiles_scanned_dynamically(self):
        """profile 清單動態掃描目錄,不硬編(§3.3)。"""
        for name in ["alpha_profile", "beta_profile"]:
            (self.hermes_home / "profiles" / name).mkdir(parents=True)
        self.write_auth_json(self.hermes_home / "profiles" / "alpha_profile" / "auth.json")
        result = data_stage3.get_hermes_credential_status()
        self.assertEqual(
            set(result["profiles"].keys()),
            {"(global-root)", "alpha_profile", "beta_profile"},
        )
        self.assertEqual(result["profiles"]["beta_profile"], {"auth_json_exists": False})


# ---------------------------------------------------------------------------
# 功能三 — 統一排程健康表+模型漂移旗標
# ---------------------------------------------------------------------------

FAKE_LIST_TIMERS_OUTPUT = (
    "Fri 2026-07-24 08:10:00 CST  11h left  Wed 2026-07-23 08:10:00 CST  12h ago  "
    "hermes-test.timer  hermes-test.service\n"
    "n/a  n/a  n/a  n/a  hermes-never.timer  hermes-never.service\n"
    "Fri 2026-07-24 09:00:00 CST  12h left  n/a  n/a  "
    "other-app.timer  other-app.service\n"
)


class ScheduleTableSystemdTests(Stage3TestBase):
    def _write_timer(self, name: str, on_calendar: str, unit: str | None = None):
        data_stage3.SYSTEMD_UNIT_DIR.mkdir(exist_ok=True)
        lines = ["[Unit]", f"Description=TEST {name}", "", "[Timer]",
                 f"OnCalendar={on_calendar}"]
        if unit:
            lines.append(f"Unit={unit}")
        lines += ["Persistent=true", "", "[Install]", "WantedBy=timers.target", ""]
        (data_stage3.SYSTEMD_UNIT_DIR / f"{name}.timer").write_text(
            "\n".join(lines), encoding="utf-8")

    def test_static_fields_survive_without_systemctl(self):
        """§4.4 DoD 第 2 項(路徑 A 退化):systemctl 完全不可用時,
        job_name/schedule_expr 依然正確,其餘動態欄位=無法查詢。"""
        self._write_timer("hermes-test", "*-*-* 08:10:00", "hermes-test.service")
        with mock.patch.object(data, "get_systemd_status", return_value={}):
            rows = data_stage3.get_cron_schedule_table()
        systemd_rows = [r for r in rows if r["source"] == "systemd"]
        self.assertEqual(len(systemd_rows), 1)
        row = systemd_rows[0]
        self.assertEqual(row["job_name"], "hermes-test")
        self.assertEqual(row["schedule_expr"], "*-*-* 08:10:00")
        for field in ["deployed", "timer_active", "last_result", "next_trigger", "last_trigger"]:
            self.assertEqual(row[field], "無法查詢", field)
        self.assertEqual(row["model_drift"], "n/a")

    def test_real_repo_timer_files_all_parsed(self):
        """§4.4 DoD 第 1 項(A 半):對 repo 內真實 .timer 檔(靜態設定、
        非執行期資料,依 §4.5 不需 fixture 隔離)斷言 6 個 OnCalendar 全部
        抽出——含尚未部署的 bridge-pipeline/notifier。"""
        data_stage3.SYSTEMD_UNIT_DIR = REPO_SYSTEMD_DIR
        with mock.patch.object(data, "get_systemd_status", return_value={}):
            rows = [r for r in data_stage3.get_cron_schedule_table() if r["source"] == "systemd"]
        names = {r["job_name"] for r in rows}
        self.assertEqual(names, {
            "hermes-rss", "hermes-cron-daily-memory-check", "hermes-bridge-scanner",
            "hermes-bridge", "hermes-bridge-pipeline", "hermes-bridge-notifier",
        })
        for row in rows:
            self.assertNotEqual(row["schedule_expr"], "無法查詢", row["job_name"])

    def test_deployed_state_reuses_get_systemd_status(self):
        """§4.4 DoD 第 6 項:路徑 A 的部署狀態複用 data.get_systemd_status()
        既有邏輯(mock 它、驗證輸出反映 mock 值=真的在用它,不是平行實作)。"""
        self._write_timer("hermes-test", "*-*-* 08:10:00", "hermes-test.service")
        self._write_timer("hermes-missing", "*-*-* 09:00:00")
        fake_status = {
            "hermes-test.timer": {"pid": "-", "last_exit": "active/waiting", "load": "loaded"},
            "hermes-test.service": {"pid": "-", "last_exit": "inactive/dead", "load": "loaded"},
        }
        with mock.patch.object(data, "get_systemd_status", return_value=fake_status), \
             mock.patch.object(data_stage3, "_list_timers_status", return_value={}):
            rows = {r["job_name"]: r for r in data_stage3.get_cron_schedule_table()
                    if r["source"] == "systemd"}
        self.assertTrue(rows["hermes-test"]["deployed"])
        self.assertEqual(rows["hermes-test"]["timer_active"], "active")
        self.assertEqual(rows["hermes-test"]["last_result"], "成功")  # oneshot dead=成功
        self.assertFalse(rows["hermes-missing"]["deployed"])
        self.assertEqual(rows["hermes-missing"]["timer_active"], "未安裝")

    def test_failed_service_marked_failed(self):
        self._write_timer("hermes-test", "*-*-* 08:10:00", "hermes-test.service")
        fake_status = {
            "hermes-test.timer": {"pid": "-", "last_exit": "active/waiting", "load": "loaded"},
            "hermes-test.service": {"pid": "-", "last_exit": "failed/failed", "load": "loaded"},
        }
        with mock.patch.object(data, "get_systemd_status", return_value=fake_status), \
             mock.patch.object(data_stage3, "_list_timers_status", return_value={}):
            rows = {r["job_name"]: r for r in data_stage3.get_cron_schedule_table()}
        self.assertEqual(rows["hermes-test"]["last_result"], "失敗")

    def test_list_timers_output_parsing(self):
        """§4.4 DoD 第 3 項:mock subprocess 輸出,斷言 NEXT/LAST 解析正確;
        「n/a 欄位」「非 hermes unit 排除」「systemctl 不存在」邊界都測。"""
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=FAKE_LIST_TIMERS_OUTPUT, stderr="")
        with mock.patch.object(data_stage3.subprocess, "run", return_value=completed):
            parsed = data_stage3._list_timers_status()
        self.assertEqual(parsed["hermes-test.timer"]["next"], "Fri 2026-07-24 08:10:00 CST")
        self.assertEqual(parsed["hermes-test.timer"]["last"], "Wed 2026-07-23 08:10:00 CST")
        self.assertEqual(parsed["hermes-never.timer"], {"next": "n/a", "last": "n/a"})
        self.assertNotIn("other-app.timer", parsed)
        # systemctl 不存在 → {}
        with mock.patch.object(data_stage3.subprocess, "run", side_effect=FileNotFoundError):
            self.assertEqual(data_stage3._list_timers_status(), {})
        # 輸出為空 → {}
        empty = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with mock.patch.object(data_stage3.subprocess, "run", return_value=empty):
            self.assertEqual(data_stage3._list_timers_status(), {})

    def test_next_last_trigger_mapped_into_rows(self):
        self._write_timer("hermes-test", "*-*-* 08:10:00", "hermes-test.service")
        self._write_timer("hermes-never", "*-*-* 07:00:00", "hermes-never.service")
        fake_status = {
            "hermes-test.timer": {"pid": "-", "last_exit": "active/waiting", "load": "loaded"},
            "hermes-never.timer": {"pid": "-", "last_exit": "active/waiting", "load": "loaded"},
        }
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=FAKE_LIST_TIMERS_OUTPUT, stderr="")
        with mock.patch.object(data, "get_systemd_status", return_value=fake_status), \
             mock.patch.object(data_stage3.subprocess, "run", return_value=completed):
            rows = {r["job_name"]: r for r in data_stage3.get_cron_schedule_table()}
        self.assertEqual(rows["hermes-test"]["next_trigger"], "Fri 2026-07-24 08:10:00 CST")
        self.assertEqual(rows["hermes-test"]["last_trigger"], "Wed 2026-07-23 08:10:00 CST")
        self.assertEqual(rows["hermes-never"]["last_trigger"], "從未觸發")


class ScheduleTableNativeCronTests(Stage3TestBase):
    def test_jobs_json_parsed_with_source_flag(self):
        """§4.4 DoD 第 4 項:fixture jobs.json 正確抽出欄位,
        source == "hermes-native"。"""
        self.write_jobs_json(self.hermes_home / "cron" / "jobs.json", [
            self.make_native_job(),
            self.make_native_job(id="FAKE-job-2", name="TEST_garmin", no_agent=True,
                                 script="FAKE_script.py", last_status=None,
                                 last_run_at=None),
        ])
        self.write_global_config("FAKE-model-default")
        rows = [r for r in data_stage3.get_cron_schedule_table()
                if r["source"] == "hermes-native"]
        self.assertEqual(len(rows), 2)
        by_name = {r["job_name"]: r for r in rows}
        news = by_name["TEST_ai_news"]
        self.assertEqual(news["schedule_expr"], "0 8 * * *")
        self.assertTrue(news["deployed"])
        self.assertEqual(news["timer_active"], "active")
        self.assertEqual(news["last_result"], "ok")
        self.assertEqual(news["next_trigger"], "2026-07-24T08:00:00Z")
        self.assertEqual(news["last_trigger"], "2026-07-23T08:00:00Z")
        garmin = by_name["TEST_garmin"]
        self.assertEqual(garmin["last_result"], "尚未執行")
        self.assertEqual(garmin["last_trigger"], "從未觸發")

    def test_prompt_content_not_extracted(self):
        """內容欄位(prompt/script)不進表格(§4.2 只抽排程健康欄位)。"""
        self.write_jobs_json(self.hermes_home / "cron" / "jobs.json",
                             [self.make_native_job()])
        rows = data_stage3.get_cron_schedule_table()
        body = json.dumps(rows, ensure_ascii=False)
        self.assertNotIn("FAKE_PROMPT_CONTENT_should_not_leak", body)

    def test_store_missing_path_b_empty_path_a_unaffected(self):
        """§4.4 DoD 第 2 項(兩路徑獨立退化):store 不存在 → 路徑 B 空,
        路徑 A 照常;LOCALAPPDATA 未設同樣安全。"""
        data_stage3.SYSTEMD_UNIT_DIR = REPO_SYSTEMD_DIR
        with mock.patch.object(data, "get_systemd_status", return_value={}):
            rows = data_stage3.get_cron_schedule_table()
        self.assertEqual([r for r in rows if r["source"] == "hermes-native"], [])
        self.assertEqual(len([r for r in rows if r["source"] == "systemd"]), 6)
        data_stage3.HERMES_HOME = None
        with mock.patch.object(data, "get_systemd_status", return_value={}):
            rows = data_stage3.get_cron_schedule_table()
        self.assertEqual([r for r in rows if r["source"] == "hermes-native"], [])
        self.assertEqual(len([r for r in rows if r["source"] == "systemd"]), 6)

    def test_bad_jobs_json_does_not_break_table(self):
        (self.hermes_home / "cron").mkdir()
        (self.hermes_home / "cron" / "jobs.json").write_text("{broken", encoding="utf-8")
        rows = data_stage3.get_cron_schedule_table()  # 不噴例外
        self.assertEqual([r for r in rows if r["source"] == "hermes-native"], [])

    def test_profile_stores_also_scanned(self):
        self.write_jobs_json(self.hermes_home / "cron" / "jobs.json",
                             [self.make_native_job()])
        self.write_jobs_json(
            self.hermes_home / "profiles" / "fakeprofile" / "cron" / "jobs.json",
            [self.make_native_job(id="FAKE-job-p", name="TEST_profile_job")])
        rows = [r for r in data_stage3.get_cron_schedule_table()
                if r["source"] == "hermes-native"]
        self.assertEqual({r["job_name"] for r in rows},
                         {"TEST_ai_news", "TEST_profile_job"})


class ModelDriftTests(Stage3TestBase):
    """§4.4 DoD 第 5 項:漂移旗標三情境(a)(b)(c)。"""

    def _lanes_with_cost_tiers(self):
        data_stage3.CAPABILITY_LANES_PATH.write_text(
            "lanes:\n"
            "  - id: lane-old\n    capability: c\n    model: FAKE-model-old\n"
            "    cost_tier: free\n"
            "  - id: lane-new\n    capability: c\n    model: FAKE-model-new\n"
            "    cost_tier: paid\n",
            encoding="utf-8",
        )

    def test_aligned_when_snapshot_equals_default(self):
        self.write_jobs_json(self.hermes_home / "cron" / "jobs.json",
                             [self.make_native_job(model_snapshot="FAKE-model-new")])
        self.write_global_config("FAKE-model-new")
        row = [r for r in data_stage3.get_cron_schedule_table()
               if r["source"] == "hermes-native"][0]
        self.assertEqual(row["model_drift"], "aligned")
        self.assertIsNone(row["drift_cost_direction"])

    def test_drifted_when_snapshot_differs(self):
        self._lanes_with_cost_tiers()
        self.write_jobs_json(self.hermes_home / "cron" / "jobs.json",
                             [self.make_native_job(model_snapshot="FAKE-model-old")])
        self.write_global_config("FAKE-model-new")
        row = [r for r in data_stage3.get_cron_schedule_table()
               if r["source"] == "hermes-native"][0]
        self.assertEqual(row["model_drift"], "DRIFTED")
        self.assertEqual(row["drift_cost_direction"], "更貴")  # free → paid

    def test_drift_direction_unknown_without_cost_info(self):
        self.write_jobs_json(self.hermes_home / "cron" / "jobs.json",
                             [self.make_native_job(model_snapshot="FAKE-model-old")])
        self.write_global_config("FAKE-model-new")
        row = [r for r in data_stage3.get_cron_schedule_table()
               if r["source"] == "hermes-native"][0]
        self.assertEqual(row["model_drift"], "DRIFTED")
        self.assertEqual(row["drift_cost_direction"], "未知")

    def test_script_job_always_na(self):
        self.write_jobs_json(
            self.hermes_home / "cron" / "jobs.json",
            [self.make_native_job(no_agent=True, model_snapshot="FAKE-model-old")])
        self.write_global_config("FAKE-model-new")
        row = [r for r in data_stage3.get_cron_schedule_table()
               if r["source"] == "hermes-native"][0]
        self.assertEqual(row["model_drift"], "n/a")
        self.assertIsNone(row["drift_cost_direction"])

    def test_pinned_agent_job_na(self):
        """已 pin(model 有值)的 agent job 不納入漂移判斷。"""
        self.write_jobs_json(
            self.hermes_home / "cron" / "jobs.json",
            [self.make_native_job(model="FAKE-model-pinned",
                                  model_snapshot="FAKE-model-old")])
        self.write_global_config("FAKE-model-new")
        row = [r for r in data_stage3.get_cron_schedule_table()
               if r["source"] == "hermes-native"][0]
        self.assertEqual(row["model_drift"], "n/a")


# ---------------------------------------------------------------------------
# 功能一 — Hermes session 列表
# ---------------------------------------------------------------------------

class HermesSessionsTests(Stage3TestBase):
    def test_normalized_fields_and_no_content_recursively(self):
        """§2.4 第 1 項:normalized 欄位正確;遞迴檢查整個回傳結構不含
        content key,且 messages 內容(含假憑證)不出現在任何字串值。"""
        self.write_state_db(data_stage3.HERMES_STATE_DB)
        sessions = data_stage3.get_hermes_sessions()
        self.assertEqual(len(sessions), 2)
        # 依 started_at 反排序:telegram(較晚)在前
        self.assertEqual(sessions[0]["session_id"], "TEST_session_tg")
        self.assertEqual(sessions[0]["session_source"], "telegram")
        self.assertEqual(sessions[0]["title"], "TEST 標題二")
        self.assertEqual(sessions[0]["message_count"], 1)
        self.assertTrue(sessions[0]["started_at"].startswith("2026-"))
        for session in sessions:
            self.assertEqual(set(session.keys()), set(data_stage3.SESSION_FIELD_ALLOWLIST))
        keys = set(_walk_keys(sessions))
        self.assertNotIn("content", keys)
        self.assertNotIn("metadata", keys)
        self.assertNotIn("session_key", keys)
        self.assertNotIn("chat_id", keys)
        body = json.dumps(sessions, ensure_ascii=False)
        self.assertNotIn("FAKE_MESSAGE_CONTENT", body)
        self.assertNotIn(FAKE_ACCESS_TOKEN, body)
        self.assertNotIn("FAKE_session_key_123", body)
        self.assertNotIn("FAKE_chat_id_999", body)

    def test_missing_state_db_returns_empty(self):
        """§2.4 第 2 項:state.db 不存在 → [],不噴例外。"""
        data_stage3.HERMES_STATE_DB = self.tmpdir / "nowhere" / "state.db"
        self.assertEqual(data_stage3.get_hermes_sessions(), [])

    def test_source_filter(self):
        """§2.4 第 3 項:source 篩選正確。"""
        self.write_state_db(data_stage3.HERMES_STATE_DB)
        cli_only = data_stage3.get_hermes_sessions(source="cli")
        self.assertEqual([s["session_id"] for s in cli_only], ["TEST_session_cli"])

    def test_limit(self):
        self.write_state_db(data_stage3.HERMES_STATE_DB)
        self.assertEqual(len(data_stage3.get_hermes_sessions(limit=1)), 1)


# ---------------------------------------------------------------------------
# 模組層防護:import guard(比照 test_api.py 的 AST 白名單模式)
# ---------------------------------------------------------------------------

class Stage3ImportGuardTests(unittest.TestCase):
    """data_stage3.py 的 import 集合鎖白名單——不含任何已知寫入模組;
    HermesSessionAdapter 走 importlib 檔案路徑載入,不在 import 語句裡。"""

    ALLOWED = {
        "importlib.util", "json", "os", "re", "subprocess",
        "datetime", "pathlib", "yaml", "data", "redact",
    }
    FORBIDDEN = {
        "db", "hermes.db", "bridge_dispatch", "hermes.bridge_dispatch",
        "cron", "cron.jobs", "shutil", "adapter",
    }

    def test_imports_whitelisted(self):
        tree = ast.parse((DASHBOARD_DIR / "data_stage3.py").read_text(encoding="utf-8"))
        found: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    found.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                found.add(node.module or "")
        normalized = set()
        for name in found:
            if name.startswith("datetime"):
                normalized.add("datetime")
            elif name.startswith("pathlib"):
                normalized.add("pathlib")
            else:
                normalized.add(name)
        self.assertEqual(normalized - self.ALLOWED, set(),
                         f"data_stage3.py 出現白名單外 import:{normalized - self.ALLOWED}")
        self.assertEqual(normalized & self.FORBIDDEN, set())

    def test_source_has_no_write_calls(self):
        """原始碼不含任何寫入型呼叫(粗篩防呆;唯讀鐵律的靜態檢核)。"""
        source = (DASHBOARD_DIR / "data_stage3.py").read_text(encoding="utf-8")
        for needle in ["write_text(", "open(", "unlink(", "rmtree(", "mkdir(",
                       "INSERT ", "UPDATE ", "DELETE "]:
            self.assertNotIn(needle, source, f"data_stage3.py 不得出現 {needle}")


if __name__ == "__main__":
    unittest.main()
