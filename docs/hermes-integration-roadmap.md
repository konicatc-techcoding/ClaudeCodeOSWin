# Hermes Integration Roadmap — Stage 0 之後的階段性里程碑

日期：2026-07-07　狀態：**規劃定稿（v1）**　負責規劃：planning domain
**更新（2026-07-09）**：Stage 1 標記完成（範圍重定義為 **Pre-Bridge Foundation**，
見 [stage1-checkpoint.md](stage1-checkpoint.md)）；Stage 1.4（部署同步）補進歷史；
Stage 2 前置條件依 checkpoint 未完項更新。
**更新（2026-07-10）**：Stage 2 三項前置決策已由使用者拍板並記錄（側別＝WSL 部署側、
載體＝`hermes/state/bridge_state.db`、不接自動路由）；原 Stage 1 DoD 1/2 實走完成
（idempotency 修正已下發部署側）——**Stage 2 gate 已解**，詳見 Stage 2 節。
**更新（2026-07-12）**：Stage 2.5（Episode Triage & Queue Foundation）規劃提案已產出
（[stage2.5-episode-triage-proposal.md](stage2.5-episode-triage-proposal.md)），
待使用者核准；本次更新同時修正 Stage 2 原始設計敘述中已被 2.4c/2.4d 取代的部分
（見下方 Stage 2 節與新增的 Stage 2.5 節）。
**更新（2026-07-12，第二次）**：Stage 2.5 提案依使用者 7 點回饋收斂為 **v3**——
job identity 改為三元組、正式設計 dead-letter recovery 機制、修正執行語意措辭、
解決 2.5b 候選查詢的 N-gate 遺漏、鎖定 no-tools 入口點設計（但技術可行性列為
明確的 start blocker）、鎖定模型／決定性契約參數。**v3 仍有 2 項 start blocker
尚未解除**（見提案文件第 18 節），Stage 2.5c 在解除之前不建議開工。
**更新（2026-07-12，第三次）**：Stage 2.5 提案依使用者 8 點精確度回饋收斂為
**v4**——拿掉 2.5b 候選資格判定裡會靜默略過 `payload_hash` 漂移的前置過濾
條件（改為每個候選一律呼叫或模擬呼叫 `enqueue_once`）；dead-letter recovery
補上 append-only 稽核表 `job_requeue_events` 並改寫為 atomic conditional
state transition；把 `hermes/worker.py` 的 source-specific dispatch 正式
納入 2.5c 範圍；精確化「不能寫檔案」的邊界（handler／模型層 vs queue
infrastructure 層）；**統一 blocker 敘事——v4 現在只剩一項真正的硬 start
blocker**（no-tools 技術強制力，見提案第 18 節），另一項移為 2.5c 實作期間
的技術決策（提案第 8 節）。
**更新（2026-07-12，第四次）**：Stage 2.5 提案依使用者 3 點精確度回饋收斂為
**v5**——(1) 並行 requeue 的 SQLite 語意精確化：查讀 `hermes/db.py` 實際的
`_db()`／`get_connection()` 後確認並行呼叫的輸家不一定乾淨拿到
`rowcount=0`，WAL 模式下可能撞到 `SQLITE_BUSY`／`SQLITE_BUSY_SNAPSHOT`，
`requeue_dead_letter()` 改寫為四分支並行安全狀態機（新增
`RequeueRetryableDBError` 例外類別），明確不改動共用 `_db()` 的全域鎖定
策略；(2) 把「不做 dispatch」的籠統措辭拆成兩層——Stage 2.6 的 domain／
action dispatch（禁止）vs 2.5c 執行 triage job 本身所需的 worker
source-specific execution routing（允許、屬本階段必要範圍），避免誤讀；
(3) `requeue_dead_letter()` 的 `actor` 參數新增顯式驗證（空字串／純空白
一律拒絕，先於任何 DB 操作），CLI 不提供任何掩蓋身份的假預設值。
**更新（2026-07-17）**：Stage 2.5 提案先收斂為 **v6**（分支 4c／4d 測試改為
決定性 fault injection，見提案第 4.1d、19 節）並經使用者核准開工；**同日
2.5a／2.5b／2.5c 實作、WSL 部署與 2.5d 驗收全部完成，Stage 2.5 全階段
✅ 完成並關閉**——2.5c 唯一 start blocker（no-tools 技術強制力）已於開工前
實測解除（最終旗標組合與實測發現見提案第 18 節解除紀錄），實作 commit、
測試數、部署證據與 2.5d 驗收紀錄見提案**第 20 節**與下方 Stage 2.5 節。
下一階段為 **Stage 2.6 — domain dispatch（尚未規劃）**。
**更新（2026-07-17，第二次）**：Stage 2.6 規劃提案同日產出並收斂為 **v2**
（[stage2.6-domain-dispatch-proposal.md](stage2.6-domain-dispatch-proposal.md)，
九項開放問題全數拍板、皆採建議值），隨後 **2.6a／2.6b／2.6c 實作、WSL 部署
與 2.6d 驗收同日全部完成，Stage 2.6 全階段 ✅ 完成並關閉**——完工紀錄正本
見提案 **§15**、摘要見下方新增的 Stage 2.6 節；優先順序總結同步更新
（下一個要規劃的是 **Stage 2.7 — Slack 投遞與排程化**，或直接進 Stage 3）。
**更新（2026-07-18）**：Stage 2.7 規劃提案同日產出並收斂為 **v2**
（[stage2.7-notification-scheduling-proposal.md](stage2.7-notification-scheduling-proposal.md)，
九項開放問題全數拍板、皆採建議值），隨後 **2.7a／2.7b 實作、2.7c 部署與
真實驗收同日全部完成，Stage 2.7 全階段 ✅ 完成並關閉**——過程中發現並解除
了提案 §13 唯一的 start blocker（WSL 側 Hermes-agent 套件版本落後 Windows
main 1223 個 commit，比原評估嚴重許多，處置故事詳見提案 §15.2）；完工紀錄
正本見提案 **§15**、摘要見下方新增的 Stage 2.7 節；優先順序總結同步更新
（下一階段為 **Stage 3**，或視需要另開新規劃；2026-07-19 08:15／08:25 兩組
timer 首次自然觸發待確認，見提案 §15.4 遺留事項）。
**更新（2026-07-21）**：新增 **Stage 4 — CoS → Hermes 執行橋接**（Domain
Execution Router、憑證獨立化、Telegram 推播；2026-07-20～21 於一次很長的
互動式 session 中完成，見下方新增小節）——資料流向與 Stage 2.x 相反
（Stage 2.x 是「Hermes → 匯入進 ClaudeCodeOS 記憶」，本階段是「ClaudeCodeOS／
CoS → 主動呼叫出去執行 Hermes」），故獨立列階，不掛在 Stage 2.x 底下。
**排列順序提醒**：Stage 4 依使用者裁示排在文件順序中 Stage 3 之後，但 Stage 4
實際完成時間點在 Stage 3 開工之前——這純屬文件排列順序（配合「反方向」的
分類理由），不代表 Stage 4 依賴 Stage 3、也不代表兩者有時間先後關係。
**更新（2026-07-23）**：新增 **Stage 5 — Web UI 遷移（Dashboard 全面轉移）**
——使用者拍板推翻 stage3 提案 v2 §0.1「不另開新入口」，dashboard 觀測功能
全面轉移到以 AgentOSUI 範本為雛形的新 Web UI（規劃提案
[webui-migration-proposal.md](webui-migration-proposal.md) 同日 v1→v2 收斂，
§9 六項拍板後核准可開工，含 bridge 最小寫入例外的使用者親定安全規格）；
**Stage 3 三項功能凍結於 Streamlit 載體**，設計正本仍為
[stage3-dashboard-observability-proposal.md](stage3-dashboard-observability-proposal.md)
v2 §2–4，實作載體改為 Stage 5 的 P2（見 Stage 3 節註記與下方新增的
Stage 5 節）。
**更新（2026-07-24）**：**Stage 5 四個 phase（P0–P3）全部完成並經使用者
驗收**——P0 `bf2bfe2`／P1 `f1b9104`／P2 `3da90ae`／P3 `0bbd6c1`（P3＝PTY
終端機，提案 [webui-pty-terminal-proposal.md](webui-pty-terminal-proposal.md)
2026-07-23 同日 v1→v2 全案核准後實作）；P2 上線首日即支撐一次真實憑證稽核
與清理（零明文接觸）、統一排程健康表抓到 `aichain-orchestrator-daily` 失敗。
Stage 3 四條 DoD 已透過 Stage 5 P2 在新載體達成。剩餘：**Streamlit 並行
觀察期自 2026-07-24 起算**，期滿後退役決策。見下方 Stage 5 節完工摘要。

這份文件是 **Hermes 整合軌**（Hermes ↔ ClaudeCodeOS 資料與記憶管線）的階段性里程碑，接續
[docs/hermes-shared-storage-bootstrap.md](hermes-shared-storage-bootstrap.md)（Stage 0 報告）。
它不取代根目錄的 [ROADMAP.md](../ROADMAP.md)——那份追蹤的是整個系統的 `v0.1-alpha`／`v0.1-beta`
里程碑；本文件只管 Hermes 整合這一條線。若未來要把本軌某個 stage 標成系統級里程碑
（例如 `v0.2`），由互動式 CoS session 更新根 ROADMAP.md，不在本文件內處理。

**既定前提**：Windows Hermes（`C:\Users\razer\AppData\Local\hermes`）為唯一 Source of Truth。
所有 stage 都不得產生第二份 state.db、不得寫入 Hermes 原始資料（adapter 的 read-only 保證是硬性邊界）。

節奏沿用根 ROADMAP.md 的原則：**實作 → 驗證 → Commit → Milestone**，不同時開兩個 stage。

---

## Stage 0 — Hermes Shared Storage Bootstrap ✅ 完成（2026-07-07）

六項 DoD 全部通過，詳見 [hermes-shared-storage-bootstrap.md](hermes-shared-storage-bootstrap.md)。摘要：

- Windows 為唯一 SoT；Ubuntu (WSL2) 經 symlink 共用 `state.db`／`sessions/`／`skills/`／`memories/`。
- 已知限制（by design，不是故障）：**Windows Hermes 運行中時，Ubuntu 對 state.db 的一般存取
  （含唯讀）一律干淨失敗**；`immutable=1` 快照讀取不受影響。這條限制直接約束 Stage 2 的設計（見下）。
