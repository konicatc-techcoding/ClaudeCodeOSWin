// FAKE claude CLI fixture(僅供 pty-server 測試,經 ConPTY spawn)。
// 行為:啟動印 READY 標記;echo 收到的 stdin;收到含 "exit" 的一行即
// 以 exit code 42 結束(模擬使用者在 claude 內打 /exit)。
// argv 含 --spam 時持續輸出(模擬長任務執行中輸出不斷、無使用者輸入)。
let buffer = "";

process.stdout.write("FAKE_CLAUDE_READY\r\n");

if (process.argv.includes("--spam")) {
  setInterval(() => {
    process.stdout.write("FAKE_CLAUDE_LONG_TASK_OUTPUT\r\n");
  }, 60);
}

process.stdin.on("data", (chunk) => {
  const text = chunk.toString("utf8");
  buffer = (buffer + text).slice(-512);
  process.stdout.write(`FAKE_ECHO:${text}`);
  if (buffer.includes("exit")) {
    process.exit(42);
  }
});

// stdin 關閉(ConPTY 收攤)也結束,避免殘留
process.stdin.on("end", () => process.exit(0));
