# Roadmap — Claude Code OS

> **目前部署環境（2026-07-09 更新）**：目標環境是 **Windows/WSL2**（本 repo 位於原生
> Windows `C:\Users\razer\dev\ClaudeCodeOSWin`，常駐服務跑在 WSL2 側，以
> `hermes/systemd/` 為主，見 [WINDOWS_WSL_SETUP.md](WINDOWS_WSL_SETUP.md)）。
> 下方 milestone 表裡的「launchd 常駐」是當時 macOS 環境的歷史記錄，原樣保留；
> `hermes/launchd/` 目錄僅為 macOS legacy/reference，不再是 live runtime。
> Windows 側的 bridge／Task Scheduler 排程是未來選項（Stage 2 設計決策），尚未實作。
> Hermes 整合軌（Session Bridge／Episode Capture）的最新階段性狀態見
> [docs/hermes-integration-roadmap.md](docs/hermes-integration-roadmap.md)，本檔不重複追蹤該軌細節。

## 節奏

每個主要能力都走同一個順序，不同時開兩個：**實作 → 驗證 → Commit → Milestone**。前一個里程碑沒確認是穩定基線，不開始下一個能力的實作。

## Milestones

| Milestone | 狀態 | 日期 | 內容 |
|---|---|---|---|
| `v0.1-alpha` | ✅ 已達成 | 2026-07-04 | Runtime 核心穩定基線：CoS + 五個 domain + delegation policy、consolidate-memory、Hermes job queue、SAT 九項全過、launchd 常駐、Telegram Polling Adapter（live 驗證通過） |
| `v0.1-beta` | ✅ 已達成 | 2026-07-04 | `v0.1-alpha` 之上補完全部 event source（Telegram 已在 alpha 完成；這次加上 Cron、RSS）+ Dashboard；76 個單元測試全過，jobs.db 一致性複查零違規 |
| Cron Adapter | ✅ 已完成 | 2026-07-04 | 無狀態、排程交給 launchd；用臨時 30 秒 plist 驗證過「launchd 真的自己觸發」，正式改裝 `daily-memory-check` 每天 08:00 |
| RSS Adapter | ✅ 已完成 | 2026-07-04 | 無狀態、抓取/去重/`feedparser` 解析/`enqueue()`；真實 feed（hnrss.org）+ 臨時 launchd smoke test 驗證過，正式改裝 30 分鐘排程 |
| Dashboard | ✅ 已完成 | 2026-07-04 | Streamlit，localhost-only、read-only（獨立資料層、mode=ro 強制）；用 `streamlit.testing.v1.AppTest` 對真實資料跑過，零例外、密鑰不外洩 |

## `v0.1-alpha` 涵蓋範圍

詳細設計見 [ARCHITECTURE.md](ARCHITECTURE.md)，測試證據見 [SAT_REPORT.md](SAT_REPORT.md)、[INTEGRATION_TEST.md](INTEGRATION_TEST.md)，Hermes 細節見 [hermes/README.md](hermes/README.md)、[hermes/DESIGN.md](hermes/DESIGN.md)。

- **Chief of Staff**：只做決策/分派/整合；`delegation_policy.yaml` 強制分派、不讓模型自己判斷「任務很小就自己做」；含跨領域依賴（`planning` → `knowledge`）。
- **五個 domain subagent** 全部 `active`：intelligence / engineering / automation / knowledge / planning，逐一通過 routing test。
- **consolidate-memory skill**：`memory/inbox/` 整併流程，`memory/` 已有第一筆真實內容。
- **Hermes SQLite job queue**（`hermes/db.py` + `hermes/worker.py`）：`queued/running/completed/failed/dead_letter` 狀態機、retry 指數退避、dead-letter、reaper（worker crash 回收）、session resume（`thread_id` + 24h TTL）、`cost_usd` 成本統計。
- **System Acceptance Test**：九項檢查全過（多筆 job、retry/dead-letter/reaper、同/跨 thread resume、45 分鐘長跑、jobs.db 一致性、log 完整性、delegation policy 合規、memory consolidation、成本統計）。
- **launchd 常駐部署**：`worker.py` 常駐，過程中修好兩個環境坑（`PATH` 不繼承、`KeepAlive:{Crashed:true}` 不涵蓋 `SIGKILL`）；部署層跟 Runtime 解耦，換 systemd/Docker 不用動 Runtime 程式碼。
- **Telegram Polling Adapter**：長輪詢、白名單、`delivered_at` 回覆追蹤；用真實 bot（`@CCAgenticOSbot`）完整跑過一輪收發，使用者在 Telegram 上確認收到回覆。

## 已知技術債（不影響 `v0.1-alpha` 穩定基線的判定，留著追蹤）

- `scripts/route_model.py` 的例外處理仍不完整（code review 當時列出的其餘發現，只修了路徑邊界檢查那項）
- `scripts/requirements.txt` 沒有鎖定版本
- headless 模式下，`.claude/settings.json` 的 Bash 白名單以外的指令仍會被擋（目前夠用，不是完整方案）
- `automation` 領域尚未真正串接排程去觸發 `knowledge` 的 inbox 整併

## 下一步

`v0.1-alpha` 規劃的四項能力（Cron、RSS、Dashboard，加上更早的 Telegram）都完成了，準備標記下一個里程碑。之後的方向待討論——可能是把這幾個 adapter 跑穩一段時間、或是回頭處理「已知技術債」清單、或是新的能力（例如 Dashboard 要不要開放 Telegram 之外的其他投遞管道）。