- Rollback 已實測完整 round-trip。

同日完成的相鄰基礎（不屬於 Stage 0 DoD，但為後續 stage 的前提）：

- **HermesSessionAdapter**（`hermes/session_adapter/`）：read-only importer，20 tests 全過、
  已對真實資料驗證。normalized schema（`claudecodeos.event.v1`／`claudecodeos.session.v1`）、
  `event_id` 去重 key、`to-inbox` CLI、snapshot 模式皆已可用。見
  [hermes/session_adapter/README.md](../hermes/session_adapter/README.md)。
- **Hermes 平台排查**：default gateway 開機自啟已修復；sticky profile 被 Dashboard UI 切換的
  機制已查明（建議非破壞性緩解）；確認 gateway 沒自啟不是效能問題。

---

## Stage 0.5 — 平台收尾清單（部分完成；殘項為非阻塞）

> **進度更新（2026-07-09，見 [stage1-checkpoint.md](stage1-checkpoint.md) 第 2／5 節）**：
> default gateway 排程修復、profile 診斷（不刪 profile 的非破壞建議）、gateway 未啟動
> 根因查明（非效能問題）已在 Stage 1 期間完成。下表依此更新。
> **2026-07-10 起，殘項定位為非阻塞**（不擋 Stage 2 開工），仍建議儘早清完——
> gateway 健康狀態直接影響 session 資料是否持續累積，而 Stage 2 的自動化管線
> 以「session 有在產生」為前提。

| 項目 | 狀態 | 決定後的執行領域 |
|---|---|---|
| default gateway 排程／自啟（含原 `Hermes_Gateway` Scheduled Task 問題） | ✅ 已修復（Stage 1 期間） | — |
| nemocoding／gptcoding gateway 未啟動 | ✅ 根因查明結案（非效能問題；診斷見 checkpoint） | — |
| profile 處置 | ✅ 診斷完成，採「不刪 profile」建議 | — |
| financialresearch 自啟腳本修復（腳本已壞） | ⏳ 殘項（非阻塞） | engineering |
| codereviewer profile 去留（從未裝自啟） | ✅ 使用者拍板移除（2026-07-20，Phase 2a 稽核）；Windows 側實際刪除由 automation 平行處理；本 repo 側登記收尾見 `registry/capability_lanes.yaml` | — |

> **Phase 2d 補記（2026-07-20）**：剩餘四條 `hermes-*` capability lane（`hermes-gptcoding`、
> `hermes-nemocoding`、`hermes-financialresearch`、`hermes-intelligence`）各跑了一次真實
> （非 mock）`scripts/dispatch_domain.py` 呼叫，全部成功，四條均由 `status: reference`
> 轉 `active`；其中三條（openai-codex 訂閱制）的 `cost_tier` 也依真實呼叫回報的
> `usage.cost_status: included` 由 `unknown` 改記 `included`。詳見
> `registry/capability_lanes.yaml`。
| 三個殭屍 Startup vbs 清除 | ⏳ 殘項（非阻塞） | automation |
| sticky profile 緩解（自動化一律 `--profile default` 的習慣／機制化） | ⏳ 殘項（非阻塞） | engineering／automation |

---

## Stage 1 — Pre-Bridge Foundation ✅ 完成（2026-07-09）

**Checkpoint 文件：[stage1-checkpoint.md](stage1-checkpoint.md)**（交付清單、驗證證據
123 tests／0 失敗、基準數據、未完項去向、rollback 索引都在那裡，此處不重複）。

**範圍重定義的誠實說明**：本 stage 原規劃為「Knowledge 手動匯入路徑」。實際完成的是
bridge 開工前的**全部地基**，故完成定名 Pre-Bridge Foundation：

1. HermesSessionAdapter（read-only，20 tests，對真實資料驗證）
2. Stage 0.5 部分項（見上表）
3. Dashboard Jobs 修復＋確立「job pipeline 原生 Windows 跑不動」的架構事實（設計現況非缺陷）
4. venv 整併（單一 Windows `.venv`）＋ mac→Windows 路徑全案轉換
5. Memory taxonomy 三層定義＋N-gate consolidation 政策（[memory-taxonomy.md](memory-taxonomy.md)、
   [`registry/consolidation_policy.yaml`](../registry/consolidation_policy.yaml)）；
   `daily-memory-check` prompt 已接上 N-gate——**這是原 Stage 1 DoD 3 的定稿**
6. Stage 1 收斂：部署敘述修正（launchd 降級 legacy，目標環境 Windows/WSL2）、
   Capability Lanes schema（[capability-lanes.md](capability-lanes.md)）、
   Bridge State schema（[memory-bridge-state.md](memory-bridge-state.md)，格式定稿、實作待 Stage 2）
7. **Stage 1.4 — 部署同步**：方案定稿（[deployment-sync-plan.md](deployment-sync-plan.md)、
   `scripts/sync_to_wsl.sh`）＋ Telegram bot 邊界定案（`telegram.json` 不同步；
   ClaudeCodeOS bot＝控制入口、Hermes profile bots＝對話入口）＋**首次同步已完成**
   （0 檔案差異；備份 `/home/razer/backups/ClaudeCodeOSWin-wsl-pre-sync-20260709T182843.tar.gz`）

原 Stage 1 DoD 1／2（真實 session 實走 + idempotent 驗證）當時未執行、移列 Stage 2
必要前置——**已於 2026-07-10 完成**（見 Stage 2 的 gate 狀態）。

---

## Stage 2 — Session Bridge 自動化（原始藍圖：cron 偵測 → export → enqueue → headless CoS；
現況已由 2.4c／2.4d／2.5 取代，見下方各小節與「目標」段落的說明）

