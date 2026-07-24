// Hermes 更新——唯讀升級預檢的 UI 渲染層測試(docs/webui-update-button-proposal.md §3)。
// 形態比照 resident-render.test.mjs:rolldown bundle 純渲染元件 + react-dom/server。
// 核心斷言:
//  - **雙基準並列**:每端同時渲染 backup(私有備份/防重演)與 upstream(官方上游)
//    兩組的落後/領先/ff 與說明;peer 標「僅供參考」;整體燈標示 overall_driver。
//    關鍵回歸:origin 0/0 但官方落後 16 時,卡片不得呈現為綠。
//  - 五態各自呈現(燈色 class + 狀態文字)。
//  - **零執行入口**:渲染輸出零 <button>(執行/升級/同步鈕未核准;唯讀先行)。
//  - 原始碼靜態鎖定:取數僅 apiGet、無直連 fetch、view 內唯一 onClick 是 reload。
import assert from "node:assert/strict";
import test from "node:test";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { pathToFileURL, fileURLToPath } from "node:url";
import { rolldown } from "rolldown";
import { computeStyle } from "./helpers/css-cascade.mjs";

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
  tmp = await mkdtemp(join(tmpdir(), "update-precheck-render-"));
  const bundlePath = join(tmp, "update-precheck-render-entry.mjs");
  await writeFile(bundlePath, output[0].code);
  mod = await import(pathToFileURL(bundlePath).href);
});

test.after(async () => {
  if (tmp) await rm(tmp, { recursive: true, force: true });
});

function comparison(overrides) {
  return {
    remote: "origin",
    url: "https://github.com/konicatc-techcoding/hermes-agent-private.git",
    role: "backup",
    role_label: "私有備份/防重演基準(本機與雲端是否同步)",
    ref: "origin/main",
    tip: "970118870",
    applicable: true,
    counts_toward_overall: true,
    behind: 0,
    ahead: 0,
    can_ff: true,
    diverged: false,
    diverge_commits: [],
    light: "green",
    light_text: "已是最新",
    summary: "備份同步 ✓(防重演基準有效)",
    ...overrides,
  };
}

const UPSTREAM_ORANGE = comparison({
  remote: "upstream",
  url: "https://github.com/NousResearch/hermes-agent.git",
  role: "upstream",
  role_label: "官方上游(有無新版可吸收)",
  ref: "upstream/main",
  tip: "46c7a4076",
  behind: 16,
  ahead: 12,
  can_ff: false,
  diverged: true,
  diverge_commits: ["970118870 fix(merge): slack ledger", "11f698fce merge(v0.19.0)"],
  light: "orange",
  light_text: "帶客製 diverge——需人工受控 merge",
  summary: "官方有 16 個新 commit,因帶 12 個客製屬 diverged,需受控 merge(不可自動)",
});

const PEER = comparison({
  remote: "windows-side",
  url: "/mnt/c/Users/razer/AppData/Local/hermes/hermes-agent",
  role: "peer",
  role_label: "其他基準(僅供參考)",
  ref: "windows-side/main",
  counts_toward_overall: false,
  summary: "與此基準一致",
});

function windowsTarget(overrides) {
  return {
    id: "windows",
    label: "Windows Hermes (live gateway)",
    repo: "C:/Users/x/AppData/Local/hermes/hermes-agent",
    light: "orange",
    light_text: "帶客製 diverge——需人工受控 merge",
    advice: "備份同步 ✓(防重演基準有效)；官方有 16 個新 commit,因帶 12 個客製屬 diverged,需受控 merge(不可自動)",
    blocking_reasons: [],
    overall_driver: "upstream",
    queryable: true,
    head: { short: "970118870", describe: "rescue/pre-remerge-20260724-14-g970118870", branch: "main" },
    dirty: false,
    remotes: ["origin", "upstream"],
    comparisons: [comparison({}), UPSTREAM_ORANGE],
    rescue_refs: [
      { name: "rescue/pre-updater-merge-20260724", object: "c12c64f9e", type: "commit" },
      { name: "rescue/pre-remerge-20260724", object: "df1464ef9", type: "commit" },
    ],
    rescue_count: 2,
    expect_custom: true,
    expect_rescue: true,
    service: { kind: "gateway_state.json", detail: "gateway 就緒", ready: true, state: "running" },
    ...overrides,
  };
}

