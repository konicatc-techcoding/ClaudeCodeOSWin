// AgentOS Local Bridge — 最小寫入例外(2026-07-23 核准,規格見
// docs/webui-migration-proposal.md §5.4;逐條為硬性 DoD)。
//
// 安全規格落實方式(白名單第一群組:Hermes dashboard 操作):
// 1. 僅四種白名單操作端點:啟動 / health / 重新載入 / 停止,全部列在
//    下方 route 表——除了下方第二群組的枚舉 route,其他路徑一律 404。
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
//
// 白名單第二群組:WSL systemd 服務控制(2026-07-27 使用者核准,
// docs/webui-service-control-proposal.md v1.1 §2.2/§2.3 選項 a)。
// 與第一群組以獨立常數分列,測試斷言兩群組各自的完整枚舉,防互相滲透:
// a. 單元枚舉寫死:SERVICE_UNIT_WHITELIST(僅 hermes-worker.service /
//    hermes-telegram.service,不含 timer);動詞枚舉寫死:
//    SERVICE_OP_WHITELIST(僅 start/stop/restart)。
// b. 指令固定模板:`wsl -d Ubuntu systemctl --user <op> <unit>`——
//    <op>/<unit> 只能來自上述兩個枚舉(SERVICE_ROUTES 是兩個枚舉的
//    笛卡兒積),模板其餘部分是凍結常數 SERVICE_COMMAND,無其他參數入口。
// c. HTTP 介面以 route 全字串嚴格比對(枚舉索引);白名單外一律
//    400 + audit。不做 PID ownership(單元由 systemd 管理,非本 process
//    子程序)——邊界由「具名白名單窮舉」替代(提案 §2.1 對照表)。
// d. 明確不做:enable/disable/mask、daemon-reload、unit 檔、
//    `wsl --terminate`(提案 §0.2;動詞枚舉封閉,技術上不存在入口)。
// e. audit log:沿用同一份 logs/webui_bridge_audit.log,每次操作一筆
//    (時間、單元、動詞、結果/exit code,含拒絕)。
// f. 重複操作防護:同一單元已有操作進行中 → 409 + audit。

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

