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

// --- 2026-08-04:第三條軸(生效模型)+ 憑證×模型交叉檢查(唯讀告警)---

function credProfile(overrides = {}) {
  return {
    auth_json_exists: true,
    mtime: "2026-08-04T00:00:00+00:00",
    providers: ["prov-cred"],
    credential_pool: {
      "prov-cred": {
        entry_count: 1,
        entries: [{
          id: "FAKE-entry-1", priority: 1, last_status: "ok",
          last_refresh: "2026-08-04T00:00:00Z", source: "TEST_source", label: "TEST_label",
        }],
      },
    },
    effective_provider: "prov-model",
    effective_model: "FAKE-vendor/FAKE-model-x",
    effective_model_source: "global",
    credential_model_consistency: {
      light: "orange",
      text: "生效模型 provider「prov-model」不在本 store 的 credential_pool 中,可能依賴環境變數",
      effective_provider: "prov-model",
      entry_count: null,
      exhausted_entry_count: 0,
    },
    ...overrides,
  };
}

test("憑證頁:每列有生效模型三欄,且兩條 provider 軸措辭不撞名", () => {
  const html = mod.renderCredentials([], {
    available: true,
    profiles: { "(global-root)": credProfile() },
  });
  // A:三欄實際帶出值
  assert.ok(html.includes("prov-model"), "生效模型 provider 必須顯示");
  assert.ok(html.includes("FAKE-vendor/FAKE-model-x"), "生效 model.default 必須顯示");
  assert.ok(html.includes("繼承全域"), "設定來源(global)必須標示");
  // 命名正名:兩條軸各自有明確中文欄名,不再都叫 provider
  assert.ok(html.includes(mod.EFFECTIVE_PROVIDER_LABEL_TEXT));
  assert.ok(html.includes(mod.CREDENTIAL_PROVIDER_LABEL_TEXT));
  assert.notEqual(mod.EFFECTIVE_PROVIDER_LABEL_TEXT, mod.CREDENTIAL_PROVIDER_LABEL_TEXT);
  // 舊的無前綴 "providers(僅名稱)" 措辭不得殘留(那是撞名的成因)
  assert.ok(!html.includes("providers(僅名稱)"), "撞名的舊欄名不得殘留");
  // 憑證軸的 provider 名仍在(兩軸並列,不是取代)
  assert.ok(html.includes("prov-cred"));
  // 摺疊摘要行也要照得到模型軸(誤解成因:改了全域 provider 卻「哪都沒變」)
  assert.ok(html.includes("cred-summary-model"));
});

test("憑證頁:交叉檢查橙燈——文案誠實說「可能依賴環境變數」,不斷言壞掉", () => {
  const html = mod.renderCredentials([], {
    available: true,
    profiles: { "(global-root)": credProfile() },
  });
  assert.ok(html.includes("可能依賴環境變數"));
  assert.ok(html.includes("cred-consistency-orange"), "橙燈 class 必須套用");
  for (const word of ["壞掉", "失效", "設定錯誤"]) {
    assert.ok(!html.includes(word), `告警文案不得出現斷言式措辭:${word}`);
  }
  // 唯讀邊界:整頁零操作按鈕(沒有清除/重新登入/修復入口)
  assert.ok(!html.includes("<button"), "憑證頁渲染輸出不得含任何 button");
});

test("憑證頁:綠燈/灰燈各自套用對應 class,燈號由後端判定不由前端硬算", () => {
  const green = mod.renderCredentials([], {
    available: true,
    profiles: {
      p: credProfile({
        credential_model_consistency: {
          light: "green", text: "生效模型 provider「prov-cred」在本 store 有 1 筆憑證條目",
          effective_provider: "prov-cred", entry_count: 1, exhausted_entry_count: 0,
        },
      }),
    },
  });
  assert.ok(green.includes("cred-consistency-green"));
  const gray = mod.renderCredentials([], {
    available: true,
    profiles: {
      p: credProfile({
        effective_provider: null, effective_model: "無法查詢", effective_model_source: "unknown",
        credential_model_consistency: {
          light: "gray", text: "生效模型 provider 無法查詢,略過憑證交叉檢查",
          effective_provider: null, entry_count: null, exhausted_entry_count: 0,
        },
      }),
    },
  });
  assert.ok(gray.includes("cred-consistency-gray"));
  assert.ok(gray.includes("略過憑證交叉檢查"));
});

test("憑證頁:auth.json 不存在時,生效模型軸仍要顯示(本次補洞的重點)", () => {
  const html = mod.renderCredentials([], {
    available: true,
    profiles: {
      default: {
        auth_json_exists: false,
        effective_provider: "prov-model",
        effective_model: "FAKE-model-inherited",
        effective_model_source: "global",
        credential_model_consistency: {
          light: "orange", text: "本 store 無 auth.json,生效模型 provider「prov-model」在此無任何憑證條目,可能依賴環境變數或上層設定",
          effective_provider: "prov-model", entry_count: null, exhausted_entry_count: 0,
        },
      },
    },
  });
  assert.ok(html.includes("auth.json 不存在"));
  assert.ok(html.includes("FAKE-model-inherited"), "無 auth.json 也要看得到生效模型");
});

