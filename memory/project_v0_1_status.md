---
name: v0-1-domain-status
description: Current project status (v0.1-alpha/beta milestones, capability-based model routing, relocation) plus environment-specific gotchas (workspace trust, Preview tool binding) and what remains unbuilt
metadata:
  type: project
---

五個領域 subagent（intelligence / engineering / automation / knowledge / planning）已全部建立完成，並各自通過 CoS routing test（確認 CoS 會正確分派給對應 subagent_type，不會自己代打）。

Hermes SQLite job queue（`hermes/db.py` + `hermes/worker.py`）已建置完成：job schema、狀態流轉（queued/running/completed/failed/dead_letter）、retry/backoff、dead-letter、reaper（worker crash 回收）、session resume（`sessions` 表，24h TTL）都已實作並通過測試。headless 模式下的最小 Bash 白名單（py_compile/mkdir/mv/ls/find/cat）與 WebSearch/WebFetch 權限也已解決。

2026-07-04 完成一次完整的 System Acceptance Test（SAT，見 SAT_REPORT.md）：12 筆設計過的 job、retry/dead-letter/reaper 回歸、同/跨 thread session resume、45 分鐘長時間運行（531 次 poll 零錯誤）、jobs.db 一致性、log 完整性、delegation policy 合規性、memory consolidation、成本統計，九項全部通過。SAT 之後補上 `jobs.cost_usd` 結構化欄位（原本只能從 log 解析加總）。

`worker.py` 已用 launchd 常駐部署（過程中修好 PATH 不繼承、`KeepAlive` 不涵蓋 SIGKILL 兩個環境問題）。Telegram Polling Adapter 已完成並通過 live 驗證——用真實 bot `@CCAgenticOSbot` 收發過真實訊息，使用者在 Telegram 上確認收到回覆。

2026-07-04 把這個狀態標記為 **`v0.1-alpha` 里程碑**（見 ROADMAP.md），確認 Runtime／Worker／Telegram 是穩定基線後才繼續下一個能力。

同一天接著完成 Cron Adapter、RSS Adapter、Dashboard（Streamlit，localhost-only、read-only、獨立資料層不 import `hermes/db.py`），標記為 **`v0.1-beta` 里程碑**。76 個單元測試全過。

**專案位置變更（2026-07-04）**：專案根目錄從 `~/Documents/Claude Code/ClaudeCodeOS` 搬到 `~/dev/ClaudeCodeOS`——原本位置在 macOS 的 TCC 保護資料夾（`~/Documents`）底下，導致 Preview 工具的瀏覽器預覽功能連不上（`Operation not permitted`）。搬遷時同時把 worker／telegram／cron-daily-memory-check／rss 四個 launchd 服務都重新從新位置安裝（Telegram 這次是第一次變成常駐 launchd 服務，先前只有手動 `--once` 測試過）。舊位置目前保留當備份，未來除非有重大理由不再變更專案位置。

Model Router 改成 capability-based：`registry/agents.yaml` 每個領域新增 `default_capability` 欄位（唯一真相來源，subagent 檔案不再寫死 capability 名稱）。intelligence→`bulk_research`（nemotron）、engineering→`complex_coding`（GPT-5.5）、automation/knowledge/planning→`claude_native`（新增的 capability，`registry/model_router.yaml`）。`scripts/route_model.py` 沒改一行程式碼。81 個測試全過。

**環境限定的操作眉角（不是專案設計問題，是這台機器/這個 session 才有的坑）**：
- Claude Code 的 workspace trust 機制：專案搬到新路徑（或任何全新路徑）第一次用時，`.claude/settings.json` 的權限設定會被忽略，headless 呼叫直接失敗（`this workspace has not been trusted`）。要先在新路徑跑一次 `claude` 互動式接受 trust 對話框。搬家/換路徑時要記得這一步，不然背景任務會全部卡住。
- Preview 工具（`preview_start`）在**這個 session 裡**綁定的是舊路徑 `~/Documents/Claude Code/ClaudeCodeOS`，重試無效——不會因為專案搬家跟著換。要用瀏覽器看 Dashboard，得手動起一個 streamlit server 自己開瀏覽器連，或是在新路徑開一個全新的 session。

