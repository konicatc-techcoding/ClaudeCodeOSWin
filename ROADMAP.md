# Roadmap — Claude Code OS

> **目前部署環境（2026-09-03 更新）**：目標環境是 **Windows/WSL2**（本 repo 位於原生
> Windows `C:\Users\razer\dev\ClaudeCodeOSWin`，見 [WINDOWS_WSL_SETUP.md](WINDOWS_WSL_SETUP.md)）。
> **runtime 是雙軌，不是單軌**：
> - **WSL2 側 `systemd --user`**（`hermes/systemd/`）跑 worker、Telegram 常駐 service，
>   以及 `hermes-cron-daily-memory-check`、`hermes-rss`、`hermes-bridge` 三個 timer。
> - **Windows 側的 bridge（127.0.0.1:8787）與 Task Scheduler 排程已上線數月，是主要
>   runtime 的一部分**——`HermesWslKeepAlive`（含 TimeTrigger PT15M backstop）、
>   Web UI 觀測面 stack，以及 bridge-scanner／pipeline／notifier 這三條實際跑在
>   Windows Task Scheduler 上的排程都在這一側。
>
> ⚠️ 上面第二點在 2026-09-03 之前本檔寫的是「未來選項（Stage 2 設計決策），尚未實作」，
> **那是錯的、且會誤導新 session**，已更正。
>
> **一個尚未拍板的落差**：`hermes/systemd/` 裡的 `bridge-scanner`／`pipeline`／`notifier`
> 三個 unit **repo 有、WSL 沒裝**，同名功能實際跑在 Windows Task Scheduler。這是刻意雙軌
> 還是遺留、以及 repo 內三個 unit 檔的去留，**尚未決定**（見 [STATUS.md](STATUS.md) 第 3 節），
> 本檔不對此下結論，只誠實記錄雙軌現況。
>
> 下方 milestone 表裡的「launchd 常駐」是當時 macOS 環境的歷史記錄，原樣保留；
> `hermes/launchd/` 目錄僅為 macOS legacy/reference，不再是 live runtime。
> Hermes 整合軌（Stage 0–5）的完整定義、DoD 與證據見
> [docs/hermes-integration-roadmap.md](docs/hermes-integration-roadmap.md)；
> 「現在在哪」見 [STATUS.md](STATUS.md) 第 1 節。本檔只保留里程碑層級的一行摘要，
> 不重複追蹤該軌細節。

> ⚠️ **`Phase 2a–2h` 這套編號不可信，引用前必須先查證語意**（2026-09-03 查證所得）。
> 它是 2026-07-20/21 一次長 session 事後貼上的流水帳式編號，**不是規劃里程碑**：
> `Phase 2c` 全 repo 零命中；`2e`／`2f` 一號多義（`registry/capability_lanes.yaml:298`
> 指憑證清理、`docs/hermes-integration-roadmap.md:659` 指 lane 轉 active＋補文件、
> `memory/hermes-task-category-model-routing-preference.md` 指「讓 subagent 真正呼叫」）；
> 且這套編號**從未進過任何 commit message**。它與 `Stage N` 編號體系沒有對應關係——
> 談階段請一律用 Stage，不要用 Phase 2x。

## 節奏

每個主要能力都走同一個順序，不同時開兩個：**實作 → 驗證 → Commit → Milestone**。前一個里程碑沒確認是穩定基線，不開始下一個能力的實作。

## Milestones

