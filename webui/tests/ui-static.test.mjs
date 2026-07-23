// P0 DoD 靜態檢核:mock 清零(DoD 第 4 條)、localhost-only 設定、
// 託管殘留 grep 清零(DoD 第 2 條)。
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

test("mock 硬性清零:範本假資料字串不得出現在 UI 原始碼", async () => {
  const app = await readFile(join(root, "src", "App.tsx"), "utf8");
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
  for (const s of forbidden) {
    assert.ok(!app.includes(s), `App.tsx 不得含 mock 字串: ${s}`);
  }
});

test("未接線區塊不呈現:P0 只有 Hermes Dashboard 一個 view", async () => {
  const app = await readFile(join(root, "src", "App.tsx"), "utf8");
  assert.ok(!/type View =/.test(app), "P0 不存在多 view 切換");
  assert.match(app, /HERMES_BRIDGE_URL = "http:\/\/127\.0\.0\.1:8787"/);
  assert.match(app, /HERMES_DASHBOARD_URL = "http:\/\/127\.0\.0\.1:9119"/);
});

test("localhost-only:vite 設定 host 寫死 127.0.0.1", async () => {
  const config = await readFile(join(root, "vite.config.ts"), "utf8");
  assert.match(config, /LOCALHOST_ONLY = "127\.0\.0\.1"/);
  assert.ok(!config.includes("0.0.0.0"), "不得 bind 0.0.0.0");
  assert.ok(!/host:\s*true/.test(config), "不得 host: true(等同對外)");
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