> **進度更新（2026-07-10）**：**gate 已解，可開工。**三項前置決策已由使用者拍板
> （見下方「前置決策拍板記錄」）；原 Stage 1 DoD 1/2 實走已完成——idempotency 修正
> （adapter deterministic 檔名＋`.processed` 掃描＋exit code 3）已完成並下發部署側。
> Stage 0.5 殘項定位為非阻塞。**開工後的第一步工作**：bridge_state schema v1 與
> 拍板欄位清單的對齊（[memory-bridge-state.md](memory-bridge-state.md) 第 6 節）
> ——✅ **已完成（2026-07-10）**：v1 in-place 修訂為 17 欄、測試 7→10 全綠、文件鏡像同動。
>
> **Stage 2.2 ✅ 完成（2026-07-10）**：bridge_state repository 層（`hermes/bridge_state.py`，
> 17 欄 table、event_id UNIQUE、enum 由 registry yaml 驗證不複製第二份）；部署側
> `hermes/state/bridge_state.db` 已 CLI init 並 smoke 驗證；隔離測試改為環境無關語義後
> 兩側同套測試全綠。
>
> **Stage 2.3 ✅ 完成（2026-07-10）**：bridge scanner（`hermes/bridge_scanner.py`，
> scan／reconcile 分離、`--since` 必填或 `--all-history` 明確啟用、既有狀態只 touch
> last_seen 絕不重設、snapshot-only 讀取）。部署側真實寫入已執行並驗證：reconcile 回填
> gate session 1 筆 `imported`（檔名比對依據已記錄）、scan 62 sessions／cutover 後 0 筆、
> 冪等重跑通過、禁區（state.db／jobs.db／telegram.json／inbox）零修改。
>
> **Cutover policy ✅ 已設定化（Stage 2.4a，2026-07-10）**：`2026-07-10T00:00:00Z`
> 是 Stage 2.3 拍板的 cutover 值（bridge 正式啟用日；此前 session 屬 pre-bridge
> 歷史，唯一有價值的舊 session 已由 reconcile 回填），現以
> **`hermes/config/bridge.yaml`（政策底線，版控＋同步下發，讀不到即 fail loud）＋
> `bridge_state.db` 的 `bridge_meta` scan watermark（部署側可拋棄進度，只前進
> 不後退）**雙層承載：scanner 無 `--since`/`--all-history` 時安全預設
> effective since ＝ max(cutover, watermark)，不再依賴人工記憶帶 `--since`
> （原「無參數→exit 2」改為此安全預設；「預設不得全掃」由 cutover 底線繼續保證）。
> 設計細節見 [memory-bridge-state.md](memory-bridge-state.md) 第 7 節。
> **2.4b 排程化前提醒**：部署側首次排程執行前先下發本次變更並跑一次真實 scan
> 確認 watermark 開始推進；排程一律不帶 `--since`。
>
> **Stage 2.4b unit 檔完成（2026-07-10）**：`hermes/systemd/hermes-bridge-scanner.service`
> ＋ `.timer`（每天 08:05，落在 08:00 memory-check 與 08:10 bridge 之間；oneshot、
> 無參數 scan、無 Restart——失敗不推進 watermark 由下次觸發補掃；`Persistent=true`
> 與既有 timer 一致）。install/uninstall 用法已涵蓋；靜態測試
> `hermes/test_systemd_units.py` 守住「排程一律無參數 scan」。**只排程 scan**，
> reconcile 不進排程（回填/對帳工具，人工或 2.4c 串接再定——**這條決定至今
> （2026-07-12）仍然成立，Stage 2.5 提案在設計 2.5b 候選查詢時重新查證過，
> reconcile 依然是人工／CLI 觸發，未排程**）。
> **Stage 2.4b ✅ 部署完成（2026-07-10）**：五項完成標準全過——sync 下發（bridge_state.db
> 未被覆蓋）→ dry-run 逐筆候選檢視（0 筆，watermark 後無新完結 session）→ 真實 scan
> → 冪等重跑 → 故意失敗（exit 1、watermark 不推進）→ Hermes/inbox fingerprint 零寫入
> **全過後才 enable timer**。手動 systemd start 成功（oneshot Finished、log 落檔、
> watermark 經 systemd 路徑推進）。**timer 已 enable，下次觸發 2026-07-11 08:05 CST**
> （時區確認 Asia/Taipei；enable 時無 catch-up，新 timer 無歷史戳記）。六個 units
> 全 active、無 failed。**管線現況：偵測→discovered 全自動；discovered→inbox 為
> 下一階段（2.4c）**。
>
> **Stage 2.4c ✅ 程式部署完成、敏感路徑已驗證（2026-07-10 實作；部署與敏感阻擋
> 驗證已完成）**：bridge importer（`hermes/bridge_importer.py`，
> `import [--dry-run] [--limit N]`）——discovered→政策判定→inbox 落地：敏感
> fail-closed（pattern 由 consolidation_policy.yaml `guardrails.sensitive.detection`
> 載入、對完整內容判定、只記類別標籤絕不記命中原文）→ needs_review；4.2 結構性
> 排除（test/too_short）→ skipped；錯誤 → failed（重試上限
> `bridge.yaml max_import_retries=3`，達上限轉 needs_review）；通過者先落地檔案
> 再記 to_inbox，DB 更新失敗／檔案已存在由 reconcile 回填（實測走通）。狀態對應表
> 見 memory-bridge-state.md §3.1。25 tests 全綠、既有全套零回歸。程式已下發部署
> 側並實地跑過敏感阻擋路徑（fail-closed 行為與設計相符，未落地敏感內容）。
> **尚未 enqueue、尚未 headless CoS、尚未 importer timer**——那是後續階段。
> DoD 1「在 Windows Hermes 正常運行時仍能運作」的完整 Desktop end-to-end 驗證
> **不再獨立列為 2.4c 未完項**，併入 2.4d-4 的部署 rollout 驗證一起做（見下方
> 2.4d 節與 [stage2.4d-episode-capture-proposal.md](stage2.4d-episode-capture-proposal.md)
> 第 8.1 節 migration runbook）。
>
> （2026-07-09 舊註，保留脈絡：去重狀態的記錄格式已先行定稿——
> `claudecodeos.bridge_state.v1`，[`registry/bridge_state_schema.yaml`](../registry/bridge_state_schema.yaml)；
> capability → 執行通道的對應已有 registry 層定義（[capability-lanes.md](capability-lanes.md)），
> bridge state 的 `selected_capability_lane` 欄位引用其 lane id。bridge 本身仍未實作。）
>
> **Stage 2.4d — Episode Capture（2026-07-11 起，設計已核准，見
> [stage2.4d-episode-capture-proposal.md](stage2.4d-episode-capture-proposal.md)）**：
> 實測發現 Desktop／TUI 的 `ended_at` 結構性不可靠——64 個既有 session 只有 20
> 個有 `ended_at` 值（**Desktop 0/2、TUI 8/46**），且這個比例不會隨時間補上（多數
> session 是「可長期復用的上下文容器」，本來就不會被「結束」）。原本以「`ended_at`
> 已設＝完結」為匯入判準的 Stage 2 設計，因此**結構性漏掉大部分 session**——
> Stage 2 的匯入單位改為 **episode／capture checkpoint**（同一 session 可切出多個
> immutable episode），**不再是整個 session**。schema 隨之升級為
> `claudecodeos.bridge_state.v2`（`bridge_sessions` 22 欄＋新表 `bridge_cursors`，
> 見 [memory-bridge-state.md](memory-bridge-state.md)）。**自本次修訂起明確強調**：
> episode 的切刀 trigger 現在是四選一（`ended`／`archived`／`inactivity`／
> `manual`），`ended_at` 只是其中一種、且不是主要或預設的判準——下方風險表相應
> 更新，避免有人誤讀成「主要仍看 ended_at」。
> **✅ Stage 2.4d 全鏈路完成並上線（2026-07-12）**：2.4d-1（schema v2＋
> repository：`create_episode`／`migrate` CLI／content hash 純函式）→ 2.4d-2
> （scanner episode 偵測：`ended`／`archived`／`inactivity` 三型 trigger、
> `checkpoint` 手動子指令，含 stale-ended 修正與 archived level-triggered
> 語義收斂）→ 2.4d-3（importer episode 化：range export、episode-aware 查重、
> reconcile cursor recovery，含 capture_trigger 缺失 fail-closed 修正）→
> 2.4d-4（部署 migration＋上線）——全部完成，測試矩陣（提案 §10，27＋條）
> 全綠，全程遵守既有硬邊界（唯讀 snapshot、不寫 Hermes 原始資料、不建第二份
> state.db）。
>
> **部署現況**：`episodes.enabled=true`、`episode_cutover=2026-07-12T06:36:18Z`
> （2.4d-4 部署翻 enabled 前的精確 UTC 時刻，非日期概念值）；
> `hermes-bridge-scanner.timer` **active／enabled**，每日 08:05 CST 觸發
> （落在 08:00 memory-check 與 08:10 skill-sync 之間）；WSL 睡眠期間錯過的
> 觸發由 systemd `Persistent=true` 於下次喚醒時補跑（既有行為，2.4b 起即如此）。
> importer **仍未排程化**（人工 CLI 執行）、**未 enqueue、未接 headless CoS**——
> 這些維持既有邊界，屬後續階段（**現況更新：這個 gap 現由 Stage 2.5 的規劃提案
> 開始銜接，見下方獨立小節與 [stage2.5-episode-triage-proposal.md](stage2.5-episode-triage-proposal.md)；
> importer 本身仍維持人工 CLI，Stage 2.5 明確不改變這一點**）。
>
> **✅ 第一筆真實正常 episode 端到端驗收通過（2026-07-12）**：
> `session_id=20260712_164627_419d23`、
> `event_id=hermes:20260712_164627_419d23:6991..7022`、`trigger=archived`、
> boundary `6991..7022`、cursor `last_captured_message_id=7022`、
> `episode_seq=1`。政策判定 allow（useful／length／sensitive／hash 全通過）→
> 落地 `memory/inbox/hermes_session_20260712_164627_419d23_ep6991-7022.md`
> （檔名／DB event_id／frontmatter `event_id_range` 三處一致）→ 最終狀態
> `to_inbox`／`memory_type=episodic`／`useful_chat=true`。scan／importer／
> reconcile 各自重跑皆冪等（boundary／hash／cursor／`imported_inbox_path`
> 全不變、零重複落地）。Hermes `state.db`／`jobs.db`／`telegram.json` 全程
> fingerprint 零 bridge 寫入（唯一異動來自 Hermes 本身與既有 worker 的正常
> 運作）。過程中另處理一筆 too_short 的 archived episode（結構性排除，`skipped`，
> 4 事件中只有 2 個 message 型、低於 4 則門檻）——驗證了「字元數足夠但 message
> 型事件不足」也會正確觸發結構性排除，非 bug。
>
> **已知非阻塞缺口**：Unarchive（`archived` 1→0）對真實 Hermes Desktop UI
> 的 live round-trip 驗證尚未執行（僅 fixture 邏輯驗證），不影響已上線的
> archived trigger 判斷（level-triggered，不依賴追蹤這個轉換本身）。此缺口
> 與 Stage 2.5 的 exactly-once enqueue 設計有交互關係，已在
> [stage2.5-episode-triage-proposal.md](stage2.5-episode-triage-proposal.md)
> 中一併討論（episode 的 `event_id` 去重是否在 unarchive/re-archive round-trip
> 後仍然穩定），但不是 Stage 2.5 需要解決的問題，維持列為非阻塞。

**目標（原始設計，2026-07-07 定稿——已被 Stage 2.4c／2.4d 與 Stage 2.5 取代，
本段自 2026-07-12 起明確標記為歷史紀錄，不代表現況，僅保留作歷史對照）**：
匯入單位已由上方 2.4d 更新為 episode／capture checkpoint，`ended_at` 不再是
唯一判準——這點原段落已有註記。**本次修訂額外明確指出**：下方描述的
「export → enqueue → headless CoS，由 CoS 依匯入政策決定要不要寫 inbox」
這個機制本身，**也已經被 Stage 2.4c／2.4d 取代**——「該不該寫入 memory」這個
判斷，現在是 importer（`hermes/bridge_importer.py`）在匯入當下就完成的政策
判定（敏感 fail-closed、4.2 結構性排除、落地 to_inbox），**不是**由 enqueue
之後的 headless CoS 事後判斷。enqueue／headless CoS 在管線中的角色，經使用者
2026-07-12 拍板重新定位，改由 **Stage 2.5 — Episode Triage & Queue
Foundation**（見下方獨立小節、規格正本
[stage2.5-episode-triage-proposal.md](stage2.5-episode-triage-proposal.md)）
承接，而且範圍限縮為**對已經到達 `to_inbox` 的內容做唯讀結構化分診**，
不再是「判斷要不要寫 inbox」。**以下原始設計敘述純屬歷史藍圖，不是現行架構、
不是現行行為**：新增一個 cron 觸發的 bridge（模式同
`hermes/adapters/hermes_bridge.py` 的 skills 同步）：定期偵測 Hermes
**新完結**的 session（`ended_at` 已設），`export_session()` 後 `enqueue()`
給 headless CoS，由 CoS 依匯入政策（memory-taxonomy 4.2／4.3）決定要不要寫
inbox（headless 只能新增 inbox 檔案，符合既有邊界）。之後由既有的
`daily-memory-check` 整併路徑收尾。**（歷史藍圖敘述結束——現行架構請見上方
各 2.x 小節與下方 Stage 2.5 小節）**

### 前置決策拍板記錄（2026-07-10，使用者拍板；本節為決策正本）

1. **Bridge 側別 → WSL 部署側**。理由：worker、jobs.db、systemd timers、runtime logs
   都在 WSL，bridge 放 WSL 側降低排程與 enqueue 複雜度。Hermes `state.db` 維持只讀
   來源、不寫回；Windows Hermes 運行導致直讀被鎖時，bridge **必須**用既有 snapshot／
   `immutable=1` 讀取路徑，**不可改寫 Hermes DB**。
2. **Bridge state 載體 → 獨立 SQLite `hermes/state/bridge_state.db`**。只記 ClaudeCodeOS
   的 bridge 狀態——不是 Hermes memory DB、不是第二份 Hermes state.db；不寫回 Hermes
   `state.db`；`memory/inbox/` 仍只是匯入落地區、不當狀態資料庫。至少追蹤：`session_id`、
   `source`、`first_seen_at`、`last_seen_at`、`import_status`、`imported_inbox_path`、
   `processed_path`、`error_reason`、`retry_count`、`updated_at`。**此清單與 schema v1
   有出入——對齊明文列為 Stage 2 實作的第一步**，對照表與不現在改 yaml 的理由見
   [memory-bridge-state.md](memory-bridge-state.md) 第 6 節。附帶一致性：`hermes/state/`
   在 `.gitignore` 且在部署同步排除清單——`bridge_state.db` 天然只存在 WSL 部署側、
   不被同步或版控，與決策 1 相合。
