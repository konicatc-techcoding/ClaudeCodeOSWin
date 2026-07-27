// FAKE wsl fixture — service-control 測試專用,絕不碰真實 wsl/systemd。
// 行為:把收到的 argv 記到 FAKE_WSL_SPAWN_LOG(JSON lines),依
// FAKE_WSL_EXIT_CODE 決定 exit code(預設 0),FAKE_WSL_DELAY_MS 可延遲
// 結束(製造併發窗口)。與 fake-hermes.mjs 同一套 FAKE_ 前綴慣例。
import { appendFileSync } from "node:fs";

const logPath = process.env.FAKE_WSL_SPAWN_LOG;
if (logPath) {
  appendFileSync(logPath, `${JSON.stringify({ pid: process.pid, args: process.argv.slice(2) })}\n`, "utf8");
}

const exitCode = Number(process.env.FAKE_WSL_EXIT_CODE ?? "0");
const delayMs = Number(process.env.FAKE_WSL_DELAY_MS ?? "0");

setTimeout(() => process.exit(Number.isInteger(exitCode) ? exitCode : 1), delayMs);
