// 未推送 commit 的離線保險快照(批次 1 止血)的 UI 渲染層測試。
// 形態比照 update-precheck-render.test.mjs:rolldown bundle 純渲染元件 +
// react-dom/server,對「渲染輸出」做斷言(無瀏覽器)。
//
// 核心斷言:
//  - 三態(fresh 綠／stale 黃／never 灰)各自帶正確 class 與可辨識文字。
//  - **不搶橙燈**:任何情境的渲染輸出都不得出現 orange/red 燈 class——
//    橙專屬升級預檢的「未 push(ahead>0)」,兩層語意不得互相打架。
//  - **零操作入口**:面板渲染輸出零 <button>、零 onclick(唯讀觀測面;
//    使用者刻意沒建排程,重跑 guard 一律手動,不從 UI 觸發)。
//  - 誠實呈現資料新舊:必定顯示「最近一次保險」的年齡文字;never 態要講明
//    是「從未執行」,不得留白或假裝現況。
//  - CSS:燈色沿用既有 token(綠 #34d399／黃 #fbbf24／灰 #6b7280),無 !important。
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
    input: join(root, "tests", "fixtures", "update-precheck-render-entry.tsx"),
    platform: "node",
    logLevel: "silent",
  });
  const { output } = await bundle.generate({ format: "esm" });
  await bundle.close();
  tmp = await mkdtemp(join(tmpdir(), "repo-guard-render-"));
  const bundlePath = join(tmp, "repo-guard-render-entry.mjs");
  await writeFile(bundlePath, output[0].code);
  mod = await import(pathToFileURL(bundlePath).href);
});

test.after(async () => {
  if (tmp) await rm(tmp, { recursive: true, force: true });
});

function guardTarget(overrides = {}) {
  return {
    id: "hermes-agent",
    label: "Hermes Agent（Install 鈕會 reset --hard 的那個 repo）",
    status: "fresh",
    light: "green",
    light_text: "保險新鮮",
    summary: "2.0 小時前保全了 3 個 commit，bundle 可還原。（此為快照，不代表目前暴露狀態）",
    created_at: "2026-09-03T20:02:48",
    age_hours: 2,
    age_text: "2.0 小時前",
    bundle: "C:/FAKE/store/hermes-agent-20260903-200247.bundle",
    bundle_bytes: 3232833,
    covered_commits: 3,
    covered_refs: ["refs/stash (+3)"],
    dirty_files: 0,
    ...overrides,
  };
}

function guard(overrides = {}, targets) {
  return {
    checked_at: "2026-09-03T12:07:23+00:00",
    store_root: "C:/FAKE/store",
    fresh_hours: 24,
    scheduled: false,
    note: "本卡片顯示的是「最近一次離線保險快照」，不是目前的未推送狀態——本端點唯讀，不會觸發 guard 執行。",
    overall_light: "green",
    targets: targets ?? [guardTarget()],
    ...overrides,
  };
}

test("渲染:fresh 態帶綠燈 class 與年齡/保全數", () => {
  const html = mod.renderGuard(guard());
  assert.ok(html.includes("guard-light-green"), "fresh 須為綠燈");
  assert.ok(html.includes("2.0 小時前"), "須顯示最近一次保險的年齡");
  assert.ok(html.includes("保險新鮮"));
  assert.ok(html.includes("refs/stash (+3)"), "須列出涵蓋的 ref");
  assert.ok(html.includes("3.1 MB"), `bundle 大小須人類可讀,實得:${html.slice(0, 0)}`);
});

test("渲染:stale 態帶黃燈,並顯示過期語意(不拿舊資料假裝現況)", () => {
  const t = guardTarget({
    status: "stale",
    light: "yellow",
    light_text: "快照可能過期",
    age_hours: 216,
    age_text: "9.0 天前",
    summary: "最近一次保險是 9.0 天前（保全了 3 個 commit）。之後新增的 commit 未必在保險內",
  });
  const html = mod.renderGuard(guard({ overall_light: "yellow" }, [t]));
  assert.ok(html.includes("guard-light-yellow"), "stale 須為黃燈");
  assert.ok(html.includes("9.0 天前"));
  assert.ok(html.includes("未必在保險內"), "必須誠實講出快照可能不涵蓋新 commit");
  assert.ok(!html.includes("guard-light-green"));
});