test("憑證頁:last_status=exhausted 的條目有紅色標示(class + pill)", () => {
  const profile = credProfile();
  profile.credential_pool["prov-cred"].entries[0].last_status = "exhausted";
  profile.credential_model_consistency.exhausted_entry_count = 1;
  const html = mod.renderCredentials([], { available: true, profiles: { gptcoding: profile } });
  assert.ok(html.includes("cred-entry-exhausted"), "耗盡條目的列必須帶標示 class");
  assert.ok(html.includes("cred-status-exhausted"), "last_status 必須以紅色 pill 呈現");
  assert.ok(html.includes("exhausted"));
  // 非耗盡的條目不得誤標
  const ok = mod.renderCredentials([], { available: true, profiles: { p: credProfile() } });
  assert.ok(!ok.includes("cred-entry-exhausted"));
  assert.ok(!ok.includes("cred-status-exhausted"));
});

test("CSS cascade:耗盡列的紅色標示確實蓋過 .data-table td 的預設色", async () => {
  const { readFile } = await import("node:fs/promises");
  const { computeStyle } = await import("./helpers/css-cascade.mjs");
  const css = await readFile(join(root, "src", "globals.css"), "utf8");
  const chain = [
    { tag: "div", classes: ["cred-pool-block"] },
    { tag: "table", classes: ["data-table"] },
    { tag: "tbody", classes: [] },
    { tag: "tr", classes: ["cred-entry-exhausted"] },
    { tag: "td", classes: [] },
  ];
  const style = computeStyle(css, chain);
  assert.equal(style.color, "#f3b1b7", "耗盡列的文字色必須勝出(否則紅標形同不存在)");
  assert.ok(style.background && style.background.includes("239,111,124"), "耗盡列須有紅色底");

  // 摘要行的模型軸小字必須勝過 `.cred-profile summary small` 的預設灰
  const summaryChain = [
    { tag: "details", classes: ["cred-profile"] },
    { tag: "summary", classes: [] },
    { tag: "small", classes: ["cred-summary-model"] },
  ];
  assert.equal(computeStyle(css, summaryChain).color, "#8fb8e8",
    "摘要行的生效模型小字須與憑證軸小字顏色可辨,不得被預設灰蓋掉");
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
// 總覽:Worker / Adapter 狀態卡片(systemd 快照三分支;2026-07-28 換源修正)
// ---------------------------------------------------------------------------

const SYSTEMD_BASE = {
  checked_at: "2026-07-28T00:00:00+00:00",
  distro: { name: "Ubuntu", running: true, detail: "TEST" },
  timers: {},
};

test("總覽 systemd 卡片:wsl_down 顯示「WSL 未運作」,不得偽裝成「未安裝」", () => {
  const html = mod.renderSystemdMetrics({
    ...SYSTEMD_BASE,
    status: "wsl_down",
    status_text: "WSL 未運作",
    reason: "WSL distro Ubuntu 未運作(未探測 systemd,避免喚醒 distro)",
    distro: { name: "Ubuntu", running: false, detail: "distro 狀態:Stopped" },
    units: null,
  });
  assert.ok(html.includes("WSL 未運作"));
  assert.ok(html.includes("避免喚醒"), "退化原因須呈現給使用者");
  assert.ok(!html.includes("未安裝"), "查不到不得顯示成未安裝");
});

test("總覽 systemd 卡片:unavailable 顯示「無法查詢」,不得偽裝成「未安裝」", () => {
  const html = mod.renderSystemdMetrics({
    ...SYSTEMD_BASE,
    status: "unavailable",
    status_text: "無法查詢",
    reason: "wsl -d systemctl --user 查詢失敗,單元狀態無法查詢",
    units: null,
  });
  assert.ok(html.includes("無法查詢"));
  assert.ok(!html.includes("未安裝"), "查不到不得顯示成未安裝");
});

test("總覽 systemd 卡片:ok 顯示真實狀態;單元缺席才是「未安裝」", () => {
  const html = mod.renderSystemdMetrics({
    ...SYSTEMD_BASE,
    status: "ok",
    status_text: "查詢成功",
    reason: null,
    units: {
      "hermes-worker.service": { pid: "-", last_exit: "active/running", load: "loaded" },
      "hermes-rss.timer": { pid: "-", last_exit: "inactive/dead", load: "loaded" },
      // hermes-telegram.service / hermes-cron-daily-memory-check.timer 缺席 → 未安裝
    },
  });
  assert.ok(html.includes("運作中"));
  assert.ok(html.includes("已停止(等排程)"));
  assert.ok(html.includes("未安裝"), "查得到且單元不存在才顯示未安裝");
  assert.ok(!html.includes("WSL 未運作"));
  assert.ok(!html.includes("無法查詢"));
  // 唯讀邊界:卡片零操作按鈕
  assert.ok(!html.includes("<button"));
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
