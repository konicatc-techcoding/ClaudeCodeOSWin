# hermes/systemd — 部署層（WSL2 版本，v0.1）

這個目錄是 macOS 版 `hermes/launchd/` 在 WSL2 環境下的等效替代。跟 `launchd/` 一樣，這裡只負責「怎麼啟動 adapter/worker、掛掉要不要重開、多久觸發一次」，**不是 Runtime 的一部分**——`hermes/worker.py`、`hermes/db.py`、`hermes/adapters/*.py` 完全不知道自己是被 systemd 啟動的，只認得標準的 `SIGTERM`/`SIGINT`。

## 前提：WSL2 要先開啟 systemd

WSL2 預設不跑 systemd。要用這個目錄底下的東西，distro 裡要先設定：

```ini
# /etc/wsl.conf
[boot]
systemd=true
```

改完在 **Windows** 端（PowerShell）跑：

```powershell
wsl --shutdown
```

再重新打開 WSL distro 讓設定生效。需要 Windows 11 22H2 以後、且 WSL 版本夠新（`wsl --update`）。如果環境不支援 systemd，見下方「systemd 不可用時的備案」。

## launchd → systemd 對應表

| launchd 概念 | systemd 等效 |
|---|---|
| `~/Library/LaunchAgents/*.plist`（user-level） | `~/.config/systemd/user/*.service`（`systemctl --user`，同樣不需要 root） |
| `RunAtLoad` | `WantedBy=default.target` + `systemctl --user enable` |
| `KeepAlive: true` | `Restart=always` |
| `ThrottleInterval` | `RestartSec` |
| `StartInterval`（固定間隔觸發） | `.timer` 的 `OnUnitActiveSec` |
| `StartCalendarInterval`（固定時刻觸發） | `.timer` 的 `OnCalendar` |
| `EnvironmentVariables.PATH` | `.service` 的 `Environment=PATH=...` |
| `StandardOutPath`/`StandardErrorPath` | `StandardOutput=append:...`/`StandardError=append:...` |
| `launchctl bootstrap/load` | `systemctl --user daemon-reload` + `enable --now` |
| `launchctl bootout/unload` | `systemctl --user disable --now` |
| `launchctl list` | `systemctl --user status` / `list-timers` |

## 這個目錄底下有什麼

- `hermes-worker.service` — 常駐 worker（等效 `hermes-worker.plist`）
- `hermes-telegram.service` — 常駐 telegram 輪詢（等效 `hermes-telegram.plist`）
- `hermes-rss.service` + `hermes-rss.timer` — 每 30 分鐘觸發一次（等效 `hermes-rss.plist` 的 `StartInterval:1800`）
- `hermes-cron-daily-memory-check.service` + `.timer` — 每天 08:00 觸發一次（等效同名 plist 的 `StartCalendarInterval`）
- `hermes-bridge.service` + `.timer` — 每天 08:10 觸發一次（等效 `hermes-bridge.plist`；skill-sync，**維持 WSL systemd 管理，不在 2026-07-23 排程權移交範圍內**）
- `hermes-bridge-scanner.service` + `.timer` — `bridge_scanner.py scan`（Stage 2.4b 新增，無 launchd 前身；**timer 已於 2026-07-23 disable+mask，排程權在 Windows Task Scheduler，見下方「bridge 三件組的排程模型」**）。**只排程 scan**：ExecStart 無參數，走 2.4a 安全預設（effective since ＝ max(config cutover, watermark)），排程一律不帶 `--since`；`reconcile` 是回填/對帳工具，人工才用，**刻意不進排程**。失敗（如 config 缺失）→ unit failed 可觀測，不設 Restart——失敗不推進 watermark，下次觸發從同一下界重掃，不會跳漏
- `hermes-bridge-pipeline.service` + `.timer` — bridge pipeline（Stage 2.7b 新增，見 docs/stage2.7-notification-scheduling-proposal.md §9 2.7b；**timer 已 disable+mask，排程權在 Windows Task Scheduler，見下**）。單一 oneshot service 依序執行 `bridge_importer.py import --limit 10` → `bridge_triage_enqueuer.py enqueue --max-new 5`；任一步驟失敗，後續步驟不執行、unit failed 可觀測（多行 `ExecStart=` 的既有 systemd 語義，不需另寫 wrapper）。旗標固定，排程一律不加其他範圍／dry-run 參數
- `hermes-bridge-notifier.service` + `.timer` — `bridge_notifier.py notify`（Stage 2.7b 新增；**timer 已 disable+mask，排程權在 Windows Task Scheduler，見下**）。走預設頻道（`bridge_notifier.py` 的 `DEFAULT_CHANNEL`＝正式頻道 `#agentos`）與預設 send-cli。notifier 對 jobs.db 唯讀，失敗（含 `hermes` CLI 不可用）→ fail loud、unit failed，不落 `notification_log`，下輪補送
- `install.sh` / `uninstall.sh` — 安裝/移除腳本，用法跟原本 `hermes/launchd/install.sh` 一致，只是底層換成 `systemctl --user`