| Milestone | 狀態 | 日期 | 內容 |
|---|---|---|---|
| `v0.1-alpha` | ✅ 已達成 | 2026-07-04 | Runtime 核心穩定基線：CoS + 五個 domain + delegation policy、consolidate-memory、Hermes job queue、SAT 九項全過、launchd 常駐、Telegram Polling Adapter（live 驗證通過） |
| `v0.1-beta` | ✅ 已達成 | 2026-07-04 | `v0.1-alpha` 之上補完全部 event source（Telegram 已在 alpha 完成；這次加上 Cron、RSS）+ Dashboard；76 個單元測試全過，jobs.db 一致性複查零違規 |
| Cron Adapter | ✅ 已完成 | 2026-07-04 | 無狀態、排程交給部署層；正式改裝 `daily-memory-check` 每天 08:00（macOS 期為 launchd，現為 WSL2 systemd timer） |
| RSS Adapter | ✅ 已完成 | 2026-07-04 | 無狀態、抓取/去重/`feedparser` 解析/`enqueue()`；真實 feed（hnrss.org）驗證過，正式改裝 30 分鐘排程 |
| Dashboard | ✅ 已完成 | 2026-07-04 | Streamlit，localhost-only、read-only（Streamlit 版本 2026-08-15 退役 `55e249d`，由 Stage 5 Web UI 取代） |
| Stage 0 — Hermes Shared Storage Bootstrap | ✅ 完成 | 2026-07-07 | Hermes 共用儲存層盤點與基線 |
| Stage 0.5 — 平台收尾清單 | ✅ 全數結案 | 2026-07-20／2026-09-03 | gateway 自啟、profile 處置、codereviewer 去留等先行結案；最後三項殘項於 2026-09-03 查證後**全數判定過時**（是「已無意義」不是「未完成」） |
| Stage 1 — Pre-Bridge Foundation | ✅ 完成 | 2026-07-09 | 交付與證據見 [stage1-checkpoint.md](docs/stage1-checkpoint.md)；同日完成 Windows 側 `git init`（baseline `03c7a0e`） |
| Stage 2 — Session Bridge 自動化（2.1–2.4d） | ✅ 全鏈路完成上線 | 2026-07-12 | bridge_state schema v1 → scanner → cutover policy → Episode Capture 全鏈路 |
| Stage 2.5 — Episode Triage & Queue Foundation | ✅ 完成並關閉 | 2026-07-17 | 提案收斂至 v6 後核准實作 |
| Stage 2.6 — Domain Dispatch | ✅ 完成並關閉 | 2026-07-17 | 提案 v2 九項拍板後同日核准實作 |
| Stage 2.7 — Notification & Scheduling | ✅ 完成並關閉 | 2026-07-18 | 通知與排程化 |
| Stage 4 — CoS → Hermes 執行橋接 | ✅ 完成 | 2026-07-20～21 | Domain Execution Router、憑證獨立化、Telegram 推播 |
| Stage 3 — Hermes Session 檢視頁 | ✅ 完成（載體改新 Web UI） | 2026-07-24 | 2026-07-23 拍板方案 B 後凍結 Streamlit 實作，DoD 四條透過 Stage 5 P2 在新載體達成 |
| Stage 5 — Web UI 遷移 | ✅ 四個 phase 全部完成 | 2026-07-23～24（Streamlit 退役 2026-08-15） | 新 Web UI（`webui/`，Vite+React）＋唯讀 API＋PTY 終端機；Streamlit 退役後無剩餘事項 |

**Stage 0–5 目前全數關閉。** 各 Stage 的階段定義、DoD 與驗收證據一律以
[docs/hermes-integration-roadmap.md](docs/hermes-integration-roadmap.md) 為權威來源，
本表只放一行摘要；表列順序為「階段編號」而非完成時間（Stage 4 早於 Stage 3 完成，
因為 Stage 3 的載體被 Stage 5 取代）。

## `v0.1-alpha` 涵蓋範圍

詳細設計見 [ARCHITECTURE.md](ARCHITECTURE.md)，測試證據見 [SAT_REPORT.md](SAT_REPORT.md)、[INTEGRATION_TEST.md](INTEGRATION_TEST.md)，Hermes 細節見 [hermes/README.md](hermes/README.md)、[hermes/DESIGN.md](hermes/DESIGN.md)。

- **Chief of Staff**：只做決策/分派/整合；`delegation_policy.yaml` 強制分派、不讓模型自己判斷「任務很小就自己做」；含跨領域依賴（`planning` → `knowledge`）。
- **五個 domain subagent** 全部 `active`：intelligence / engineering / automation / knowledge / planning，逐一通過 routing test。
- **consolidate-memory skill**：`memory/inbox/` 整併流程，`memory/` 已有第一筆真實內容。
- **Hermes SQLite job queue**（`hermes/db.py` + `hermes/worker.py`）：`queued/running/completed/failed/dead_letter` 狀態機、retry 指數退避、dead-letter、reaper（worker crash 回收）、session resume（`thread_id` + 24h TTL）、`cost_usd` 成本統計。
- **System Acceptance Test**：九項檢查全過（多筆 job、retry/dead-letter/reaper、同/跨 thread resume、45 分鐘長跑、jobs.db 一致性、log 完整性、delegation policy 合規、memory consolidation、成本統計）。
- **launchd 常駐部署**：`worker.py` 常駐，過程中修好兩個環境坑（`PATH` 不繼承、`KeepAlive:{Crashed:true}` 不涵蓋 `SIGKILL`）；部署層跟 Runtime 解耦——後來實際換成 WSL2 systemd，Runtime 程式碼確實沒動。
- **Telegram Polling Adapter**：長輪詢、白名單、`delivered_at` 回覆追蹤；用真實 bot（`@CCAgenticOSbot`）完整跑過一輪收發，使用者在 Telegram 上確認收到回覆。

## 已知技術債（2026-09-03 逐項複查；標記為複查當下的判定）

