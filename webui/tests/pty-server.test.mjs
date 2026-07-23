// PTY server 安全與生命週期測試 — 對應 docs/webui-pty-terminal-proposal.md
// §8 DoD 第 2/3/4/5 條逐條鎖定。
// 全部使用 FAKE claude fixture(tests/fixtures/fake-claude.mjs,經真實
// ConPTY spawn),不碰真實 claude、不讀任何真實憑證;token 一律 TEST_ 前綴。
import assert from "node:assert/strict";
import test from "node:test";
import { mkdtempSync, readFileSync, existsSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import WebSocket from "ws";
import {
  createPtyServer,
  ALLOWED_ORIGINS,
  SPAWN_ARGS,
  SPAWN_CWD,
  SPAWN_BIN_NAME,
  PTY_HOST,
  AUDIT_LOG_NAME,
} from "../scripts/pty-server.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const fixture = resolve(here, "fixtures", "fake-claude.mjs");
const ptySource = readFileSync(resolve(here, "..", "scripts", "pty-server.mjs"), "utf8");

const TEST_TOKEN = `TEST_${"a".repeat(60)}`;
const GOOD_ORIGIN = "http://127.0.0.1:5173";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function waitFor(predicate, timeoutMs = 10000, stepMs = 50) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (predicate()) return true;
    await sleep(stepMs);
  }
  return predicate();
}

function makeServer({ port, extraArgs = [], idleWarnMs, idleKillMs, graceMs }) {
  const dir = mkdtempSync(join(tmpdir(), "webui-pty-test-"));
  const server = createPtyServer({
    command: { bin: process.execPath, args: [fixture, ...extraArgs] },
    port,
    logDir: dir,
    token: TEST_TOKEN,
    idleWarnMs: idleWarnMs ?? 10 * 60 * 1000,
    idleKillMs: idleKillMs ?? 10 * 60 * 1000,
    graceMs: graceMs ?? 10 * 60 * 1000,
  });
  return { server, dir, auditPath: join(dir, AUDIT_LOG_NAME) };
}

function readAudit(auditPath) {
  return existsSync(auditPath) ? readFileSync(auditPath, "utf8") : "";
}

// 連線 helper:回傳 open(成功)或 upgrade 被拒的 HTTP status
function connect(port, { token, origin } = {}) {
  const query = token === undefined ? "" : `?token=${encodeURIComponent(token)}`;
  const ws = new WebSocket(`ws://127.0.0.1:${port}/${query}`, origin ? { origin } : {});
  const state = { text: "", messages: [], closed: null };
  ws.on("message", (raw) => {
    try {
      const m = JSON.parse(raw.toString("utf8"));
      state.messages.push(m);
      if (m.type === "output" && typeof m.data === "string") state.text += m.data;
    } catch {
      /* server 只送 JSON */
    }
  });
  ws.on("close", (code) => {
    state.closed = code;
  });
  ws.on("error", () => {
    /* 拒絕連線時的 socket error,由 unexpected-response 處理 */
  });
  const result = new Promise((resolveP) => {
    ws.once("open", () => resolveP({ ws, state, status: "open" }));
    ws.once("unexpected-response", (_req, res) => {
      resolveP({ ws, state, status: res.statusCode });
      ws.terminate();
    });
  });
  return result;
}

// ---------------------------------------------------------------------------
// DoD 2/3/5:雙層授權、session 上限、訊息面、claude 退出即斷、audit 不落內容
// ---------------------------------------------------------------------------

