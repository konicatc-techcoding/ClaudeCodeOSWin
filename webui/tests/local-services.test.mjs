// 「本機服務」sidebar 狀態區(2026-08-03,唯讀零操作鍵)靜態鎖定:
// - 純唯讀:零 <button、零 onClick(啟動必須在 UI 外做——UI 死了按鈕也死,
//   本區塊誠實面對這個結構,只給燈號與啟動指引)。
// - 取數同源不重造:8799 走 ResidentStatus 共享 store 的輪詢成敗
//   (useResidentApiState,零額外請求)、8787 走 ServiceControl 抽出的
//   bridge health 共享 store(useBridgeHealth)——本檔唯一的 fetch 位點
//   是 PTY 8801 health(重用 Terminal view 的檢查端點),節奏 30 秒,
//   不新增高頻輪詢。
// - localhost-only:唯一 URL 是寫死的 127.0.0.1:8801/health。
import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");

async function readComponent() {
  return readFile(join(root, "src", "LocalServices.tsx"), "utf8");
}

test("零操作鍵:LocalServices.tsx 不得含 button/onClick(含 disabled 假按鈕)", async () => {
  const source = await readComponent();
  assert.ok(!source.includes("<button"), "本機服務區塊不得含任何 button");
  assert.ok(!source.includes("onClick"), "本機服務區塊不得含任何點擊處理器");
  assert.ok(!source.toLowerCase().includes("disabled"), "不得做 disabled 假控制");
});

test("取數同源:8799/8787 訂閱既有共享 store,唯一 fetch 位點是 PTY 8801 health", async () => {
  const source = await readComponent();
  assert.ok(source.includes("useResidentApiState"), "8799 須複用 resident 共享輪詢的成敗(零額外請求)");
  assert.ok(source.includes("useBridgeHealth"), "8787 須訂閱 ServiceControl 的 bridge health 共享 store");
  const fetches = source.match(/fetch\(/g) ?? [];
  assert.equal(fetches.length, 1, "fetch 位點僅允許 PTY health 探測一處");
  assert.match(source, /PTY_HEALTH_URL = "http:\/\/127\.0\.0\.1:8801\/health"/, "PTY 端點須寫死 127.0.0.1:8801");
});

test("輪詢節奏:PTY 探測沿用既有 30 秒節奏,不新增高頻輪詢", async () => {
  const source = await readComponent();
  assert.match(source, /PTY_HEALTH_POLL_MS = 30_000/, "PTY 輪詢須為 30 秒(與 resident/bridge 同節奏)");
  const intervals = source.match(/setInterval\(/g) ?? [];
  assert.equal(intervals.length, 1, "本檔只允許一個 interval(PTY 共享 store)");
});

test("誠實邊界:UI 5173 恆綠須註明判斷依據;附啟動指引指向 UI 外的 launcher", async () => {
  const source = await readComponent();
  assert.ok(source.includes("本頁能載入即代表"), "UI 恆綠必須誠實註明「本頁能載入即代表運行」");
  assert.ok(source.includes("start_webui_stack"), "須附一行指引:未全綠時用 scripts/start_webui_stack 啟動");
  // 四個服務的 port 全數呈現
  for (const port of ["8799", "8787", "8801", "5173"]) {
    assert.ok(source.includes(port), `區塊須含 port ${port}`);
  }
});

test("共享 store 來源不回退:ServiceControl 仍只有兩個 fetch 位點且匯出 useBridgeHealth", async () => {
  const source = await readFile(join(root, "src", "ServiceControl.tsx"), "utf8");
  assert.ok(source.includes("export function useBridgeHealth"), "bridge health 須為可訂閱的共享 store");
  const fetches = source.match(/fetch\(/g) ?? [];
  assert.equal(fetches.length, 2, "抽出共享 store 後 fetch 位點仍須恰為 health + 服務操作兩處");
});

test("掛載位置(2026-08-03 遷移):LocalServices 與 ServiceControl 並排於總覽頁 grid、Worker/Adapter 狀態上方", async () => {
  const app = await readFile(join(root, "src", "App.tsx"), "utf8");
  assert.ok(!app.includes("<LocalServices />"), "App.tsx(sidebar)不得再掛本機服務區塊");
  const overview = await readFile(join(root, "src", "views", "Overview.tsx"), "utf8");
  const gridIdx = overview.indexOf('className="service-overview-grid"');
  const serviceIdx = overview.indexOf("<ServiceControl />");
  const localIdx = overview.indexOf("<LocalServices />");
  const workerPanelIdx = overview.indexOf("Worker / Adapter 狀態");
  assert.ok(gridIdx >= 0 && serviceIdx > gridIdx && localIdx > serviceIdx && workerPanelIdx > localIdx,
    "本機服務區塊必須與服務控制鍵同在總覽頁兩欄 grid、Worker/Adapter 狀態上方");
});