1. **`scripts/route_model.py` 的例外處理仍不完整** — **仍成立（但範圍已大幅縮小）**。
   原 code review 針對的 `call_openrouter` 對外呼叫路徑已於 2026-07-20 整段移除，
   相關的網路／HTTP／JSON 例外處理已不存在、也不需要。**剩下兩處仍成立**：
   (a) `load_config()`（:45-47）對 `registry/model_router.yaml` 缺檔或 YAML 解析失敗
   沒有任何處理，會直接吐 traceback；
   (b) **與 `dispatch_domain.py` 行為不一致**——`resolve_route()`（:50-54）對**未知
   capability 靜默回退**成 `{"model": default, "via": "native"}`，而 `dispatch_domain.py:193`
   對同一份 `model_router.yaml` 的同一種錯誤明確拋 `DispatchError("registry_error", ...)`。
   一個吞掉、一個 fail loud。**這算不算「例外處理不完整」可以爭論，但它是真實的行為
   不一致，且靜默的那一側正是使用者手打 capability 的入口**——判定為仍該處理
   （改成 fail loud，或明文記錄「靜默回退是刻意的」，二選一）。

2. **`scripts/requirements.txt` 沒有鎖定版本** — **仍成立**。檔案內容至今仍是
   `pyyaml` / `feedparser` 兩行裸套件名，零版本約束。

3. **headless 模式下 `.claude/settings.json` 的 Bash 白名單以外指令仍會被擋** —
   **仍成立，但性質已從「限制」變成「知情的取捨」**。allow 清單至今仍只有九條
   （`py_compile`／`mkdir`／`mv`／`ls`／`find`／`cat` 等）加 `memory/inbox/**` 讀寫。
   實務上有兩條繞道使它不再是痛點：triage 走 `hermes/adapter/invoke_cos_triage.sh` 的
   `--permission-mode dontAsk` ＋極窄 `--allowedTools`（刻意 fail-closed，不是被擋到）；
   需要真正動手的工作走 `scripts/dispatch_domain.py` 或前台 session。**沒有找到任何
   「因為白名單而失敗」的近期事證。** 附帶提醒：`.claude/settings*.json` 是
   `scripts/sync_to_wsl.sh` 的排除項，WSL 側白名單要單獨維護。

4. **`automation` 領域尚未真正串接排程去觸發 `knowledge` 的 inbox 整併** —
   **原敘述已作廢（接線早就做了），但取而代之的是更嚴重的問題：這條鏈已死一個月。**
   - **接線確實存在，且 `daily-memory-check` 確實涵蓋 inbox 整併**：
     `hermes/config/cron_jobs.yaml` 的 prompt 第 2–3 步就是「檢查 `memory/inbox/` →
     對照 `registry/consolidation_policy.yaml` 的 N-gate 門檻 → 達標即分派 `knowledge`
     執行 consolidate-memory」，第 4 步另含 retention review。
   - **但它從 2026-08-04 起每一輪都失敗**（`hermes/jobs.db` 實查，WSL 側）：
     `source` 為 cron 的 job 統計是 2026-07 完成 14／dead_letter 4、**2026-08 完成 3／
     dead_letter 25、2026-09 dead_letter 3**；**最後一次成功是 2026-08-03**，之後連續
     28 輪全部 `dead_letter`，錯誤訊息一律 `invoke_cos.sh exit code 1`（stderr 為空）。
   - **這正是 inbox 最舊檔案（2026-07-31）掛一個月沒被收的原因**——N-gate 的
     `max_pending_age_days: 7` 早就達標，斷的是**整條 cron → headless CoS 執行鏈**，
     不是門檻沒過、更不是沒接線。批次 0 要「手動」派 knowledge 跑 `/consolidate-memory`
     才清得掉，根因在此。
   - ⚠️ **失效起點 2026-08-04 與 hermes-agent 兩側升 v0.19.1 是同一天**，高度可疑，
     但**本次未查證因果**（`invoke_cos.sh` 只吐 exit code 1、無 stderr，需另開一次
     engineering 診斷）。**建議升級為獨立待辦，優先級高於原第 4 項技術債。**

## 下一步

**Stage 0–5 全數關閉，目前沒有任何「開工中」的階段。** 系統面沒有做到一半的實作，
剩下的是階段遺留與待拍板議題（清單見 [STATUS.md](STATUS.md) 第 3 節）。

2026-09-03 由 `planning` 產出**帶優先序的七批次開工順序，序列執行**（沿用本檔
「不同時開兩個能力」的節奏）：

0. **清場** — ✅ 已完成（2026-09-03）：工作樹乾淨、inbox 清空、Stage 0.5 結案、觀察項補到期日。
1. **止血** — ✅ 已完成（2026-09-03）：未 push 的客製 commit 不能被桌面 Install 鈕靜默吃掉。
2. **校正脈絡** — 本檔四處翻修 + 技術債四項判定 + `scripts/dispatch_domain.py` docstring。
3. **規則引擎（依任務類型自動選模型）** — 起草前**先拍板**兩題：循環依賴、規則是建議還是強制。
4. **集中拍板（60–90 分）** — 議程第 0 項為批次 3 的兩題，其餘議題見 STATUS.md 第 3 節。
5. **執行拍板結果**。
6. **低優先收尾**。

批次的細節與各項依據見 [STATUS.md](STATUS.md) 第 4 節（權威來源），本節只留骨架。
