// Bridge 安全規格測試 — 對應提案 §4.1 P0 DoD 第 5 條(a)–(d)逐條鎖定。
// 全部使用 FAKE_HERMES fixture,不碰真實 hermes、不讀任何真實憑證。
import assert from "node:assert/strict";
import test from "node:test";
import { spawn } from "node:child_process";
import { mkdtempSync, readFileSync, existsSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createBridge, FIXED_COMMAND, BRIDGE_HOST, AUDIT_LOG_NAME } from "../scripts/bridge.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const fixture = resolve(here, "fixtures", "fake-hermes.mjs");

const TEST_DASHBOARD_PORT = 9319;
const TEST_BRIDGE_PORT = 8790;
const TEST_DASHBOARD_URL = `http://127.0.0.1:${TEST_DASHBOARD_PORT}`;
const BRIDGE_URL = `http://127.0.0.1:${TEST_BRIDGE_PORT}`;

// 測試注入:用 node 執行 fixture 模擬 hermes;參數形狀與 FIXED_COMMAND 相同
const FAKE_ARGS = ["dashboard", "--host", "127.0.0.1", "--port", String(TEST_DASHBOARD_PORT), "--no-open"];

function makeTempDir() {
  return mkdtempSync(join(tmpdir(), "webui-bridge-test-"));
}

function makeBridge(logDir, spawnLogPath) {
  process.env.FAKE_HERMES_SPAWN_LOG = spawnLogPath;
  return createBridge({
    command: { bin: process.execPath, args: [fixture, ...FAKE_ARGS] },
    dashboardUrl: TEST_DASHBOARD_URL,
    bridgePort: TEST_BRIDGE_PORT,
    logDir,
    startTimeoutMs: 20000,
  });
}

function readSpawnLog(spawnLogPath) {
  if (!existsSync(spawnLogPath)) return [];
  return readFileSync(spawnLogPath, "utf8").trim().split("\n").filter(Boolean).map((l) => JSON.parse(l));
}

async function dashboardReachable() {
  try {
    const r = await fetch(TEST_DASHBOARD_URL, { signal: AbortSignal.timeout(1000) });
    return r.ok;
  } catch {
    return false;
  }
}

