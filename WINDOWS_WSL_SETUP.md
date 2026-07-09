# WINDOWS_WSL_SETUP.md — ClaudeCodeOSWin 複本說明

這份文件說明 `ClaudeCodeOSWin` 跟原版 `ClaudeCodeOS` 的差異，以及在 WSL2 上安裝/啟動常駐服務的步驟。

> **狀態更新（2026-07-09）**：搬遷已完成。本 repo 自 2026-07-08 起位於原生 Windows
> （`C:\Users\razer\dev\ClaudeCodeOSWin`），WSL2 側部署複本位於
> `/home/razer/dev/ClaudeCodeOSWin`（ext4），`hermes/systemd/` 的常駐服務
> （worker、telegram、rss timer、cron-daily-memory-check timer、bridge timer）已安裝並運作中。
> 下方原始文字是搬遷前在 macOS 上準備複本時寫的，「使用者需要自己確認的事項」
> 多數已在實機驗證，保留作安裝步驟參考。

這份複本當時是在 macOS 上、用同一台機器複製出來的**實驗性複本**，用途是先把路徑/腳本改對。所有「移到 Windows 機器後」才需要做的事，都在下方「使用者需要自己確認的事項」列出來。

## 跟原版的差異

| 項目 | 原版 `ClaudeCodeOS`（macOS） | 複本 `ClaudeCodeOSWin`（WSL2 目標） |
|---|---|---|
| 常駐機制 | `launchd`（`hermes/launchd/*.plist` + `launchctl`） | `systemd --user`（`hermes/systemd/*.service` + `*.timer`） |
| 安裝/移除腳本 | `hermes/launchd/install.sh` / `uninstall.sh` | `hermes/systemd/install.sh` / `uninstall.sh`（用法一致，底層換成 `systemctl --user`） |
| Dashboard 狀態來源 | `dashboard/data.py` 的 `get_launchd_status()`（跑 `launchctl list`） | 新增 `get_systemd_status()`（跑 `systemctl --user list-units`），`app.py` 改用這個 |
| 專案路徑 | `/Users/zackchiu/dev/ClaudeCodeOS` | 目前在 mac 上是 `/Users/zackchiu/dev/ClaudeCodeOSWin`；**搬到 Windows 機器後預期會落在 WSL2 的 `/home/<user>/dev/ClaudeCodeOSWin`**（systemd unit 檔案裡用 `%h`，會自動對應到當時登入使用者的 home，不用手動改路徑） |
| `hermes/launchd/` 目錄 | 是實際使用中的部署設定 | 保留在複本裡僅供對照參考，WSL2 底下不會作用（沒有 `launchctl`），每個檔案/README 都加了提示 |
| 其他程式碼（CoS 決策邏輯、delegation policy、五個 domain subagent、Hermes job queue／db.py／adapters） | — | **完全沒動**，這些都是純 Python/Bash + 文字設定，跟作業系統無關，macOS 跟 WSL2（本質是 Linux）行為一致 |

沒有變更的部分：`CLAUDE.md`、`registry/`、`ARCHITECTURE.md`、`delegation_policy.md` 等純文字設定檔——這些裡面沒有寫死 macOS 專屬路徑或指令，不需要調整。`hermes/adapter/invoke_cos.sh` 原本就用 `BASH_SOURCE` 做相對路徑解析，沒有寫死絕對路徑，也不需要調整。

## 為什麼選 systemd（而不是純 cron 或其他方案）

launchd 的核心概念（宣告式常駐設定、crash 自動重啟、固定間隔/固定時刻觸發、per-user 不需要 root）在 systemd 都有直接對應：`Restart=always` 對應 `KeepAlive`，`.timer` 的 `OnUnitActiveSec`/`OnCalendar` 對應 `StartInterval`/`StartCalendarInterval`，`systemctl --user` 對應 launchd 的 user-level LaunchAgent。純 cron 也能做「固定時間觸發」，但沒有「crash 自動重啟」的等效機制（`hermes-worker`、`hermes-telegram` 是要長駐輪詢的常駐程序，不是一次性 job，需要這個能力）。所以：

- **常駐型**（`hermes-worker`、`hermes-telegram`）→ 純 `.service`（`Restart=always`）
- **排程型**（`hermes-rss`、`hermes-cron-daily-memory-check`、`hermes-bridge`）→ `.service`（oneshot）+ `.timer`

完整對應表見 [hermes/systemd/README.md](hermes/systemd/README.md)。

## WSL2 安裝步驟（搬到 Windows 機器後）

1. **確認 WSL2 + systemd 可用**（前提，不是這份複本的一部分）：
   - Windows 端：`wsl --version` 確認是 WSL2；`wsl --update` 更新到最新。
   - distro 內：編輯 `/etc/wsl.conf`，加上：
     ```ini
     [boot]
     systemd=true
     ```
   - Windows 端跑 `wsl --shutdown`，重新開啟 distro 讓設定生效。
   - 驗證：distro 內跑 `systemctl --version`，能印出版本號就表示成功。

2. **把這份複本放到 WSL2 檔案系統內**（不是 Windows 的 `/mnt/c/...`）：
   建議路徑 `~/dev/ClaudeCodeOSWin`（即 `/home/<user>/dev/ClaudeCodeOSWin`）。放在 WSL2 原生檔案系統（而非 `/mnt/c/`）效能較好，也符合 systemd unit 檔案裡 `%h/dev/ClaudeCodeOSWin` 的預期路徑。