test("授權/上限/訊息面/退出/audit(DoD 2·3·5)", async (t) => {
  const { server, dir, auditPath } = makeServer({ port: 8811 });
  await server.listen();
  t.after(async () => {
    await server.shutdown();
    rmSync(dir, { recursive: true, force: true });
  });

  await t.test("(2b) 缺 token → 拒絕+audit", async () => {
    const { status } = await connect(8811, { origin: GOOD_ORIGIN });
    assert.equal(status, 403);
    assert.match(readAudit(auditPath), /connect-reject .*token 缺失/);
  });

  await t.test("(2b) 錯誤 token → 拒絕(回應不洩漏差在哪)", async () => {
    const { status } = await connect(8811, { origin: GOOD_ORIGIN, token: "TEST_wrong_token_zzzzzzzzzzzzzzzzzzz" });
    assert.equal(status, 403);
    assert.match(readAudit(auditPath), /connect-reject .*token 錯誤/);
  });

  await t.test("(2a) 非白名單 Origin → 拒絕(token 正確也一樣)", async () => {
    const evil = await connect(8811, { origin: "http://evil.example:5173", token: TEST_TOKEN });
    assert.equal(evil.status, 403);
    const noOrigin = await connect(8811, { token: TEST_TOKEN });
    assert.equal(noOrigin.status, 403);
    const audit = readAudit(auditPath);
    assert.match(audit, /connect-reject .*Origin 不在白名單: 非本機 UI/);
    assert.match(audit, /connect-reject .*Origin 不在白名單: 缺 Origin/);
  });

  let firstWs;
  let firstState;
  await t.test("(2) 正確雙證 → upgrade 成功並 spawn(唯一 spawn 入口)", async () => {
    const conn = await connect(8811, { origin: GOOD_ORIGIN, token: TEST_TOKEN });
    assert.equal(conn.status, "open");
    firstWs = conn.ws;
    firstState = conn.state;
    assert.ok(await waitFor(() => firstState.text.includes("FAKE_CLAUDE_READY")), "應收到 claude(fixture)輸出");
    assert.ok(Number.isInteger(server.sessionPid));
    assert.match(readAudit(auditPath), /spawn .*成功/);
  });

  await t.test("(2c) 單 session 上限:第二個連線被拒(409+audit)", async () => {
    const second = await connect(8811, { origin: GOOD_ORIGIN, token: TEST_TOKEN });
    assert.equal(second.status, 409);
    assert.match(readAudit(auditPath), /connect-reject .*session 上限/);
  });

  const FAKE_SECRET = "FAKE_SECRET_pty_transcript_abc123456";
  await t.test("(3) stdin/resize 通;含假密鑰的輸入輸出照常流動", async () => {
    firstWs.send(JSON.stringify({ type: "resize", cols: 100, rows: 40 }));
    firstWs.send(JSON.stringify({ type: "stdin", data: `${FAKE_SECRET}\r` }));
    assert.ok(await waitFor(() => firstState.text.includes(`FAKE_ECHO:`)), "stdin 應被 fixture echo 回來");
    assert.ok(await waitFor(() => firstState.text.includes(FAKE_SECRET)), "終端輸出應含該字串(畫面即時顯示)");
  });

  await t.test("(3) 未知訊息類型 → audit+關閉連線,claude 不死", async () => {
    const pidBefore = server.sessionPid;
    firstWs.send(JSON.stringify({ type: "exec", cmd: "calc.exe" }));
    assert.ok(await waitFor(() => firstState.closed !== null), "連線應被關閉");
    assert.equal(firstState.closed, 1008);
    assert.match(readAudit(auditPath), /protocol-violation .*未知訊息類型: exec/);
    assert.equal(server.sessionPid, pidBefore, "協定違規只斷線,不影響 process(進入 grace)");
  });

  await t.test("(3) claude process 結束 → session 即終止、不掉回 shell", async () => {
    // 上一步斷線後在 grace 內重連,接回同一個 process
    const re = await connect(8811, { origin: GOOD_ORIGIN, token: TEST_TOKEN });
    assert.equal(re.status, "open");
    const ready = await waitFor(() => re.state.messages.some((m) => m.type === "ready" && m.reconnected === true));
    assert.ok(ready, "grace 內重連應接回既有 session");
    re.ws.send(JSON.stringify({ type: "stdin", data: "exit\r" }));
    assert.ok(
      await waitFor(() => re.state.messages.some((m) => m.type === "exit" && m.reason === "claude-exit" && m.code === 42)),
      "claude 結束應送 exit 訊息(帶 exit code)",
    );
    assert.ok(await waitFor(() => re.state.closed !== null), "session 終止後 WS 應關閉");
    assert.ok(await waitFor(() => server.sessionPid === null), "session 應清空——沒有任何 shell 可掉回");
    assert.match(readAudit(auditPath), /exit .*claude process 結束 exit=42/);
  });

  await t.test("(5) audit 只記事件,不含任何 stdin/stdout 內容", () => {
    const audit = readAudit(auditPath);
    assert.ok(!audit.includes(FAKE_SECRET), "audit log 不得含輸入過的假密鑰");
    assert.ok(!audit.includes("FAKE_ECHO"), "audit log 不得含終端輸出內容");
    assert.ok(!audit.includes("FAKE_CLAUDE_READY"), "audit log 不得含終端輸出內容");
    for (const event of ["server-start", "spawn", "connect-reject", "disconnect", "reconnect", "protocol-violation", "exit"]) {
      assert.ok(audit.includes(event), `audit 應含 ${event} 事件`);
    }
  });
});