**2026-07-06（來源：Hermes 技能自主學習偵測，`hermes_bridge.py` → job queue → headless CoS）**：`hermes/adapters/hermes_bridge.py` 的技能同步管線第一次端到端跑通——雜湊比對偵測到技能變更、經由 job queue enqueue、worker 派工、headless CoS 被 `claude -p` 觸發，整條路徑證實可動。這次觸發的變更是測試用技能 `_hermes_bridge_test`，內容本身無保留價值，但「管線已驗證可動」這個里程碑值得記住。之後若再看到 `_hermes_bridge_test` 或其他明顯測試性質的技能變更通知，可視為管線健康檢查訊號，不需要每次深究。

**2026-07-07（`ClaudeCodeOSWin`：Windows/WSL2 複本首次完整跑起來）**：把 `ClaudeCodeOS` 複製到 Windows 機器（WSL2 Ubuntu），完成端到端 bring-up——WSL2 + systemd 確認可用（這次用的 Ubuntu 映像檔預設就內建 `systemd=true`，不需要手動改 `/etc/wsl.conf`）、專案搬到 WSL2 原生檔案系統 `~/dev/ClaudeCodeOSWin`（而非 `/mnt/c/...`）、Claude Code CLI 原生安裝於 Ubuntu 內並完成授權、Python venv 在 WSL2 內重新建置（複製過去的 macOS 版 `.venv` 不可沿用）、五個常駐服務（`hermes-worker`、`hermes-telegram`、`hermes-rss`、`hermes-cron-daily-memory-check`、`hermes-bridge`）全部安裝並驗證為 active/排程正常。過程中「workspace trust」這個環境眉角（見上方 2026-07-04 那條）在新機器/新路徑上原樣重現——systemd 服務第一次非互動執行時全部因為 workspace 未被信任而失敗（連鎖顯示成 "Not logged in · Please run /login"，具誤導性），手動互動跑一次 `claude`/`invoke_cos.sh` 接受信任對話框後即自動解除，不需要額外設定；這證實了這個坑是「每次搬到新路徑都會重現」的通用模式，不是單一環境的偶發問題。RSS、Telegram 兩條路徑都各自驗證過至少一筆成功處理的 job。細節見 [WINDOWS_WSL_SETUP.md](../WINDOWS_WSL_SETUP.md)。

