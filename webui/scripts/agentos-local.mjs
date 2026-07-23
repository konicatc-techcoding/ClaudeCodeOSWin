// AgentOS 一鍵啟動:Local Bridge(127.0.0.1:8787)+ Vite dev server
// (127.0.0.1:5173)。bridge 一律以零參數建立=全部使用 bridge.mjs 內的
// 凍結常數(指令白名單、bind 位址、port)。
import { spawn } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createBridge, BRIDGE_HOST, BRIDGE_PORT } from "./bridge.mjs";

const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");

const bridge = createBridge(); // 零參數:production 全走凍結常數
await bridge.listen();
console.log(`AgentOS Local Bridge: http://${BRIDGE_HOST}:${BRIDGE_PORT}`);
console.log(`audit log: ${bridge.auditLogPath}`);

// Windows 注意:node_modules/.bin/vite 是 .cmd shim,spawn 不經 shell 會
// EINVAL/ENOENT(實測見 webui/README.md)。修正方式:不用 shim、不用
// shell:true,直接以 node 執行 vite 的 JS 入口——指令字串仍是寫死常數。
const viteEntry = resolve(projectRoot, "node_modules", "vite", "bin", "vite.js");
const devServer = spawn(process.execPath, [viteEntry], {
  cwd: projectRoot,
  env: process.env,
  stdio: "inherit",
  shell: false,
});

async function shutdown(signal) {
  if (devServer.exitCode === null) devServer.kill(signal);
  await bridge.shutdown();
  process.exit(0);
}

process.on("SIGINT", () => void shutdown("SIGINT"));
process.on("SIGTERM", () => void shutdown("SIGTERM"));
devServer.on("exit", (code) => {
  if (code && code !== 0) console.error(`AgentOS UI 已停止,exit code ${code}`);
  void shutdown("SIGTERM");
});