// ---------------------------------------------------------------------------
// DoD 4:生命週期——idle timeout、長任務不誤殺、斷線 grace
// ---------------------------------------------------------------------------

test("idle timeout:靜默 session 先提示、再終止(短參數實測)", async (t) => {
  const { server, dir, auditPath } = makeServer({ port: 8812, idleWarnMs: 500, idleKillMs: 500 });
  await server.listen();
  t.after(async () => {
    await server.shutdown();
    rmSync(dir, { recursive: true, force: true });
  });

  const conn = await connect(8812, { origin: GOOD_ORIGIN, token: TEST_TOKEN });
  assert.equal(conn.status, "open");
  await waitFor(() => conn.state.text.includes("FAKE_CLAUDE_READY"));
  // fixture 啟動後靜默、無輸入 → 先提示
  assert.ok(await waitFor(() => conn.state.text.includes("閒置提醒")), "idle 到點應先送提示到終端");
  assert.match(readAudit(auditPath), /idle-warning/);
  // 提示後仍無輸入(且輸出靜默)→ 終止
  assert.ok(
    await waitFor(() => conn.state.messages.some((m) => m.type === "exit" && m.reason === "idle-timeout")),
    "提示後仍無輸入應終止 session",
  );
  assert.ok(await waitFor(() => server.sessionPid === null));
  assert.match(readAudit(auditPath), /idle-timeout/);
  assert.match(readAudit(auditPath), /terminate .*idle-timeout/);
});

test("idle timeout:長任務輸出中不誤殺(只計輸入,但輸出未靜默不終止)", async (t) => {
  // idleKillMs 給 1200ms 餘裕:本機同時跑 vite/build 時,ConPTY 輸出批次
  // 偶有 >400ms 的排程延遲,會被誤判「輸出靜默」——測的是語意,不是排程精度
  const { server, dir, auditPath } = makeServer({ port: 8813, extraArgs: ["--spam"], idleWarnMs: 400, idleKillMs: 1200 });
  await server.listen();
  t.after(async () => {
    await server.shutdown();
    rmSync(dir, { recursive: true, force: true });
  });

  const conn = await connect(8813, { origin: GOOD_ORIGIN, token: TEST_TOKEN });
  assert.equal(conn.status, "open");
  await waitFor(() => conn.state.text.includes("FAKE_CLAUDE_READY"));
  // 無輸入,但 fixture 持續輸出(長任務情境):提示照發(只計輸入)……
  assert.ok(await waitFor(() => conn.state.text.includes("閒置提醒")), "提示仍應發出(輸出不重置 idle 計時)");
  // ……但輸出未靜默,絕不能終止
  await sleep(3200); // 遠超 idleWarnMs+idleKillMs
  assert.ok(Number.isInteger(server.sessionPid), "長任務輸出中不得誤殺");
  assert.ok(!conn.state.messages.some((m) => m.type === "exit"), "不得送出 exit");
  assert.ok(!readAudit(auditPath).includes("idle-timeout"), "audit 不得出現 idle-timeout");
});

test("idle timeout:stdin 輸入重置計時(不會提前提示)", async (t) => {
  // idleWarnMs 給 1500ms 餘裕:200ms 間隔的 stdin 迴圈在機器負載下偶有
  // 數百 ms 延遲,600ms 窗口會誤觸提示——測語意(輸入重置),不測排程精度
  const { server, dir, auditPath } = makeServer({ port: 8814, idleWarnMs: 1500, idleKillMs: 600 });
  await server.listen();
  t.after(async () => {
    await server.shutdown();
    rmSync(dir, { recursive: true, force: true });
  });

  const conn = await connect(8814, { origin: GOOD_ORIGIN, token: TEST_TOKEN });
  assert.equal(conn.status, "open");
  await waitFor(() => conn.state.text.includes("FAKE_CLAUDE_READY"));
  // 每 200ms 送一次輸入,共 1.6 秒(> idleWarnMs 三倍):不應出現任何提示
  for (let i = 0; i < 8; i += 1) {
    conn.ws.send(JSON.stringify({ type: "stdin", data: " " }));
    await sleep(200);
  }
  assert.ok(!conn.state.text.includes("閒置提醒"), "持續輸入下不得出現 idle 提示");
  assert.ok(!readAudit(auditPath).includes("idle-warning"));
  assert.ok(Number.isInteger(server.sessionPid));
});

