// jobs 管線新鮮度燈號的 UI 渲染層測試。
// 形態比照 repo-guard-render.test.mjs:rolldown bundle 純渲染元件 +
// react-dom/server,對「渲染輸出」做斷言(無瀏覽器)。
//
// 核心斷言:
//  - **五態各自可辨**(trigger_dead / executor_dead / executor_degraded /
//    inconclusive / healthy):燈 class 正確,且 state 字串本身出現在輸出裡
//    ——兩個「死」共用橙色,必須靠文字分辨得出是觸發端還是執行端。
//  - **inconclusive 不亮警示色**:灰,且輸出不得出現 orange/yellow 燈 class。
//    這是最容易做錯的一項——「還在跑」是正常狀態,不是警告。
//  - **不使用 red**:紅是常駐/服務層級「不可用」的既有語意,新鮮度不搶。
//  - fail-soft:unavailable 時灰燈 + 誠實原因,且明講「灰 ≠ 沒事」。
//  - **零操作入口**:卡片渲染輸出零 <button>、零 onclick(唯讀觀測面;
//    絕不從 UI 觸發任何 job 或告警)。
//  - 判準不在前端重算:元件不得出現門檻數字或 classify 邏輯的字面。
//  - CSS:燈色沿用既有 token(綠 #34d399／黃 #fbbf24／橙 #fb923c／灰 #6b7280),
//    無 !important。
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
    input: join(root, "tests", "fixtures", "jobs-freshness-render-entry.tsx"),
    platform: "node",
    logLevel: "silent",
  });
  const { output } = await bundle.generate({ format: "esm" });
  await bundle.close();
  tmp = await mkdtemp(join(tmpdir(), "jobs-freshness-render-"));
  const bundlePath = join(tmp, "jobs-freshness-render-entry.mjs");
  await writeFile(bundlePath, output[0].code);
  mod = await import(pathToFileURL(bundlePath).href);
});

test.after(async () => {
  if (tmp) await rm(tmp, { recursive: true, force: true });
});

// 各狀態的後端 row(欄位與 dashboard/data_jobs_freshness._source_row 一致)
const ROWS = {
  healthy: {
    source: "rss",
    description: "RSS adapter",
    state: "healthy",
    state_label: "健康",
    state_short: "健康",
    light: "green",
    alerting: false,
    reason: "window 內 enqueued=48、completed=46、dead_letter=2",
    expect_enqueue: true,
    lookback_hours: 24,
    enqueued: 48,
    completed: 46,
    dead_letter: 2,
    stuck: 0,
    last_completed_at: "2026-09-04T11:02:00+00:00",
    last_completed_age_hours: 1.0,
    last_completed_age_text: "1.0 小時前",
  },
  trigger_dead: {
    source: "cron",
    description: "cron adapter（daily-memory-check）",
    state: "trigger_dead",
    state_label: "觸發端死了（連 enqueue 都沒有）",
    state_short: "觸發端死了",
    light: "orange",
    alerting: true,
    reason: "window 內 enqueued=0 < 門檻 1——觸發器（timer／Task Scheduler）沒有進件",
    expect_enqueue: true,
    lookback_hours: 48,
    enqueued: 0,
    completed: 0,
    dead_letter: 0,
    stuck: 0,
    last_completed_at: "2026-08-04T08:03:00+00:00",
    last_completed_age_hours: 748.0,
    last_completed_age_text: "31.2 天前",
  },
  executor_dead: {
    source: "cron",
    description: null,
    state: "executor_dead",
    state_label: "執行端死了（有進件、零 completed）",
    state_short: "執行端死了",
    light: "orange",
    alerting: true,
    reason: "window 內 completed=0、dead_letter=2——有進件但全部失敗，執行端死了",
    expect_enqueue: true,
    lookback_hours: 48,
    enqueued: 2,
    completed: 0,
    dead_letter: 2,
    stuck: 0,
    last_completed_at: "2026-08-04T08:03:00+00:00",
    last_completed_age_hours: 748.0,
    last_completed_age_text: "31.2 天前",
  },
  executor_degraded: {
    source: "rss",
    description: null,
    state: "executor_degraded",
    state_label: "執行端退化（dead_letter 比例超標）",
    state_short: "執行端退化",
    light: "yellow",
    alerting: true,
    reason: "dead_letter 比例 6/10＝60% ≥ 門檻 50%",
    expect_enqueue: true,
    lookback_hours: 24,
    enqueued: 12,
    completed: 4,
    dead_letter: 6,
    stuck: 0,
    last_completed_at: "2026-09-04T09:00:00+00:00",
    last_completed_age_hours: 3.0,
    last_completed_age_text: "3.0 小時前",
  },
  inconclusive: {
    source: "telegram",
    description: null,
    state: "inconclusive",
    state_label: "進行中（無結論，不告警）",
    state_short: "進行中",
    light: "gray",
    alerting: false,
    reason: "window 內 enqueued=2，尚無終結結果、也沒有卡太久的 job——可能只是還在跑，不告警",
    expect_enqueue: false,
    lookback_hours: 48,
    enqueued: 2,
    completed: 0,
    dead_letter: 0,
    stuck: 0,
    last_completed_at: null,
    last_completed_age_hours: null,
    last_completed_age_text: "（從未成功過）",
  },
};

