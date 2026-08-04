// 個別服務小燈號測試(2026-07-27 拍板;掛在 ServiceControl 每列服務名旁)。
// 形態比照 resident-render.test.mjs:rolldown bundle 純渲染元件 + react-dom/server。
// 核心斷言:
// 1. 狀態映射各分支——active(綠)/active+gateway 暖機(worker 黃)/activating(黃)/
//    failed(紅)/inactive(紅)/未載入/distro 未運作/API 失敗(灰「無法查詢」)。
// 2. 與聚合燈資料同源:個別燈取數走 ResidentStatus 的共享 store(8799 唯讀),
//    絕不從 bridge(8787)拿狀態;文字/tooltip 給狀態名,不只靠顏色。
// 3. 不因新增燈號引入第二條輪詢:ServiceControl 零新增 fetch/interval,
//    ResidentStatus 維持單一 apiGet 位點+單一 setInterval。
import assert from "node:assert/strict";
import test from "node:test";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { pathToFileURL, fileURLToPath } from "node:url";
import { rolldown } from "rolldown";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");

const WORKER = "hermes-worker.service";
const TELEGRAM = "hermes-telegram.service";

let mod;
let tmp;

test.before(async () => {
  const bundle = await rolldown({
    input: join(root, "tests", "fixtures", "service-unit-light-entry.tsx"),
    platform: "node",
    logLevel: "silent",
  });
  const { output } = await bundle.generate({ format: "esm" });
  await bundle.close();
  tmp = await mkdtemp(join(tmpdir(), "service-unit-light-"));
  const bundlePath = join(tmp, "service-unit-light-entry.mjs");
  await writeFile(bundlePath, output[0].code);
  mod = await import(pathToFileURL(bundlePath).href);
});

test.after(async () => {
  if (tmp) await rm(tmp, { recursive: true, force: true });
});

function unitInfo(state, sub) {
  return { state, sub, load: "loaded", resident: true };
}

