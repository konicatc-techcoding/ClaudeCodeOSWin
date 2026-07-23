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
