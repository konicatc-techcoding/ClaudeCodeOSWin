// jobs「資料年齡」呈現的渲染層測試(2026-09-04 拓撲修正)。
//
// 這一組測試守的是**一條規則**:Windows 觀測面讀的是 WSL 推來的 jobs.db 快照,
// 所以 Jobs 頁/成本頁/新鮮度卡片**都不准讓使用者以為看到的是當下狀態**。
// 具體斷言:
//  - 六種資料狀態(live/fresh/stale/expired/never/error)各自可辨,且
//    **年齡文字一定出現在輸出裡**(expired/never/error 也要,不能只在正常時顯示)。
//  - 後端沒給 data_* 欄位時,退化成「資料年齡:未知」,**絕不預設成即時**。
//  - 快照過期時,新鮮度卡片整體轉灰,而且**當時看到的異常仍寫在文字裡**
//    (轉灰不等於閉嘴)。
//  - 快照偏舊時,綠燈已由後端降成黃——前端照著渲染,不自行還原成綠。
//  - 零操作入口(唯讀觀測面):無 button/onclick/form。
//  - 三個吃 jobs.db 的頁面(Jobs/成本/新鮮度卡片)都掛了橫幅——靜態檢查。
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
    input: join(root, "tests", "fixtures", "jobs-data-age-entry.tsx"),
    platform: "node",
    logLevel: "silent",
  });
  const { output } = await bundle.generate({ format: "esm" });
  await bundle.close();
  tmp = await mkdtemp(join(tmpdir(), "jobs-data-age-render-"));
  const bundlePath = join(tmp, "jobs-data-age-entry.mjs");
  await writeFile(bundlePath, output[0].code);
  mod = await import(pathToFileURL(bundlePath).href);
});

test.after(async () => {
  if (tmp) await rm(tmp, { recursive: true, force: true });
});

// 欄位與 dashboard/data_jobs_snapshot.resolve_jobs_source() 一致。
function age(overrides = {}) {
  return {
    data_source: "snapshot",
    data_status: "fresh",
    data_captured_at: "2026-09-04T11:40:00+00:00",
    data_age_hours: 0.33,
    data_age_text: "20 分鐘前",
    data_age_label: "20 分鐘前的快照",
    data_trusted: true,
    data_summary: "資料為 20 分鐘前拍攝的 jobs.db 快照（1.5 小時內視為新鮮）。",
    data_note: "Windows 觀測面讀的是 WSL 定期推來的 jobs.db 快照，不是 runtime db。",
    data_fresh_hours: 1.5,
    data_expire_hours: 6,
    data_snapshot_dir: "C:/FAKE/AppData/Local/AgentOS/jobs-snapshot",
    data_jobs_count: 2461,
    ...overrides,
  };
}

test("橫幅:runtime(live)標成即時,且不謊稱有快照", () => {
  const html = mod.renderDataAge(
    age({ data_source: "runtime", data_status: "live", data_age_text: "即時",
          data_captured_at: null, data_summary: "直接讀本機 runtime jobs.db，資料即時。" }),
  );
  assert.ok(html.includes("即時（runtime db）"));
  assert.ok(html.includes("data-age-green"));
});

test("橫幅:fresh 顯示綠 + 年齡文字", () => {
  const html = mod.renderDataAge(age());
  assert.ok(html.includes("data-age-green"));
  assert.ok(html.includes("快照．新鮮"));
  assert.ok(html.includes("20 分鐘前"), "年齡文字必須看得到");
  assert.ok(html.includes("2026-09-04T11:40:00+00:00"), "快照時間戳要可核對");
});

test("橫幅:stale 轉黃並說明「不能只靠它下結論」", () => {
  const html = mod.renderDataAge(
    age({
      data_status: "stale", data_trusted: false, data_age_text: "3.0 小時前",
      data_summary:
        "資料為 3.0 小時前拍攝的快照，已超過 1.5 小時——之後發生的事不在裡面；「一切正常」這種結論不能只靠它下。",
    }),
  );
  assert.ok(html.includes("data-age-yellow"));
  assert.ok(html.includes("快照．偏舊"));
  assert.ok(html.includes("3.0 小時前"));
  assert.ok(html.includes("不能只靠它下"));
});

