// AgentOS Local Bridge — 最小寫入例外(2026-07-23 核准,規格見
// docs/webui-migration-proposal.md §5.4;逐條為硬性 DoD)。
//
// 安全規格落實方式:
// 1. 僅四種白名單操作端點:啟動 / health / 重新載入 / 停止,全部列在
//    下方 route 表,其他路徑一律 404——不存在其他操作入口。
// 2. 無任意 shell command API:spawn 的指令與參數是本檔案內的凍結常數
//    (FIXED_COMMAND),HTTP 介面不讀取 request body、不接受任何指令/
//    參數字串。stop 用的 PID 來自 bridge 自己的 spawn 紀錄,不來自請求。
// 3. PID/process ownership:stop/reload 只作用於「本 bridge spawn 的」
//    child process;對非本 bridge 啟動的 Hermes process 一律 409 拒絕。
//    (範本原版 `hermes dashboard --stop` 是 CLI 全域停止語意,已棄用。)
// 4. 重複啟動防護:dashboard 已在線(不論誰啟動)或已有啟動中 promise
//    時,再收到啟動請求=no-op,不產生第二個 process。
// 5. localhost-only:bind 寫死 127.0.0.1;CORS 只允許本機 origin。
// 6. audit log:每次 start/stop/reload 操作(含拒絕)寫一筆到 logs/。

