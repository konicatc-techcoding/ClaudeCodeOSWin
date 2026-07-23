---
name: project-bridge-windows-scheduler-deployment
description: 2026-07-23 Hermes→CCOS bridge 部署完成——bridge 三個 WSL timer disable+mask，排程權移交 Windows Task Scheduler `HermesBridgeDaily`（每日 08:05、錯過補跑、不重疊、30min 上限），冷啟實測通過、watermark 修復。實現 CoS/Hermes 分工 roadmap #2（Hermes 當記憶來源的管線上線）。含去重三道保險與維運要點
metadata:
  type: project
---

來源：2026-07-23 互動式前台 CoS session（同日實測驗證）。本則記錄 Hermes→CCOS bridge 的**部署完成**與**排程權移交 Windows Task Scheduler** 的架構，供日後任何「bridge 排程去哪改／timer 為什麼是 masked／會不會雙跑」的問題直接引用。

## 里程碑意義

這實現了 [[project_cos-hermes-division-of-labor]] roadmap **#2**——「完成 Hermes→CCOS bridge 部署＝真正能動的記憶整合」。Hermes 作為「記憶來源之一（唯讀單向）」的管線至此**上線且有可靠排程**：episode capture 管線（scanner／importer episode 化）早於 2026-07-12 實作完成並啟用（`hermes/config/bridge.yaml` `episodes.enabled: true`、cutover `2026-07-12T06:36:18Z`、222 測試綠、`memory/inbox/.processed/` 已有真實 episode 落地檔）；2026-07-23 補上的是最後一塊——**排程從「WSL on-demand、靠手動喚醒 catch-up」變成「Windows always-on 每日固定觸發」**。

## 排程架構（2026-07-23 起）

- **Windows Task Scheduler task：`HermesBridgeDaily`**——每日 08:05；`StartWhenAvailable`（錯過時刻補跑）、`MultipleInstances IgnoreNew`（不重疊）、30 分鐘執行上限。
- 動作：`wsl.exe -d Ubuntu -- bash -lc 'systemctl --user start hermes-bridge-scanner.service ; systemctl --user start hermes-bridge-pipeline.service ; systemctl --user start hermes-bridge-notifier.service'`——Windows 喚醒 WSL（distro Stopped 會自動 boot），三個 service **依序**執行。
- **WSL 側三個 bridge timer（`hermes-bridge-scanner/pipeline/notifier.timer`）已 `disable + mask`，`.service` 保留**（供 Windows 觸發或人工啟動）。
- **移交範圍只有 bridge 三個**（使用者拍板）：skill-sync 的 `hermes-bridge.timer` 與其他 timer（rss、cron-daily-memory-check）維持 WSL systemd 管理不變。

## 去重三道保險

1. timer disable 後不在 `timers.target` → 喚醒 distro 不會 Persistent catch-up（第一道，機制性）。
2. mask（第二道保險，防誤 enable/start timer）。
3. idempotency：scanner watermark／enqueue_once／notification_log（第三道，即使真的重跑也不重不漏）。

## 驗證記錄（2026-07-23 實測）

- **冷啟實測通過**：distro Stopped → task 觸發 → 自動 boot → 三 service 嚴格序列各恰好跑一次（11:53:36–11:54:12）→ exit 0、零 failed units、masked timer 不雙跑。
- **同日維運**：`reconcile` 對帳 15 筆完成；scanner watermark 從卡住的 07-19 推進到 07-23——backlog 約 1471 筆 cron session 清畢（多為 too_short 被 importer 排除），0 筆落 inbox、0 Slack 送出。

## 維運要點（How to apply）

- **要改 bridge 排程（時刻／頻率／停用），去 Windows Task Scheduler 改 `HermesBridgeDaily`**——不是改 WSL timer；WSL timer 保持 masked。
- **動 bridge schema 前的 runbook 前置條件（timer 必須 disabled，見 stage2.4d proposal §8）在此模型下常態成立**——timer 已 mask，不需再額外停用；但 migration 窗口內要記得暫停 `HermesBridgeDaily`。
- 驗證入口：`journalctl --user -u hermes-bridge-*` 與 `systemctl --user list-units --failed`。
- 部署模型文件正本：`hermes/systemd/README.md`「bridge 三件組的排程模型」節；架構層敘述見 ARCHITECTURE.md §4.2。

## 相關記憶

- [[project_cos-hermes-division-of-labor]] — 本次部署即其 roadmap #2 的完成；「Hermes＝記憶來源之一（唯讀單向）」自此有可靠排程的實體管線。
- [[project_v0_1_status]] — v0.1 領域狀態與 bridge 端到端驗證脈絡；本則接續其 bridge 進度線。
- [[feedback_agent-os-acceptance-testing-preference]] — 本次驗收照該偏好：先單筆冷啟鏈路實測，再清 backlog 批次。