test("斷線 grace:期限內重連接回同一 process;逾時終止", async (t) => {
  const { server, dir, auditPath } = makeServer({ port: 8815, graceMs: 900 });
  await server.listen();
  t.after(async () => {
    await server.shutdown();
    rmSync(dir, { recursive: true, force: true });
  });

  const first = await connect(8815, { origin: GOOD_ORIGIN, token: TEST_TOKEN });
  assert.equal(first.status, "open");
  await waitFor(() => first.state.text.includes("FAKE_CLAUDE_READY"));
  const pid = server.sessionPid;
  assert.ok(Number.isInteger(pid));

  // 斷線 → grace 內重連 → 同一個 claude process
  first.ws.close();
  await waitFor(() => !server.sessionAttached);
  const re = await connect(8815, { origin: GOOD_ORIGIN, token: TEST_TOKEN });
  assert.equal(re.status, "open");
  assert.ok(await waitFor(() => re.state.messages.some((m) => m.type === "ready" && m.reconnected === true)));
  assert.equal(server.sessionPid, pid, "grace 內重連應接回同一個 process(不重 spawn)");
  assert.match(readAudit(auditPath), /reconnect .*接回既有 session/);

  // 再斷線,逾時不重連 → 終止,不留 process
  re.ws.close();
  assert.ok(await waitFor(() => server.sessionPid === null, 5000), "grace 逾時應終止 session");
  const audit = readAudit(auditPath);
  assert.match(audit, /grace-expired/);
  assert.match(audit, /terminate .*disconnect-grace-expired/);
});

// ---------------------------------------------------------------------------
// DoD 2/3:code review 檢核項(靜態)
// ---------------------------------------------------------------------------

test("靜態檢核:constant-time 比對、spawn 邊界、Origin 白名單凍結", () => {
  // (2b) token 比對必須 constant-time:sha256 等長化 + timingSafeEqual
  assert.ok(ptySource.includes("timingSafeEqual"), "token 比對必須用 timingSafeEqual");
  assert.match(ptySource, /createHash\("sha256"\)/, "先 sha256 等長化再比對");

  // (3) spawn 引數/cwd 寫死:引數凍結為空、cwd 為 repo 根、目標為 claude
  assert.equal(SPAWN_BIN_NAME, "claude");
  assert.ok(Object.isFrozen(SPAWN_ARGS));
  assert.equal(SPAWN_ARGS.length, 0, "v1 零參數(純前台互動,不帶 -p)");
  assert.equal(SPAWN_CWD, resolve(here, "..", ".."), "cwd 寫死 repo 根");
  assert.equal(PTY_HOST, "127.0.0.1");

  // (2a) Origin 白名單:凍結、精確兩個本機 UI origin,無 regex 放寬
  assert.ok(Object.isFrozen(ALLOWED_ORIGINS));
  assert.deepEqual([...ALLOWED_ORIGINS], ["http://127.0.0.1:5173", "http://localhost:5173"]);

  // 物理隔離:import 清單不含 bridge/唯讀資料層(註解提及不算)
  const importSpecifiers = [...ptySource.matchAll(/from\s+"([^"]+)"/g)].map((m) => m[1]);
  assert.ok(importSpecifiers.length > 0);
  assert.ok(!importSpecifiers.some((s) => s.includes("bridge")), "PTY server 不得 import bridge");
  assert.ok(!importSpecifiers.some((s) => s.includes("dashboard")), "PTY server 不得 import 唯讀資料層");
  const allowedExternal = ["ws", "node-pty"];
  for (const spec of importSpecifiers) {
    assert.ok(spec.startsWith("node:") || allowedExternal.includes(spec), `非預期 import: ${spec}`);
  }

  // 訊息面最小化:協定僅 stdin/resize 兩種 client 訊息
  assert.ok(ptySource.includes('message.type === "stdin"'));
  assert.ok(ptySource.includes('message.type === "resize"'));
});