import { spawn, execFile } from "node:child_process";
import { createServer } from "node:http";
import { appendFileSync, mkdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");

// ---- 凍結常數:指令、參數、bind 位址(安全規格 §5.4「指令與參數寫死」) ----
export const BRIDGE_HOST = "127.0.0.1"; // 不提供參數化入口
export const FIXED_COMMAND = Object.freeze({
  bin: "hermes",
  args: Object.freeze(["dashboard", "--host", "127.0.0.1", "--port", "9119", "--no-open"]),
});
export const DASHBOARD_URL = "http://127.0.0.1:9119";
export const BRIDGE_PORT = 8787;
// 冷啟動實測 2026-07-23:34.7 秒(hermes v0.18.2,web UI 已 build 過)。
// 範本原值 90 秒;考量 hermes web UI 需重 build 的情境與 gateway 3.5 分鐘
// 慢啟動教訓(memory/hermes-gateway-init-slow.md),放寬為 240 秒。
export const START_TIMEOUT_MS = 240000;
export const DEFAULT_LOG_DIR = join(projectRoot, "..", "logs");
export const AUDIT_LOG_NAME = "webui_bridge_audit.log";

const allowedOrigin = /^http:\/\/(localhost|127\.0\.0\.1):\d+$/;

// createBridge():production 由 agentos-local.mjs 以「零參數」呼叫,全部
// 走上面的凍結常數。options 僅供測試注入假 hermes fixture 與臨時目錄——
// 這些注入點只存在於 process 內部,HTTP 介面完全碰不到。
export function createBridge(options = {}) {
  const command = options.command ?? FIXED_COMMAND;
  const dashboardUrl = options.dashboardUrl ?? DASHBOARD_URL;
  const bridgePort = options.bridgePort ?? BRIDGE_PORT;
  const logDir = options.logDir ?? DEFAULT_LOG_DIR;
  const startTimeoutMs = options.startTimeoutMs ?? START_TIMEOUT_MS;
  const auditLogPath = join(logDir, AUDIT_LOG_NAME);

  let ownedChild = null; // 本 bridge spawn 的 child(ownership 的唯一依據)
  let startPromise = null; // 併發啟動去重
  let childLog = "";

  function audit(operation, pid, result) {
    // 每次 start/stop/reload 操作寫一筆:時間、操作、PID、結果。
    try {
      mkdirSync(logDir, { recursive: true });
      const line = `${new Date().toISOString()} | ${operation} | pid=${pid ?? "-"} | ${result}\n`;
      appendFileSync(auditLogPath, line, "utf8");
    } catch (error) {
      console.error(`audit log 寫入失敗: ${error.message}`);
    }
  }

  function ownedAlive() {
    return ownedChild !== null && ownedChild.exitCode === null;
  }

  async function dashboardReady() {
    try {
      const response = await fetch(dashboardUrl, { redirect: "manual", signal: AbortSignal.timeout(1500) });
      return response.status >= 200 && response.status < 500;
    } catch {
      return false;
    }
  }

  async function waitForDashboard() {
    const deadline = Date.now() + startTimeoutMs;
    while (Date.now() < deadline) {
      if (await dashboardReady()) return;
      if (!ownedAlive()) {
        throw new Error(childLog.trim() || "hermes dashboard 已停止,請檢查 Hermes 設定");
      }
      await new Promise((r) => setTimeout(r, 800));
    }
    throw new Error(`Hermes Dashboard 在 ${startTimeoutMs / 1000} 秒內沒有回應`);
  }

  function spawnOwnedDashboard() {
    childLog = "";
    // 指令與參數為凍結常數;不經 shell(Windows 下 CreateProcess 可直接
    // 解析 PATH 上的 hermes.exe,實測 2026-07-23 通過,毋須 shell:true)。
    const child = spawn(command.bin, [...command.args], {
      cwd: projectRoot,
      env: process.env,
      stdio: ["ignore", "pipe", "pipe"],
      shell: false,
    });
    child.stdout.on("data", (c) => { childLog = `${childLog}${c}`.slice(-6000); });
    child.stderr.on("data", (c) => { childLog = `${childLog}${c}`.slice(-6000); });
    child.on("error", (error) => { childLog = `${childLog}${error.message}`.slice(-6000); });
    ownedChild = child;
    return child;
  }

  async function killOwnedDashboard() {
    const pid = ownedChild.pid;
    if (!Number.isInteger(pid)) throw new Error("owned PID 無效,拒絕操作");
    if (process.platform === "win32") {
      // taskkill 參數:唯一的變數是 bridge 自己記錄的整數 PID,絕非請求輸入。
      await new Promise((resolveKill, rejectKill) => {
        execFile("taskkill", ["/PID", String(pid), "/T", "/F"], (error, _stdout, stderr) => {
          // process 已自行結束時 taskkill 會報錯,視同已停止
          if (error && ownedAlive()) rejectKill(new Error(stderr.trim() || error.message));
          else resolveKill();
        });
      });
    } else {
      ownedChild.kill("SIGTERM");
    }
    const deadline = Date.now() + 15000;
    while (Date.now() < deadline) {
      if (!ownedAlive() && !(await dashboardReady())) {
        ownedChild = null;
        return pid;
      }
      await new Promise((r) => setTimeout(r, 300));
    }
    throw new Error("已送出停止指令,但 Hermes Dashboard 仍在回應");
  }

  async function startDashboard() {
    // 重複啟動防護:已在線(不論誰啟動)=no-op,不產生第二個 process
    if (await dashboardReady()) {
      const external = !ownedAlive();
      audit("start", external ? null : ownedChild.pid, external ? "no-op(已有外部 dashboard 在線)" : "no-op(已在線,重複啟動)");
      return { ok: true, dashboardUrl, reused: true, external };
    }
    if (startPromise) return startPromise; // 併發啟動去重

    startPromise = (async () => {
      const child = spawnOwnedDashboard();
      try {
        await waitForDashboard();
        audit("start", child.pid, "成功");
        return { ok: true, dashboardUrl, reused: false, external: false };
      } catch (error) {
        audit("start", child.pid, `失敗: ${error.message}`);
        throw error;
      }
    })();

    try {
      return await startPromise;
    } finally {
      startPromise = null;
    }
  }

  async function stopDashboard() {
    // ownership 驗證:只停「由本 bridge spawn 的」process
    if (!ownedAlive()) {
      if (await dashboardReady()) {
        audit("stop", null, "拒絕(dashboard 在線但非本 bridge 啟動)");
        const error = new Error("目前的 Hermes Dashboard 不是由本 Bridge 啟動,拒絕停止(請用啟動它的方式關閉)");
        error.statusCode = 409;
        throw error;
      }
      audit("stop", null, "no-op(沒有由本 bridge 啟動的 process)");
      return { ok: true, dashboardUrl, alreadyStopped: true };
    }
    const pid = await killOwnedDashboard();
    audit("stop", pid, "成功");
    return { ok: true, dashboardUrl, stoppedPid: pid };
  }

  async function reloadDashboard() {
    // ownership 驗證:只重載「由本 bridge spawn 的」process
    if (!ownedAlive()) {
      audit("reload", null, "拒絕(沒有由本 bridge 啟動的 process)");
      const error = new Error("沒有由本 Bridge 啟動的 Hermes Dashboard 可重新載入");
      error.statusCode = 409;
      throw error;
    }
    const oldPid = await killOwnedDashboard();
    const child = spawnOwnedDashboard();
    try {
      await waitForDashboard();
      audit("reload", child.pid, `成功(原 pid=${oldPid})`);
      return { ok: true, dashboardUrl, previousPid: oldPid, pid: child.pid };
    } catch (error) {
      audit("reload", child.pid, `失敗: ${error.message}`);
      throw error;
    }
  }

  function sendJson(response, status, payload, origin) {
    if (origin && allowedOrigin.test(origin)) {
      response.setHeader("Access-Control-Allow-Origin", origin);
      response.setHeader("Vary", "Origin");
    }
    response.writeHead(status, { "Content-Type": "application/json; charset=utf-8" });
    response.end(JSON.stringify(payload));
  }

  const server = createServer(async (request, response) => {
    // 注意:本 server 從不讀取 request body——HTTP 介面不接受任何指令/參數。
    const origin = request.headers.origin || "";
    if (origin && !allowedOrigin.test(origin)) {
      sendJson(response, 403, { ok: false, error: "只允許本機 AgentOS UI 連接" });
      return;
    }

    if (request.method === "OPTIONS") {
      response.setHeader("Access-Control-Allow-Origin", origin);
      response.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
      response.setHeader("Access-Control-Allow-Headers", "Content-Type");
      response.writeHead(204);
      response.end();
      return;
    }

    // ---- 白名單操作端點(僅此四種,無其他操作入口) ----
    if (request.method === "GET" && request.url === "/health") {
      sendJson(response, 200, {
        ok: true,
        dashboardOnline: await dashboardReady(),
        owned: ownedAlive(),
        dashboardUrl,
      }, origin);
      return;
    }

    if (request.method === "POST" && request.url === "/api/hermes/dashboard") {
      try {
        sendJson(response, 200, await startDashboard(), origin);
      } catch (error) {
        sendJson(response, error.statusCode ?? 500, { ok: false, error: error.message || "Hermes Dashboard 啟動失敗" }, origin);
      }
      return;
    }

    if (request.method === "POST" && request.url === "/api/hermes/dashboard/reload") {
      try {
        sendJson(response, 200, await reloadDashboard(), origin);
      } catch (error) {
        sendJson(response, error.statusCode ?? 500, { ok: false, error: error.message || "Hermes Dashboard 重新載入失敗" }, origin);
      }
      return;
    }

    if (request.method === "POST" && request.url === "/api/hermes/dashboard/stop") {
      try {
        sendJson(response, 200, await stopDashboard(), origin);
      } catch (error) {
        sendJson(response, error.statusCode ?? 500, { ok: false, error: error.message || "Hermes Dashboard 關閉失敗" }, origin);
      }
      return;
    }

    sendJson(response, 404, { ok: false, error: "Not found" }, origin);
  });

  function listen() {
    return new Promise((resolveListen, rejectListen) => {
      server.once("error", rejectListen);
      // bind 寫死 127.0.0.1(BRIDGE_HOST 常數),無參數化入口
      server.listen(bridgePort, BRIDGE_HOST, () => resolveListen(server.address()));
    });
  }

  async function shutdown() {
    if (ownedAlive()) {
      try {
        await killOwnedDashboard();
        audit("stop", null, "成功(bridge 關閉時清理自有 process)");
      } catch (error) {
        console.error(`關閉時清理 dashboard 失敗: ${error.message}`);
      }
    }
    await new Promise((r) => server.close(r));
  }

  return {
    listen,
    shutdown,
    server,
    auditLogPath,
    get ownedPid() {
      return ownedAlive() ? ownedChild.pid : null;
    },
  };
}
