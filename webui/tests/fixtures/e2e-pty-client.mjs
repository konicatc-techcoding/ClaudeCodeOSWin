// P3 真實 E2E client(一次性驗證用,不屬於 npm test 套件):
// 對「npm run local 啟動的真實 PTY server + Vite」做端對端驗證——
// 1) Vite 200;2) token 經 Vite 轉譯注入前端模組(與瀏覽器取得途徑相同);
// 3) 錯誤憑證被拒;4) 正確雙證 → spawn 真實 claude、I/O 通(不執行任何
//    實質指令,/exit 立即退出);5) claude 退出=session 終止。
// 用法: node tests/fixtures/e2e-pty-client.mjs
import WebSocket from "ws";

const VITE = "http://127.0.0.1:5173";
const ORIGIN = "http://127.0.0.1:5173";
const log = (...a) => console.log("[e2e]", ...a);
const fail = (msg) => {
  console.error("[e2e] FAIL:", msg);
  process.exit(1);
};

// 1) Vite dev server 起來了
const page = await fetch(`${VITE}/`);
if (page.status !== 200) fail(`Vite 首頁非 200: ${page.status}`);
log("Vite 首頁 200 OK");

// 2) 以瀏覽器同樣的途徑取得 token:抓 Vite 轉譯後的 Terminal 模組
const mod = await fetch(`${VITE}/src/views/Terminal.tsx`);
if (mod.status !== 200) fail(`Terminal 模組非 200: ${mod.status}`);
const code = await mod.text();
const tokenMatch = code.match(/"([0-9a-f]{64})"/);
if (!tokenMatch) fail("轉譯後模組內找不到 64-hex token(VITE_AGENTOS_PTY_TOKEN 注入失敗)");
const token = tokenMatch[1];
log("token 已經 Vite env 注入前端模組(64 hex,per-boot)");

// 3) 授權負面案例:壞 Origin / 壞 token
async function expectReject(url, origin, label) {
  const status = await new Promise((resolve) => {
    const ws = new WebSocket(url, origin ? { origin } : {});
    ws.once("open", () => resolve("open"));
    ws.once("unexpected-response", (_q, res) => {
      resolve(res.statusCode);
      ws.terminate();
    });
    ws.once("error", () => {});
  });
  if (status === "open") fail(`${label} 竟然連上了`);
  log(`${label} → 被拒(HTTP ${status})`);
}
await expectReject(`ws://127.0.0.1:8801/?token=${token}`, "http://evil.example:5173", "非白名單 Origin");
await expectReject(`ws://127.0.0.1:8801/?token=${"0".repeat(64)}`, ORIGIN, "錯誤 token");

// 4) 正確雙證 → 真實 claude session;只確認 I/O 通即 /exit
const ws = new WebSocket(`ws://127.0.0.1:8801/?token=${token}`, { origin: ORIGIN });
let text = "";
let exitMsg = null;
ws.on("message", (raw) => {
  try {
    const m = JSON.parse(raw.toString("utf8"));
    if (m.type === "output") text += m.data;
    else if (m.type === "exit") exitMsg = m;
    else if (m.type === "ready") log(`ready: pid=${m.pid} reconnected=${m.reconnected}`);
  } catch {}
});
await new Promise((resolve, reject) => {
  ws.once("open", resolve);
  ws.once("unexpected-response", (_q, res) => reject(new Error(`upgrade 被拒 ${res.statusCode}`)));
}).catch((e) => fail(e.message));
log("WS 已連上,等待 claude TUI 輸出…");
ws.send(JSON.stringify({ type: "resize", cols: 120, rows: 30 }));

const deadline = Date.now() + 60000;
while (Date.now() < deadline && text.length < 200) await new Promise((r) => setTimeout(r, 250));
if (text.length < 200) fail(`60 秒內 claude 輸出不足(len=${text.length})`);
const hasAnsi = /\x1b\[/.test(text);
log(`claude 輸出已到達(bytes=${text.length},含 ANSI 序列=${hasAnsi})`);

// 等 TUI 輸出靜默(初始化完成)再退出;不執行任何實質指令
let lastLen = -1;
const settleDeadline = Date.now() + 90000;
while (Date.now() < settleDeadline) {
  await new Promise((r) => setTimeout(r, 3000));
  if (text.length === lastLen) break;
  lastLen = text.length;
}
log(`TUI 輸出已靜默(bytes=${text.length}),送出 /exit`);
ws.send(JSON.stringify({ type: "stdin", data: "/exit" }));
await new Promise((r) => setTimeout(r, 2000));
ws.send(JSON.stringify({ type: "stdin", data: "\r" }));

let exitDeadline = Date.now() + 20000;
while (Date.now() < exitDeadline && exitMsg === null) await new Promise((r) => setTimeout(r, 250));
if (!exitMsg) {
  // 後備:Ctrl+C ×2(claude 的標準離開途徑之一)
  log("/exit 未生效,改送 Ctrl+C ×2");
  ws.send(JSON.stringify({ type: "stdin", data: "" }));
  await new Promise((r) => setTimeout(r, 600));
  ws.send(JSON.stringify({ type: "stdin", data: "" }));
  exitDeadline = Date.now() + 20000;
  while (Date.now() < exitDeadline && exitMsg === null) await new Promise((r) => setTimeout(r, 250));
}
if (!exitMsg) fail("退出指令後仍未收到 exit 訊息");
log(`claude 退出 → session 終止(reason=${exitMsg.reason}, code=${exitMsg.code})`);

// 5) 收尾:health 應回報無 active session
await new Promise((r) => setTimeout(r, 800));
const health = await (await fetch("http://127.0.0.1:8801/health")).json();
if (health.sessionActive) fail("session 結束後 health 仍回報 active");
log("health: session 已清空,無殘留");
log("E2E PASS");
process.exit(0);