## bridge 三件組的排程模型（2026-07-23 起）

**排程權已移交 Windows Task Scheduler**：`hermes-bridge-scanner/pipeline/notifier`
三個 `.timer` 已 `systemctl --user disable` + `mask`（**`.service` 保留**，供
Windows 觸發或人工 `systemctl --user start` 啟動）。排程本體是 Windows Task
Scheduler 的 **`HermesBridgeDaily`** task：

- 每日 08:05 觸發；`StartWhenAvailable`（錯過時刻補跑）、
  `MultipleInstances IgnoreNew`（不重疊）、30 分鐘執行上限
- 動作：`wsl.exe -d Ubuntu -- bash -lc 'systemctl --user start
  hermes-bridge-scanner.service ; systemctl --user start
  hermes-bridge-pipeline.service ; systemctl --user start
  hermes-bridge-notifier.service'`——由 always-on 的 Windows 喚醒 WSL
  （distro Stopped 也會自動 boot），三個 service 依序執行

理由：WSL 是 on-demand、不是 always-on，WSL timer 的排定時刻常因 distro
睡眠被跳過；Windows 才是這台機器真正 always-on 的排程層。冷啟實測已通過
（distro Stopped → task 觸發 → 自動 boot → 三 service 嚴格序列各恰好跑一次
→ exit 0、零 failed units）。

**去重三道保險**：(1) timer disable 後不在 `timers.target`，喚醒 distro 不會
Persistent catch-up；(2) mask 為第二道保險；(3) idempotency（scanner
watermark／enqueue_once／notification_log）為第三道。

**維運要點**：要改 bridge 排程，去 Windows Task Scheduler 改 `HermesBridgeDaily`
（不是改 WSL timer）；其他 timer（`hermes-bridge.timer` skill-sync、rss、
cron-daily-memory-check）**維持 WSL systemd 管理不變**。timer 已 mask 也代表
「schema migration 前 timer 必須 disabled」的 runbook 前置條件常態成立（見
docs/stage2.4d-episode-capture-proposal.md §8）。驗證入口：`journalctl --user
-u hermes-bridge-*` 與 `systemctl --user list-units --failed`。

## 開機自動啟動（2026-07-24 起：`HermesWslKeepAlive` + linger）

**問題**：Windows 開機後 Ubuntu distro 是 Stopped，常駐服務（worker／telegram）
不會自己起來；而且即使被喚醒（例如 `HermesBridgeDaily` 08:05 觸發），**最後一個
`wsl.exe` client 結束後 WSL 仍會把 distro terminate——systemd 背景服務不足以讓
distro 存活**（2026-07-24 實測：08:05 bridge 跑完後，09:23 檢查時 distro 已回到
Stopped）。所以解法要兩件事同時成立：**開機喚醒 + 常駐 keep-alive client**。

兩側設定：

1. **WSL 側——linger**（讓 `systemctl --user` 單元不依賴登入 session）：

   ```bash
   loginctl enable-linger razer          # 一次性；本機實測不需 sudo
   loginctl show-user razer --property=Linger   # 應回 Linger=yes
   ```

   linger 開了之後，distro 一 boot、`user@1000` 就起，enabled 的 hermes 單元
   （worker／telegram／rss／cron timer）約 3 秒內自動 active，**不需要任何人
   開 shell**。linger=no 的行為（歷史坑）：user 單元跟著 wsl session 走，
   session 一結束服務就被停掉。