function wslTarget(overrides) {
  return {
    id: "wsl",
    label: "WSL Hermes (Ubuntu)",
    repo: "/home/x/.hermes/hermes-agent",
    light: "blue",
    light_text: "可 ff-only 前進",
    advice: "官方有 5 個新 commit,無客製分歧,結構上可 ff-only 前進",
    blocking_reasons: [],
    overall_driver: "upstream",
    queryable: true,
    head: { short: "c12c64f9e", describe: "pre-upstream-merge", branch: "main" },
    dirty: false,
    remotes: ["origin", "windows-side"],
    comparisons: [
      comparison({
        remote: "origin",
        url: "https://github.com/NousResearch/hermes-agent.git",
        role: "upstream",
        role_label: "官方上游(有無新版可吸收)",
        ref: "origin/main",
        behind: 5,
        ahead: 0,
        can_ff: true,
        light: "blue",
        light_text: "可 ff-only 前進",
        summary: "官方有 5 個新 commit,無客製分歧,結構上可 ff-only 前進",
      }),
      PEER,
    ],
    rescue_refs: [],
    rescue_count: 0,
    expect_custom: false,
    expect_rescue: false,
    service: { kind: "systemd --user", detail: "常駐單元 active:2/2" },
    ...overrides,
  };
}

function payload(targets) {
  return {
    checked_at: "2026-07-24T00:00:00+00:00",
    remote_note: "遠端資訊只讀本地已有 refs(<remote>/main),可能過期——階段一預檢未執行 fetch",
    stage: "唯讀升級預檢(階段一);寫入/執行功能未核准,無任何執行鈕",
    targets,
  };
}

// ---------------------------------------------------------------------------
// 雙基準並列
// ---------------------------------------------------------------------------

test("雙基準:同一張卡同時呈現備份組與官方組的落後/領先與說明", () => {
  const html = mod.renderCard(windowsTarget({}));
  // 備份組(origin,0/0,綠)
  assert.ok(html.includes("私有備份/防重演基準"), "須呈現備份基準組");
  assert.ok(html.includes("origin/main"), "須顯示備份 ref");
  assert.ok(html.includes("備份同步"), "須顯示備份同步說明");
  // 官方組(upstream,落後 16 / 領先 12,橙)
  assert.ok(html.includes("官方上游"), "須呈現官方基準組");
  assert.ok(html.includes("upstream/main"), "須顯示官方 ref");
  assert.ok(html.includes("官方有 16 個新 commit"), "須顯示官方落後數與 diverged 說明");
  assert.ok(html.includes("不可自動"), "官方 diverged 須明示不可自動");
  // 兩組各有自己的燈
  assert.ok(html.includes("update-light-green"), "備份組須為綠");
  assert.ok(html.includes("update-light-orange"), "官方組須為橙");
});

test("關鍵回歸:origin 0/0 但官方落後 16 → 卡片整體不得呈現為綠", () => {
  const html = mod.renderCard(windowsTarget({}));
  assert.ok(html.includes('class="update-card update-light-orange"'),
    "整體卡片須為橙(不得被 origin 0/0 誤導成綠)");
  assert.ok(html.includes("主導整體燈"), "須標示是哪一組主導整體燈");
});

test("overall_driver 標示:僅主導組帶標記", () => {
  const html = mod.renderCard(windowsTarget({}));
  const markers = html.match(/主導整體燈/g) ?? [];
  assert.equal(markers.length, 1, "恰有一組被標為主導");
});

test("peer 組標示為僅供參考(不計入整體燈)", () => {
  const html = mod.renderCard(wslTarget({}));
  assert.ok(html.includes("僅供參考"), "peer 組須標示僅供參考");
  assert.ok(html.includes("windows-side/main"), "須顯示 peer ref");
});

