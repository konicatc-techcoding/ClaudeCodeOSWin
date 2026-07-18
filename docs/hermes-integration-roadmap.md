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
| codereviewer profile 去留（從未裝自啟） | ⏳ 殘項（非阻塞，使用者決定） | 使用者；移除則 automation 執行 |
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
   Stage 2.x**。

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

## Stage 3 — Dashboard Hermes Session 檢視頁

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

## 建議優先順序（總結，2026-07-17 更新）

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
6. **Stage 2.7 — Slack 投遞與排程化**（下一個候選階段，**尚未規劃**）：2.6 拍板
   明確列為另案（2.6 提案 §7；頻道對應屆時由使用者指定，機制起點參考
   `delivered_at` 模式、投遞側落 hermes-agent）。是否開工、或直接進 Stage 3，
   以 2.6d 建立的成本基準（dispatch 單筆 $0.846）與 needs_review 觸發率
   觀察（現況 0）為決策依據，由使用者拍板。
7. **Stage 3**：觀測性收尾。

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
| **Stage 2.5 的「輸出層混淆」風險**（2026-07-12 新增，見提案第 10 節） | episode 內容中嵌入的指令可能操縱 triage handler 的 `decision`/`summary`輸出 | Prompt 結構性隔離＋重申權限限制＋測試矩陣明確驗證（提案第 14 節第 15 項）；**這條緩解的實際強度目前繫於提案第 18 節唯一的 start blocker（no-tools 技術可行性）**，尚未技術驗證前不應假設攻擊面已完全侷限在輸出內容。**2026-07-17 更新**：blocker 已實測解除（zero-tools 技術強制，提案第 18 節解除紀錄），prompt injection 測試（矩陣第 15、20 項）已隨 2.5c（commit `8444e7d`）落地——攻擊面已如設計侷限在輸出內容層；輸出層本身的殘餘風險由 `needs_review` 觸發率監測（提案第 20.3 節，2.6 前小修候選）持續觀察。**2026-07-17 第二次更新**：觸發率監測已隨 2.6b 的 CLI decision 計數落地（2.6 提案 §5.4），2.6d 盤點現況：memory_only=5／action_candidate=2／needs_review=0／異常=0；下游 dispatch 的注入緩解鏈（人工核准硬 gate、episode 全文不入 prompt、殘餘風險誠實標註）見 2.6 提案 §10 |
| **並行 `requeue_dead_letter` 呼叫的 SQLite 例外處理**（2026-07-12 第四次修訂新增） | 若實作沿用 v4 的二分支假設，WAL 模式下的 `SQLITE_BUSY`／`SQLITE_BUSY_SNAPSHOT` 例外可能未被正確分類，誤報「已被別人 requeue」或讓例外未經處理往外拋 | 提案第 4.1b／4.1c 節已改寫為四分支狀態機並定義 `RequeueRetryableDBError`；2.5a 實作與測試（提案第 14 節第 21 項）需嚴格遵循，不得簡化回二分支假設。**2026-07-17 更新**：已隨 2.5a（commit `4aef9d1`）依四分支狀態機實作，並以決定性 fault-injection 測試（提案第 14 節第 21、22 項）＋聚合並行整合測試（第 23 項）鎖定 |