function payload(overrides = {}, rows) {
  return {
    checked_at: "2026-09-04T12:00:00+00:00",
    status: "ok",
    available: true,
    reason: null,
    note: "此區塊是即時計算（每次載入直接對 jobs.db 唯讀查詢）。本端點唯讀：不送任何通知、不寫任何東西、不觸發任何 job。",
    config_path: "C:/FAKE/registry/jobs_watchdog.yaml",
    jobs_db: "C:/FAKE/hermes/jobs.db",
    overall_light: "green",
    overall_text: "5 個 source 皆無異常",
    summary: "所有受監控 source 都沒有「跑都沒跑／全部失敗」的跡象。",
    thresholds: {
      lookback_hours: 48,
      min_expected_enqueued: 1,
      stuck_backlog_hours: 2,
      dead_letter_ratio_threshold: 0.5,
      min_terminal_sample: 4,
    },
    alerting_states: ["trigger_dead", "executor_dead", "executor_degraded"],
    sources: rows ?? [ROWS.healthy],
    ...overrides,
  };
}

test("渲染:healthy 帶綠燈,且不出現任何警示燈 class", () => {
  const html = mod.renderFreshness(payload());
  assert.ok(html.includes("fresh-light-green"), "healthy 須為綠燈");
  assert.ok(!html.includes("fresh-light-orange"));
  assert.ok(!html.includes("fresh-light-yellow"));
  assert.ok(html.includes("健康"));
});

test("渲染:trigger_dead 帶橙燈,並講明是「觸發端」而非執行端", () => {
  const html = mod.renderFreshness(
    payload({ overall_light: "orange", overall_text: "1／5 個 source 異常" }, [ROWS.trigger_dead]),
  );
  assert.ok(html.includes("fresh-light-orange"), "trigger_dead 須為橙燈");
  assert.ok(html.includes("觸發端死了"));
  assert.ok(html.includes("trigger_dead"), "state 字串本身要出現,兩個死態才分得開");
  assert.ok(html.includes("31.2 天前"), "須顯示最後一次成功有多久以前(嚴重程度)");
  assert.ok(!html.includes("executor_dead"));
});

test("渲染:executor_dead 帶橙燈,講明是「執行端」(2026-08 的形態)", () => {
  const html = mod.renderFreshness(
    payload({ overall_light: "orange", overall_text: "1／5 個 source 異常" }, [ROWS.executor_dead]),
  );
  assert.ok(html.includes("fresh-light-orange"), "executor_dead 須為橙燈");
  assert.ok(html.includes("執行端死了"));
  assert.ok(html.includes("executor_dead"));
  assert.ok(!html.includes("trigger_dead"), "兩個死態不得混為一談");
});