test("WSL 的 origin 指向官方時,以「官方上游」角色呈現(不依 remote 名稱)", () => {
  const html = mod.renderCard(wslTarget({}));
  assert.ok(html.includes("官方上游"), "WSL 的 origin 須以官方上游角色呈現");
  assert.ok(html.includes("官方有 5 個新 commit"), "須顯示可吸收的官方新 commit 數");
});

test("基準組不適用(ref 不存在)時誠實標示,不偽造數字", () => {
  const html = mod.renderCard(
    windowsTarget({
      light: "gray",
      light_text: "無法查詢",
      overall_driver: "upstream",
      comparisons: [
        comparison({}),
        comparison({
          remote: "upstream", role: "upstream", role_label: "官方上游(有無新版可吸收)",
          ref: "upstream/main", tip: null, applicable: false, behind: null, ahead: null,
          can_ff: null, diverged: null, light: "gray", light_text: "無法查詢",
          summary: "不適用/無法查詢:本地無 upstream/main ref(未 fetch 過此 remote)",
        }),
      ],
    }),
  );
  assert.ok(html.includes("不適用/無法查詢"), "不適用組須誠實標示");
  assert.ok(html.includes("未 fetch 過此 remote"), "須說明原因");
  assert.ok(html.includes("—"), "缺數字以 — 呈現,不偽造 0");
});

// ---------------------------------------------------------------------------
// 五態
// ---------------------------------------------------------------------------

const FIVE_STATES = [
  ["green", "已是最新"],
  ["blue", "可 ff-only 前進"],
  ["orange", "帶客製 diverge——需人工受控 merge"],
  ["red", "異常——需人工檢查"],
  ["gray", "無法查詢"],
];

for (const [light, text] of FIVE_STATES) {
  test(`燈號五態:${light} 呈現對應 class 與狀態文字`, () => {
    const html = mod.renderCard(windowsTarget({ light, light_text: text }));
    assert.ok(html.includes(`update-card update-light-${light}`), `須含 update-light-${light}`);
    assert.ok(html.includes(text), "狀態文字須呈現");
    assert.ok(html.includes("update-dot"), "須含燈號色點");
  });
}

test("關鍵不對稱:Windows 帶客製=橙、WSL 落後無分歧=藍(可前進)", () => {
  const html = mod.renderCards(payload([windowsTarget({}), wslTarget({})]));
  assert.ok(html.includes("update-card update-light-orange"), "Windows 端須為橙");
  assert.ok(html.includes("update-card update-light-blue"), "WSL 端須為藍(可前進)");
});

// ---------------------------------------------------------------------------
// 零執行入口
// ---------------------------------------------------------------------------

test("零執行入口:五態卡片渲染輸出皆不含任何 button(階段二寫入未核准)", () => {
  for (const [light, text] of FIVE_STATES) {
    const html = mod.renderCard(windowsTarget({ light, light_text: text }));
    assert.ok(!html.includes("<button"), `${light} 態卡片不得含 button`);
  }
  const both = mod.renderCards(payload([windowsTarget({}), wslTarget({})]));
  assert.ok(!both.includes("<button"), "兩端並列渲染不得含任何 button");
  assert.ok(!mod.renderCards(null).includes("<button"), "無資料時也不得含 button");
});

// ---------------------------------------------------------------------------
// 事實欄位
// ---------------------------------------------------------------------------

test("HEAD、rescue ref 如實呈現", () => {
  const html = mod.renderCard(windowsTarget({}));
  assert.ok(html.includes("970118870"), "須顯示 HEAD short sha");
  assert.ok(html.includes("rescue/pre-updater-merge-20260724"), "須列出 rescue ref");
  assert.ok(html.includes("c12c64f9e"), "須顯示 rescue ref 指向");
});

