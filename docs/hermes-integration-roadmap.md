# Hermes Integration Roadmap — Stage 0 之後的階段性里程碑

日期：2026-07-07　狀態：**規劃定稿（v1）**　負責規劃：planning domain
**更新（2026-07-09）**：Stage 1 標記完成（範圍重定義為 **Pre-Bridge Foundation**，
見 [stage1-checkpoint.md](stage1-checkpoint.md)）；Stage 1.4（部署同步）補進歷史；
Stage 2 前置條件依 checkpoint 未完項更新。
**更新（2026-07-10）**：Stage 2 三項前置決策已由使用者拍板並記錄（側別＝WSL 部署側、
載體＝`hermes/state/bridge_state.db`、不接自動路由）；原 Stage 1 DoD 1/2 實走完成
（idempotency 修正已下發部署側）——**Stage 2 gate 已解**，詳見 Stage 2 節。

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

## Stage 2 — Session Bridge 自動化（cron 偵測 → export → enqueue → headless CoS）

> **進度更新（2026-07-10）**：**gate 已解，可開工。**三項前置決策已由使用者拍板
> （見下方「前置決策拍板記錄」）；原 Stage 1 DoD 1/2 實走已完成——idempotency 修正
> （adapter deterministic 檔名＋`.processed` 掃描＋exit code 3）已完成並下發部署側。
> Stage 0.5 殘項定位為非阻塞。**開工後的第一步工作**：bridge_state schema v1 與
> 拍板欄位清單的對齊（[memory-bridge-state.md](memory-bridge-state.md) 第 6 節）
> ——✅ **已完成（2026-07-10）**：v1 in-place 修訂為 17 欄、測試 7→10 全綠、文件鏡像同動。
>
> （2026-07-09 舊註，保留脈絡：去重狀態的記錄格式已先行定稿——
> `claudecodeos.bridge_state.v1`，[`registry/bridge_state_schema.yaml`](../registry/bridge_state_schema.yaml)；
> capability → 執行通道的對應已有 registry 層定義（[capability-lanes.md](capability-lanes.md)），
> bridge state 的 `selected_capability_lane` 欄位引用其 lane id。bridge 本身仍未實作。）

**目標**：新增一個 cron 觸發的 bridge（模式同 `hermes/adapters/hermes_bridge.py` 的 skills
同步）：定期偵測 Hermes **新完結**的 session（`ended_at` 已設），`export_session()` 後
`enqueue()` 給 headless CoS，由 CoS 依匯入政策（memory-taxonomy 4.2／4.3）決定要不要寫
inbox（headless 只能新增 inbox 檔案，符合既有邊界）。之後由既有的 `daily-memory-check`
整併路徑收尾。

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

## 建議優先順序（總結，2026-07-10 更新）

1. ~~Stage 1~~ ✅ 完成（Pre-Bridge Foundation，見 [stage1-checkpoint.md](stage1-checkpoint.md)）。
2. ~~Stage 2 必要前置~~ ✅ gate 已解（2026-07-10）：DoD 1/2 實走完成、三項前置決策拍板並記錄。
3. **Stage 2**（現在的第一優先）：可開工；**第一步工作＝bridge_state schema 對齊**
   （memory-bridge-state.md 第 6 節）。Windows 側 `git init` 已完成（baseline `03c7a0e`）；
   Stage 0.5 四殘項非阻塞、可平行清理。
4. **Stage 3**：觀測性收尾。

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
| `immutable=1` 讀 live db 的快照一致性 | 讀到不一致快照 | 只處理 `ended_at` 已設的完結 session，降低撞上寫入中資料的機率 |
| `messages.content` 含敏感資料 | 敏感內容進入長期記憶正本 | 匯入政策已定稿（memory-taxonomy 4.3 guardrails，fail-closed）；adapter 明確不過濾，責任在落地前判斷層 |
| sticky profile 被 UI 切換 | 不帶 `--profile` 的 CLI 自動化讀錯 db | 所有自動化一律明確 `--profile default`（Stage 0 報告既有結論）；緩解機制列 Stage 0.5 殘項 |
| gateway 自啟不齊（Stage 0.5 殘項未清） | session 資料累積不完整，Stage 2 價值打折 | 殘項非阻塞，建議與 Stage 2 平行清完 |
| ~~Windows 開發正本無版控~~ **已解**（2026-07-09 `git init`，baseline `03c7a0e`） | （解除前）程式層 rollback 只能靠 WSL pre-sync tarball；bridge 這種長期演化元件無變更歷史 | 已完成 `git init` 與 baseline commit，後續變更均入版控；git-based sync 仍列 sync plan v0.2 升級路徑；rollback 現況索引見 checkpoint 第 6 節 |
| state.db 訊息數相對 Stage 0 基準下降（2026-07-09 觀察） | 若是讀錯 profile db，bridge 會處理錯的資料集 | Stage 2 DoD 5 基準複查；讀取一律 `--profile default` ＋ snapshot |
| **bridge_state schema v1 與拍板欄位清單有出入**（2026-07-10 新增） | 不先對齊就實作，會產生兩套欄位語意並存 | 對齊明文列為 Stage 2 實作第一步（memory-bridge-state.md 第 6 節）；schema／測試／文件三者同動 |