test("渲染:executor_degraded 帶黃燈(有 completed,只是比例超標)", () => {
  const html = mod.renderFreshness(
    payload({ overall_light: "yellow", overall_text: "1／5 個 source 異常" }, [
      ROWS.executor_degraded,
    ]),
  );
  assert.ok(html.includes("fresh-light-yellow"), "degraded 須為黃燈");
  assert.ok(html.includes("executor_degraded"));
  assert.ok(!html.includes("fresh-light-orange"), "退化不得升成橙(語意不同)");
});

test("渲染:inconclusive 是灰燈,**不得**亮任何警示色——「還在跑」是正常狀態", () => {
  const html = mod.renderFreshness(
    payload({ overall_light: "gray", overall_text: "1 個 source 皆無異常" }, [ROWS.inconclusive]),
  );
  assert.ok(html.includes("fresh-light-gray"), "inconclusive 須為灰燈");
  assert.ok(!html.includes("fresh-light-orange"), "進行中不得亮橙");
  assert.ok(!html.includes("fresh-light-yellow"), "進行中不得亮黃");
  assert.ok(html.includes("進行中"));
  assert.ok(html.includes("不告警"), "須明說這個狀態不會觸發告警");
});

test("渲染:五態同時出現時各自帶對的燈,整體燈取最嚴重", () => {
  const html = mod.renderFreshness(
    payload({ overall_light: "orange", overall_text: "3／5 個 source 異常" }, [
      ROWS.healthy,
      ROWS.trigger_dead,
      ROWS.executor_dead,
      ROWS.executor_degraded,
      ROWS.inconclusive,
    ]),
  );
  for (const cls of ["fresh-light-green", "fresh-light-yellow", "fresh-light-orange", "fresh-light-gray"]) {
    assert.ok(html.includes(cls), `五態並存時須出現 ${cls}`);
  }
  for (const state of Object.keys(ROWS)) {
    assert.ok(html.includes(state), `state ${state} 須可辨識`);
  }
  assert.ok(html.includes("3／5 個 source 異常"));
});

test("渲染:任何情境都不得使用 red(紅屬常駐/服務層級的既有語意)", () => {
  for (const key of Object.keys(ROWS)) {
    const html = mod.renderFreshness(payload({ overall_light: ROWS[key].light }, [ROWS[key]]));
    assert.ok(!html.includes("fresh-light-red"), `${key} 不得出現 red 燈 class`);
    assert.ok(!html.includes("#f87171"), `${key} 不得使用常駐燈的紅色值`);
    assert.ok(!html.includes("update-light-"), "不得沿用預檢的燈 class(語意不同層)");
    assert.ok(!html.includes("guard-light-"), "不得沿用離線保險的燈 class");
  }
});

test("渲染:unavailable 時灰燈 + 誠實原因,且明講「灰 ≠ 沒事」", () => {
  const html = mod.renderFreshness(
    payload(
      {
        status: "unavailable",
        available: false,
        reason: "jobs.db 不存在：C:/FAKE/hermes/jobs.db",
        overall_light: "gray",
        overall_text: "無法判斷",
        summary: "無法評估 jobs 管線新鮮度：jobs.db 不存在（灰燈代表「無法判斷」，不代表沒事）",
        thresholds: null,
      },
      [],
    ),
  );
  assert.ok(html.includes("fresh-light-gray"));
  assert.ok(html.includes("無法判斷"));
  assert.ok(html.includes("不代表沒事"), "灰燈語意必須講白,不得被讀成『沒事』");
  assert.ok(html.includes("jobs.db 不存在"), "原因要說出來,不留白");
});

test("渲染:後端未回資料時優雅退化,不炸開", () => {
  for (const value of [null, undefined]) {
    const html = mod.renderFreshness(value);
    assert.ok(html.length > 0);
    assert.ok(html.includes("未取得"), "須給明確說明而非空白");
  }
});