test("red 態呈現 blocking_reasons", () => {
  const html = mod.renderCard(
    windowsTarget({
      light: "red",
      light_text: "異常——需人工檢查",
      overall_driver: "target",
      blocking_reasons: ["工作樹有未提交變更(status --porcelain 非空)", "預期存在的 rescue ref 全部遺失"],
    }),
  );
  assert.ok(html.includes("update-reasons"), "red 態須呈現原因清單");
  assert.ok(html.includes("未提交變更"), "須列出髒樹原因");
});

test("diverge commit 以 details 呈現、不展開 diff", () => {
  const html = mod.renderCard(windowsTarget({}));
  assert.ok(html.includes("<details"), "分歧 commit 以可摺疊 details 呈現");
  assert.ok(html.includes("970118870 fix(merge)"), "須列 commit 摘要行");
  assert.ok(!html.includes("diff --git"), "不得展開 diff");
});

test("無資料(API 未連線)→ 提示訊息,不噴例外、零 button", () => {
  const html = mod.renderCards(null);
  assert.ok(html.includes("唯讀 API 未連線"));
  assert.ok(!html.includes("<button"));
});

test("無任何比較基準時誠實提示,不噴例外", () => {
  const html = mod.renderCard(windowsTarget({ comparisons: [] }));
  assert.ok(html.includes("無可用的比較基準"));
});

// ---------------------------------------------------------------------------
// 原始碼靜態鎖定
// ---------------------------------------------------------------------------

test("原始碼:取數僅經 apiGet、無直連 fetch、view 內唯一 onClick 是 reload", async () => {
  const source = await readFile(join(root, "src", "views", "UpdatePrecheck.tsx"), "utf8");
  assert.ok(source.includes('apiGet<UpdatePrecheckPayload>("/api/update-precheck")'),
    "取數須經唯讀 apiGet");
  assert.ok(!source.includes("fetch("), "不得自行 fetch");
  assert.ok(!source.includes("<button"), "不得自訂 <button(僅共用 RefreshButton)");
  const onClicks = source.match(/onClick=\{[^}]*\}/g) ?? [];
  for (const oc of onClicks) {
    assert.equal(oc, "onClick={reload}", `view 內 onClick 只能是 reload(讀取),發現:${oc}`);
  }
  for (const forbidden of ["hermes update", "--ff-only", '"reset"', '"merge"', '"fetch"', '"pull"']) {
    assert.ok(!source.includes(forbidden), `原始碼不得出現寫入面字面:${forbidden}`);
  }
});

test("原始碼:App.tsx 掛載 Hermes 更新 view 於 nav", async () => {
  const app = await readFile(join(root, "src", "App.tsx"), "utf8");
  assert.ok(app.includes("<UpdatePrecheckView />"), "App 須掛載升級預檢 view");
  assert.ok(app.includes('id: "update"'), "nav 須含 update 項");
});

// ---------------------------------------------------------------------------
// 工作樹 pill:class 組合 + **實際 cascade 計算**(特異性優先權)
//
// 2026-07-24 回歸:pill 的 color 曾因 `.update-facts span`(0,1,1)蓋過
// `.update-tree-clean`(0,1,0)而變成灰色。以下不只看原始碼,而是用
// tests/helpers/css-cascade.mjs 實際解析 globals.css、計算特異性並解出
// 最終 color 值,確保「綠/橙真的會生效」。
// ---------------------------------------------------------------------------

// DOM 結構取自 UpdateTargetCard:.update-card > .update-facts > div > span.pill
const chainFor = (pillClass) => [
  { tag: "div", classes: ["update-card", "update-light-orange"] },
  { tag: "div", classes: ["update-facts"] },
  { tag: "div", classes: [] },
  { tag: "span", classes: ["update-tree", pillClass] },
];
// 對照組:同一容器內的欄位標籤 span(無 pill class)
const LABEL_CHAIN = [
  { tag: "div", classes: ["update-card", "update-light-orange"] },
  { tag: "div", classes: ["update-facts"] },
  { tag: "div", classes: [] },
  { tag: "span", classes: [] },
];

