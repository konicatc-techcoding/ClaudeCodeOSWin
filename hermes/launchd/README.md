> **ClaudeCodeOSWin 複本注意**：這個目錄是 macOS 專屬（`launchd`/`launchctl`），在 WSL2 底下不會作用。這份複本的常駐部署請改用 `hermes/systemd/`（見 [systemd/README.md](../systemd/README.md)），這個目錄僅保留供對照參考。

# hermes/launchd — 部署層（v0.1，僅適用 macOS）

這個目錄只負責「怎麼啟動 `worker.py`、掛掉要不要重開」，**不是 Runtime 的一部分**。`hermes/worker.py`、`hermes/db.py` 完全不知道自己是被 launchd 啟動的——`worker.py` 只認得標準的 `SIGTERM`/`SIGINT`。之後如果想換成 systemd、Docker、supervisord 或其他部署方式，只需要換掉這個目錄底下的東西，不用動 `hermes/worker.py` 或 `hermes/db.py` 一行程式碼。

## 安裝 / 移除

```bash
hermes/launchd/install.sh                    # 預設安裝 worker
hermes/launchd/install.sh hermes-telegram     # 安裝 telegram adapter（要先設定好 hermes/config/telegram.json！）
hermes/launchd/uninstall.sh                   # 預設移除 worker
hermes/launchd/uninstall.sh hermes-telegram   # 移除 telegram adapter
```

安裝方式是在 `~/Library/LaunchAgents/` 建一個指向這個 repo 的 symlink，所以之後改 plist 內容，重新 `install.sh` 一次（或 `launchctl kickstart -k`）就會生效，不用複製檔案。

**telegram 在裝之前一定要先建好 `hermes/config/telegram.json`**（`bot_token`/`allowed_chat_ids`）——沒設定檔會直接 exit，配上 `KeepAlive: true` 會變成每 10 秒 crash-loop 一次，一直洗 log 直到你補上設定檔。

## 這份 plist 做的事

- `RunAtLoad`：登入時啟動
- `KeepAlive: {Crashed: true}`：非正常結束（crash）才自動重啟；正常結束（目前 worker.py 設計上不會自己結束）不重啟
- `ThrottleInterval: 10`：避免 crash-loop 時瘋狂重啟
- `StandardOutPath`/`StandardErrorPath`：導到 `logs/hermes/launchd.stdout.log`/`launchd.stderr.log`——這兩個只會接住「logging 設定好之前就掛掉」的漏網訊息，正常運作的紀錄都在 `logs/hermes/worker.log`

## 常用指令

```bash
launchctl list | grep hermes-worker           # 查看是否在跑、上次結束的 exit code
launchctl kickstart -k gui/$(id -u)/com.zackchiu.claudecodeos.hermes-worker   # 強制重啟
tail -f logs/hermes/worker.log                # 看即時 log
```