function payload(overrides) {
  return {
    light: "green",
    text: "背景常駐中",
    detail: "常駐單元全部 active,gateway 就緒",
    checked_at: "2026-07-27T00:00:00+00:00",
    distro: { name: "Ubuntu", running: true, detail: "distro 狀態:Running" },
    resident_units: [WORKER, TELEGRAM],
    units: {
      [WORKER]: unitInfo("active", "running"),
      [TELEGRAM]: unitInfo("active", "running"),
    },
    gateway: { ready: true, state: "running", detail: "gateway 就緒" },
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// 狀態映射各分支(拍板項 2)
// ---------------------------------------------------------------------------

test("active + gateway 就緒 → 綠燈,文字為狀態名 active", () => {
  for (const unit of [WORKER, TELEGRAM]) {
    const { light, text } = mod.buildUnitLight(payload({}), unit);
    assert.equal(light, "green");
    assert.equal(text, "active");
  }
});

test("gateway 暖機:worker 黃燈「暖機中」;telegram 不受影響維持綠", () => {
  const warming = payload({
    light: "yellow",
    text: "啟動中/暖機中",
    gateway: {
      ready: false,
      state: null,
      detail: "gateway 狀態檔不存在——可能暖機中(啟動後約 3.5 分鐘才寫入)",
    },
  });
  const worker = mod.buildUnitLight(warming, WORKER);
  assert.equal(worker.light, "yellow");
  assert.equal(worker.text, "暖機中");
  // 前端不自己發明暖機計時:直接繼承資料層 gateway.detail(3.5 分鐘教訓內建)
  assert.ok(worker.detail.includes("3.5 分鐘"), "detail 須帶出資料層的暖機說明");
  const telegram = mod.buildUnitLight(warming, TELEGRAM);
  assert.equal(telegram.light, "green");
  assert.equal(telegram.text, "active");
});

test("gateway pid 已死(2026-08-04 事故):worker 紅燈「gateway 已死」,非黃燈暖機", () => {
  // 事故形態:狀態檔宣稱 running 但 pid 不存在——資料層 dead=true。
  // 暖機是「還沒起來」,這是「起來過但死了」,不得以黃燈掩蓋。
  const dead = payload({
    light: "red",
    text: "背景服務未運作",
    gateway: {
      ready: false,
      state: "running",
      dead: true,
      pid: 16224,
      pid_alive: false,
      detail: "狀態檔宣稱 running 但pid 16224 不存在(狀態檔停更於 2026-08-03T00:04:00+00:00)",
    },
  });
  const worker = mod.buildUnitLight(dead, WORKER);
  assert.equal(worker.light, "red");
  assert.equal(worker.text, "gateway 已死");
  assert.ok(worker.detail.includes("pid 16224 不存在"), "detail 須帶出資料層的死亡說明");
  assert.ok(worker.detail.includes("狀態檔停更於"), "detail 須帶出狀態檔停更時間");
  // telegram 不受 gateway 影響
  const telegram = mod.buildUnitLight(dead, TELEGRAM);
  assert.equal(telegram.light, "green");
});

test("activating → 黃燈,文字為狀態名 activating", () => {
  const p = payload({ units: { [WORKER]: unitInfo("activating", "auto-restart"), [TELEGRAM]: unitInfo("active", "running") } });
  const { light, text, detail } = mod.buildUnitLight(p, WORKER);
  assert.equal(light, "yellow");
  assert.equal(text, "activating");
  assert.ok(detail.includes("啟動中"));
});

test("failed → 紅燈;inactive → 紅燈;文字帶出各自狀態名(不靠顏色區分)", () => {
  const p = payload({
    units: { [WORKER]: unitInfo("failed", "failed"), [TELEGRAM]: unitInfo("inactive", "dead") },
    gateway: null,
  });
  const failed = mod.buildUnitLight(p, WORKER);
  assert.equal(failed.light, "red");
  assert.equal(failed.text, "failed");
  const inactive = mod.buildUnitLight(p, TELEGRAM);
  assert.equal(inactive.light, "red");
  assert.equal(inactive.text, "inactive");
  assert.notEqual(failed.text, inactive.text, "紅燈兩情境必須以文字區分");
});

test("罕見 state(deactivating 等)→ 灰燈+誠實顯示原文,不偽造已知狀態", () => {
  const p = payload({ units: { [WORKER]: unitInfo("deactivating", "stop-sigterm"), [TELEGRAM]: unitInfo("active", "running") } });
  const { light, text } = mod.buildUnitLight(p, WORKER);
  assert.equal(light, "gray");
  assert.equal(text, "deactivating");
});

test("查無資料三情境 → 灰燈「無法查詢」(mock 清零:不顯示假狀態)", () => {
  // (a) API 失敗/未連線
  const apiDown = mod.buildUnitLight(null, WORKER);
  assert.equal(apiDown.light, "gray");
  assert.equal(apiDown.text, "無法查詢");
  assert.ok(apiDown.detail.includes("8799"), "detail 須指明唯讀 API 未連線");
  // (b) distro 未運作(資料層止步不探測服務層)
  const distroDown = mod.buildUnitLight(
    payload({
      light: "red",
      distro: { name: "Ubuntu", running: false, detail: "distro 狀態:Stopped" },
      units: null,
      gateway: null,
    }),
    WORKER,
  );
  assert.equal(distroDown.light, "gray");
  assert.equal(distroDown.text, "無法查詢");
  assert.ok(distroDown.detail.includes("未探測"), "distro 未運作須誠實標示服務層未探測");
  // (c) 單元未載入(systemd 查無此單元)
  const missing = mod.buildUnitLight(payload({ units: {} }), TELEGRAM);
  assert.equal(missing.light, "gray");
  assert.equal(missing.text, "無法查詢");
  assert.ok(missing.detail.includes("未載入"));
});

// ---------------------------------------------------------------------------
// 渲染輸出(文字+tooltip 給狀態名,不只靠顏色;唯讀零操作入口)
// ---------------------------------------------------------------------------

test("渲染:燈色 class + 可見狀態文字 + title tooltip + aria-label", () => {
  const html = mod.renderUnitLight(payload({}), WORKER);
  assert.ok(html.includes("service-unit-light-green"), "須含燈色 class");
  assert.ok(html.includes("service-unit-light-dot"), "須含色點");
  assert.ok(html.includes(">active<"), "狀態名須為可見文字,不只靠顏色");
  assert.ok(html.includes(`title="${WORKER}:active`), "tooltip 須含單元名與狀態名");
  assert.ok(html.includes(`aria-label="${WORKER} 狀態:active"`), "須有 aria-label");
});

test("渲染:各分支輸出零 button/onclick(個別燈唯讀,操作只在既有按鈕)", () => {
  const cases = [
    [payload({}), WORKER],
    [payload({ gateway: { ready: false, state: null, detail: "暖機中" } }), WORKER],
    [payload({ units: { [WORKER]: unitInfo("failed", "failed") }, gateway: null }), WORKER],
    [null, WORKER],
  ];
  for (const [status, unit] of cases) {
    const html = mod.renderUnitLight(status, unit);
    assert.ok(!html.includes("<button"), "個別燈不得含 button");
    assert.ok(!html.toLowerCase().includes("onclick"), "個別燈不得含點擊處理");
  }
});

test("與聚合燈一致性:同一份 payload 下,個別燈不與聚合狀態矛盾", () => {
  // 全綠 payload:聚合綠 ↔ 兩顆個別燈皆綠
  const allGreen = payload({});
  assert.equal(allGreen.light, "green");
  assert.equal(mod.buildUnitLight(allGreen, WORKER).light, "green");
  assert.equal(mod.buildUnitLight(allGreen, TELEGRAM).light, "green");
  // 部分停止 payload(聚合 orange):active 者綠、inactive 者紅
  const partial = payload({
    light: "orange",
    text: "部分服務未運作",
    units: { [WORKER]: unitInfo("active", "running"), [TELEGRAM]: unitInfo("inactive", "dead") },
    gateway: null,
  });
  assert.equal(mod.buildUnitLight(partial, WORKER).light, "green");
  assert.equal(mod.buildUnitLight(partial, TELEGRAM).light, "red");
});

// ---------------------------------------------------------------------------
// 原始碼靜態鎖定:同源取數、不開第二條輪詢、讀寫分離不回退
// ---------------------------------------------------------------------------

test("同源不加倍:ServiceControl 經 useResidentStatus 取數,零新增 fetch/輪詢", async () => {
  const source = await readFile(join(root, "src", "ServiceControl.tsx"), "utf8");
  assert.ok(source.includes("useResidentStatus()"), "個別燈取數必須走共享 hook");
  assert.ok(source.includes("<ServiceUnitLight"), "每列服務須掛個別燈");
  assert.ok(!source.includes("apiGet"), "ServiceControl 不得自行呼叫唯讀 API(共享 store 一條路)");
  assert.ok(!source.includes("/api/resident-status"), "ServiceControl 不得直打 resident-status 端點");
  const intervals = source.match(/setInterval\(/g) ?? [];
  assert.equal(intervals.length, 1, "setInterval 位點僅允許既有的 bridge health 探測一處(不開第二條輪詢)");
});

test("單一輪詢來源:ResidentStatus 恰一個 apiGet 位點+一個 setInterval(共享 store)", async () => {
  const source = await readFile(join(root, "src", "ResidentStatus.tsx"), "utf8");
  const apiGets = source.match(/apiGet</g) ?? [];
  assert.equal(apiGets.length, 1, "resident-status 取數位點必須唯一(聚合燈與個別燈同源)");
  const intervals = source.match(/setInterval\(/g) ?? [];
  assert.equal(intervals.length, 1, "輪詢 interval 必須唯一");
  assert.ok(source.includes("useSyncExternalStore"), "共享 store 須以訂閱方式對多元件供數");
});

test("讀寫分離:燈號(ResidentStatus.tsx)零 bridge 引用——狀態絕不從 8787 拿", async () => {
  const source = await readFile(join(root, "src", "ResidentStatus.tsx"), "utf8");
  // 註解文字允許提到 bridge(說明讀寫分離),但程式碼不得出現 bridge URL/端點
  assert.ok(!source.includes("127.0.0.1:8787"), "燈號取數不得指向 bridge(寫入面)");
  assert.ok(!source.includes("/api/service/"), "燈號不得引用第二群組操作端點");
});