3. **model_router／Capability Lane → 不接自動路由**。Stage 2 bridge 不依賴 Capability
   Lane 自動路由；`capability_lanes.yaml` 目前為 reference/planning 層；
   `model_router.yaml` 的 TODO 模型值先不硬接 active route；OpenRouter／Hermes profile／
   Gemini 等 lane 保留 reference 或 experimental，**bridge 穩定後再進 Stage 3 或
   Stage 2.x**。（2026-07-20 現況更新：OpenRouter provider 相關路徑因
   `OPENROUTER_API_KEY` 從未真正設定過而經使用者拍板全部移除，`engineering`／
   `intelligence` 改用 `claude_native`；Hermes profile lane 已在 Phase 2d
   通過真實 smoke test 轉 `active`，見 ARCHITECTURE.md 第 5 節與
   `capability-lanes.md`。此節其餘內容為 2026-07-10 決策當下的記錄，不再更動。）

### 必要前置（gate）狀態 — ✅ 已解（2026-07-10）

1. ✅ **原 Stage 1 DoD 1／2 實走**：完成。idempotency 修正（adapter deterministic 檔名＋
   `.processed` 掃描＋exit code 3）已完成並下發部署側。
2. ✅ **前置決策三項**：已拍板並記錄（見上節）。
3. ⏳ **非阻塞殘留**：Stage 0.5 四殘項（見上表）。Windows 側 `git init` **已完成**
   （2026-07-09，baseline commit `03c7a0e`「stage1 checkpoint baseline before bridge」，
   敏感檔案經 `git check-ignore` 逐項驗證後排除；後續變更均已入版控）。

- **負責領域**：`engineering`（bridge 實作、schema 對齊、去重狀態、測試）；`automation`
  （排程與工作流串接）；`knowledge`（下游整併，沿用既有機制）。
- **設計問題狀態**（原「必須先決的設計問題」，2026-07-10 更新）：
  1. **Bridge 跑在哪一側** → ✅ 已拍板：WSL 部署側（決策 1）。當初的取捨分析
     （WSL 側必須 snapshot 讀 vs Windows 側要跨界 enqueue 進 WSL 的 jobs.db）以
     「排程與 enqueue 複雜度較低」定案 WSL 側。
  2. **去重狀態** → 格式已定稿（`claudecodeos.bridge_state.v1`）＋✅ 載體已拍板
     （決策 2）；schema v1 與拍板欄位清單的對齊＝實作第一步。不可寫進 Hermes 資料。
  3. **雜訊控制** → 政策既定：測試性質 session（類比 `_hermes_bridge_test` 的健康
     檢查訊號）不應每次都進 CoS 深究——政策即 memory-taxonomy 4.2／4.3。
- **Definition of Done**：
  1. Bridge 依排程執行，**且在 Windows Hermes 正常運行（gateway/Desktop 開著）時仍能運作**
     ——這條直接驗證互斥限制下的 snapshot／immutable 讀取方案（決策 1 的落地驗證）。
  2. 一個新完結的真實 session 被偵測到**恰好一次**（重跑不重複 enqueue），headless CoS
     收到後產出 inbox 檔案或明確記錄「依政策略過」（bridge state `skipped` + reason）。
  3. 零寫入 Hermes 原始資料；bridge 自身狀態存放於 `hermes/state/bridge_state.db`
     （WSL 部署側，決策 2）。
  4. 單元測試（fixtures 模式沿用 `session_adapter/tests/`）＋ 至少一次真實排程觸發的證據
     （沿用 Cron Adapter 當年「臨時短間隔排程觀察自動觸發」的驗證慣例）。
  5. 基準複查：對照 checkpoint 第 4 節的 state.db 基準（62 sessions／3,156 messages），
     確認訊息數相對 Stage 0 基準下降的原因（Hermes compaction 或讀錯 profile db）。

**為什麼排第二**：它是這條軌的核心報酬——Hermes 對話自動沉澱為長期記憶——但它同時是
風險最集中的 stage（互斥限制、快照一致性、去重、雜訊），值得在人工路徑驗證後、
帶著明確政策再動手。

---

## Stage 2.5 — Episode Triage & Queue Foundation ✅ 完成（2026-07-17；提案收斂至 v6 後核准實作）

> **✅ Stage 2.5 全階段完成並關閉（2026-07-17）**：提案在 v5 之後再收斂一版為
> **v6**（分支 4c／4d 測試改為決定性 fault injection、真實並行整合測試收斂為
> 只驗證聚合不變量，見提案第 4.1d、19 節），經使用者核准後開工——
> **2.5a**（commit `4aef9d1`）／**2.5b**（commit `24cb7fb`）／**2.5c**
> （commit `8444e7d`）實作完成，三個子階段分別新增 **19／19／27 個沙箱
> 測試**、全部測試綠；已以 `scripts/sync_to_wsl.sh --apply` 下發 WSL 部署側
> （備份 `~/backups/ClaudeCodeOSWin-wsl-pre-sync-20260717T142215.tar.gz`），
> 部署側三個 2.5 測試套件在 WSL `.venv` 下全綠，worker 已載入
> source-specific execution routing。
>
> **2.5c 唯一 start blocker（no-tools 技術強制力）已於開工前實測解除**——
> 最終旗標組合（`--tools "" --disallowedTools "mcp__*" --allowedTools
> "StructuredOutput" --permission-mode dontAsk --json-schema …`）與五項實測
> 發現（`--disallowedTools "*"` 與 `--bare` 不可用的原因、mktemp 中性 cwd、
> `StructuredOutput` 純輸出通道無副作用、envelope `result` 即 JSON 字串）
> 記錄於提案**第 18 節解除紀錄**；提案第 17 節開放問題採用值已轉正（triage
> timeout＝120s、輸入上限＝50,000 字元，實作為
> `hermes/bridge_triage_handler.py` 模組常數並有測試鎖定）。
>
> **2.5d 驗收完成（2026-07-17，使用者放寬「每日 ≤1」為同日多筆）**：5 筆
> 生產 job 全部 completed／`attempts=1`／`thread_id=None`／五欄合法 JSON，
> 三種 `decision` 皆有真實模型輸出實例（`needs_review` 以零污染行為探測
> 驗證，非生產 job），另有一筆測試 session 被 importer too_short 門檻正確
> 攔截的負面案例；總驗收成本約 $0.40——逐筆明細、偏差觀察與遺留待辦
> （prompt v2 候選、候選池殘餘、`--event-id` 旗標）見提案**第 20 節**。
> **下一階段：Stage 2.6 — domain dispatch（尚未規劃）**。
>
> 以下自「**狀態**」起為 2026-07-12 的 v5 規劃期敘述，保留為歷史紀錄
> （v5→v6 差異見提案第 19 節，實作與驗收事實以上方本段與提案第 20 節為準）。

**狀態**：規劃提案 **v5**，**待使用者核准，尚未開工**。完整設計正本：
[stage2.5-episode-triage-proposal.md](stage2.5-episode-triage-proposal.md)（`planning`
domain 起草，交叉核對本文件、[memory-bridge-state.md](memory-bridge-state.md)、
[memory-taxonomy.md](memory-taxonomy.md)、`hermes/db.py`、`hermes/worker.py`、
`hermes/bridge_importer.py`、`hermes/bridge_state.py`、`hermes/bridge_scanner.py`、
`.claude/skills/consolidate-memory/SKILL.md`、`registry/delegation_policy.yaml` 後定稿）。

**v5 是依使用者 3 點精確度回饋對 v4 的修正版本**，重點決定：

- **並行 requeue 的 SQLite 語意**：查讀 `hermes/db.py` 實際的
  `get_connection()`／`_db()` 後確認，v4「輸家永遠乾淨拿到 `rowcount=0`」
  的假設不完全正確——連線用預設（deferred）transaction，WAL 模式下並行
  呼叫可能撞到 `sqlite3.OperationalError`（一般鎖爭用或
  `SQLITE_BUSY_SNAPSHOT`），不只是乾淨的 0-row UPDATE。`requeue_dead_letter()`
  改寫為四分支並行安全狀態機（成功／乾淨拒絕／busy 衝突後重新查詢確認已被
  搶先＝正規化為拒絕／busy 衝突但仍是 dead_letter＝新例外
  `RequeueRetryableDBError`，由呼叫端決定是否重試），**明確不修改共用
  `_db()` 的全域鎖定策略**（不影響既有 `rss`／`telegram`／`cron` 路徑）。
- **Dispatch 措辭範圍精確化**：把先前「Stage 2.5 不做 dispatch」的籠統
  說法拆成兩層——**Stage 2.6 的 domain／action dispatch**（禁止，本階段
  範圍外）vs **2.5c 執行 triage job 本身所需的 worker source-specific
  execution routing**（第 7.5 節，允許、且是 2.5c 必要範圍），避免誤讀
  成連 worker 內部的呼叫入口選擇都被禁止。
- **`actor` 顯式驗證**：`requeue_dead_letter()` 在任何 DB 操作之前顯式
  拒絕空字串／純空白的 `actor`（`ValueError`）；CLI 用 argparse
  `required=True`，不提供任何掩蓋身份的假預設值；稽核表儲存的是
  `strip()` 正規化後的字串。

**定位（與 Stage 2.4c/2.4d、daily N-gate 的邊界，刻意寫清楚避免職責重疊）**：
Stage 2.4c/2.4d 已經擁有 episode 偵測、政策判定（是否寫入 inbox）、落地
`memory/inbox/`；daily N-gate／`consolidate-memory` pass 已經擁有 inbox →
`memory/*.md` 正本的整併判斷與寫入（**查證確認：這個 pass 完全是檔案目錄
操作，不寫 `bridge_state.db`**——`bridge_state.db` 事後補登整併結果，唯一
途徑是人工執行既有的 `bridge_scanner.py reconcile`，本身也未排程）。
**Stage 2.5 不重新做這兩件事，也不做 Stage 2.6 的 domain／action
dispatch**——它只對已經合法到達過 `to_inbox` 的 episode 做唯讀、結構化的
「分診」（`decision`: `memory_only`／`action_candidate`／`needs_review`），
不修改任何 memory 檔案、不呼叫任何 domain subagent。**這不包括**第 7.5
節 2.5c 執行 triage job 本身所需的 worker source-specific execution
routing——那是 job queue 內部決定用哪個呼叫入口執行同一個 triage job，
不涉及 domain subagent 或使用者核准流程，正式列在本階段範圍內。

