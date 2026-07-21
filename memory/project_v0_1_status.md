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

**2026-07-10（Stage 2.4c 完成：bridge importer 實作＋部署＋敏感阻擋實地驗證通過）**：
`hermes/bridge_importer.py`（discovered→政策判定→inbox 落地）完成，25 tests 全綠、
既有全套零回歸；程式已下發部署側，敏感 fail-closed 路徑已實地跑過驗證（命中敏感內容
時正確轉 `needs_review`、不落地、decision_reason 只記類別標籤）。**管線現況：
偵測→discovered→inbox 落地全部可動；尚未 enqueue、尚未接 headless CoS、尚未裝
importer 排程**（人工 CLI 執行，維持 2.4c 既有邊界）。

**2026-07-11／12（Stage 2.4d-1 完成：episode capture schema v2＋repository 層）**：
實測發現 Desktop／TUI 的 `ended_at` 結構性不可靠（64 個既有 session 只有 20 個
有值，Desktop 0/2、TUI 8/46），Stage 2 匯入單位改為 episode／capture checkpoint
（同一 session 可切多個 immutable episode），設計見
docs/stage2.4d-episode-capture-proposal.md（已核准）。`registry/bridge_state_schema.yaml`
升級為 v2（`bridge_sessions` 17→22 欄＋新表 `bridge_cursors`）；`hermes/bridge_state.py`
加上 `create_episode`（原子＋UNIQUE conflict 冪等）、`migrate` CLI、content hash 純函式；
`hermes/session_adapter/adapter.py` snapshot 一致性補強（fingerprint＋retry＋
`quick_check`）；archived trigger 契約已用真實 Desktop session 實地驗證（Archive
0→1 持久、`ended_at`／內容不變，後續收斂為 level-triggered 語義：只看當下值，
不追蹤轉換）。

**2026-07-12（Stage 2.4d 全鏈路完成並上線：operational acceptance checkpoint）**：
2.4d-2（scanner episode 偵測：`ended`／`archived`／`inactivity` 三型 trigger、
`checkpoint` 手動子指令，含 stale-ended 修正——`ended_at` 過期時正確回退檢查
inactivity，不再永久卡死復活 session）→ 2.4d-3（importer episode 化：range
export、episode-aware 查重、reconcile cursor recovery，含 capture_trigger 缺失
fail-closed 修正——recovery 不猜測 trigger 來源）→ 2.4d-4（部署 migration＋上線）
全部完成，測試矩陣（提案 §10）全綠。

**部署現況**：`episodes.enabled=true`、`episode_cutover=2026-07-12T06:36:18Z`
（部署翻 enabled 前的精確 UTC 時刻）；`hermes-bridge-scanner.timer` active／
enabled，每日 08:05 CST，WSL 睡眠期間錯過的觸發由 `Persistent=true` 補跑
（既有行為）。importer 仍未排程化（人工 CLI）、未 enqueue、未接 headless CoS。

**✅ 第一筆真實正常 episode 端到端驗收通過**：`session_id=20260712_164627_419d23`、
`event_id=hermes:20260712_164627_419d23:6991..7022`、`trigger=archived`、
boundary `6991..7022`、cursor `last_captured_message_id=7022`／`episode_seq=1`。
政策 allow（useful／length／sensitive／hash 全通過）→ 落地
`memory/inbox/hermes_session_20260712_164627_419d23_ep6991-7022.md`（檔名／
event_id／frontmatter 三處一致）→ 最終狀態 `to_inbox`／`episodic`／
`useful_chat=true`。scan／importer／reconcile 重跑皆冪等（boundary／hash／
cursor／`imported_inbox_path` 不變、零重複落地）。Hermes `state.db`／
`jobs.db`／`telegram.json` 全程 fingerprint 零 bridge 寫入。過程中另有一筆
too_short 的 archived episode 正確判定 `skipped`（4 事件僅 2 個 message 型，
低於 4 則門檻——字元數足夠但 message 型事件不足，證明結構性排除非 bug）。

**已知非阻塞缺口**：Unarchive（`archived` 1→0）對真實 Hermes Desktop UI 的
live round-trip 驗證尚未執行（僅 fixture 邏輯驗證），不影響已上線的
level-triggered 判斷。

**WSL 開發環境現況（2026-07-12 釐清，避免與 scanner timer 的 Persistent catch-up
行為混淆）**：目前 WSL 側是 **on-demand**——互動 session 手動用 `wsl.exe` 呼叫、
用完不常駐；不是 always-on production 環境。這與 `hermes-bridge-scanner.timer`
的 `Persistent=true` 行為要分開理解：timer 排程本身已部署為系統 systemd 常駐
timer（見 hermes/systemd/），但 **WSL distro 本身不是持續開機的**——distro 睡眠
期間排定的觸發時刻會被跳過，等下次有人手動喚醒 WSL（例如開一個互動 session）時，
systemd 的 `Persistent=true` 才會觸發 catch-up 補跑一次錯過的排程。因此「timer
已 enable、每天 08:05 觸發」不代表這台機器天天都有人在 08:05 那個時間點真的
跑過 scan——多數情況下是之後某次手動喚醒 WSL 時補跑的。

**2026-07-20（Model Router 移除 OpenRouter 路由；上方 07-04 記錄的 capability 對照已過期）**：
`OPENROUTER_API_KEY` 自系統建成以來從未真正設定過（無 `.env`、無 shell 環境變數），
三條 OpenRouter 路由（`complex_coding`／`google_ecosystem`／`bulk_research`）實務上
從未打通；使用者拍板全部移除。`engineering`／`intelligence` 的 `default_capability`
改回 `claude_native`，跟 `automation`／`knowledge`／`planning` 一致——**五個領域現在
全部是 `claude_native`**。`complex_coding`／`bulk_research` 這兩個 capability key
保留在 `registry/model_router.yaml`（`via: native`），因為 `hermes-nemocoding`／
`hermes-gptcoding`／`hermes-financialresearch`／`hermes-intelligence` 四條 active
Hermes lane 仍引用其名稱做 capability 分類，只是不再對外呼叫；`google_ecosystem`
key 因無任何殘留參照而完全移除。上方 2026-07-04 那條記錄的「intelligence→
bulk_research（nemotron）、engineering→complex_coding（GPT-5.5）」對照自此日起
已不成立，見 commit `b910312`。

尚未動工：
- Model Router 的 MCP server 版本（目前 script adapter 夠用）
- Dashboard 要不要開放 Telegram 以外的投遞管道
- Stage 2.5：episode/legacy discovered → enqueue → headless CoS 的自動化串接（importer 排程化）
- Unarchive live round-trip 對真實 Hermes Desktop UI 的驗證（非阻塞 test gap）
