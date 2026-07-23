// AgentOS 一鍵啟動:Local Bridge(127.0.0.1:8787)+ PTY server
// (127.0.0.1:8801,獨立 process)+ Vite dev server(127.0.0.1:5173)。
// bridge 一律以零參數建立=全部使用 bridge.mjs 內的凍結常數(指令白名單、
// bind 位址、port)。
//
// PTY token(P3,docs/webui-pty-terminal-proposal.md §2):每次啟動產生
// per-boot 隨機 token(32 bytes hex),只經環境變數注入兩個子 process
// (PTY server 與 Vite),不寫入任何持久檔案、不進 git。launcher 關閉時
// 先終止子 process(Windows 用 taskkill /T 樹殺,確保 ConPTY 下的 claude
// 不留孤兒),再退出。
import { spawn, execFile } from "node:child_process";
import { randomBytes } from "node:crypto";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createBridge, BRIDGE_HOST, BRIDGE_PORT } from "./bridge.mjs";

const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");

const bridge = createBridge(); // 零參數:production 全走凍結常數
await bridge.listen();
console.log(`AgentOS Local Bridge: http://${BRIDGE_HOST}:${BRIDGE_PORT}`);
console.log(`audit log: ${bridge.auditLogPath}`);

// ---- PTY server(獨立 process,物理隔離;不 import pty-server 模組) ----
// per-boot 隨機 token:只存在於記憶體與子 process 環境變數,不落磁碟。
const ptyToken = randomBytes(32).toString("hex");
const ptyEntry = resolve(projectRoot, "scripts", "pty-server.mjs");
const ptyServer = spawn(process.execPath, [ptyEntry], {
  cwd: projectRoot,
  env: { ...process.env, AGENTOS_PTY_TOKEN: ptyToken },
  stdio: "inherit",
  shell: false,
});

// Windows 注意:node_modules/.bin/vite 是 .cmd shim,spawn 不經 shell 會
// EINVAL/ENOENT(實測見 webui/README.md)。修正方式:不用 shim、不用
// shell:true,直接以 node 執行 vite 的 JS 入口——指令字串仍是寫死常數。
// token 經 VITE_ 前綴環境變數注入前端 bundle 執行期(dev server 記憶體,
// 不落磁碟)。
const viteEntry = resolve(projectRoot, "node_modules", "vite", "bin", "vite.js");
const devServer = spawn(process.execPath, [viteEntry], {
  cwd: projectRoot,
  env: { ...process.env, VITE_AGENTOS_PTY_TOKEN: ptyToken },
  stdio: "inherit",
  shell: false,
});

function killChildTree(child) {
  if (child.exitCode !== null) return Promise.resolve();
  if (process.platform === "win32") {
    // Windows 無法送 graceful signal 給非 console 子 process;taskkill /T
    // 樹殺確保 PTY server 底下的 claude(ConPTY 子孫)一併結束,不留孤兒。
    // PID 來自 launcher 自己 spawn 的紀錄,絕非外部輸入。
    return new Promise((r) => {
      execFile("taskkill", ["/PID", String(child.pid), "/T", "/F"], () => r());
    });
  }
  child.kill("SIGTERM");
  return Promise.resolve();
}

let shuttingDown = false;
async function shutdown(signal) {
  if (shuttingDown) return;
  shuttingDown = true;
  if (devServer.exitCode === null) devServer.kill(signal);
  await killChildTree(ptyServer);
  await bridge.shutdown();
  process.exit(0);
}

process.on("SIGINT", () => void shutdown("SIGINT"));
process.on("SIGTERM", () => void shutdown("SIGTERM"));
devServer.on("exit", (code) => {
  if (code && code !== 0) console.error(`AgentOS UI 已停止,exit code ${code}`);
  void shutdown("SIGTERM");
});
ptyServer.on("exit", (code) => {
  // PTY server 停止不拖垮整個 UI(唯讀 view 不依賴它);UI 端會顯示
  // 「服務未啟動」狀態與啟動指引,不做假介面。
  if (code && code !== 0 && !shuttingDown) {
    console.error(`AgentOS PTY server 已停止,exit code ${code}(ClaudeCode CLI view 將顯示服務未啟動)`);
  }
});