test("cascade:工作樹乾淨 pill 的 color 實際解析為 --green(#3dd49b)", async () => {
  const css = await readFile(join(root, "src", "globals.css"), "utf8");
  const style = computeStyle(css, chainFor("update-tree-clean"));
  assert.equal(style.color, "#3dd49b", `color 應為 --green,實得 ${style.color}`);
  assert.equal(style.background, "rgba(61,212,155,.14)");
});

test("cascade:工作樹髒 pill 的 color 實際解析為 --orange(#f0a24b)", async () => {
  const css = await readFile(join(root, "src", "globals.css"), "utf8");
  const style = computeStyle(css, chainFor("update-tree-dirty"));
  assert.equal(style.color, "#f0a24b", `color 應為 --orange,實得 ${style.color}`);
  assert.equal(style.background, "rgba(240,162,75,.16)");
});

test("cascade:pill 的其他屬性未被 .update-facts span 汙染", async () => {
  const css = await readFile(join(root, "src", "globals.css"), "utf8");
  const style = computeStyle(css, chainFor("update-tree-clean"));
  // .update-facts span 會設 uppercase / .04em / 12px / block——pill 必須全部蓋掉
  assert.equal(style["text-transform"], "none", "pill 不得被 uppercase 影響");
  assert.equal(style["letter-spacing"], "0", "pill 不得被 .04em 字距影響");
  assert.equal(style["font-size"], "13px", "pill 字級須為自身的 13px");
  assert.equal(style.display, "inline-block", "pill 須為 inline-block(非 block)");
  assert.equal(style["font-weight"], "800", "pill 須加粗");
});

test("cascade:對照組——同容器的一般標籤 span 仍為 --muted(證明解析器有反映該規則)", async () => {
  const css = await readFile(join(root, "src", "globals.css"), "utf8");
  const style = computeStyle(css, LABEL_CHAIN);
  assert.equal(style.color, "#8f96a6", "標籤 span 應維持 --muted 灰");
  assert.equal(style["text-transform"], "uppercase");
});

test("cascade 解析器有偵錯能力:還原成舊的低特異性寫法時,會抓到 color 變灰", async () => {
  const css = await readFile(join(root, "src", "globals.css"), "utf8");
  // 把 pill 顏色規則還原成 bug 版本(缺 .update-facts 前綴 → 0,1,0)
  const buggy = css
    .replace(".update-facts .update-tree-clean {", ".update-tree-clean {")
    .replace(".update-facts .update-tree-dirty {", ".update-tree-dirty {");
  assert.notEqual(buggy, css, "測試前提:必須真的改到規則");
  const style = computeStyle(buggy, chainFor("update-tree-clean"));
  assert.equal(style.color, "#8f96a6",
    "bug 版本應被解析器抓出 color 退化為 --muted 灰(證明本測試不是空過)");
});

// ---------------------------------------------------------------------------
// 工作樹 pill:渲染輸出的 class 組合與文字
// ---------------------------------------------------------------------------

test("渲染:工作樹乾淨/髒各自帶正確 class 組合與可辨識文字", () => {
  const clean = mod.renderCard(windowsTarget({ dirty: false }));
  assert.ok(clean.includes('class="update-tree update-tree-clean"'), "乾淨須帶 clean class");
  assert.ok(clean.includes("工作樹乾淨"));
  assert.ok(!clean.includes("update-tree-dirty"));

  const dirty = mod.renderCard(windowsTarget({ dirty: true }));
  assert.ok(dirty.includes('class="update-tree update-tree-dirty"'), "髒須帶 dirty class");
  assert.ok(dirty.includes("工作樹髒"));
  assert.ok(!dirty.includes("update-tree-clean"));
});

test("渲染:dirty 未知(null)時不顯示 pill,不臆測狀態", () => {
  const html = mod.renderCard(windowsTarget({ dirty: null }));
  assert.ok(!html.includes("update-tree"), "未知狀態不得顯示工作樹 pill");
});