**四個子階段**（皆人工觸發，本階段不安裝任何新 timer；importer 維持人工 CLI 不變）：

- **2.5a**：`jobs.db` migration（新增 `external_key`／`payload_hash`／
  `prompt_version`／`requeue_count`／`last_requeued_at` 五欄＋
  `UNIQUE(source, external_key, prompt_version)`＋新表
  `job_requeue_events`）、`enqueue_once`／`requeue_dead_letter` API
  （含 `actor` 顯式驗證與四分支並行安全狀態機）、回歸測試。
  ——✅ 完成（commit `4aef9d1`，2026-07-17）
- **2.5b**：手動 enqueuer CLI（`--dry-run`，不呼叫模型；候選資格＝
  「episode 曾合法到達 to_inbox，目前 bridge 狀態可為 to_inbox 或
  imported，artifact 可唯一定位」，對每個候選都無條件呼叫／模擬呼叫
  `enqueue_once`）。
  ——✅ 完成（commit `24cb7fb`，2026-07-17）
- **2.5c**：no-tools 結構化 triage handler ＋ `hermes/worker.py` 的
  source-specific execution routing（固定 JSON schema 輸出，最小
  權限——**開工前須先解除唯一的 start blocker「no-tools 技術可行性未
  確認」**，見提案第 18 節）。
  ——✅ 完成（commit `8444e7d`，2026-07-17；blocker 已於開工前解除，
  解除紀錄見提案第 18 節）
- **2.5d**：3–5 次人工實跑驗收（初始上限每日 1 次）。
  ——✅ 驗收完成（2026-07-17，使用者放寬為同日多筆；紀錄見提案第 20 節）

**明確排除於本階段之外**：`action_candidate` 的實際使用者核准與 domain 分派
→ **Stage 2.6**（另案設計，本文件與提案僅先點名，不設計）——與 2.5c 的
worker execution routing 是不同層級的概念，見上方定位段落。

**負責領域**：`engineering`（2.5a/2.5b/2.5c 全部程式碼與 schema、2.5d 驗收本身）；
`automation` 在本階段角色接近零（本階段刻意不安裝任何 timer），未來若要把
2.5b 排程化才會進入 automation 的範圍（分工原則見提案第 15 節）。

---

## Stage 2.6 — Domain Dispatch ✅ 完成（2026-07-17；提案 v2 九項拍板後同日核准實作）

> **✅ Stage 2.6 全階段完成並關閉（2026-07-17）**：規劃提案
> [stage2.6-domain-dispatch-proposal.md](stage2.6-domain-dispatch-proposal.md)
> （`planning` domain 起草）同日 v1→v2 收斂——九項開放問題全數拍板、皆採
> 建議值，核心為 **dispatch 語意選項 (a)**（核准佇列，無任何自動 dispatch；
> 人是未信任 episode 內容與有工具執行環境之間的結構性 gate）——之後依
> a→b→c→d 順序、每個子階段開工前經使用者核准實作：
>
> - **2.6a**（commit `63c2812`）：triage prompt v2（`bridge_episode_triage_v2`，
>   owner enum 名單由 `registry/agents.yaml` 注入 prompt 與驗證器的雙端硬化、
>   `summary`／`reason` 固定繁體中文）＋ enqueuer `--event-id` 旗標＋候選池
>   殘餘收尾——2.5 §20.3 三項遺留待辦就此收掉；handler 41／enqueuer 29 測試綠。
> - **2.6b**（commit `092b668`）：`jobs.db` 新增 `dispatch_records`＋
>   `dispatch_events`（append-only 稽核）＋核准 CLI（`list`／`approve`／
>   `reject`，`--dry-run`）；本子階段 approve 只落資料不派工（兩段式核准
>   節奏，如拍板執行）；32 測試綠。
> - **2.6c**（commit `c5e557c`）：執行閉環——approve→`enqueue_once`
>   （`source='bridge_domain_dispatch'`、`prompt_version='bridge_domain_dispatch_v1'`）
>   →worker 既有 else 路徑→`invoke_cos.sh`；`resume-approved`／`status`
>   子指令；dispatch 套件共 51 測試綠。**`hermes/worker.py` 零改動**（以
>   測試斷言把關）、`hermes/db.py` 零刪除。
> - **部署**：`sync_to_wsl.sh --apply` 下發 WSL（服務停／起乾淨），部署側
>   dispatch 測試綠。
> - **2.6d 驗收**：2 筆真實候選**全數走完人工決策閉環**——`1b84a9e3`
>   （engineering 建議、ResearchHelper）經使用者 **reject**（舊 episode、
>   專案不在本 repo、subagent 缺存取脈絡；reject 路徑真實走過，稽核
>   event_seq=2）；`e0c0dfce`（automation 建議）核准、任務描述人眼定案後
>   dispatch job `06128712` 約 4.5 分鐘 completed（`attempts=1`、
>   `thread_id=NULL`、成本 **$0.846**＝dispatch 單筆成本基準），headless
>   CoS **真實以 Agent 工具分派 automation subagent**，回傳誠實的結構化
>   狀態報告（唯讀邊界如實聲明、間接證據齊備、不越權、零檔案修改）。
>   失敗路徑（dead_letter→requeue）經使用者拍板以沙箱覆蓋滿足（22 個
>   fault-injection mock 測試＋2.5a requeue CLI 已實測），不刻意誘發真實
>   失敗——原 DoD「失敗路徑實走」以此方式滿足／調整（提案 §15.4）。
> - **遺留（不阻塞關閉）**：07-18 09:00 cron 自然執行後的 Slack 投遞最終
>   確認（人工檢視）——**✅ 已解決（2026-07-18，過程有轉折）**：cron 準時
>   執行但未投遞，診斷發現非 Slack 鏈問題，而是 default profile web
>   backend（brave-free）系統性 HTTP 422 → agent 回 `[SILENT]` →
>   scheduler 依設計跳過投遞；default profile 換 Tavily backend 後手動
>   重跑，真實新聞摘要成功投遞 #ai-news，07-19 起全自動——§15.3
>   automation subagent 報告所指向的原始問題至此完整結案（細節與新增的
>   「cron prompt 無聲失敗改進」未排程待辦見提案 §15.6）；automation
>   subagent 建議的 cron／platform 唯讀橋接構想列**未排程想法**；
>   Slack 投遞維持既拍板結論，屬 **Stage 2.7**。
>
> 完工紀錄正本（逐筆驗收、完成定義覆核、拍板調整、遺留事項）見提案 **§15**。

---

## Stage 2.7 — Notification & Scheduling ✅ 完成（2026-07-18；提案 v2 九項拍板後同日核准實作）

> **✅ Stage 2.7 全階段完成並關閉（2026-07-18）**：規劃提案
> [stage2.7-notification-scheduling-proposal.md](stage2.7-notification-scheduling-proposal.md)
> （`planning` domain 起草）同日 v1→v2 收斂——九項開放問題全數拍板、皆採
> 建議值，核心為 **(A) 獨立 notifier 掃描器**＋**(B) triage 段排程化**
> （pipeline 串行 importer→enqueuer），鐵律「dispatch 人工核准 gate 不可被
> 排程化繞過」全程守住——之後依 a→b→c 順序、每個子階段開工前經使用者核准
> 實作：
>
> - **2.7a**（commit `86287f7`）：notifier 核心（`hermes/bridge_notifier.py`）
>   ——`notification_log` 表、六種事件類型（候選待核准、needs_review、
>   dispatch 完成／死信、triage 死信、anomaly）判定、`hermes send` 子程序
>   封裝（mock 可注入）、message-key 冪等組裝、訊息樣板（summary 截斷
>   200 字元＋標註）、`--dry-run`；附帶 enqueuer `--max-new` 旗標。
>   42 測試綠。
> - **2.7b**（commit `23d9f6a`）：`hermes-bridge-pipeline.service/.timer`
>   （08:15）與 `hermes-bridge-notifier.service/.timer`（08:25）兩組
>   systemd unit 寫好、尚未 enable（enable 留給 2.7c 部署動作）；
>   `hermes-bridge-scanner` 過時註解一併修正（零行為變更）。19 測試綠。
> - **2.7c 部署與驗收**：過程中發現並解除提案唯一的 start blocker——原以為
>   只是「WSL 側 `hermes send` 沒實測過」，實測後發現 WSL 的 Hermes-agent
>   是一份完全獨立的 git checkout，落後 Windows main **1223 個 commit**、
>   缺 Slack delivery hardening；使用者拍板選項「1b」（一次性 git
>   fast-forward 升級到 Windows 同一 commit，不建立長期自動同步機制），
>   經安全查證（`hermes send` 路徑不觸碰 `state.db`、WSL 本地 commit 是
>   Windows main 的祖先）後執行，三項實測（fail-closed 阻擋、真實投遞、
>   去重 no-op）全過。`sync_to_wsl.sh --apply` 部署後，notifier 真實投遞
>   到測試頻道 `C0BHZC2EG84` 成功、重跑冪等（0 送出／1 略過）、**鐵律
>   稽核通過**（`dispatch_records` 表筆數執行前後維持 2 筆不變）；兩組
>   timer 已 enable，下次觸發 2026-07-19 08:15／08:25。
> - **遺留（不阻塞關閉）**：(1) 2026-07-19 首次自然排程觸發待人工確認
>   （pipeline／notifier 是否正確運作）；(2) dispatch 是否可用 Hermes
>   輕量／免費模型——查證確認 dispatch 執行路徑（`invoke_cos.sh` →
>   `claude -p`）與 Hermes profile、`scripts/route_model.py` 三者完全
>   獨立，列為未排程的未來架構決策（需新整合路徑＋先解決 profile 資料
>   跨機同步）；(3) WSL／Windows 側 Hermes-agent 版本同步無自動化機制
>   （1b 是一次性動作，非持續流程，Windows 側日後再升級需人工重跑同一
>   套 fast-forward）。
>
> 完工紀錄正本（逐筆事實、start blocker 完整故事、部署驗收紀錄、遺留事項）
> 見提案 **§15**。

---

## Stage 3 — Dashboard Hermes Session 檢視頁

