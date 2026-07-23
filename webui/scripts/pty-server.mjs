// AgentOS PTY Server — ClaudeCode CLI 真終端機(P3,設計正本:
// docs/webui-pty-terminal-proposal.md v2,已核准)。
//
// 定位:這是整個 Web UI 風險最高的單一功能——瀏覽器可觸達的完整前台
// claude session。本檔案的每一條邊界都是提案的硬性 DoD:
//
// 1. 物理隔離:獨立 process、獨立 port(127.0.0.1:8801)。本檔案不 import
//    bridge.mjs、不 import 唯讀資料層的任何東西;唯讀側(dashboard/api.py、
//    bridge.mjs)也不 import 本檔案。與 8787/8799 零共用程式碼路徑。
// 2. 雙層連線授權(缺一不可):
//    (a) WS upgrade 的 Origin 白名單——僅 http://127.0.0.1:5173 與
//        http://localhost:5173(Vite dev server 的兩種本機寫法),精確
//        全字串比對;缺 Origin 或不在白名單一律拒絕(擋 T1 cross-site
//        WebSocket hijacking 與 T5 DNS rebinding)。
//    (b) launcher per-boot 隨機 token——經環境變數 AGENTOS_PTY_TOKEN 注入,
//        不落磁碟;比對走 sha256 + timingSafeEqual(constant-time)。
//    拒絕回應不洩漏「差在哪」(提案 §2 第 3 點);audit 記精確原因。
// 3. spawn 範圍寫死:只能 spawn claude CLI(啟動時解析一次絕對路徑並鎖定,
//    Windows 下處理 .exe/.cmd shim);引數陣列凍結為空(零使用者可控參數);
//    cwd 寫死 repo 根。claude process 結束=session 終止,不掉回任何 shell
//    (PTY 生命週期嚴格等於 claude process 的生命週期)。
// 4. 訊息面最小化:client→server 僅 stdin 與 resize 兩種訊息;未知訊息
//    類型→audit + 關閉連線。任何新訊息類型都需回提案增補(§2 第 4 點)。
// 5. 生命週期:同時最多 1 個 session;idle 30 分鐘無 stdin 先提示、再 5
//    分鐘終止——只計輸入,且終止前確認近期無輸出(長任務輸出中不誤殺);
//    WS 斷線 60 秒 grace 可重連,逾時終止;不做跨啟動 reattach。
// 6. audit log 只記事件(logs/webui_pty_audit.log),絕不落 stdin/stdout
//    內容——終端流未經 redact 掃描,可能含明文憑證(教訓一)。
import { createHash, timingSafeEqual } from "node:crypto";
import { createServer } from "node:http";
import { appendFileSync, mkdirSync, existsSync } from "node:fs";
import { delimiter, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { execFile } from "node:child_process";
import { WebSocketServer } from "ws";
import pty from "node-pty";

const webuiRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");

// ---- 凍結常數:bind 位址、port、Origin 白名單、spawn 目標(無參數化入口) ----
export const PTY_HOST = "127.0.0.1"; // 不提供參數化入口
export const PTY_PORT = 8801; // 與 8787(bridge)/8799(唯讀 API)物理區隔
// Origin 白名單:僅 Vite dev server 的兩種本機寫法,精確全字串比對。
// 刻意不用 regex 放寬到任意 port——比 bridge 的白名單更緊,因為本服務
// 的能力面是完整終端機。
export const ALLOWED_ORIGINS = Object.freeze([
  "http://127.0.0.1:5173",
  "http://localhost:5173",
]);
export const TOKEN_ENV = "AGENTOS_PTY_TOKEN";
// spawn 目標寫死 claude;引數凍結為空(v1 零參數,純前台互動,不帶 -p);
// cwd 寫死 repo 根(CLAUDE.md 自動載入,session 即 CoS)。
export const SPAWN_BIN_NAME = "claude";
export const SPAWN_ARGS = Object.freeze([]);
export const SPAWN_CWD = resolve(webuiRoot, ".."); // repo 根,寫死
// 生命週期常數(提案 §5.1–§5.3 拍板值)
export const IDLE_WARN_MS = 30 * 60 * 1000; // 30 分鐘無 stdin → 提示
export const IDLE_KILL_MS = 5 * 60 * 1000; // 提示後再 5 分鐘 → 終止
export const GRACE_MS = 60 * 1000; // WS 斷線 60 秒 grace
export const DEFAULT_LOG_DIR = join(webuiRoot, "..", "logs");
export const AUDIT_LOG_NAME = "webui_pty_audit.log";

// 啟動時解析一次 claude 絕對路徑並鎖定(提案 §3.1)。Windows 下依 PATHEXT
// 慣例找 claude.exe / claude.cmd;找不到就啟動失敗,不 fallback 到任何 shell。
export function resolveClaudeBin() {
  const exts = process.platform === "win32" ? [".exe", ".cmd", ".bat", ""] : [""];
  for (const dir of (process.env.PATH ?? "").split(delimiter)) {
    if (!dir) continue;
    for (const ext of exts) {
      const candidate = join(dir, `${SPAWN_BIN_NAME}${ext}`);
      if (existsSync(candidate)) return candidate;
    }
  }
  throw new Error("找不到 claude CLI(PATH 內無 claude.exe/claude.cmd),PTY server 拒絕啟動");
}

function sha256(value) {
  return createHash("sha256").update(String(value), "utf8").digest();
}

// constant-time 比對:先 sha256 等長化,再 timingSafeEqual(不因長度提前返回)
function tokenMatches(expected, provided) {
  if (typeof provided !== "string" || provided.length === 0) return false;
  return timingSafeEqual(sha256(expected), sha256(provided));
}

// createPtyServer():production 由本檔案的 main 區塊以「零參數」呼叫,全部
// 走上面的凍結常數;options 僅供測試注入假 claude fixture、臨時目錄與短
// timeout——注入點只存在於 process 內部,HTTP/WS 介面完全碰不到。
export function createPtyServer(options = {}) {
  const command = options.command ?? { bin: resolveClaudeBin(), args: SPAWN_ARGS };
  const port = options.port ?? PTY_PORT;
  const logDir = options.logDir ?? DEFAULT_LOG_DIR;
  const token = options.token ?? process.env[TOKEN_ENV] ?? "";
  const idleWarnMs = options.idleWarnMs ?? IDLE_WARN_MS;
  const idleKillMs = options.idleKillMs ?? IDLE_KILL_MS;
  const graceMs = options.graceMs ?? GRACE_MS;
  const spawnCwd = options.spawnCwd ?? SPAWN_CWD;
  const auditLogPath = join(logDir, AUDIT_LOG_NAME);

  if (typeof token !== "string" || token.length < 32) {
    // token 由 launcher per-boot 產生(≥32 bytes hex);沒有 token 就不啟動,
    // 不存在「無授權模式」。
    throw new Error(`缺少 ${TOKEN_ENV}(需經 npm run local 啟動,token 為 per-boot 隨機值)`);
  }

  // 同時最多 1 個 session(提案 §5.1)。session 物件是 ownership 的唯一依據。
  let session = null;

  function audit(event, pid, detail) {
    // 只記事件:時間、事件、PID、結果/原因——絕不寫入 stdin/stdout 內容
    // (終端流未經 redact 掃描;detail 一律是固定文字+數字,不含 payload)。
    try {
      mkdirSync(logDir, { recursive: true });
      const line = `${new Date().toISOString()} | ${event} | pid=${pid ?? "-"} | ${detail}\n`;
      appendFileSync(auditLogPath, line, "utf8");
    } catch (error) {
      console.error(`pty audit log 寫入失敗: ${error.message}`);
    }
  }

  function sendJson(ws, payload) {
    if (ws && ws.readyState === ws.OPEN) ws.send(JSON.stringify(payload));
  }

  async function ensureProcessDead(pid) {
    // 先溫和(pty.kill 關閉 ConPTY)後強制(taskkill 樹殺)——提案 §5.3。
    // taskkill 的 PID 來自 session 自有紀錄,絕非請求輸入。
    if (!Number.isInteger(pid)) return;
    await new Promise((r) => setTimeout(r, 300));
    try {
      process.kill(pid, 0); // 還活著才需要強制
    } catch {
      return; // 已結束
    }
    if (process.platform === "win32") {
      await new Promise((r) => {
        execFile("taskkill", ["/PID", String(pid), "/T", "/F"], () => r());
      });
    } else {
      try {
        process.kill(pid, "SIGKILL");
      } catch {
        /* 已結束 */
      }
    }
  }

  function clearTimers(s) {
    for (const key of ["idleWarnTimer", "idleKillTimer", "graceTimer"]) {
      if (s[key]) {
        clearTimeout(s[key]);
        s[key] = null;
      }
    }
  }

  function terminateSession(reason, { notifyClient = true } = {}) {
    if (!session) return;
    const s = session;
    session = null;
    clearTimers(s);
    s.terminated = true;
    if (notifyClient && s.ws) {
      sendJson(s.ws, { type: "exit", reason });
      try {
        s.ws.close(1000, "session terminated");
      } catch {
        /* 已關閉 */
      }
    }
    const pid = s.pid;
    try {
      process.kill(pid, 0); // 先確認還活著,避免 node-pty 對已結束 process 的 kill 噪音
      s.pty.kill();
    } catch {
      /* process 已結束 */
    }
    audit("terminate", pid, `原因: ${reason}`);
    void ensureProcessDead(pid);
  }

  function armIdleKill(s) {
    // 提示後再等 idleKillMs;到點時「只計輸入」但「長任務輸出中不誤殺」:
    // 若期間有 stdin → resetIdle 已解除;若無 stdin 但 PTY 仍在輸出
    // (長任務執行中)→ 不終止,延後再查,直到輸出也靜默滿一個窗口。
    s.idleKillTimer = setTimeout(function check() {
      if (!session || session !== s) return;
      if (Date.now() - s.lastOutputAt < idleKillMs) {
        s.idleKillTimer = setTimeout(check, idleKillMs);
        return;
      }
      audit("idle-timeout", s.pid, `無 stdin 逾 ${idleWarnMs + idleKillMs}ms 且輸出靜默,終止 session`);
      terminateSession("idle-timeout");
    }, idleKillMs);
  }

  function resetIdle(s) {
    // 只有 stdin 會走到這裡——輸出不重置 idle 計時(提案 §5.2 只計輸入)
    if (s.idleWarnTimer) clearTimeout(s.idleWarnTimer);
    if (s.idleKillTimer) clearTimeout(s.idleKillTimer);
    s.idleKillTimer = null;
    s.idleWarnTimer = setTimeout(() => {
      if (!session || session !== s) return;
      audit("idle-warning", s.pid, `無 stdin 輸入已 ${idleWarnMs}ms,送出提示`);
      // 提示只送往前端終端顯示(server→client 訊息),不注入 claude stdin
      sendJson(s.ws, {
        type: "output",
        data: `\r\n\x1b[33m[AgentOS] 閒置提醒:已 ${Math.round(idleWarnMs / 60000)} 分鐘無輸入;再 ${Math.round(idleKillMs / 60000)} 分鐘無輸入(且無執行中輸出)將終止 session。\x1b[0m\r\n`,
      });
      armIdleKill(s);
    }, idleWarnMs);
  }

  function spawnSession(ws) {
    // 唯一的 spawn 入口:upgrade 全數驗證通過後。指令=啟動時鎖定的 claude
    // 絕對路徑;引數=凍結空陣列;cwd=repo 根。client 沒有任何管道影響這三者。
    const child = pty.spawn(command.bin, [...command.args], {
      name: "xterm-256color",
      cols: 120,
      rows: 30,
      cwd: spawnCwd,
      env: { ...process.env, ...(options.spawnEnv ?? {}) },
    });
    const s = {
      pty: child,
      pid: child.pid,
      ws,
      idleWarnTimer: null,
      idleKillTimer: null,
      graceTimer: null,
      lastOutputAt: 0,
      terminated: false,
    };
    session = s;
    audit("spawn", child.pid, `成功(claude session 啟動,cwd=${spawnCwd})`);

    child.onData((data) => {
      s.lastOutputAt = Date.now();
      // 不落地、不緩存:輸出只即時轉發給已連線的 client(斷線 grace 期間
      // 的輸出直接丟棄——不做 server 端 buffer/reattach,提案 §0.3)
      sendJson(s.ws, { type: "output", data });
    });

    child.onExit(({ exitCode }) => {
      // claude process 結束=session 終止,不掉回任何 shell(提案 §3.1):
      // PTY 生命週期嚴格等於 claude process,這裡只剩清理與通知。
      if (s.terminated) return;
      s.terminated = true;
      if (session === s) session = null;
      clearTimers(s);
      audit("exit", s.pid, `claude process 結束 exit=${exitCode},session 終止`);
      sendJson(s.ws, { type: "exit", reason: "claude-exit", code: exitCode });
      if (s.ws) {
        try {
          s.ws.close(1000, "claude exited");
        } catch {
          /* 已關閉 */
        }
      }
    });

    resetIdle(s);
    return s;
  }

  function attachWs(s, ws, isReconnect) {
    s.ws = ws;
    if (isReconnect && s.graceTimer) {
      clearTimeout(s.graceTimer);
      s.graceTimer = null;
      audit("reconnect", s.pid, "grace 內重連,接回既有 session");
    }

    ws.on("message", (raw, isBinary) => {
      // ---- 訊息面最小化:僅 stdin / resize 兩種(提案 §2 第 4 點) ----
      let message = null;
      if (!isBinary) {
        try {
          message = JSON.parse(raw.toString("utf8"));
        } catch {
          message = null;
        }
      }
      if (message && message.type === "stdin" && typeof message.data === "string") {
        s.pty.write(message.data);
        resetIdle(s); // 只有 stdin 重置 idle
        return;
      }
      if (
        message &&
        message.type === "resize" &&
        Number.isInteger(message.cols) &&
        Number.isInteger(message.rows) &&
        message.cols > 0 &&
        message.cols <= 1000 &&
        message.rows > 0 &&
        message.rows <= 1000
      ) {
        try {
          s.pty.resize(message.cols, message.rows);
        } catch {
          /* resize 失敗不致命 */
        }
        return; // resize 不記 audit(提案 §5.4)、不重置 idle
      }
      // 未知訊息類型:audit + 關閉連線(不記 payload 內容,只記 type 字面
      // 的長度受限摘要——type 本身不是終端資料)
      const typeLabel = message && typeof message.type === "string" ? message.type.slice(0, 32) : "(非 JSON/無 type)";
      audit("protocol-violation", s.pid, `未知訊息類型: ${typeLabel},關閉連線`);
      try {
        ws.close(1008, "unknown message type");
      } catch {
        /* 已關閉 */
      }
    });

    ws.on("close", () => {
      if (!session || session !== s || s.ws !== ws || s.terminated) return;
      s.ws = null;
      audit("disconnect", s.pid, `WS 斷線,grace ${graceMs}ms 內可重連`);
      s.graceTimer = setTimeout(() => {
        if (!session || session !== s) return;
        audit("grace-expired", s.pid, `斷線逾 ${graceMs}ms 未重連,終止 session`);
        terminateSession("disconnect-grace-expired", { notifyClient: false });
      }, graceMs);
    });
  }

  // ---- HTTP 層:僅 GET /health(狀態探測,供 UI 顯示服務狀態);其他 404 ----
  const server = createServer((request, response) => {
    const origin = request.headers.origin || "";
    if (origin && !ALLOWED_ORIGINS.includes(origin)) {
      response.writeHead(403, { "Content-Type": "application/json; charset=utf-8" });
      response.end(JSON.stringify({ ok: false, error: "connection rejected" }));
      return;
    }
    if (origin) {
      response.setHeader("Access-Control-Allow-Origin", origin);
      response.setHeader("Vary", "Origin");
    }
    if (request.method === "GET" && request.url === "/health") {
      response.writeHead(200, { "Content-Type": "application/json; charset=utf-8" });
      response.end(JSON.stringify({ ok: true, sessionActive: session !== null }));
      return;
    }
    response.writeHead(404, { "Content-Type": "application/json; charset=utf-8" });
    response.end(JSON.stringify({ ok: false, error: "Not found" }));
  });

  const wss = new WebSocketServer({ noServer: true });

  function rejectUpgrade(socket, statusLine, reason, pid = null) {
    audit("connect-reject", pid, reason);
    // 拒絕回應不洩漏「差在哪」(提案 §2 第 3 點);session 上限例外——
    // §5.1 要求明確錯誤訊息。
    socket.write(`HTTP/1.1 ${statusLine}\r\nConnection: close\r\nContent-Length: 0\r\n\r\n`);
    socket.destroy();
  }

  server.on("upgrade", (request, socket, head) => {
    // 驗證順序(提案 §2):Origin 白名單 → token(constant-time)→ session 上限
    const origin = request.headers.origin || "";
    if (!ALLOWED_ORIGINS.includes(origin)) {
      rejectUpgrade(socket, "403 Forbidden", `拒絕(Origin 不在白名單: ${origin ? "非本機 UI" : "缺 Origin"})`);
      return;
    }
    let provided = null;
    try {
      // 只讀 token 一個參數,其他 query 一律忽略
      provided = new URL(request.url, `http://${PTY_HOST}`).searchParams.get("token");
    } catch {
      provided = null;
    }
    if (!tokenMatches(token, provided)) {
      rejectUpgrade(socket, "403 Forbidden", `拒絕(token ${provided ? "錯誤" : "缺失"})`);
      return;
    }
    const activeAttached = session !== null && session.ws !== null;
    if (activeAttached) {
      // 同時最多 1 個 session:已有連線中的 session → 明確拒絕(§5.1)
      rejectUpgrade(socket, "409 Conflict", "拒絕(已達 session 上限 1,既有 session 連線中)", session.pid);
      return;
    }

    wss.handleUpgrade(request, socket, head, (ws) => {
      if (session !== null) {
        // 斷線 grace 中:同 token 重連,接回既有 claude process
        attachWs(session, ws, true);
        sendJson(ws, { type: "ready", pid: session.pid, reconnected: true });
      } else {
        const s = spawnSession(ws);
        attachWs(s, ws, false);
        sendJson(ws, { type: "ready", pid: s.pid, reconnected: false });
      }
    });
  });

  function listen() {
    return new Promise((resolveListen, rejectListen) => {
      server.once("error", rejectListen);
      // bind 寫死 127.0.0.1(PTY_HOST 常數),無參數化入口
      server.listen(port, PTY_HOST, () => {
        audit("server-start", process.pid, `listening ${PTY_HOST}:${port}`);
        resolveListen(server.address());
      });
    });
  }

  async function shutdown() {
    // launcher 關閉/SIGTERM:先終止 child 再退出,不留孤兒 process(§5.3)
    if (session) terminateSession("server-shutdown");
    for (const client of wss.clients) {
      try {
        client.terminate();
      } catch {
        /* 已關閉 */
      }
    }
    await new Promise((r) => wss.close(r));
    await new Promise((r) => server.close(r));
    audit("server-stop", process.pid, "PTY server 關閉");
  }

  return {
    listen,
    shutdown,
    server,
    auditLogPath,
    get sessionPid() {
      return session ? session.pid : null;
    },
    get sessionAttached() {
      return session !== null && session.ws !== null;
    },
  };
}

// ---- production 入口:node scripts/pty-server.mjs(由 agentos-local.mjs spawn) ----
const isMain = process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isMain) {
  let ptyServer;
  try {
    ptyServer = createPtyServer(); // 零參數:全部走凍結常數 + 環境變數 token
  } catch (error) {
    console.error(`PTY server 啟動失敗: ${error.message}`);
    process.exit(1);
  }
  await ptyServer.listen();
  console.log(`AgentOS PTY server: ws://${PTY_HOST}:${PTY_PORT}(ClaudeCode CLI)`);
  console.log(`audit log: ${ptyServer.auditLogPath}`);

  let shuttingDown = false;
  async function shutdownAndExit() {
    if (shuttingDown) return;
    shuttingDown = true;
    await ptyServer.shutdown();
    process.exit(0);
  }
  process.on("SIGINT", () => void shutdownAndExit());
  process.on("SIGTERM", () => void shutdownAndExit());
}