test("橫幅:expired/never/error 皆為灰,且都講得出「多舊/為什麼沒有」", () => {
  const expired = mod.renderDataAge(
    age({ data_status: "expired", data_trusted: false, data_age_text: "1.4 天前",
          data_summary: "資料為 1.4 天前拍攝的快照，已超過 6 小時——快照產出本身可能也停了。" }),
  );
  assert.ok(expired.includes("data-age-gray"));
  assert.ok(expired.includes("快照．已過期"));
  assert.ok(expired.includes("1.4 天前"));

  const never = mod.renderDataAge(
    age({ data_source: "missing", data_status: "never", data_captured_at: null,
          data_age_text: null, data_trusted: false,
          data_summary: "找不到任何 jobs.db 快照——Windows 觀測面沒有資料可看。" }),
  );
  assert.ok(never.includes("data-age-gray"));
  assert.ok(never.includes("沒有快照"));
  assert.ok(never.includes("找不到"));

  const broken = mod.renderDataAge(
    age({ data_status: "error", data_trusted: false,
          data_summary: "快照讀取失敗（可能損毀）（快照拍攝於 12 分鐘前）——資料不可用。" }),
  );
  assert.ok(broken.includes("data-age-gray"));
  assert.ok(broken.includes("快照不可用"));
  assert.ok(broken.includes("資料不可用"));
});

test("橫幅:後端沒給資料年齡時,顯示「未知」而**不是**預設成即時", () => {
  for (const value of [null, undefined, {}]) {
    const html = mod.renderDataAge(value);
    assert.ok(html.includes("資料年齡：未知"), "缺欄位時必須明說未知");
    assert.ok(html.includes("不要假設這是即時資料"));
    assert.ok(!html.includes("即時（runtime db）"), "絕不可以退化成『即時』");
  }
});

test("橫幅:零操作入口(唯讀觀測面)", () => {
  const html = mod.renderDataAge(age({ data_status: "expired" }));
  assert.ok(!html.includes("<button"));
  assert.ok(!html.includes("onclick"));
  assert.ok(!html.includes("<form"));
});

// --- 新鮮度卡片 × 資料年齡 ---

function freshnessPayload(dataAge, overrides = {}, rows) {
  return {
    checked_at: "2026-09-04T12:00:00+00:00",
    status: "ok",
    available: true,
    reason: null,
    note: "判準與門檻與 Slack 看門狗共用同一份 registry/jobs_watchdog.yaml。",
    config_path: "C:/FAKE/registry/jobs_watchdog.yaml",
    jobs_db: "C:/FAKE/AppData/Local/AgentOS/jobs-snapshot/jobs.snapshot.db",
    overall_light: "green",
    overall_text: "1 個 source 皆無異常",
    summary: "所有受監控 source 都沒有「跑都沒跑／全部失敗」的跡象。",
    thresholds: {
      lookback_hours: 48,
      min_expected_enqueued: 1,
      stuck_backlog_hours: 2,
      dead_letter_ratio_threshold: 0.5,
      min_terminal_sample: 4,
    },
    alerting_states: ["trigger_dead", "executor_dead", "executor_degraded"],
    sources: rows ?? [
      {
        source: "rss",
        description: "RSS adapter",
        state: "healthy",
        state_label: "健康",
        state_short: "健康",
        light: "green",
        light_before_data_age: null,
        data_stale: false,
        alerting: false,
        reason: "window 內 enqueued=48、completed=46、dead_letter=2",
        expect_enqueue: true,
        lookback_hours: 24,
        enqueued: 48,
        completed: 46,
        dead_letter: 2,
        stuck: 0,
        last_completed_at: "2026-09-04T11:51:00+00:00",
        last_completed_age_hours: 0.15,
        last_completed_age_text: "9 分鐘前",
      },
    ],
    ...dataAge,
    ...overrides,
  };
}

test("卡片:新鮮快照——綠燈照舊,但資料年齡仍必須出現", () => {
  const html = mod.renderFreshness(freshnessPayload(age()));
  assert.ok(html.includes("fresh-light-green"));
  assert.ok(html.includes("快照．新鮮"));
  assert.ok(html.includes("20 分鐘前"), "「9 分鐘前成功」旁邊必須看得到資料本身多舊");
});

test("卡片:偏舊快照——後端把綠降黃,前端照渲染(不自行還原成綠)", () => {
  const html = mod.renderFreshness(
    freshnessPayload(
      age({ data_status: "stale", data_trusted: false, data_age_text: "3.0 小時前" }),
      { overall_light: "yellow" },
      [
        {
          source: "rss",
          description: null,
          state: "healthy",
          state_label: "健康",
          state_short: "健康（資料偏舊，僅供參考）",
          light: "yellow",
          light_before_data_age: "green",
          data_stale: true,
          alerting: false,
          reason: "window 內 enqueued=48、completed=46、dead_letter=2",
          expect_enqueue: true,
          lookback_hours: 24,
          enqueued: 48,
          completed: 46,
          dead_letter: 2,
          stuck: 0,
          last_completed_at: "2026-09-04T08:51:00+00:00",
          last_completed_age_text: "3.2 小時前",
          last_completed_age_hours: 3.2,
        },
      ],
    ),
  );
  assert.ok(html.includes("fresh-light-yellow"), "偏舊資料不得呈現綠燈");
  assert.ok(!html.includes("fresh-light-green"));
  assert.ok(html.includes("僅供參考"));
  assert.ok(html.includes("快照．偏舊"));
});

