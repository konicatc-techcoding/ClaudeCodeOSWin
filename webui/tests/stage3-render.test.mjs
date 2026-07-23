// P2:Stage 3 三項功能的 UI 渲染層測試——三層假密鑰斷言的第三層
// (資料層:dashboard/test_data_stage3.py;API 回應全文:dashboard/test_api.py)。
//
// 形態:比照範本 tests/rendered-html.test.mjs 的 rendered-HTML 測試,改以
// rolldown(vite 8 既有依賴,零新增)bundle 純渲染元件+react-dom/server,
// 對「UI 渲染輸出」做斷言。核心語意:**餵入刻意含假秘密欄位的資料**
// (模擬 API 層防線失效),斷言 UI 欄位白名單仍不渲染那些值——
// UI 層是獨立防線,不是被動信任上游。
// fixture 一律 FAKE_/TEST_ 前綴假值,不使用任何真實憑證片段。
import assert from "node:assert/strict";
import test from "node:test";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { pathToFileURL, fileURLToPath } from "node:url";
import { rolldown } from "rolldown";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");

const FAKE_ACCESS_TOKEN = "FAKE_SECRET_ui_access_abc123456";
const FAKE_REFRESH_TOKEN = "FAKE_SECRET_ui_refresh_zyx98765";
const FAKE_SESSION_KEY = "FAKE_ui_session_key_777";
const FAKE_MESSAGE_CONTENT = "FAKE_UI_MESSAGE_CONTENT_MUST_NOT_RENDER";

let mod;
let tmp;

test.before(async () => {
  const bundle = await rolldown({
    input: join(root, "tests", "fixtures", "stage3-render-entry.tsx"),
    platform: "node",
    logLevel: "silent",
  });
  const { output } = await bundle.generate({ format: "esm" });
  await bundle.close();
  tmp = await mkdtemp(join(tmpdir(), "stage3-render-"));
  const bundlePath = join(tmp, "stage3-render-entry.mjs");
  await writeFile(bundlePath, output[0].code);
  mod = await import(pathToFileURL(bundlePath).href);
});

test.after(async () => {
  if (tmp) await rm(tmp, { recursive: true, force: true });
});

// ---------------------------------------------------------------------------
// 功能二:憑證/Lane 狀態
// ---------------------------------------------------------------------------

test("憑證頁:白名單欄位渲染、含秘密的欄位即使出現在資料裡也不渲染", () => {
  const lanes = [
    { id: "test-lane", capability: "complex_coding", provider: "FAKE_provider", status: "active", cost_tier: "free" },
  ];
  const credentials = {
    available: true,
    profiles: {
      "(global-root)": {
        auth_json_exists: true,
        mtime: "2026-07-23T00:00:00+00:00",
        providers: ["fake-provider"],
        credential_pool: {
          "fake-provider": {
            entry_count: 1,
            entries: [
              {
                id: "FAKE-entry-1",
                priority: 1,
                last_status: "ok",
                last_refresh: "2026-07-23T00:00:00Z",
                source: "TEST_source",
                label: "TEST_label",
                // 模擬上游防線全數失效、秘密欄位一路漏到 UI 的情境:
                access_token: FAKE_ACCESS_TOKEN,
                refresh_token: FAKE_REFRESH_TOKEN,
              },
            ],
          },
        },
      },
    },
  };
  const html = mod.renderCredentials(lanes, credentials);
  // UI 欄位白名單是獨立防線:未列舉的欄位(含秘密)不渲染
  assert.ok(!html.includes(FAKE_ACCESS_TOKEN), "access_token 值不得出現在渲染輸出");
  assert.ok(!html.includes(FAKE_REFRESH_TOKEN), "refresh_token 值不得出現在渲染輸出");
  // 白名單欄位正常呈現(正向斷言)
  assert.ok(html.includes("TEST_label"));
  assert.ok(html.includes("TEST_source"));
  assert.ok(html.includes("fake-provider"));
  assert.ok(html.includes("test-lane"));
});

test("lane 表:實際生效模型欄——profile 自訂/繼承全域/native 三態呈現,無空白", () => {
  const lanes = [
    {
      id: "lane-profile", capability: "c", model: null, hermes_profile: "alpha", status: "active",
      effective_model: "FAKE-model-alpha", effective_model_source: "profile", effective_provider: "FAKE-prov",
    },
    {
      id: "lane-global", capability: "c", model: null, hermes_profile: "beta", status: "active",
      effective_model: "FAKE-model-global", effective_model_source: "global", effective_provider: "FAKE-prov",
    },
    {
      id: "lane-native", capability: "c", model: null, status: "active",
      effective_model: "(native session)", effective_model_source: "native", effective_provider: null,
    },
  ];
  const html = mod.renderCredentials(lanes, { available: true, profiles: {} });
  // 生效模型有值時必須呈現實際值(非 registry 的 null)
  assert.ok(html.includes("FAKE-model-alpha"), "profile 自訂模型必須渲染實際值");
  assert.ok(html.includes("profile 自訂"), "須標示 profile 自訂");
  assert.ok(html.includes("FAKE-model-global"), "繼承全域時必須帶出全域值");
  assert.ok(html.includes("繼承全域"), "須標示繼承全域");
  assert.ok(html.includes("(native session)"), "native lane 標示 native session");
  // 欄名/tooltip 註明資料來源是 config.yaml 而非 registry
  assert.ok(html.includes("實際生效模型"));
  assert.ok(html.includes("config.yaml"), "須註明來源為 profile config");
  // registry 的 null 不再以空白/「—」冒充 model 值
  assert.ok(!html.includes("<td></td>"), "不得出現完全空白的 cell");
});