2. **Windows 側——Task Scheduler task `HermesWslKeepAlive`**：

   - 觸發：**At log on**（使用者 `razer`）。Windows 10 19045 沒有「開機就啟動
     WSL」的官方選項（Windows 11 才有的 boot 相關功能不適用）；WSL VM 是
     per-user 的，login 觸發是這台機器可行的最早時點。
   - 動作：`wscript.exe //B //Nologo
     "C:\Users\razer\dev\ClaudeCodeOSWin\hermes\windows\hermes-wsl-keepalive.vbs"`
     ——vbs 以**隱藏視窗**執行 `wsl.exe -d Ubuntu --exec sleep infinity` 並
     等待。這個永遠不結束的 client 就是 keep-alive：只要它活著，WSL 不會
     terminate Ubuntu。（不直接掛 `wsl.exe` 的原因：Interactive 任務會在桌面
     留一個永久可見的 console 視窗。）
   - 設定：**執行時間上限＝無限**（`PT0S`；預設 72 小時會把 keep-alive 殺掉，
     必關）、`MultipleInstances IgnoreNew`（不重複啟動）、**失敗自動重啟
     10 次／間隔 1 分鐘**（2026-07-27 由 3 次調大；`wsl --shutdown` 之後 task
     會偵測到 client 死掉並自動復活整條鏈）、電池模式照常執行。Principal
     比照 `HermesBridgeDaily`（`razer`／Interactive／Limited，建立不需提權）。
   - **自癒 backstop（2026-07-27 起，keepalive-hardening 提案第一階段）**：
     除 LogonTrigger 外另有一個 **TimeTrigger（StartBoundary 在過去）攜帶
     `Repetition PT15M`**——每 15 分鐘 tick 一次，task 活著時被 IgnoreNew
     吞掉（no-op），死了（含 RestartOnFailure 用盡放棄後）最壞 15 分鐘內
     自動拉回。Repetition 放 TimeTrigger 而非 LogonTrigger 的原因（實測）：
     LogonTrigger 的 Repetition 要等 trigger 真的觸發（＝下次登入）才上膛，
     重註冊後的當前 session 完全沒有 tick；TimeTrigger 註冊後立即生效。
     另一個實測教訓：**RestartOnFailure 只對「觸發器啟動」的執行生效**，
     `schtasks /run` 手動啟動的實例失敗後不會被它重啟——手動介入後的保險
     同樣靠 TimeTrigger tick。
   - `.wslconfig` 的 `[wsl2] vmIdleTimeout` **不是替代方案**——它管的是 utility
     VM 的 idle 關機，擋不住 distro 本身被 terminate。

**驗證紀錄**（2026-07-24 實測）：`wsl --terminate Ubuntu`（Stopped 確認）→
`schtasks /run /tn HermesWslKeepAlive` → distro 自動 boot → worker／telegram
於 boot 後 **3.1 秒** 自動 active（`ActiveEnterTimestampMonotonic`≈3.09s，
早於任何人為 probe，證明是 linger 拉起、不是被檢查指令帶起）→ 持續觀察
5 分鐘：distro 維持 Running、`NRestarts=0`、零 failed units、三個 timer
（rss／cron-daily-memory-check／bridge skill-sync）正常排定；telegram adapter
log 顯示輪詢正常（同日稍早並有真實訊息 enqueue 紀錄），worker 在 boot 後
實際 claim 並完成多筆 RSS job（端到端管線活著）。

**驗證紀錄（2026-07-27 實測，自癒 backstop）**：背景＝task 於 07-24 失效後
永久放棄、靜默停擺三天（見 auto-memory `wsl-keepalive-monitoring-gap`）。
重註冊（Count=10＋TimeTrigger PT15M）後實測：`wsl --terminate Ubuntu`
（12:13，task 轉 Ready／Last Result=1）→ **零人工介入** → 12:30:00 tick
自動拉回 task（Running、下個 tick 12:45 已排定）→ distro 復活 → worker／
telegram 於 12:30:07 由 linger 自動 active。

**維運要點**：

- 冷啟後驗證服務，記得 hermes gateway（hermes-agent 側）啟動後約 **3.5 分鐘**
  才寫狀態檔，不要提早誤判 not running（見 memory）。