// ---- 白名單第二群組凍結常數:WSL systemd 服務控制(v1.1,2026-07-27 核准) ----
// 單元枚舉:僅兩個常駐 service,不含 timer(提案 §5 項 2 拍板)。
export const SERVICE_UNIT_WHITELIST = Object.freeze(["hermes-worker.service", "hermes-telegram.service"]);
// 動詞枚舉:僅三種 runtime 操作;enable/disable/mask/daemon-reload 技術上無入口。
export const SERVICE_OP_WHITELIST = Object.freeze(["start", "stop", "restart"]);
// 指令固定模板前綴:`wsl -d Ubuntu systemctl --user` + <op> + <unit>。
export const SERVICE_COMMAND = Object.freeze({
  bin: "wsl.exe",
  args: Object.freeze(["-d", "Ubuntu", "systemctl", "--user"]),
});
export const SERVICE_ROUTE_PREFIX = "/api/service/";
// route 表=兩個枚舉的笛卡兒積(6 條),lookup 用全字串嚴格比對——
// op/unit 永遠取自表內凍結值,絕不從 URL 解析出來。
export const SERVICE_ROUTES = (() => {
  const routes = new Map();
  for (const unit of SERVICE_UNIT_WHITELIST) {
    for (const op of SERVICE_OP_WHITELIST) {
      routes.set(`${SERVICE_ROUTE_PREFIX}${unit}/${op}`, Object.freeze({ unit, op }));
    }
  }
  return routes;
})();
export const SERVICE_TIMEOUT_MS = 30000; // wsl 呼叫上限,避免拖住 bridge

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
  // 第二群組:僅供測試注入假 wsl fixture;production 零參數=SERVICE_COMMAND
  const serviceCommand = options.serviceCommand ?? SERVICE_COMMAND;
  const serviceTimeoutMs = options.serviceTimeoutMs ?? SERVICE_TIMEOUT_MS;
  const auditLogPath = join(logDir, AUDIT_LOG_NAME);

  let ownedChild = null; // 本 bridge spawn 的 child(ownership 的唯一依據)
  let startPromise = null; // 併發啟動去重
  let childLog = "";
  const serviceInFlight = new Set(); // 第二群組重複操作防護(以單元名為鍵)

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

  function auditService(op, unit, result) {
    // 第二群組 audit:每次服務控制操作(含拒絕)一筆——時間、動詞、單元、
    // 結果/exit code。沿用同一份 audit log 落點。
    try {
      mkdirSync(logDir, { recursive: true });
      const line = `${new Date().toISOString()} | service:${op} | unit=${unit} | ${result}\n`;
      appendFileSync(auditLogPath, line, "utf8");
    } catch (error) {
      console.error(`audit log 寫入失敗: ${error.message}`);
    }
  }

  function ownedAlive() {
    return ownedChild !== null && ownedChild.exitCode === null;
  }

  async function runServiceControl(route) {
    const { op, unit } = route;
    // 縱深防禦:route 表本來就是兩個白名單的笛卡兒積,這裡再覆核一次枚舉
    // 成員——白名單外一律 400 + audit(提案 §2.2)。
    if (!SERVICE_OP_WHITELIST.includes(op) || !SERVICE_UNIT_WHITELIST.includes(unit)) {
      auditService(op, unit, "拒絕(白名單外,route 表遭污染——不應發生)");
      const error = new Error("白名單外的服務或操作");
      error.statusCode = 400;
      throw error;
    }
    if (serviceInFlight.has(unit)) {
      auditService(op, unit, "拒絕(該單元已有操作進行中)");
      const error = new Error(`${unit} 已有操作進行中,請等它完成後再試`);
      error.statusCode = 409;
      throw error;
    }
    serviceInFlight.add(unit);
    try {
      const { code, failure } = await new Promise((resolveExec) => {
        // 指令固定模板:`wsl -d Ubuntu systemctl --user <op> <unit>`。
        // <op>/<unit> 僅來自 SERVICE_ROUTES 凍結表;模板其餘部分是凍結
        // 常數;execFile 不經 shell,無任何其他參數入口。
        execFile(serviceCommand.bin, [...serviceCommand.args, op, unit], { timeout: serviceTimeoutMs }, (error) => {
          if (!error) resolveExec({ code: 0, failure: null });
          else if (typeof error.code === "number") resolveExec({ code: error.code, failure: null });
          else resolveExec({ code: null, failure: error.killed ? `timeout ${serviceTimeoutMs}ms` : error.message });
        });
      });
      if (code === 0) {
        auditService(op, unit, "成功 exit=0");
        return { ok: true, unit, op, exitCode: 0 };
      }
      auditService(op, unit, code === null ? `失敗(${failure})` : `失敗 exit=${code}`);
      const error = new Error(
        code === null
          ? `systemctl --user ${op} ${unit} 執行失敗(${failure})`
          : `systemctl --user ${op} ${unit} 失敗(exit=${code})`,
      );
      error.statusCode = 500;
      error.exitCode = code;
      throw error;
    } finally {
      serviceInFlight.delete(unit);
    }
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

    // ---- 白名單第二群組:WSL systemd 服務控制(v1.1)----
    // route 以全字串嚴格比對枚舉表;op/unit 取自表內凍結值,不從 URL 解析。
    // 前綴命中但不在枚舉表(白名單外的單元/動詞)→ 400 + audit。
    if (request.method === "POST" && request.url.startsWith(SERVICE_ROUTE_PREFIX)) {
      const route = SERVICE_ROUTES.get(request.url);
      if (!route) {
        auditService("-", "-", `拒絕(白名單外路徑:${String(request.url).slice(0, 120)})`);
        sendJson(response, 400, { ok: false, error: "白名單外的服務或操作(單元與動詞枚舉皆寫死)" }, origin);
        return;
      }
      try {
        sendJson(response, 200, await runServiceControl(route), origin);
      } catch (error) {
        sendJson(
          response,
          error.statusCode ?? 500,
          { ok: false, error: error.message || "服務控制操作失敗", exitCode: error.exitCode ?? null },
          origin,
        );
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