test("渲染:never 態帶灰燈,講明從未執行且不留白", () => {
  const t = guardTarget({
    status: "never",
    light: "gray",
    light_text: "從未執行",
    summary: "沒有找到保險快照——若此 repo 目前有未推送的 commit，被 reset --hard 吃掉就救不回來。",
    created_at: null,
    age_hours: null,
    age_text: null,
    bundle: null,
    bundle_bytes: null,
    covered_commits: null,
    covered_refs: [],
  });
  const html = mod.renderGuard(guard({ overall_light: "gray" }, [t]));
  assert.ok(html.includes("guard-light-gray"), "never 須為灰燈");
  assert.ok(html.includes("從未執行"));
  assert.ok(html.includes("救不回來"), "從未執行的風險要講白");
  assert.ok(html.includes("—"), "未知數值以破折號呈現,不留白、不臆測");
});

test("渲染:三態都不得出現 orange/red 燈(橙保留給預檢的『未 push』)", () => {
  for (const light of ["green", "yellow", "gray"]) {
    const html = mod.renderGuard(guard({ overall_light: light }, [guardTarget({ light })]));
    assert.ok(!html.includes("orange"), `${light} 態渲染輸出不得含 orange`);
    assert.ok(!html.includes("#fb923c"), `${light} 態不得用預檢的橙色值`);
    assert.ok(!html.includes("update-light-"), "不得沿用預檢的燈 class(語意不同層)");
  }
});

test("渲染:零操作入口——無 <button>、無 onclick(重跑 guard 不從 UI 觸發)", () => {
  const html = mod.renderGuard(guard());
  assert.ok(!html.includes("<button"), "唯讀觀測面不得有按鈕");
  assert.ok(!html.includes("onclick"), "不得有任何 DOM 事件處理器");
  assert.ok(!html.includes("<form"), "不得有表單");
});

test("渲染:無排程時誠實標示(拍板不建 Task Scheduler)", () => {
  const html = mod.renderGuard(guard());
  assert.ok(html.includes("無排程"), "沒排程就要標示,否則會誤以為資料會自己更新");
  assert.ok(html.includes("不會觸發 guard 執行"), "須說明本頁唯讀不觸發執行");
});

test("渲染:後端未提供 repo_guard 時優雅退化,不炸開", () => {
  for (const value of [null, undefined]) {
    const html = mod.renderGuard(value);
    assert.ok(html.length > 0);
    assert.ok(html.includes("未取得"), "須給明確說明而非空白");
  }
});

test("CSS:燈色沿用既有 token,且整段無 !important", async () => {
  const css = await readFile(join(root, "src", "globals.css"), "utf8");
  // 只切出 guard 這一段:切到檔尾會撈到本段以外既有規則的 !important(假陽性)。
  const start = css.indexOf(".guard-panel");
  const endMarker = ".guard-target-refs";
  const end = css.indexOf("}", css.indexOf(endMarker, start)) + 1;
  assert.ok(start >= 0 && end > start, "測試前提:找得到 guard 樣式區塊");
  const block = css.slice(start, end);
  assert.ok(block.includes(".guard-light-green { border-left-color: #34d399; }"),
    "綠須沿用 .update-light-green 同一顆");
  assert.ok(block.includes(".guard-light-yellow { border-left-color: #fbbf24; }"),
    "黃須沿用既有 .cred-consistency-yellow / .service-note-converging 同一顆");
  assert.ok(block.includes(".guard-light-gray { border-left-color: #6b7280; }"));
  assert.ok(!block.includes("!important"), "排版層慣例:不得使用 !important");
  assert.ok(!block.includes("#fb923c"), "guard 樣式不得引入預檢的橙");
});

test("原始碼靜態鎖定:guard 面板不新增取數路徑(資料隨 precheck payload 來)", async () => {
  const src = await readFile(join(root, "src", "views", "UpdatePrecheck.tsx"), "utf8");
  const apiGets = src.match(/apiGet</g) ?? [];
  assert.equal(apiGets.length, 1, "整個 view 只能有一處 apiGet(不得為 guard 另開取數)");
  assert.ok(!src.includes("/api/repo-guard"), "不得另開 repo-guard 端點的取數 URL");
  assert.ok(!src.includes("fetch("), "不得直連 fetch");
});