- 手動重建鏈路（例如 `wsl --shutdown` 之後不想等自動重啟）：
  `schtasks /run /tn HermesWslKeepAlive`。
- **刻意 `wsl --shutdown`／`--terminate` 想讓 distro 休息時**：≤15 分鐘內
  會被 TimeTrigger tick 復活（2026-07-27 拍板知情接受）——要保持關機，
  **先停用 task**（`schtasks /change /tn HermesWslKeepAlive /disable`），
  用完再 `/enable`。
- 要停用開機自動啟動：Task Scheduler 停用/刪除 `HermesWslKeepAlive`；要連
  「distro boot 就起服務」一起關，再加 `loginctl disable-linger razer`。
- 限制：trigger 是 At log on，**Windows 重開機後要有人登入 `razer` 一次**
  服務才會起來；無人登入的冷開機狀態下 Telegram bot 不可用（Win10 無
  boot-time WSL 選項，若未來升 Windows 11 可再評估）。

## 安裝 / 移除

```bash
hermes/systemd/install.sh                                    # 預設安裝 worker（常駐）
hermes/systemd/install.sh hermes-telegram                    # 安裝 telegram（常駐，先設定好 hermes/config/telegram.json）
hermes/systemd/install.sh hermes-rss                          # 安裝 rss（service+timer，每 30 分鐘）
hermes/systemd/install.sh hermes-cron-daily-memory-check      # 安裝 cron（service+timer，每天 08:00）
hermes/systemd/install.sh hermes-bridge                       # 安裝 hermes bridge（service+timer，每天 08:10）
hermes/systemd/install.sh hermes-bridge-scanner               # 安裝 bridge scanner（注意：timer 已 mask，排程權在 Windows Task Scheduler，見上節）
hermes/systemd/install.sh hermes-bridge-pipeline              # 安裝 bridge pipeline（同上：timer 已 mask，勿重新 enable）
hermes/systemd/install.sh hermes-bridge-notifier              # 安裝 bridge notifier（同上：timer 已 mask，勿重新 enable）

hermes/systemd/uninstall.sh                                   # 預設移除 worker
hermes/systemd/uninstall.sh hermes-telegram                   # 移除 telegram
```

安裝方式一樣是在 `~/.config/systemd/user/` 建一個指向這個 repo 的 symlink，改完 unit 檔案後重新跑一次 `install.sh`（或 `systemctl --user daemon-reload` + `restart`）就會生效，不用複製檔案。

## 常用指令

```bash
systemctl --user status hermes-worker              # 查看是否在跑、上次結束的狀態
systemctl --user restart hermes-worker              # 強制重啟
systemctl --user list-timers                        # 查看所有排程 timer 下次觸發時間
journalctl --user -u hermes-worker -f                # 看 systemd 層級的 log（跟 logs/hermes/*.log 互補）
tail -f logs/hermes/worker.log                       # 看 Runtime 自己寫的 log（跟 launchd 版本一致，沒變）
```

## `Environment=PATH=...` 這件事，为什么還需要

跟 macOS 版本一樣的坑：systemd `--user` 啟動的程序，`PATH` 不一定跟你互動式 shell 一樣完整（尤其 `claude` CLI 若裝在 `~/.local/bin` 或 nvm/pyenv 之類的路徑）。每個 `.service` 都明確設定 `Environment=PATH=...`，把常見安裝位置列進去；如果你的 `claude` CLI 裝在其他路徑，記得同步修改對應的 `.service` 檔案。

## systemd 不可用時的備案

如果目標 WSL distro 因為某些原因無法啟用 systemd（例如公司管控的映像檔、舊版 WSL），退回用 `cron` + `@reboot`：

```bash
# crontab -e
@reboot /home/<user>/dev/ClaudeCodeOSWin/hermes/systemd/fallback_start_worker.sh
```

但這個備案沒有 `KeepAlive`/`Restart=always` 的等效行為（cron 只在開機時跑一次，process 掛掉不會自動重啟），需要額外包一層監控（例如簡單的 `while true; do ...; done` supervisor loop，或 `cron` 搭配每分鐘檢查 pid 是否存在）。v0.1 沒有實作這個備案腳本，只在這裡記錄選項；若之後真的遇到這個情境，屬於 `engineering` 領域的後續任務。