3. **安裝 Claude Code CLI**（WSL2 內，跟平常 Linux 安裝方式一致），並確認 `claude` 在 `PATH` 上（`which claude`）。如果裝在非標準路徑（例如 `~/.local/bin` 以外），記得同步修改 `hermes/systemd/*.service` 裡的 `Environment=PATH=...`。

4. **建 Python venv、裝依賴**：
   ```bash
   cd ~/dev/ClaudeCodeOSWin
   python3 -m venv .venv
   .venv/bin/pip install -r scripts/requirements.txt
   ```
   （原本 mac 上的 `.venv` 也被複製過去了，但 venv 內的 binary 是跟 macOS/Python 版本綁定的，**在 WSL2 上一定要重新建立**，不能沿用複製過去的 `.venv`。）

5. **設定密鑰/設定檔**：
   - `hermes/config/telegram.json`——**這份複本裡目前含有真實的 bot token**（從原版複製過來的），細節見下方「安全性提醒」。
   - 若要用 OpenRouter（`scripts/route_model.py` 的 `via=openrouter`），設定環境變數 `OPENROUTER_API_KEY`。

6. **安裝常駐服務**：
   ```bash
   hermes/systemd/install.sh hermes-worker
   hermes/systemd/install.sh hermes-telegram    # 先確認 telegram.json 已設定好
   hermes/systemd/install.sh hermes-rss
   hermes/systemd/install.sh hermes-cron-daily-memory-check
   hermes/systemd/install.sh hermes-bridge
   ```
   若要在沒有互動登入 session 時也持續運作，考慮跑 `loginctl enable-linger $USER`（讓 `systemd --user` 開機時就啟動，不等實際登入 WSL2）。

7. **驗證**：
   ```bash
   systemctl --user list-timers                # 看排程 timer 下次觸發時間
   systemctl --user status hermes-worker         # 看 worker 是否 active (running)
   tail -f logs/hermes/worker.log                # 看 Runtime 自己的 log
   ```

8. **（可選）跑 dashboard**：
   ```bash
   .venv/bin/streamlit run dashboard/app.py --server.address=localhost
   ```
   總覽頁的「Worker / Adapter 狀態」已改用 `systemctl --user` 查詢，會顯示 systemd 版本的服務名稱。

## 使用者需要自己確認的事項（到 Windows 機器上才能驗證）

以下事項在這台 mac（沒有 WSL 環境）上無法實際測試，只做了靜態檢查（`bash -n` 語法檢查、grep 確認無殘留 macOS 路徑、既有 unit test 全過），需要你在真正的 WSL2 環境上手動確認：

1. **systemd 在你的 WSL2 版本上是否真的可用**——這是整個方案的前提，如果你的 WSL/Windows 版本太舊或環境被公司管控政策鎖住無法啟用 systemd，`hermes/systemd/install.sh` 會直接檢查 `systemctl` 是否存在並提示你，但你需要自己決定退回 cron 方案（見 [hermes/systemd/README.md](hermes/systemd/README.md) 最後一節「systemd 不可用時的備案」，目前只記錄選項、沒有實作腳本）。
2. **`%h`（systemd specifier）在你的環境下是否真的解析成正確的 home 目錄**——理論上會是 `/home/<你的 WSL 使用者名稱>`，但沒有實機驗證過。
3. **`claude` CLI 的實際安裝路徑**——確認它在 `hermes/systemd/*.service` 的 `Environment=PATH=...` 涵蓋範圍內，否則常駐服務會因為找不到 `claude` 指令而 crash-loop（這是原本 macOS 版本也踩過的坑，見 `hermes/README.md` 的歷史記錄）。
4. **`.venv` 需要在 WSL2 內重新建立**（見上方步驟 4）——複製過去的 `.venv` 是 macOS 的 binary，不能直接用。
5. **crash-loop / `Restart=always` 的實際行為**——原本 macOS 版本測試時發現 `KeepAlive` 的 crash 定義不涵蓋 `SIGKILL`；`systemd` 的 `Restart=always` 理論上沒有這個問題（任何退出方式都重啟），但這是理論推導，沒有在真實 WSL2 環境上重現過那個測試（人工 `kill -9` 觀察是否重啟）。
6. **systemd timer 的實際觸發時間是否準確**（`OnCalendar=*-*-* 08:00:00` 這類設定）——時區設定跟 WSL2 distro 本身的系統時間有關，沒有實機驗證過。
7. **檔案系統效能與行尾符號（CRLF/LF）**——如果之後改用 Windows 端編辑器編輯這些檔案，注意存回 WSL2 時不要被轉成 CRLF（尤其是 `.sh` 檔案，CRLF 會讓 `bash -n` 語法檢查失敗、執行時出現 `$'\r': command not found` 這類錯誤）。

## 安全性提醒（重要）

這份複本是用 `cp`/`rsync` 整份複製的，**沒有排除 `hermes/config/telegram.json`**（它在原版是 `.gitignore` 排除、不進版控，但檔案系統層級的複製不會理會 `.gitignore`）。也就是說 `ClaudeCodeOSWin/hermes/config/telegram.json` 裡目前含有真實的 Telegram bot token。

在把這份複本實際搬到另一台 Windows 機器、或以任何方式分享/上傳這個目錄之前：
- 考慮先在 BotFather 用 `/revoke` 重新產生一組新 token，把舊 token 汰換掉；或
- 手動清空/刪除複本裡的 `hermes/config/telegram.json`，改在目標機器上重新建立。

`hermes/jobs.db` 已在複製時排除（依任務指示），不含歷史 job 資料。`logs/` 也已排除。