> **載體變更（2026-07-23）**：使用者拍板**方案 B——全面轉移新 Web UI**
> （見 [webui-migration-proposal.md](webui-migration-proposal.md) 與下方
> 新增的 Stage 5 節），推翻 stage3 提案 v2 §0.1「不另開新入口」拍板——
> **本 stage 三項功能（session 列表／憑證與 Lane 檢視／統一排程健康表）
> 凍結、不在 Streamlit 開工**；設計正本仍為
> [stage3-dashboard-observability-proposal.md](stage3-dashboard-observability-proposal.md)
> v2 §2–4，實作載體改為新 Web UI，搬遷排入 **Stage 5 的 P2**（順序沿用
> 既拍板的功能二→三→一）。以下原文（含 DoD 四條）保留——DoD 內容仍被
> stage3 提案 v2 功能一逐字沿用，只是落地載體改變。
>
> **✅ 完工補記（2026-07-24）**：本節 **DoD 四條已透過 Stage 5 P2 在新
> 載體（新 Web UI ＋ Python 唯讀 API）達成**（commit `3da90ae`，含 stage3
> 提案 v2 全部三項功能，非僅 session 列表）——驗收證據見下方 Stage 5 節
> 完工摘要。本 stage 就此關閉，Streamlit 版本未實作、也不再實作
> （Streamlit 進入並行觀察期後退役，見 Stage 5 遺留事項）。

**目標**：在既有 Streamlit dashboard 加一頁 Hermes session 檢視（用 adapter 的
`list_sessions()`），維持 dashboard 的既有鐵律：localhost-only、read-only（技術上強制，
不靠自律）、獨立資料層。

- **依賴**：僅依賴 HermesSessionAdapter（已完成）。技術上不依賴 Stage 2，
  **刻意排最後**：它是觀測性功能，價值在 Stage 2 上線後最大（看得到 bridge 匯入了什麼、
  略過了什麼）；在管線存在之前先做，是把工排在報酬前面。
  依決策 3，capability lane 的活化（若要做）也落在本 stage 或 Stage 2.x，另案再議。
- **負責領域**：`engineering`。
- **Definition of Done**：
  1. Dashboard 新頁面列出 Hermes sessions（含 source／title／時間／message_count）。
  2. Windows Hermes 運行中時頁面仍可用（snapshot／immutable 讀取路徑）。
  3. read-only 與既有 dashboard 同等級技術強制（`mode=ro`／`immutable=1`，不 import 寫入層）。
  4. 不外洩敏感內容——列表層預設不渲染 `messages.content` 全文，或明確標註風險後由使用者決定。

---

## Stage 4 — CoS → Hermes 執行橋接（Domain Execution Router、憑證獨立化與 Telegram 推播）✅ 完成（2026-07-20～21）

**方向與定位（為什麼獨立列階，不掛在 Stage 2.x 底下）**：Stage 2.x 這條軌的資料
流向是「Hermes → 匯入進 ClaudeCodeOS 記憶」（session／episode bridge，對 Hermes
資料唯讀）；本階段做的是**反方向**——「ClaudeCodeOS／CoS → 主動呼叫出去，把
Hermes profile 當成一種可選執行通道」。兩條軌對 Hermes 的角色定位相反（一個是
資料來源、一個是執行後端），故獨立列為 Stage 4，不掛在 Stage 2.x 底下；文件順序
排在 Stage 3 之後，但實際完成時間點在 Stage 3 開工之前——見文首「更新
（2026-07-21）」的排列順序提醒。

**過程說明（與 Stage 2.5/2.6/2.7 節奏不同，如實記錄）**：本階段是在一次很長的
互動式 CoS session 裡邊做邊定案，**沒有先走 `planning` 起草提案、經使用者核准
才開工的節奏**——過程中多次直接分派 `engineering`／`automation`／`intelligence`
subagent 執行個別動作。本節是事後由 `planning` domain 補寫進文件體系，不是
先有提案再實作，如實記錄這個差異，不假裝原本就有規劃提案存在。

**起因與評估過但不採納的方案**：使用者原本想像 CoS 可以依任務類型動態調用
Hermes Profile 當模型後端，但當時 CoS 分派邏輯（Claude 原生 subagent）、Model
Router（OpenRouter）、Hermes Profile 三條路完全互不相通。也評估過「放棄 Windows
Hermes，全部搬到 WSL」的選項，**結論不建議**：會打掉已上線的 Stage 2 session
bridge，且評估當時 WSL 側五個 profile 有四個根本不存在。

**完工摘要（依實作順序）**：

- **Phase 1（commit `9922ea6`）**：新增 `scripts/dispatch_domain.py`（Domain
  Execution Router）——支援明確 `--lane`/`--capability` 選 Hermes profile
  lane、原 OpenRouter lane（後已移除）、或 native fallback；用 mktemp 中性
  目錄當 cwd 呼叫 `hermes -z`，解決 Hermes 在 repo root 自動載入 `AGENTS.md`
  導致遞迴分派的問題；29 個 mock 測試。同一 commit 內完成 **Phase 2a/2b**：
  稽核 Windows 側六個 Hermes profile（`default`／`gptcoding`／`nemocoding`／
  `financialresearch`／`intelligence`／`codereviewer`）的憑證與 provider 設定；
  移除從未真正在用、Stage 0.5 就懸著的 `codereviewer` profile（Windows 側
  實際刪除由 `automation` 執行，含備份）。
- **Phase 2d**：對四條 `hermes-*` lane（`gptcoding`／`nemocoding`／
  `financialresearch`／`intelligence`）各跑一次真實（非 mock）端到端 smoke
  test，全部成功；回傳的 `usage.cost_status` 欄位解掉 Phase 2a 稽核時卡住的
  openai-codex 訂閱制成本盤點問題。
- **Phase 2e/2f**：四條 lane 依真實證據由 `reference` 轉 `active`，
  `cost_tier` 同步更新為 `included`／`free`；`.claude/agents/engineering.md`／
  `intelligence.md` 補上「可選用 `dispatch_domain.py` 走 Hermes lane」的
  說明（`default_capability` 維持不變，可選手段、非新預設）；
  `ARCHITECTURE.md` 新增 5.1 節說明 `dispatch_domain.py` 與 `route_model.py`
  的分工。
- **OpenRouter 移除（commit `b910312`）**：查證確認 `OPENROUTER_API_KEY`
  自系統建成以來從未真正設定過（`.env` 不存在、shell 環境變數為空），三條
  OpenRouter lane／route 從未真正打通；全數移除，`route_model.py` 的
  `call_openrouter()` 死代碼一併刪除；`engineering`／`intelligence` 的
  `default_capability` 改回 `claude_native`，與其餘三個 domain 一致。
- **Stage 2.7 部署同步舊缺口（`a84d7c7` 前置發現）**：發現本機 `master` 有
  5 個 Stage 2.7 commit 從未 push 上 GitHub 的 origin/master（此前遺留的舊
  問題，本階段過程中發現並修復），已 fast-forward push。
- **PR #1**：上述 Phase 1／OpenRouter 移除變更開 PR 到 `master`（分支
  `claude/silly-kalam-e133d9`，含 commit `9922ea6`／`b910312`／`cfa476c`），
  merge 後以 `merge origin/master: Fast-forward` 併回本機 master。
- **憑證獨立化（commit `a84d7c7`）**：查證確認 `default`／`gptcoding`／
  `nemocoding` 是用 clone `default` profile 的方式建立，共用同一顆 refresh
  token；進一步查讀 hermes-agent 原始碼確認 openai-codex 的 refresh token
  單次使用、會輪替失效，共用是真實風險（程式碼裡有引用內部 issue 編號
  `#48415`／`#43589` 佐證過去真的發生過）。五個 profile（`default`／
  `gptcoding`／`nemocoding`／`financialresearch`／`intelligence`）全部重新
  走獨立 OAuth device-code 登入，舊共用憑證用官方 `hermes auth remove`
  指令逐一清除（含正確處理 `suppressed_sources` 抑制標記，避免被
  `_seed_from_singletons()` 自動重新種回）；`intelligence`／
  `financialresearch` 於 2026-07-20 完成，`gptcoding`／`nemocoding` 收尾於
  2026-07-21——逐 profile 的稽核細節見 `registry/capability_lanes.yaml`
  各 lane 內的 Phase 2e/2f/2g/2h 註記。過程中修正一個編碼 bug（commit
  `de98325`）：`dispatch_domain.py` 讀取 Hermes 子行程輸出時因 Windows
  cp950 codepage 跟 UTF-8 內容不相容而崩潰，導致真實任務執行成功但結果
  遺失，已修復並補回歸測試。
- **一次 detached HEAD 意外**：過程中（對照 git reflog，落在 `a84d7c7`
  憑證獨立化 commit 前後）本機一度落在非分支狀態（detached HEAD），已安全
  移回 `master` 並確認未遺失任何 commit。
- **真實任務驗證**：用 `hermes-financialresearch` lane 真的執行一份「AI
  供應鏈投資觀察清單」研究任務（繁體中文，六大供應鏈環節，美股台股分開，
  含引用來源與信心等級標註），證實 lane 真的能做有意義的實質工作；用
  `hermes-gptcoding` lane 真的完成一次 coding 任務（補
  `dispatch_domain.py` 的 `--help` 使用範例），證實 coding 類任務也走得
  通，且範圍守得住（只改 help text，沒碰 routing 邏輯）。
- **CoS 主動推播到 Telegram（commit `e7d38a2`）**：討論通知分流策略——
  cron／背景排程觸發的通知走 Slack（既有 `bridge_notifier.py`／`#agentos`
  慣例不變），CoS 互動觸發的通知（例如長時間的 Hermes lane 呼叫完成）改用
  新增的 Telegram 推播能力，避免使用者要在 Slack／Telegram 兩邊跳。實作
  `hermes/adapters/telegram.py` 的 `push_message()`／`push_cli()`，繞過
  job queue 直接呼叫既有的 `send_message()`；真實送出兩次測試訊息驗證送達
  （Telegram API 回應 `ok: true`）。

**關鍵決策**：

1. **不採納「全部搬到 WSL」**——會打掉已上線的 Stage 2 session bridge，且
   當時 WSL 側五個 profile 有四個根本不存在。維持 Windows Hermes 為唯一
   SoT、WSL 跑 bridge 的既有佈局不變（與 Stage 2 決策 1 一致）。
2. **Domain Execution Router 是「可選、明確 opt-in」的第三條路，不是新
   預設**——`route_model.py` 與 `default_capability` 機制完全不動，
   `dispatch_domain.py` 只在 subagent 主動判斷要用時才會被呼叫，且目前只有
   `engineering`／`intelligence` 的 `allowed_agents` 接得到現有 lane。