test("卡片:過期快照——整體轉灰,但當時看到的異常仍寫在文字裡(轉灰≠閉嘴)", () => {
  const html = mod.renderFreshness(
    freshnessPayload(
      age({ data_status: "expired", data_trusted: false, data_age_text: "1.4 天前" }),
      {
        overall_light: "gray",
        overall_text: "資料過期，無法判斷",
        summary:
          "資料為 1.4 天前拍攝的快照，已超過 6 小時。⚠️ 資料已過期，**整體轉灰、不下任何結論**。快照當時看到的狀態：cron：執行端死了。",
      },
      [
        {
          source: "cron",
          description: null,
          state: "executor_dead",
          state_label: "執行端死了（有進件、零 completed）",
          state_short: "執行端死了",
          light: "gray",
          light_before_data_age: "orange",
          data_stale: true,
          alerting: true,
          reason: "window 內 completed=0、dead_letter=2",
          expect_enqueue: true,
          lookback_hours: 48,
          enqueued: 2,
          completed: 0,
          dead_letter: 2,
          stuck: 0,
          last_completed_at: null,
          last_completed_age_text: "（從未成功過）",
          last_completed_age_hours: null,
        },
      ],
    ),
  );
  assert.ok(html.includes("fresh-light-gray"), "過期資料不得產生彩色結論");
  assert.ok(!html.includes("fresh-light-orange"));
  assert.ok(html.includes("資料過期，無法判斷"));
  assert.ok(html.includes("執行端死了"), "轉灰不代表把壞消息藏起來");
  assert.ok(html.includes("1.4 天前"));
});

test("靜態:三個吃 jobs.db 的頁面都掛了資料年齡橫幅", async () => {
  const freshness = await readFile(join(root, "src", "views", "JobsFreshness.tsx"), "utf8");
  assert.ok(freshness.includes("DataAgeBanner"), "新鮮度卡片必須顯示資料年齡");
  for (const view of ["Jobs.tsx", "Cost.tsx"]) {
    const src = await readFile(join(root, "src", "views", view), "utf8");
    assert.ok(src.includes("JobsDataAgePanel"), `${view} 必須顯示資料年齡`);
  }
});

test("靜態:橫幅元件唯讀且只用凍結的唯讀端點", async () => {
  const src = await readFile(join(root, "src", "views", "JobsDataAge.tsx"), "utf8");
  assert.ok(!src.includes("fetch("), "取數必須走唯讀 apiGet");
  assert.equal((src.match(/apiGet</g) ?? []).length, 1, "只能有一處 apiGet");
  assert.ok(src.includes('"/api/jobs-freshness"'), "取數 URL 為單一凍結字面");
  assert.ok(!src.includes("<button"), "唯讀橫幅不得有按鈕");
  assert.ok(!src.includes("onClick"), "不得有任何事件處理器");
  // 門檻不得在前端重算(1.5/6 只能來自 payload 文字)
  for (const literal of ["fresh_hours =", "expire_hours =", "age_hours >"]) {
    assert.ok(!src.includes(literal), `前端不得自行判定資料年齡:${literal}`);
  }
});

test("CSS:橫幅沿用既有燈色 token,無 !important、無紅", async () => {
  const css = await readFile(join(root, "src", "globals.css"), "utf8");
  const start = css.indexOf(".data-age {");
  const end = css.indexOf("}", css.indexOf(".data-age-gray", start)) + 1;
  assert.ok(start >= 0 && end > start, "測試前提:找得到 data-age 樣式區塊");
  const block = css.slice(start, end);
  assert.ok(block.includes(".data-age-green { border-left-color: #34d399; }"));
  assert.ok(block.includes(".data-age-yellow { border-left-color: #fbbf24; }"));
  assert.ok(block.includes(".data-age-gray { border-left-color: #6b7280; }"));
  assert.ok(!block.includes("!important"));
  assert.ok(!block.includes("#f87171"), "不得引入常駐燈的紅");
});