**2026-07-09（目標環境確立為 ClaudeCodeOSWin；Hermes 整合 Stage 0–1 完成）**：目標環境正式改為 Windows/WSL2（`C:\Users\razer\dev\ClaudeCodeOSWin`），macOS launchd 全面降級為 legacy/reference（文件已逐檔修正，live runtime 是 WSL2 systemd）。同日完成：Stage 0 Hermes 共用儲存（Windows `state.db` 為唯一 Source of Truth，WSL 經 symlink 共用，見 docs/hermes-shared-storage-bootstrap.md）、HermesSessionAdapter（read-only，hermes/session_adapter/）、memory taxonomy 與 N-gate consolidation 政策（docs/memory-taxonomy.md + registry/consolidation_policy.yaml，daily-memory-check prompt 已接上）、Capability Lane 與 Bridge State 兩個 schema（registry/capability_lanes.yaml、registry/bridge_state_schema.yaml，Stage 2 只定義未實作）。venv 已整併為單一 Windows 原生 `.venv`（Python 3.11，`Scripts\` 結構，mac/Linux 路徑引用已全案轉換）。**重要眉角**：(1) Windows repo 與 WSL 側部署複本（`/home/razer/dev/ClaudeCodeOSWin`，五個 systemd 服務從這裡跑）是兩份獨立複本，Windows 側的設定變更不會自動到達部署側，需同步機制（待 automation 定義）；(2) 喚醒 WSL distro 會觸發 systemd persistent timer 的 catch-up 補跑，可能立即消耗 headless 呼叫；(3) Windows Hermes（gateway/dashboard/Desktop）運行中時，WSL 側對 state.db 的任何存取會被互斥擋下（設計內防損壞行為，維護窗口才能全功能存取）。

**2026-07-09（Stage 1 checkpoint：Pre-Bridge Foundation 完成）**：Stage 1 正式標記完成（見 docs/stage1-checkpoint.md，roadmap 已同步更新）。驗證基準：123 tests / 0 失敗、registry 6/6 yaml、WSL 5/5 units active、兩側同步 0 檔案差異、state.db 62 sessions / 3,156 messages（計數口徑差異已結案：3,156 是 adapter 預設的 active-only 口徑，Hermes CLI stats 是全量口徑 5,316；active 3,156 + compacted 2,160 = 5,316 互補驗證，非資料異常——比較基準時要注意口徑）。部署同步機制上線（scripts/sync_to_wsl.sh，telegram.json 等密鑰分側維護不同步；ClaudeCodeOS bot＝控制入口、Hermes profile bots＝對話入口）。**範圍重定義**：原 Stage 1 DoD 1/2（真實 session 實走 to-inbox → consolidate → 正本）未執行、不計入完成，列為 Stage 2 開工 gate 的第一優先；其餘 gate：bridge 側別（初判 WSL 側＋snapshot 阻力小）、bridge state 載體、model_router TODO 佔位值、Stage 0.5 四殘項、`git init`（repo 目前無版控，多項交付因此無獨立 rollback 載體）。

**2026-07-10（Stage 2 三項前置決策拍板；gate 全數解除）**：使用者拍板——(1) **Bridge 側別＝WSL 部署側**（worker/jobs.db/timers/logs 都在 WSL，降低排程與 enqueue 複雜度；state.db 維持唯讀來源，被鎖時走 snapshot/immutable 路徑，絕不寫回）；(2) **Bridge state 載體＝獨立 SQLite `hermes/state/bridge_state.db`**（只記 ClaudeCodeOS 側處理狀態，不是 Hermes memory DB、不是第二份 state.db；hermes/state/ 在 .gitignore 與 sync 排除清單，天然只存在於部署側；memory/inbox 仍只是落地區不當狀態庫）；(3) **Capability Lane 不接自動路由**（capability_lanes.yaml 維持 reference/planning 層，model_router TODO 值不硬接，bridge 穩定後 Stage 2.x/3 再整合）。同日稍早：to-inbox idempotency blocker 已修復（deterministic 檔名 `hermes_session_<id>.md`＋.processed/.failed 掃描＋exit code 3，30 tests）並已同步部署側；真實 session import gate 實走完成（`20260628_004555_13dd7b` → reference_hermes_workspace.md）。Stage 2 開工 gate 全數解除，殘餘 Stage 0.5 四小項為非阻塞。git baseline：03c7a0e → eb08b8d → 51ba85a → 199d741。

**2026-07-10（Stage 2.1–2.3 完成：schema 對齊、repository 層、scanner）**：Stage 2.1 schema 對齊（17 欄，7→10 tests）；Stage 2.2 bridge_state repository（`hermes/bridge_state.py`，部署側 db 已 init）；Stage 2.3 scanner（`hermes/bridge_scanner.py`，scan/reconcile 分離、七條硬條件全有測試把關）。部署側真實寫入已執行：reconcile 回填 gate session 為 `imported`（1 筆，檔名比對依據）、scan cutover 後 0 筆、冪等驗證通過、禁區零修改。**Cutover policy：`2026-07-10T00:00:00Z`（bridge 啟用日）——目前只是人工 `--since` 操作參數，無獨立 watermark 儲存；Stage 2.4 排程化前必須設定化，不能依賴人工記憶。**

**2026-07-10（Stage 2.4a/2.4b 完成：cutover 設定化＋scanner 排程上線）**：2.4a——cutover 底線進 `hermes/config/bridge.yaml`（fail loud 絕不默認全掃），watermark 進 bridge_state.db `bridge_meta`（只前進不後退、dry-run 不推進、失敗不推進；上界＝snapshot 前時間戳，窗口寧可重疊不跳漏）；無參數 scan＝安全預設 max(cutover, watermark) 並標示來源。2.4b——`hermes-bridge-scanner.timer` 每日 08:05 CST（落在 08:00 memory-check 與 08:10 skill-sync 之間），oneshot 無 Restart，守門測試鎖定「排程一律無參數 scan」；部署側五項完成標準全過後才 enable，timer 已上線。**管線現況：偵測→discovered 全自動；discovered→inbox（政策判定＋敏感偵測器）為下一階段 2.4c，是整條管線唯一涉及內容判斷的環節。**

尚未動工：
- Model Router 的 MCP server 版本（目前 script adapter 夠用）
- Dashboard 要不要開放 Telegram 以外的投遞管道
- Stage 2.4c：discovered → inbox（政策判定 headless fail-closed、敏感偵測器實作、to-inbox／enqueue 串接）