3. **OpenRouter 全數移除，不保留半殘留路徑**——查證確認 API key 從未真正
   設定過，與其留著一個宣稱能用但實際打不通的路徑，選擇誠實移除死代碼與
   死路由。
4. **憑證獨立化優先於省事**——五個 profile 各自走一次獨立 OAuth
   device-code 登入雖然比繼續共用麻煩，但單次使用、會輪替失效的 refresh
   token 跨 profile 共用是真實風險（有內部 issue 編號佐證過去真的發生
   過），拍板全部改成獨立憑證。
5. **通知分流以觸發來源為準，不是以內容重要性為準**——cron／排程觸發一律
   走 Slack、CoS 互動觸發一律走 Telegram，維持路徑單純，不做「重要的才推
   Telegram」這種主觀判斷。

**安全事故（誠實記錄，不美化）**：

- 過程中多次因為讀取 Hermes 憑證檔案（`auth.json`）時工具本身不支援欄位
  過濾，意外把完整 token 明文印進對話紀錄——至少 3–4 次，包含 `nous`
  provider 的 JWT 一次、Tavily API key 兩次、`intelligence`／`gptcoding`
  憑證各一次。
- 也有幾次系統安全監控標記「可能未經授權的憑證探索／檔案刪除」；經查證，
  多數是監控系統看不到完整對話脈絡的視角限制（使用者已在對話中明確授權
  該操作），**但也有一次是真的越權**——`automation` 查 cron 排程時，
  系統性掃描並解碼了五個 profile 的 JWT payload，超出任務範圍。
- 處置現況：Tavily key 因為是免費額度，使用者判斷不重要、暫不處理（已
  記錄於 `memory/hermes-tavily-key-plaintext-todo.md`，屬既有待辦，非本
  階段新增）；`nous` token 使用者建議撤銷，**目前尚未確認是否已處理**
  （見下方遺留事項①）。

**負責領域**：本階段實際在互動式 CoS session 中直接進行（過程中分派
`engineering`／`automation`／`intelligence` 執行個別動作），非事先由
`planning` 起草提案、經核准才開工的節奏——本節屬 `planning` domain 事後
補寫進文件體系。

**遺留事項（不阻塞本階段關閉，但需要人工追蹤）**：

1. **`nous` token 撤銷待確認**——上方安全事故裡意外明文印出的 `nous`
   provider JWT，使用者已建議撤銷，但目前尚未確認是否已實際處理。
2. **「依任務類型自動選模型」規則引擎——未來獨立功能，不在本階段範圍**。
   使用者在 Phase 2f 開工前明確表示在意這個功能，但要求「適合的時機再
   做」，不要現在夾帶。現況落差與適用時機建議已記錄於
   `memory/hermes-task-category-model-routing-preference.md`，屆時建議
   比照 Stage 2.5／2.6／2.7 的既有慣例，由 `planning` domain 另外起草
   設計提案再開工。

---

## Stage 5 — Web UI 遷移（Dashboard 全面轉移）✅ 四個 phase 全部完成（2026-07-23～24 驗收；剩 Streamlit 並行觀察期）

**規劃提案正本：[webui-migration-proposal.md](webui-migration-proposal.md)**
（2026-07-23 同日 v1 草案 → §9 六項拍板 → v2 定稿核准）；
**P3 形態正本：[webui-pty-terminal-proposal.md](webui-pty-terminal-proposal.md)**
（同日 v1 草案 → §10 五項拍板＋殘餘風險知情確認 → v2 定稿核准）。

**方向與定位**：使用者 2026-07-23 拍板**方案 B**——推翻 stage3 提案 v2
§0.1「不另開新入口」拍板，dashboard 觀測功能全面轉移到以 AgentOSUI 範本
（OpenAI site-creator starter 改造版，本機參考目錄
`AgentOSUI-ClaudeCode-Reference-2026-07-23`）為雛形的新 Web UI。架構拍板：
UI 層降級為**純 Vite + React SPA**（剝掉 vinext/wrangler/Cloudflare 託管
假設）；資料層採 **Python 唯讀 API 包既有 `dashboard/data.py`**（三鐵律
的技術強制留在 Python 端單一真相）。**Stage 3 三項功能凍結於 Streamlit
載體**、設計正本仍為 stage3 提案 v2 §2–4，實作載體改為本 stage 的 P2
（Stage 3 DoD 已就此達成，見 Stage 3 節完工補記）。

**完工摘要（四個 phase，commit 皆在 master，經使用者驗收）**：

- **P0 ✅（commit `bf2bfe2`）**：範本剝離託管假設（純 Vite + React SPA、
  Cloudflare/OpenAI 殘留清零、mock 假資料清零）＋ Windows 落地驗證＋
  **bridge 最小寫入例外**依使用者親定安全規格實作（固定白名單操作、無
  任意 shell command API、PID ownership＋重複啟動防護＋localhost-only＋
  audit log）＋過渡期安全檢查 script（現況為 **9 項檢查**，較提案設計的
  8 項多一項，全過）。
- **P1 ✅（commit `f1b9104`）**：Python 唯讀 API（`dashboard/api.py`，
  stdlib、`127.0.0.1:8799`、GET-only、CORS/405/import guard 技術強制）＋
  `dashboard/redact.py`（第三道掃描共用正本）＋新 UI 與既有 Streamlit
  五區塊功能對等。
- **P2 ✅（commit `3da90ae`）**：Stage 3 三項功能（依拍板順序功能二→三→一）
  搬入新 UI。**上線首日即產生真實價值**：支撐一次真實憑證稽核與清理
  ——五個 profile 憑證池對齊應然配置、**全程零明文接觸**（功能二「技術上
  不可能外洩秘密值的唯讀檢視」的設計目的第一天就兌現）；統一排程健康表
  抓到 `aichain-orchestrator-daily` 失敗。含 2026-07-23 目視回饋修正
  （字級＋2、憑證表對齊、model 欄改為實際生效模型）。
- **P3 ✅（commit `0bbd6c1`）**：PTY 終端機（ClaudeCode CLI view，nav 位於
  總覽與 Jobs 之間；per-boot token＋Origin 白名單雙層授權、單一 session、
  不落 transcript、spawn 寫死 `claude`/repo 根）——使用者實測打字輸入與
  中文回覆通過；含按鈕樣式與 15px 字體調整。

**負責領域**：`engineering`（P0–P3 全部實作與測試）；`automation` 角色
為零；`planning` 起草兩份提案並隨拍板收斂定稿（本階段完整走「提案→拍板
→核准→實作→驗收」節奏，與 2.5/2.6/2.7 慣例一致）。

**與既有鐵律的關係（完工後現況）**：localhost-only、技術強制 read-only、
獨立資料層三鐵律已在新架構逐條重建並有測試（提案 §3）；**兩個經核准的
寫入例外**——bridge 四種白名單 process 操作（P0）、PTY 前台終端機（P3，
含 §3.2 殘餘風險知情確認）——均為獨立 process、獨立 port、audit log，
與唯讀側物理隔離。

**遺留（不阻塞功能 phase 關閉）**：

1. **Streamlit 並行觀察期自 2026-07-24 起算**（已拍板：一個自然使用週期、
   期間 Streamlit 零維護只讀），期滿後做退役決策並實際移除——這是本
   stage 唯一剩餘事項。
2. P2 排程健康表抓到的 `aichain-orchestrator-daily` 失敗屬 Hermes 原生
   cron job 的運營問題，不是 dashboard 缺陷——後續處置由 CoS 依 delegation
   policy 另行分派追蹤。
3. 提案 §7 文件連動殘項（memory 記錄等）依該表狀態欄追蹤。

---

## 建議優先順序（總結，2026-07-24 更新）

1. ~~Stage 1~~ ✅ 完成（Pre-Bridge Foundation，見 [stage1-checkpoint.md](stage1-checkpoint.md)）。
2. ~~Stage 2 必要前置~~ ✅ gate 已解（2026-07-10）：DoD 1/2 實走完成、三項前置決策拍板並記錄。
3. ~~Stage 2（2.1–2.4d）~~ ✅ 全鏈路完成並上線（2026-07-12，見上方 Stage 2.4d 節）。
4. ~~Stage 2.5~~ ✅ 全階段完成並關閉（2026-07-17）：提案收斂至 v6 後核准實作，
   2.5a/2.5b/2.5c/2.5d 全部完成、部署側驗證通過——驗收紀錄見提案第 20 節、
   摘要見上方 Stage 2.5 節。
5. ~~Stage 2.6 — domain dispatch~~ ✅ 全階段完成並關閉（2026-07-17）：提案 v2
   九項拍板後同日 2.6a/2.6b/2.6c 實作、WSL 部署、2.6d 驗收完成——完工紀錄見
   提案 §15、摘要見上方 Stage 2.6 節。Stage 2.5 遺留小修（prompt v2、候選池
   殘餘、`--event-id` 旗標）已隨 2.6a 一併收掉。
6. ~~Stage 2.7 — Slack 投遞與排程化~~ ✅ 全階段完成並關閉（2026-07-18）：提案 v2
   九項拍板後同日 2.7a/2.7b 實作、2.7c 部署與真實驗收完成，過程中發現並解除了
   提案唯一的 start blocker（WSL 側 Hermes-agent 版本落後 Windows main 1223
   個 commit）——完工紀錄見提案 §15、摘要見上方 Stage 2.7 節。2026-07-19
   首次自然排程觸發待人工確認（見提案 §15.4）。
7. ~~Stage 4 — CoS → Hermes 執行橋接~~ ✅ 完成（2026-07-20～21）：Domain
   Execution Router、OpenRouter 移除、五個 Hermes profile 憑證獨立化、
   CoS→Telegram 主動推播——摘要見上方 Stage 4 節。**與 Stage 3 走不同方向、
   互不依賴**，實際完成時間點在 Stage 3 開工之前，只是依使用者裁示的文件
   排列順序放在 Stage 3 之後（見文首「更新（2026-07-21）」）。遺留：`nous`
   token 撤銷待確認、「依任務類型自動選模型」規則引擎未排程（見上方 Stage 4
   遺留事項）。
8. ~~Stage 3~~ ✅ **DoD 已透過 Stage 5 P2 在新載體達成並關閉**（2026-07-24
   補記，commit `3da90ae`）：三項功能設計正本為 stage3 提案 v2 §2–4，
   實作落在新 Web UI＋唯讀 API（見 Stage 3 節完工補記與 Stage 5 節）。