test("渲染:零操作入口——無 <button>、無 onclick、無 <form>", () => {
  const html = mod.renderFreshness(
    payload({ overall_light: "orange" }, [ROWS.trigger_dead, ROWS.executor_dead]),
  );
  assert.ok(!html.includes("<button"), "唯讀觀測面卡片不得有按鈕");
  assert.ok(!html.includes("onclick"), "不得有任何 DOM 事件處理器");
  assert.ok(!html.includes("<form"), "不得有表單");
});

test("渲染:門檻來自後端 payload,前端不硬編數字", () => {
  const html = mod.renderFreshness(
    payload({
      thresholds: {
        lookback_hours: 72,
        min_expected_enqueued: 9,
        stuck_backlog_hours: 5,
        dead_letter_ratio_threshold: 0.25,
        min_terminal_sample: 7,
      },
    }),
  );
  assert.ok(html.includes("72h"), "window 門檻須跟著 payload 走");
  assert.ok(html.includes("9 筆"));
  assert.ok(html.includes("25%"));
});

test("原始碼靜態鎖定:前端不重算判準、不硬編門檻、不直連 fetch", async () => {
  const src = await readFile(join(root, "src", "views", "JobsFreshness.tsx"), "utf8");
  assert.ok(!src.includes("fetch("), "取數必須走唯讀 apiGet");
  const apiGets = src.match(/apiGet</g) ?? [];
  assert.equal(apiGets.length, 1, "整個 view 只能有一處 apiGet");
  assert.ok(src.includes('"/api/jobs-freshness"'), "取數 URL 為單一凍結字面");
  // 判準/門檻不得在前端重寫:不得出現比較 dead_letter 比例或 enqueued 門檻的邏輯
  for (const f of ["dead_letter /", "enqueued <", "enqueued >", "classify", "0.5"]) {
    assert.ok(!src.includes(f), `前端不得自帶判準/門檻字面:${f}`);
  }
});

test("CSS:燈色沿用既有 token,且整段無 !important", async () => {
  const css = await readFile(join(root, "src", "globals.css"), "utf8");
  // 只切出 fresh 這一段:切到檔尾會撈到本段以外既有規則的 !important(假陽性)。
  const start = css.indexOf(".fresh-panel");
  const endMarker = ".fresh-source-last";
  const end = css.indexOf("}", css.indexOf(endMarker, start)) + 1;
  assert.ok(start >= 0 && end > start, "測試前提:找得到 fresh 樣式區塊");
  const block = css.slice(start, end);
  assert.ok(block.includes(".fresh-light-green { border-left-color: #34d399; }"),
    "綠須沿用 .update-light-green / .guard-light-green 同一顆");
  assert.ok(block.includes(".fresh-light-yellow { border-left-color: #fbbf24; }"),
    "黃須沿用 .guard-light-yellow 同一顆");
  assert.ok(block.includes(".fresh-light-orange { border-left-color: #fb923c; }"),
    "橙須沿用 .update-light-orange 同一顆(不新增色彩系統)");
  assert.ok(block.includes(".fresh-light-gray { border-left-color: #6b7280; }"));
  assert.ok(!block.includes("!important"), "排版層慣例:不得使用 !important");
  assert.ok(!block.includes("#f87171"), "不得引入常駐燈的紅");
});

test("掛載點:總覽頁與 Jobs 頁都掛同一個元件(共用判準,不各做一份)", async () => {
  for (const view of ["Overview.tsx", "Jobs.tsx"]) {
    const src = await readFile(join(root, "src", "views", view), "utf8");
    assert.ok(src.includes("JobsFreshnessPanel"), `${view} 須掛上新鮮度面板`);
    assert.ok(src.includes('from "./JobsFreshness"'), `${view} 須 import 同一個元件`);
  }
});