test("憑證頁:頂部警語不可移除(逐字比對提案 §3.4 文字)", () => {
  const html = mod.renderCredentials([], { available: true, profiles: {} });
  assert.ok(html.includes(mod.WARNING_TEXT));
  assert.ok(mod.WARNING_TEXT.includes("絕不顯示 token"));
});

test("憑證頁:available=false 顯示無法查詢,不噴例外", () => {
  const html = mod.renderCredentials([], {
    available: false,
    reason: "LOCALAPPDATA 未設定,此環境無法查詢",
    profiles: {},
  });
  assert.ok(html.includes("LOCALAPPDATA 未設定"));
});

test("憑證頁原始碼:禁止對憑證資料做泛型 JSON dump(§3.4 UI 層規則)", async () => {
  const { readFile } = await import("node:fs/promises");
  const source = await readFile(join(root, "src", "views", "Credentials.tsx"), "utf8");
  assert.ok(!source.includes("JSON.stringify"), "Credentials.tsx 不得用 JSON.stringify 泛型 dump");
});

// ---------------------------------------------------------------------------
// 功能三:排程健康表
// ---------------------------------------------------------------------------

test("排程表:DRIFTED 明顯標示+花費方向;表格內零操作按鈕", () => {
  const rows = [
    {
      source: "systemd", job_name: "hermes-test", schedule_expr: "*-*-* 08:10:00",
      deployed: true, timer_active: "active", last_result: "成功",
      next_trigger: "2026-07-24 08:10:00", last_trigger: "2026-07-23 08:10:00",
      model_drift: "n/a", drift_cost_direction: null,
    },
    {
      source: "hermes-native", job_name: "TEST_ai_news", schedule_expr: "0 8 * * *",
      deployed: true, timer_active: "active", last_result: "ok",
      next_trigger: "2026-07-24T08:00:00Z", last_trigger: "從未觸發",
      model_drift: "DRIFTED", drift_cost_direction: "更貴",
    },
  ];
  const html = mod.renderSchedule(rows);
  assert.ok(html.includes("DRIFTED"));
  assert.ok(html.includes("更貴"));
  assert.ok(html.includes("drift-flag"));
  assert.ok(html.includes("systemd"));
  assert.ok(html.includes("hermes-native"));
  // §4.3/§4.7:明確不放任何操作按鈕(pin/對齊/重跑)——渲染輸出零 <button>
  assert.ok(!html.includes("<button"), "排程表渲染輸出不得含任何 button");
});

test("排程表:空資料顯示提示,不噴例外", () => {
  const html = mod.renderSchedule([]);
  assert.ok(html.includes("沒有任何排程資料"));
});

// ---------------------------------------------------------------------------
// 功能一:Hermes Sessions
// ---------------------------------------------------------------------------

test("sessions:欄位白名單——content/metadata 即使出現在資料裡也不渲染", () => {
  const sessions = [
    {
      session_id: "TEST_s1", session_source: "cli", title: "TEST 標題",
      model: "FAKE-model-a", started_at: "2026-07-09T00:00:00+00:00",
      ended_at: null, message_count: 2,
      // 模擬上游意外多回傳的欄位:UI 白名單不渲染
      content: FAKE_MESSAGE_CONTENT,
      metadata: { session_key: FAKE_SESSION_KEY, chat_id: "FAKE_ui_chat_id_888" },
    },
  ];
  const html = mod.renderSessions(sessions);
  assert.ok(!html.includes(FAKE_MESSAGE_CONTENT), "訊息內容不得渲染");
  assert.ok(!html.includes(FAKE_SESSION_KEY), "metadata.session_key 不得渲染");
  assert.ok(!html.includes("FAKE_ui_chat_id_888"), "metadata.chat_id 不得渲染");
  assert.ok(html.includes("TEST_s1"));
  assert.ok(html.includes("TEST 標題"));
  assert.ok(html.includes("cli"));
});

test("sessions:空清單顯示提示,不噴例外", () => {
  const html = mod.renderSessions([]);
  assert.ok(html.includes("沒有 session 資料"));
});

test("sessions 原始碼:不呼叫 iter_events 對應的任何全文 API(不做點入看全文)", async () => {
  const { readFile } = await import("node:fs/promises");
  const source = await readFile(join(root, "src", "views", "Sessions.tsx"), "utf8");
  // 取數只有 /api/hermes/sessions 一條路;沒有任何 per-session detail fetch
  assert.ok(source.includes("/api/hermes/sessions"));
  assert.ok(!source.includes("/api/hermes/sessions/"), "不得有單一 session 全文端點呼叫");
});
