// fetch-remotes.test.mjs 的 fixture:模擬第三群組的一條 fetch 指令。
// **絕不執行真實 git、絕不觸網、絕不碰任何真實 repo**。
// - 把收到的 argv 逐行(JSON)append 到 FAKE_FETCH_LOG,供測試斷言
//   「bridge 執行的正是凍結指令、request body/query 進不來」。
// - FAKE_FETCH_FAIL(逗號分隔的標記)命中 argv 任一元素 → stderr 吐錯誤
//   並以 exit 128 結束——模擬單條 fetch 失敗(per-remote fail-loud 案例)。
// - FAKE_FETCH_DELAY_MS:延遲結束,製造併發窗口(409 案例)。
import { appendFileSync } from "node:fs";

const args = process.argv.slice(2);
const logPath = process.env.FAKE_FETCH_LOG;
if (logPath) {
  appendFileSync(logPath, `${JSON.stringify({ args })}\n`, "utf8");
}

const delay = Number(process.env.FAKE_FETCH_DELAY_MS ?? "0");
const failMarkers = (process.env.FAKE_FETCH_FAIL ?? "").split(",").filter(Boolean);
const shouldFail = failMarkers.some((marker) => args.includes(marker));

setTimeout(() => {
  if (shouldFail) {
    process.stderr.write("fatal: unable to access 'https://github.com/...': Could not resolve host\n");
    process.exit(128);
  }
  process.exit(0);
}, Number.isFinite(delay) ? delay : 0);
