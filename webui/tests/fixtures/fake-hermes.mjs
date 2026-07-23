// FAKE_HERMES 測試替身:模擬 `hermes dashboard --host H --port P --no-open`。
// 只用於測試,不含任何真實憑證/真實 hermes 行為。
// - 把收到的 argv 與自己的 PID 以 JSON line 寫進 FAKE_HERMES_SPAWN_LOG
//   (測試用來驗證「指令參數寫死、無注入」與 spawn 次數)。
// - 在指定 host:port 起一個 HTTP server 回 200。
import { createServer } from "node:http";
import { appendFileSync } from "node:fs";

const args = process.argv.slice(2);
const spawnLog = process.env.FAKE_HERMES_SPAWN_LOG;
if (spawnLog) {
  appendFileSync(spawnLog, `${JSON.stringify({ pid: process.pid, args })}\n`, "utf8");
}

const hostIndex = args.indexOf("--host");
const portIndex = args.indexOf("--port");
const host = hostIndex >= 0 ? args[hostIndex + 1] : "127.0.0.1";
const port = portIndex >= 0 ? Number(args[portIndex + 1]) : 9119;

const delayMs = Number(process.env.FAKE_HERMES_DELAY_MS || 0);

const server = createServer((_request, response) => {
  response.writeHead(200, { "Content-Type": "text/plain" });
  response.end("FAKE_HERMES_DASHBOARD");
});

setTimeout(() => {
  server.listen(port, host, () => {
    console.log(`FAKE_HERMES_READY port=${port}`);
  });
}, delayMs);