9. ~~Stage 5 — Web UI 遷移~~ ✅ **四個 phase（P0–P3）全部完成並經使用者
   驗收**（2026-07-23～24；P0 `bf2bfe2`／P1 `f1b9104`／P2 `3da90ae`／
   P3 `0bbd6c1`，見上方 Stage 5 節完工摘要）。**剩餘：Streamlit 並行
   觀察期（2026-07-24 起算一個自然使用週期）→ 期滿後退役決策**——這是
   本軌目前唯一的排程中事項；其後的下一步視需要另開新規劃（候選：Stage 4
   遺留的「依任務類型自動選模型」規則引擎、`aichain-orchestrator-daily`
   失敗處置追蹤）。

## 持續事項（不設 stage，跨階段有效）

- **WSL／Hermes 升級後重跑驗證**：Stage 0 的互斥行為建立在 WSL2 9p 目前的實作上，
  升級後重跑 `scripts/hermes_stage0_verify.sh`（Phase A 隨時可跑）。
- **Hermes upstream 追蹤**：v0.18.0 沒有官方 remote/shared session store；若未來版本加入，
  **優先改用官方機制取代 symlink**（Stage 0 報告既定立場）。`state.db` schema 無相容性
  承諾——adapter 對 schema 變動 fail loud，`schema_version` 變動時視為 engineering 任務跟進。
- **雙邊同時互動使用不是支援情境**：Ubuntu 端 db 功能在 Windows Hermes 運行時自動降級，
  是設計行為；所有自動化設計（尤其 Stage 2）必須把這當成常態而非例外。
- **Windows repo ↔ WSL 部署複本的同步**（Stage 1.4 起生效）：v0.1 為手動觸發
  `scripts/sync_to_wsl.sh`，時機與 runbook 見 [deployment-sync-plan.md](deployment-sync-plan.md)。
- **WSL／Windows 側 Hermes-agent 套件版本同步無自動化機制**（2026-07-18 新增，
  見 Stage 2.7 §15.4）：2026-07-18 的一次性 git fast-forward（選項 1b）只解決了
  當下的版本落差，不是持續流程；Windows 側日後再升級 Hermes-agent，需人工對
  WSL 側重跑同一套流程，否則會再度漂移。
- **依任務類型自動選模型的規則引擎——未來獨立功能，尚未排程**（2026-07-21 新增，
  見 Stage 4 遺留事項）：現有架構只到 domain 層級的 `default_capability`，無法做
  「同一個 domain 底下，A 類任務用 Hermes lane、B 類任務用 Claude native」這種更細
  的區分；使用者已明確表示在意這個功能，適合時機再重提，記錄於
  `memory/hermes-task-category-model-routing-preference.md`，不要順便夾帶進其他
  不相關任務。

## 風險與未知事項

| 風險 | 影響 | 緩解 |
|---|---|---|
| Hermes schema 變動（第三方，無承諾） | adapter／bridge 直接失效（fail loud） | 去重與匯出邏輯全部集中在 adapter 一層；schema_version 變動時只需跟進一處 |
| WSL 互斥限制 vs 自動化 | WSL 側排程任務在 Windows Hermes 運行時無法一般讀取 state.db | 側別已拍板 WSL 側（決策 1）：bridge 一律走 snapshot／immutable 讀取路徑；DoD 1 直接驗證 |
| `immutable=1` 讀 live db 的快照一致性（2026-07-12 更新：緩解措施已隨 2.4d 改變，見右欄） | 讀到不一致快照 | **已更新**：不再只靠「`ended_at` 已設」這個單一判準降低風險——2.4d 起 episode 的切刀 trigger 是 `ended`／`archived`／`inactivity`／`manual` 四選一，快照一致性改由「同一次 scan snapshot 內固定 boundary」＋ importer 匯入時以 `source_content_hash` 重算比對（提案 stage2.4d §4.5）共同保證，不一致直接 `needs_review`（fail-closed），不落地 |
| `messages.content` 含敏感資料 | 敏感內容進入長期記憶正本 | 匯入政策已定稿（memory-taxonomy 4.3 guardrails，fail-closed）；adapter 明確不過濾，責任在落地前判斷層 |
| sticky profile 被 UI 切換 | 不帶 `--profile` 的 CLI 自動化讀錯 db | 所有自動化一律明確 `--profile default`（Stage 0 報告既有結論）；緩解機制列 Stage 0.5 殘項 |
| gateway 自啟不齊（Stage 0.5 殘項未清） | session 資料累積不完整，Stage 2 價值打折 | 殘項非阻塞，建議與 Stage 2 平行清完 |
| ~~Windows 開發正本無版控~~ **已解**（2026-07-09 `git init`，baseline `03c7a0e`） | （解除前）程式層 rollback 只能靠 WSL pre-sync tarball；bridge 這種長期演化元件無變更歷史 | 已完成 `git init` 與 baseline commit，後續變更均入版控；git-based sync 仍列 sync plan v0.2 升級路徑；rollback 現況索引見 checkpoint 第 6 節 |
| state.db 訊息數相對 Stage 0 基準下降（2026-07-09 觀察） | 若是讀錯 profile db，bridge 會處理錯的資料集 | Stage 2 DoD 5 基準複查；讀取一律 `--profile default` ＋ snapshot |
| **bridge_state schema v1 與拍板欄位清單有出入**（2026-07-10 新增） | 不先對齊就實作，會產生兩套欄位語意並存 | 對齊明文列為 Stage 2 實作第一步（memory-bridge-state.md 第 6 節）；schema／測試／文件三者同動 |
| **Stage 2.5 的「輸出層混淆」風險**（2026-07-12 新增，見提案第 10 節） | episode 內容中嵌入的指令可能操縱 triage handler 的 `decision`/`summary`輸出 | Prompt 結構性隔離＋重申權限限制＋測試矩陣明確驗證（提案第 14 節第 15 項）；**這條緩解的實際強度目前繫於提案第 18 節唯一的 start blocker（no-tools 技術可行性）**，尚未技術驗證前不應假設攻擊面已完全侷限在輸出內容。**2026-07-17 更新**：blocker 已實測解除（zero-tools 技術強制，提案第 18 節解除紀錄），prompt injection 測試（矩陣第 15、20 項）已隨 2.5c（commit `8444e7d`）落地——攻擊面已如設計侷限在輸出內容層；輸出層本身的殘餘風險由 `needs_review` 觸發率監測（提案第 20.3 節，2.6 前小修候選）持續觀察。**2026-07-17 第二次更新**：觸發率監測已隨 2.6b 的 CLI decision 計數落地（2.6 提案 §5.4），2.6d 盤點現況：memory_only=5／action_candidate=2／needs_review=0／異常=0；下游 dispatch 的注入緩解鏈（人工核准硬 gate、episode 全文不入 prompt、殘餘風險誠實標註）見 2.6 提案 §10。**2026-07-18 更新**：Stage 2.7 notifier 對 `needs_review`／`anomaly` 補上逐筆通知（提案 §6.2），觸發率監測遺留視為關閉 |
| **並行 `requeue_dead_letter` 呼叫的 SQLite 例外處理**（2026-07-12 第四次修訂新增） | 若實作沿用 v4 的二分支假設，WAL 模式下的 `SQLITE_BUSY`／`SQLITE_BUSY_SNAPSHOT` 例外可能未被正確分類，誤報「已被別人 requeue」或讓例外未經處理往外拋 | 提案第 4.1b／4.1c 節已改寫為四分支狀態機並定義 `RequeueRetryableDBError`；2.5a 實作與測試（提案第 14 節第 21 項）需嚴格遵循，不得簡化回二分支假設。**2026-07-17 更新**：已隨 2.5a（commit `4aef9d1`）依四分支狀態機實作，並以決定性 fault-injection 測試（提案第 14 節第 21、22 項）＋聚合並行整合測試（第 23 項）鎖定 |
| **WSL／Windows 側 Hermes-agent 套件版本漂移**（2026-07-18 新增，見 Stage 2.7 §15.2） | 兩側套件版本不同步可能導致功能缺口（如本次 `hermes send` 不支援 `--message-key`）在無人察覺下累積，且落差會隨時間放大 | 2026-07-18 已一次性 fast-forward 追平（選項 1b，安全查證見提案 §15.2）；**未建立自動同步機制**，需人工於 Windows 側每次升級 Hermes-agent 後主動對 WSL 側重跑同一套流程，列為持續事項（見上方「持續事項」節） |
| **五個 Hermes profile 曾共用同一顆 openai-codex refresh token**（2026-07-21 新增，見 Stage 4 節） | refresh token 單次使用、會輪替失效，多 profile 共用同一顆有 `refresh_token_reused`／`invalid_grant` 失效風險（hermes-agent 原始碼內有引用內部 issue `#48415`／`#43589` 佐證過去真的發生過） | 2026-07-20～21 已完成五個 profile 各自獨立 OAuth device-code 登入＋官方 `hermes auth remove` 指令清除舊共用憑證（含 `suppressed_sources` 抑制標記，避免被自動重新種回），逐 profile 證據見 `registry/capability_lanes.yaml` 對應 lane 的 Phase 2e/2f/2g/2h 註記；風險已解除，非持續性事項 |
| **憑證檔案讀取意外印出明文 token**（2026-07-21 新增，見 Stage 4「安全事故」） | 讀取 `auth.json` 等憑證檔案時工具本身不支援欄位過濾，明文 token 進入對話紀錄，若對話紀錄外洩即等同憑證外洩 | 已知案例：`nous` JWT、Tavily API key（兩次）、`intelligence`／`gptcoding` 憑證。Tavily key 因免費額度判斷不重要（見 `memory/hermes-tavily-key-plaintext-todo.md`）；`nous` token 建議撤銷但**尚未確認是否已處理**（Stage 4 遺留事項①）。技術面尚無「讀取憑證檔案時自動遮罩」的機制，屬未排程的工具改進方向。**2026-07-23 補充**：Stage 5 P0 的過渡期最小安全檢查 script 與 P2 的憑證唯讀檢視（stage3 提案 v2 功能二設計）是對此風險的結構性補口，見 Stage 5 節。**2026-07-24 更新**：P2 憑證唯讀檢視上線首日即支撐一次真實憑證稽核與清理、全程零明文接觸——結構性補口已實證有效（Stage 5 節完工摘要） |
