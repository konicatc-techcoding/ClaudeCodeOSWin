// 背景常駐狀態燈號的 UI 渲染層測試(docs/webui-service-control-proposal.md §1)。
// 形態比照 stage3-render.test.mjs:rolldown bundle 純渲染元件 + react-dom/server。
// 核心斷言:五態各自呈現(燈色 class + 狀態文字)、tooltip 分層細節、
// **渲染輸出零 <button>**(寫入部分未核准,UI 不得出現任何操作入口,
// 含 disabled 假按鈕——mock 清零原則)。
import assert from "node:assert/strict";
import test from "node:test";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { pathToFileURL, fileURLToPath } from "node:url";
import { rolldown } from "rolldown";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");

let mod;
let tmp;

test.before(async () => {
  const bundle = await rolldown({
    input: join(root, "tests", "fixtures", "resident-render-entry.tsx"),
    platform: "node",
    logLevel: "silent",
  });
  const { output } = await bundle.generate({ format: "esm" });
  await bundle.close();
  tmp = await mkdtemp(join(tmpdir(), "resident-render-"));
  const bundlePath = join(tmp, "resident-render-entry.mjs");
  await writeFile(bundlePath, output[0].code);
  mod = await import(pathToFileURL(bundlePath).href);
});

test.after(async () => {
  if (tmp) await rm(tmp, { recursive: true, force: true });
});

function payload(overrides) {
  return {
    light: "green",
    text: "背景常駐中",
    detail: "常駐單元全部 active,gateway 就緒",
    checked_at: "2026-07-24T00:00:00+00:00",
    distro: { name: "Ubuntu", running: true, detail: "distro 狀態:Running" },
    resident_units: ["hermes-worker.service", "hermes-telegram.service"],
    units: {
      "hermes-worker.service": { state: "active", sub: "running", load: "loaded", resident: true },
      "hermes-telegram.service": { state: "active", sub: "running", load: "loaded", resident: true },
    },
    gateway: { ready: true, state: "running", detail: "gateway 就緒" },
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// 五態各自呈現(提案 §1.2 狀態機)
// ---------------------------------------------------------------------------

const FIVE_STATES = [
  ["green", "背景常駐中"],
  ["yellow", "啟動中/暖機中"],
  ["orange", "部分服務未運作"],
  ["red", "背景服務未運作"],
  ["gray", "無法查詢"],
];

for (const [light, text] of FIVE_STATES) {
  test(`燈號五態:${light} 呈現對應 class 與狀態文字「${text}」`, () => {
    const html = mod.renderBadge(payload({ light, text }));
    assert.ok(html.includes(`resident-light-${light}`), `須含 resident-light-${light} class`);
    assert.ok(html.includes(`背景服務:${text}`), "狀態文字須呈現");
    assert.ok(html.includes("resident-dot"), "須含燈號色點");
  });
}

test("零操作入口:五態渲染輸出皆不含任何 button(寫入部分未核准)", () => {
  for (const [light, text] of FIVE_STATES) {
    const html = mod.renderBadge(payload({ light, text }));
    assert.ok(!html.includes("<button"), `${light} 態不得含 button`);
    assert.ok(!html.toLowerCase().includes("disabled"), `${light} 態不得含 disabled 假控制`);
  }
  assert.ok(!mod.renderBadge(null).includes("<button"), "無資料時也不得含 button");
});

// ---------------------------------------------------------------------------
// tooltip 分層細節(提案 §1.3:資料已在回應裡,不另發請求)
// ---------------------------------------------------------------------------

test("tooltip:含 distro / 常駐單元逐一 / gateway 三層細節", () => {
  const html = mod.renderBadge(payload({}));
  assert.ok(html.includes("WSL distro(Ubuntu)"), "tooltip 須含 distro 層");
  assert.ok(html.includes("hermes-worker.service:active/running"), "tooltip 須含 worker 單元狀態");
  assert.ok(html.includes("hermes-telegram.service:active/running"), "tooltip 須含 telegram 單元狀態");
  assert.ok(html.includes("gateway:gateway 就緒"), "tooltip 須含 gateway 層");
});

test("tooltip:distro 未運作(units/gateway 為 null)時誠實標示未探測", () => {
  const tip = mod.buildResidentTooltip(
    payload({
      light: "red",
      text: "背景服務未運作",
      detail: "WSL distro Ubuntu 未運作(未進行服務層探測,避免喚醒 distro)",
      distro: { name: "Ubuntu", running: false, detail: "distro 狀態:Stopped" },
      units: null,
      gateway: null,
    }),
  );
  assert.ok(tip.includes("未探測"), "未探測的層不得偽造狀態");
  assert.ok(tip.includes("避免喚醒"), "須帶出止步原因");
});

test("tooltip:單元未載入時顯示「未載入」而非空白", () => {
  const html = mod.renderBadge(
    payload({ light: "orange", text: "部分服務未運作", units: {} }),
  );
  assert.ok(html.includes("hermes-worker.service:未載入"));
});

test("無資料(API 未連線)→ 灰燈「無法查詢」,不噴例外", () => {
  const html = mod.renderBadge(null);
  assert.ok(html.includes("resident-light-gray"));
  assert.ok(html.includes("背景服務:無法查詢"));
  assert.ok(html.includes("唯讀 API 未連線"));
});

// ---------------------------------------------------------------------------
// 原始碼靜態鎖定
// ---------------------------------------------------------------------------

test("輪詢頻率:前端 30 秒(提案 §1.3 拍板值)", () => {
  assert.equal(mod.RESIDENT_POLL_INTERVAL_MS, 30_000);
});

test("原始碼:元件零 button/onClick;取數僅經唯讀 apiGet(零直連 fetch)", async () => {
  const source = await readFile(join(root, "src", "ResidentStatus.tsx"), "utf8");
  assert.ok(!source.includes("<button"), "元件不得含 button");
  assert.ok(!source.includes("onClick"), "元件不得含 onClick 操作入口");
  assert.ok(!source.includes("fetch("), "取數只能走 apiGet,不得自行 fetch");
  assert.ok(source.includes('apiGet<ResidentStatusPayload>("/api/resident-status")'));
});

test("原始碼:App.tsx 掛載燈號於共用 sidebar(全 view 可見)", async () => {
  const app = await readFile(join(root, "src", "App.tsx"), "utf8");
  assert.ok(app.includes("<ResidentStatus />"), "App 須掛載燈號元件");
  const sidebarIdx = app.indexOf('className="sidebar"');
  const residentIdx = app.indexOf("<ResidentStatus />");
  const workspaceIdx = app.indexOf('className="workspace"');
  assert.ok(sidebarIdx >= 0 && residentIdx > sidebarIdx && residentIdx < workspaceIdx,
    "燈號必須在全 view 共用的 sidebar 區塊內(不隨 view 切換卸載)");
});