test("bridge 安全規格(白名單/注入/ownership/重複啟動/CORS/audit)", async (t) => {
  const dir = makeTempDir();
  const spawnLogPath = join(dir, "spawn.log");
  const bridge = makeBridge(dir, spawnLogPath);
  const address = await bridge.listen();

  t.after(async () => {
    await bridge.shutdown();
    rmSync(dir, { recursive: true, force: true });
    delete process.env.FAKE_HERMES_SPAWN_LOG;
  });

  await t.test("(c) bind 僅 127.0.0.1", () => {
    assert.equal(address.address, "127.0.0.1");
    assert.equal(BRIDGE_HOST, "127.0.0.1");
  });

  await t.test("(b) production 指令為凍結常數", () => {
    assert.equal(FIXED_COMMAND.bin, "hermes");
    assert.deepEqual([...FIXED_COMMAND.args], ["dashboard", "--host", "127.0.0.1", "--port", "9119", "--no-open"]);
    assert.ok(Object.isFrozen(FIXED_COMMAND));
    assert.ok(Object.isFrozen(FIXED_COMMAND.args));
  });

  await t.test("(a) 白名單以外端點一律 404;白名單端點方法受限", async () => {
    const notFound = await fetch(`${BRIDGE_URL}/api/anything`, { method: "POST" });
    assert.equal(notFound.status, 404);
    const shellish = await fetch(`${BRIDGE_URL}/api/exec?cmd=calc.exe`, { method: "POST" });
    assert.equal(shellish.status, 404);
    // 白名單端點用錯誤方法 → 404(route 是「方法+路徑」整組寫死)
    const wrongMethod = await fetch(`${BRIDGE_URL}/api/hermes/dashboard`, { method: "GET" });
    assert.equal(wrongMethod.status, 404);
    const del = await fetch(`${BRIDGE_URL}/api/hermes/dashboard`, { method: "DELETE" });
    assert.equal(del.status, 404);
  });

  await t.test("CORS:非本機 origin 一律 403;本機 origin 回 ACAO", async () => {
    const evil = await fetch(`${BRIDGE_URL}/health`, { headers: { Origin: "http://evil.example" } });
    assert.equal(evil.status, 403);
    const evilPost = await fetch(`${BRIDGE_URL}/api/hermes/dashboard`, { method: "POST", headers: { Origin: "http://evil.example:5173" } });
    assert.equal(evilPost.status, 403);
    const good = await fetch(`${BRIDGE_URL}/health`, { headers: { Origin: "http://localhost:5173" } });
    assert.equal(good.status, 200);
    assert.equal(good.headers.get("access-control-allow-origin"), "http://localhost:5173");
  });

  await t.test("stop:沒有 owned process 且無外部 dashboard → no-op", async () => {
    const r = await fetch(`${BRIDGE_URL}/api/hermes/dashboard/stop`, { method: "POST" });
    const body = await r.json();
    assert.equal(r.status, 200);
    assert.equal(body.alreadyStopped, true);
  });

  await t.test("(c) reload:沒有 owned process → 409 拒絕", async () => {
    const r = await fetch(`${BRIDGE_URL}/api/hermes/dashboard/reload`, { method: "POST" });
    assert.equal(r.status, 409);
  });

  await t.test("(b) start:request body 完全被忽略,spawn 參數仍是寫死清單", async () => {
    const r = await fetch(`${BRIDGE_URL}/api/hermes/dashboard`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cmd: "calc.exe", args: ["--evil"], HERMES_BIN: "evil.exe" }),
    });
    const body = await r.json();
    assert.equal(r.status, 200);
    assert.equal(body.ok, true);
    assert.equal(body.reused, false);
    const spawns = readSpawnLog(spawnLogPath);
    assert.equal(spawns.length, 1, "只 spawn 一次");
    // fixture 收到的 argv 必須正是寫死的參數,任何 body 內容都進不來
    assert.deepEqual(spawns[0].args, FAKE_ARGS);
    assert.equal(await dashboardReachable(), true);
  });

  await t.test("重複啟動防護:已在線再啟動=no-op,不產生第二個 process", async () => {
    const r = await fetch(`${BRIDGE_URL}/api/hermes/dashboard`, { method: "POST" });
    const body = await r.json();
    assert.equal(body.ok, true);
    assert.equal(body.reused, true);
    assert.equal(readSpawnLog(spawnLogPath).length, 1, "spawn 次數仍為 1");
  });

  await t.test("health 回報 dashboardOnline 與 owned", async () => {
    const r = await fetch(`${BRIDGE_URL}/health`);
    const body = await r.json();
    assert.equal(body.ok, true);
    assert.equal(body.dashboardOnline, true);
    assert.equal(body.owned, true);
  });

  await t.test("(c) reload:重啟 owned process,PID 改變且仍在線", async () => {
    const oldPid = bridge.ownedPid;
    const r = await fetch(`${BRIDGE_URL}/api/hermes/dashboard/reload`, { method: "POST" });
    const body = await r.json();
    assert.equal(r.status, 200);
    assert.equal(body.ok, true);
    const spawns = readSpawnLog(spawnLogPath);
    assert.equal(spawns.length, 2, "reload 後累計 spawn 兩次");
    assert.notEqual(spawns[1].pid, oldPid);
    assert.equal(await dashboardReachable(), true);
  });

  await t.test("(c) stop:只停 owned process,port 釋放", async () => {
    const r = await fetch(`${BRIDGE_URL}/api/hermes/dashboard/stop`, { method: "POST" });
    const body = await r.json();
    assert.equal(r.status, 200);
    assert.equal(body.ok, true);
    assert.ok(Number.isInteger(body.stoppedPid));
    assert.equal(await dashboardReachable(), false);
    assert.equal(bridge.ownedPid, null);
  });

  await t.test("(c) stop/start 對「非本 bridge 啟動」的 dashboard:stop 拒絕、start 不搶佔", async () => {
    // 由測試自行(bridge 之外)啟動一個外部 FAKE dashboard
    const external = spawn(process.execPath, [fixture, ...FAKE_ARGS], {
      stdio: ["ignore", "pipe", "pipe"],
      env: { ...process.env, FAKE_HERMES_SPAWN_LOG: "" },
    });
    try {
      const deadline = Date.now() + 10000;
      while (Date.now() < deadline && !(await dashboardReachable())) {
        await new Promise((r) => setTimeout(r, 200));
      }
      assert.equal(await dashboardReachable(), true, "外部 dashboard 啟動");

      // stop → 409 拒絕,且外部 process 不受影響
      const stop = await fetch(`${BRIDGE_URL}/api/hermes/dashboard/stop`, { method: "POST" });
      assert.equal(stop.status, 409);
      assert.equal(await dashboardReachable(), true, "外部 dashboard 仍在線");
      assert.equal(external.exitCode, null, "外部 process 仍存活");

      // start → no-op(external),不 spawn 第二個 process
      const start = await fetch(`${BRIDGE_URL}/api/hermes/dashboard`, { method: "POST" });
      const startBody = await start.json();
      assert.equal(startBody.reused, true);
      assert.equal(startBody.external, true);
      assert.equal(readSpawnLog(spawnLogPath).length, 2, "spawn 次數不變");
    } finally {
      external.kill();
      await new Promise((r) => setTimeout(r, 500));
    }
  });

  await t.test("(d) audit log:每次操作(含拒絕)一筆;health 不寫", async () => {
    const auditPath = join(dir, AUDIT_LOG_NAME);
    assert.ok(existsSync(auditPath), "audit log 存在");
    const lines = readFileSync(auditPath, "utf8").trim().split("\n");
    const text = lines.join("\n");
    assert.match(text, /\| start \| pid=\d+ \| 成功/);
    assert.match(text, /\| start \| .* no-op/);
    assert.match(text, /\| reload \| pid=\d+ \| 成功/);
    assert.match(text, /\| stop \| pid=\d+ \| 成功/);
    assert.match(text, /\| stop \| pid=- \| 拒絕/);
    assert.match(text, /\| reload \| pid=- \| 拒絕/);
    assert.ok(!/\| health \|/.test(text), "health 查詢不寫 audit(非操作)");
    for (const line of lines) {
      assert.match(line, /^\d{4}-\d{2}-\d{2}T[\d:.]+Z \| (start|stop|reload) \| pid=(\d+|-) \| /, `audit 格式: ${line}`);
    }
  });
});

test("併發啟動去重:同時兩個啟動請求只 spawn 一個 process", async (t) => {
  const dir = makeTempDir();
  const spawnLogPath = join(dir, "spawn.log");
  process.env.FAKE_HERMES_DELAY_MS = "1500"; // 延遲就緒,製造併發窗口
  const bridge = makeBridge(dir, spawnLogPath);
  await bridge.listen();

  t.after(async () => {
    await bridge.shutdown();
    rmSync(dir, { recursive: true, force: true });
    delete process.env.FAKE_HERMES_DELAY_MS;
    delete process.env.FAKE_HERMES_SPAWN_LOG;
  });

  const [a, b] = await Promise.all([
    fetch(`${BRIDGE_URL}/api/hermes/dashboard`, { method: "POST" }).then((r) => r.json()),
    fetch(`${BRIDGE_URL}/api/hermes/dashboard`, { method: "POST" }).then((r) => r.json()),
  ]);
  assert.equal(a.ok, true);
  assert.equal(b.ok, true);
  assert.equal(readSpawnLog(spawnLogPath).length, 1, "併發下仍只 spawn 一次");
});
