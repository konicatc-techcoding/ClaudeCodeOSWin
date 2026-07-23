// 靜態檢核:mock 清零(P0 DoD 第 4 條,P1 繼續成立)、localhost-only 設定、
// 託管殘留 grep 清零(P0 DoD 第 2 條)、P1 唯讀 API 接線的 localhost-only。
import assert from "node:assert/strict";
import test from "node:test";
import { readFile, readdir } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");

async function collectSourceFiles(dir, out = []) {
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    if (["node_modules", "dist", ".git"].includes(entry.name)) continue;
    const full = join(dir, entry.name);
    if (entry.isDirectory()) await collectSourceFiles(full, out);
    else if (/\.(tsx?|mjs|json|html|css|md)$/.test(entry.name)) out.push(full);
  }
  return out;
}

async function collectSrcFiles() {
  return collectSourceFiles(join(root, "src"));
}

test("mock 硬性清零:範本假資料字串不得出現在 UI 原始碼", async () => {
  const forbidden = [
    "v0.8.2", // 假版本號
    "4 / 5", // 假 agent 數
    "18.4K", // 假 token 用量
    "1.84M", // 假 token 用量
    "Research Scout", // 假 profile 表
    "News Oracle",
    "Ops Keeper",
    "Telegram 晨間摘要", // 假排程
    "AI 新聞來源更新",
    "任務執行中", // 假任務進度卡
    "ChatView",
    "MonitorView",
    "系統運作正常", // 假系統狀態
    "Administrator", // 假使用者卡
  ];
  for (const file of await collectSrcFiles()) {
    const content = await readFile(file, "utf8");
    for (const s of forbidden) {
      assert.ok(!content.includes(s), `${file} 不得含 mock 字串: ${s}`);
    }
  }
});

test("P1 接線:bridge/hermes/唯讀 API 端點皆為寫死的 127.0.0.1 常數", async () => {
  const hermes = await readFile(join(root, "src", "views", "Hermes.tsx"), "utf8");
  assert.match(hermes, /HERMES_BRIDGE_URL = "http:\/\/127\.0\.0\.1:8787"/);
  assert.match(hermes, /HERMES_DASHBOARD_URL = "http:\/\/127\.0\.0\.1:9119"/);
  const api = await readFile(join(root, "src", "api.ts"), "utf8");
  assert.match(api, /API_BASE_URL = "http:\/\/127\.0\.0\.1:8799"/);
});

test("localhost-only:src/ 內所有 http(s) URL 只允許 127.0.0.1(啟動指令文字除外)", async () => {
  for (const file of await collectSrcFiles()) {
    const content = await readFile(file, "utf8");
    for (const match of content.matchAll(/https?:\/\/([A-Za-z0-9.-]+)/g)) {
      const host = match[1];
      assert.ok(
        host === "127.0.0.1" || host === "localhost",
        `${file} 含非本機 URL host: ${host}`,
      );
    }
  }
});

test("localhost-only:vite 設定 host 寫死 127.0.0.1", async () => {
  const config = await readFile(join(root, "vite.config.ts"), "utf8");
  assert.match(config, /LOCALHOST_ONLY = "127\.0\.0\.1"/);
  assert.ok(!config.includes("0.0.0.0"), "不得 bind 0.0.0.0");
  assert.ok(!/host:\s*true/.test(config), "不得 host: true(等同對外)");
});

// ---- P3:ClaudeCode CLI(PTY)UI 靜態檢核(提案 §7/§8 DoD 8) ----

test("P3 nav:ClaudeCode CLI 位於「總覽」與「Jobs」之間", async () => {
  const app = await readFile(join(root, "src", "App.tsx"), "utf8");
  const overviewIdx = app.indexOf('{ id: "overview"');
  const terminalIdx = app.indexOf('{ id: "terminal"');
  const jobsIdx = app.indexOf('{ id: "jobs"');
  assert.ok(overviewIdx >= 0 && terminalIdx >= 0 && jobsIdx >= 0);
  assert.ok(overviewIdx < terminalIdx && terminalIdx < jobsIdx, "nav 順序必須是 總覽 → ClaudeCode CLI → Jobs");
  assert.ok(app.includes('title: "ClaudeCode CLI"'));
});

test("P3 警語:不可移除(無條件渲染、無關閉控制)、關鍵語句存在", async () => {
  const terminal = await readFile(join(root, "src", "views", "Terminal.tsx"), "utf8");
  assert.ok(terminal.includes("TERMINAL_PAGE_WARNING"), "警語常數必須存在");
  assert.ok(terminal.includes("與本機終端機同權"), "警語須聲明與本機終端機同權");
  assert.ok(terminal.includes("唯一能寫入 memory 正本"), "警語須聲明 memory 正本寫入入口性質");
  assert.ok(terminal.includes("未經 redact 掃描"), "警語須聲明終端輸出未經 redact 掃描");
  // 警語為無條件渲染:該 JSX 行不得掛任何條件(&& / 三元)
  const warningLine = terminal.split("\n").find((l) => l.includes('className="terminal-warning"'));
  assert.ok(warningLine, "警語元素必須存在");
  assert.ok(!warningLine.includes("&&") && !warningLine.includes("?"), "警語不得條件渲染");
  assert.ok(!terminal.includes("dismiss") && !terminal.includes("關閉警語"), "不得有警語關閉控制");
});

test("P3 localhost-only:PTY 端點寫死 127.0.0.1:8801;ws:// URL 僅本機", async () => {
  const terminal = await readFile(join(root, "src", "views", "Terminal.tsx"), "utf8");
  assert.match(terminal, /PTY_HEALTH_URL = "http:\/\/127\.0\.0\.1:8801\/health"/);
  assert.match(terminal, /PTY_WS_URL = "ws:\/\/127\.0\.0\.1:8801"/);
  for (const file of await collectSrcFiles()) {
    const content = await readFile(file, "utf8");
    for (const match of content.matchAll(/wss?:\/\/([A-Za-z0-9.-]+)/g)) {
      const host = match[1];
      assert.ok(host === "127.0.0.1" || host === "localhost", `${file} 含非本機 WS host: ${host}`);
    }
  }
});

test("P3 無假介面:服務未啟動顯示啟動指引;進 view 不自動 spawn", async () => {
  const terminal = await readFile(join(root, "src", "views", "Terminal.tsx"), "utf8");
  assert.ok(terminal.includes("npm run local"), "須含啟動指引指令");
  assert.ok(terminal.includes("未啟動或無法連線"), "須有明確的服務未啟動狀態");
  // 連線(spawn)只由使用者按鈕觸發,不在 effect 內自動連線
  assert.ok(!/useEffect\([^)]*connect\(/s.test(terminal), "不得在 effect 內自動連線");
  assert.match(terminal, /onClick=\{\(\) => connect\(false\)\}/, "啟動 session 必須是明確的使用者動作");
});

test("託管假設殘留 grep 清零(OpenAI/Cloudflare 設定痕跡)", async () => {
  const files = await collectSourceFiles(root);
  // pattern 動態組字,避免本測試檔自身被 DoD 的 grep 驗收命中
  const patterns = [
    ["oai", "-"].join(""),
    ["app", "gprj"].join(""),
    ["d1", "_databases"].join(""),
    ["r2", "_buckets"].join(""),
  ];
  for (const file of files) {
    const content = await readFile(file, "utf8");
    for (const p of patterns) {
      assert.ok(!content.includes(p), `${file} 含託管殘留: ${p}`);
    }
  }
});
