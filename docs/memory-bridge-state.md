# Memory Bridge State — Stage 2 session bridge 的處理狀態記錄（定義，不實作）

日期：2026-07-09　狀態：**格式定稿（v1，2026-07-10 對齊修訂，17 欄）／儲存載體已拍板（2026-07-10）／實作待 Stage 2**
負責領域：`engineering`

這份文件定義未來 Stage 2 session bridge（[hermes-integration-roadmap.md](hermes-integration-roadmap.md)
Stage 2）的「處理狀態記錄」格式：bridge 每偵測到一個 Hermes 新完結 session，
要記下「處理到哪、判定成什麼、為什麼」，用於**去重（idempotent）**與**可追蹤性**。
機器可讀的 schema 正本在 [`registry/bridge_state_schema.yaml`](../registry/bridge_state_schema.yaml)
（`claudecodeos.bridge_state.v1`）。

**本文件只定義格式，不實作 bridge、不安裝排程**——那是 Stage 2 的工作。

> **決策更新（2026-07-10，使用者拍板）**：儲存載體定案為**獨立 SQLite
> `hermes/state/bridge_state.db`**（第 4 節）。拍板時的最少欄位清單與 schema v1
> 原 13 欄有出入——**對齊已於同日完成**（Stage 2 實作第一步）：v1 in-place 修訂
> 為 17 欄（marker 不變），對照表與定案結果見第 6 節。

## 1. 明確聲明（硬邊界，全部沿用既有規則）

1. **bridge state 不是 Hermes 的記憶資料庫**，也不是 ClaudeCodeOS 的記憶——
   它只是管線簿記。Hermes `state.db` 是 Episodic 層的事件**來源**，不是
   canonical memory（[memory-taxonomy.md](memory-taxonomy.md) 第 1 節）；
   canonical memory 仍然只有 `memory/*.md` 正本。
2. **不建第二份 `state.db`**、**不寫回 Hermes 原始資料**——adapter 的 read-only
   保證（`mode=ro` + `PRAGMA query_only=ON`）是硬性邊界（roadmap 既定前提）。
3. bridge state 只記錄 **ClaudeCodeOS 側的處理狀態**：session 內容本身留在
   Episodic 層（state.db），摘要進入 `memory/inbox/`（過渡區），這份記錄兩者都
   不是——它只回答「這個 session 我看過沒、判成什麼、檔案落在哪」。
   `memory/inbox/` 仍然只是匯入落地區，**不當狀態資料庫**（2026-07-10 拍板重申）。
4. bridge 是 headless 管線的一部分，寫入權限沿用既有規則：**只能在
   `memory/inbox/` 新增檔案，不能編輯既有檔案、不能碰 `memory/*.md` 正本**
   （ARCHITECTURE.md 第 4 節）。

## 2. 欄位摘要

完整定義（型別、必填、enum 值）見 `registry/bridge_state_schema.yaml`。摘要：

| 欄位 | 意義 |
|---|---|
| `session_id` | Hermes session id（沿用 `claudecodeos.session.v1`） |
| `source_profile` | 來源 Hermes profile（等同指出來源 db）——與 `session_source` 合起來對應拍板清單的 `source` |
| `session_source` | Hermes 的 `sessions.source`（cli/tui/telegram/cron…） |
| `import_status` | `discovered` \| `skipped` \| `to_inbox` \| `imported` \| `failed` \| `needs_review` |
| `memory_type` | `procedural` \| `semantic` \| `episodic` \| `none`（三層 taxonomy 的初步歸類） |
| `useful_chat` | memory-taxonomy 4.2 useful 判定結果（bool） |
| `selected_capability_lane` | 對應的 lane id（`registry/capability_lanes.yaml`，選填、不承載路由） |
| `decision_reason` | 一句話：為什麼是這個 import_status |
| `imported_inbox_path` | 落地的 inbox 檔案路徑（to_inbox/imported 時必填） |
| `processed_path` | 整併歸檔後的 `.processed/` 路徑（選填、僅追蹤快取——**目錄位置仍是唯一真相**，第 3 節） |
| `first_seen_at` | bridge 首次發現這個 session 的時間（UTC ISO 8601） |
| `last_seen_at` | 最近一次掃描仍看到這個 session 的時間（UTC ISO 8601） |
| `updated_at` | 最後狀態變更時間（UTC ISO 8601） |
| `retry_count` | bridge 層級的匯入嘗試次數（int，default 0）——與 `hermes/db.py` jobs.attempts 不同層、不互通 |
| `error_reason` | failed 時必填，不得含 session 敏感內容 |
| `event_id` / `event_id_range` | 去重依據，沿用 adapter 的 `hermes:<session_id>[:<rowid>]` 慣例 |

**注意**：此為 2026-07-10 對齊修訂後的 17 欄現狀；對齊決策記錄見第 6 節。

## 3. 狀態機與既有政策的對應

```
 discovered ──依 4.2/4.3 判定──▶ skipped（略過：排除訊號，或敏感內容 fail-closed）
     │                        ▶ needs_review（留給互動式 session 人工確認）
     │                        ▶ failed（error_reason 必填；重跑靠 event_id 去重）
     └──────值得留────────────▶ to_inbox（在 memory/inbox/ 新增檔案）
                                   │
                                   └─ consolidate-memory（N-gate 觸發）──▶ imported
```

- **判定規則不在這裡重新定義**：useful 判定＝memory-taxonomy 4.2；敏感內容
  guardrails＝4.3（headless 一律 fail-closed，絕不遮罩——遮罩只有互動式路徑可做，
  所以 headless 對「疑似敏感但可能值得留」的 session 記 `skipped` 或
  `needs_review`，不落地）。
- **`imported` 不是 bridge 自己判的**：inbox → 正本的唯一路徑仍是
  `consolidate-memory`（由 N-gate 觸發、`knowledge` 執行）。inbox 檔案的
  待處理/已處理/失敗**唯一真相仍是目錄位置**（`inbox/`、`.processed/`、
  `.failed/`，taxonomy 第 5 節既有立場）；bridge state 的 `imported` 狀態與
  `processed_path` 欄位都只是對照目錄位置回填的**追蹤快取**，兩者不一致時
  以目錄位置為準。
- **去重**：`event_id`（session 層級 `hermes:<session_id>`）是 idempotency key
  ——同一 session 重跑 bridge 不重複 enqueue、不重複落地（Stage 2 DoD 2 的
  「恰好一次」就靠這個欄位＋adapter `open(mode="x")` 的檔案層級保證）。
  訊息層級的 `event_id_range` 跟 inbox frontmatter（`claudecodeos.inbox.v1`）
  的同名欄位互相對照。

## 4. 儲存載體 — ✅ 已拍板（2026-07-10）：獨立 SQLite `hermes/state/bridge_state.db`

**決策**：bridge state 存放於**獨立 SQLite `hermes/state/bridge_state.db`**。
它只記 ClaudeCodeOS 的 bridge 處理狀態——**不是 Hermes memory DB、不是第二份
Hermes state.db**；絕不寫回 Hermes `state.db`。

**與其他決策的一致性（值得明文指出）**：`hermes/state/` 目前在 `.gitignore`
（runtime 資料）**且**在部署同步的排除清單
（[deployment-sync-plan.md](deployment-sync-plan.md) §2 第 3 類）——
`bridge_state.db` 放這裡**天然不會被同步、也不會進版控**，與「bridge 跑在 WSL
部署側」的側別決策（roadmap Stage 2 設計問題 1，同日拍板）一致：這個檔案只會
存在於 WSL 部署側，Windows 開發正本不會出現它的過期副本。

當時評估過的三個候選（保留作決策紀錄）：

| 載體 | 優點 | 缺點 |
|---|---|---|
| `hermes/jobs.db` 新 table | 沿用既有 `hermes/db.py` 的 SQLite 習慣與備援；一個 db 好觀測 | 把「job 生命週期」跟「bridge 簿記」耦合在同一 schema；dashboard 的 read-only 查詢層要跟著加 |
| **獨立小 SQLite（`hermes/state/bridge_state.db`）— ✅ 拍板採用** | 符合 `hermes/state/` 既有定位（「adapter 自己維護的執行狀態」，跟 rss_seen.json 同類）；schema 演進不影響 jobs.db；可做 UNIQUE(event_id) 硬性去重 | 多一個檔案 |
| `hermes/state/bridge_state.jsonl`（append-only） | 最簡單、天生 append | 去重要全檔掃描；狀態更新（discovered→to_inbox）變成多筆記錄要 last-wins 解讀，容易出錯 |

## 5. 與 Stage 2 DoD 的對應

- DoD 2「恰好一次」→ `event_id` 去重 + 檔案層級 `open(mode="x")`。
- DoD 2「明確記錄依政策略過」→ `import_status=skipped` + `decision_reason`
  （敏感 fail-closed 時 job log 僅記「依政策略過＋session_id」，taxonomy 4.3 既有規則）。
- DoD 3「bridge 自身狀態存放於 ClaudeCodeOS 側」→ 第 4 節拍板載體滿足。
- Stage 3 dashboard 觀測「匯入了什麼、略過了什麼」→ 直接讀這份 state（read-only）。

## 6. 拍板記錄與 schema 對齊 — ✅ 已對齊（2026-07-10）

### 6.1 拍板的最少追蹤欄位清單

使用者拍板時給的「至少追蹤」欄位：`session_id`、`source`、`first_seen_at`、
`last_seen_at`、`import_status`、`imported_inbox_path`、`processed_path`、
`error_reason`、`retry_count`、`updated_at`。

### 6.2 與 schema v1 原 13 欄的出入對照（決策依據記錄）＋ 定案結果

| 拍板欄位 | schema v1 原對應 | 出入性質 | 定案結果（2026-07-10 對齊） |
|---|---|---|---|
| `session_id` | `session_id` | 一致 | 不變 |
| `source` | `source_profile` ＋ `session_source` | v1 拆成兩欄（profile 與 Hermes source），拍板清單是一欄——對齊時決定合併或保留拆分 | **保留雙欄**：profile 出處（主 db＋5 個 profile db）與 adapter normalized 的 session_source 各有資訊，合併會遺失；拍板清單是「至少」非「僅限」 |
| `first_seen_at` | **無** | 新增需求 | **新增**：string、required、UTC ISO 8601（bridge 首次發現） |
| `last_seen_at` | **無** | 新增需求 | **新增**：string、required、UTC ISO 8601（最近一次掃描看到） |
| `import_status` | `status` | 命名出入（enum 值集是否沿用 v1 六值，對齊時定） | **改名為 `import_status`**（與 jobs table 的 `status` 區隔）；enum 六值不變 |
| `imported_inbox_path` | `inbox_file` | 命名出入 | **改名為 `imported_inbox_path`** |
| `processed_path` | **無**（v1 靠目錄位置為唯一真相＋`imported` 狀態） | 新增需求；須維持「目錄位置為準、state 只是追蹤快取」的既有立場（第 3 節） | **新增**：string、optional；description 明文「僅為追蹤快取、目錄位置為唯一真相」 |
| `error_reason` | `error` | 命名出入 | **改名為 `error_reason`** |
| `retry_count` | **無** | 新增需求；語意須與 job queue 既有 retry（`hermes/db.py` attempts）劃清分工 | **新增**：int、required、default 0；description 明文與 jobs.attempts（job 執行重試）不同層、不互通 |
| `updated_at` | `processed_at` | 命名出入 | **改名為 `updated_at`** |
| —（拍板清單未提） | `memory_type`、`useful_chat`、`selected_capability_lane`、`decision_reason`、`event_id`／`event_id_range` | v1 既有欄位，拍板清單是「至少」不是「僅限」——預設保留，對齊時確認 | **全數保留**（`selected_capability_lane` 維持選填、不承載路由；`event_id`／`event_id_range` 為跨檔共用慣例，不改名） |

版本決策：**v1 in-place 修訂**（marker 維持 `claudecodeos.bridge_state.v1`）——
schema 仍是 definition-only、無任何 runtime 寫入者與存量資料，升 v2 會暗示一個
不存在的 migration。對齊後 13 − 0 ＋ 4 ＝ **17 欄**。

### 6.3 對齊工作 = Stage 2 實作的第一步 — ✅ 已完成（2026-07-10）

**明文標定：`claudecodeos.bridge_state.v1`（`registry/bridge_state_schema.yaml`）
與 6.1 拍板欄位清單的對齊，是 Stage 2 實作動工時的第一步工作。**
已於 2026-07-10 由 engineering 完成：schema 修訂（17 欄）＋測試同步
（7 → 10 tests 全綠）＋本文件第 2 節鏡像三者同動。

拍板當輪（決策記錄輪）不直接改 schema 的原始理由（保留脈絡）：

1. schema yaml 有測試把關（`bridge_state` 測試套件，Stage 1 checkpoint 記錄 7 tests）
   ——改欄位必須同步改測試並跑過，屬 engineering 實作工作，不是決策記錄。
2. 出入不只是改名：`source` 合併與否、`retry_count` 與 job queue retry 的分工、
   `processed_path` 與「目錄位置為唯一真相」立場的相容寫法，都是要在實作時一併
   定案的語意問題，現在硬改反而先製造第二份不一致。
3. 對齊產出應是 schema v2（或 v1 修訂）＋測試＋本文件第 2 節鏡像三者同動。
   ——實際定案採 v1 in-place 修訂，三者同動已落實。

### 6.4 同日相關決策（正本記錄在 roadmap Stage 2，此處只記影響）

- **側別**：bridge 跑 **WSL 部署側**——worker、jobs.db、systemd timers、runtime logs
  都在 WSL；Windows Hermes 運行導致直讀被鎖時，bridge **必須**用既有 snapshot／
  `immutable=1` 讀取路徑，不可改寫 Hermes DB。
- **不接自動路由**：Stage 2 bridge 不依賴 Capability Lane 自動路由；
  `capability_lanes.yaml` 維持 reference/planning 層。因此本 schema 的
  `selected_capability_lane` **維持選填的追蹤欄位**（記「這是哪條通道產生的工作」），
  不承載任何路由行為；lane 活化（OpenRouter／Hermes profile／Gemini 等）留待
  bridge 穩定後的 Stage 2.x／Stage 3 之後再議。

## 7. Stage 2.4a（2026-07-10）：cutover 設定化＋scan watermark（`bridge_meta`）

排程化（Stage 2.4b）的硬前置——scan 範圍下界不再依賴人工記憶，分成兩層：

- **政策層 cutover**：`hermes/config/bridge.yaml` 的 `cutover`（版控、部署同步
  下發，與 cron_jobs.yaml／rss_feeds.yaml 同層慣例）。bridge 正式啟用日；此前
  session 屬 pre-bridge 歷史，自動掃描的絕對底線。scanner 讀不到設定檔或欄位
  缺失時 fail loud，**絕不默認全掃**。
- **狀態層 watermark**：`bridge_state.db` 新增 `bridge_meta(key, value)` meta
  table（`init_db`／`ensure_schema` 冪等建立；對 2.4a 之前只有 bridge_sessions
  的既有 db 是升級路徑補建），key=`scan_watermark`。語義：最近一次**真實**
  （非 dry-run）scan 成功完成時「該次掃描窗口的上界」＝建立 state.db snapshot
  **之前**取的時間戳（選它而非 max(ended_at)：後者在窗口內無新完結 session 時
  不前進，重複掃描範圍會無限增長；snapshot 時間每次真實 scan 都前進，且
  snapshot 之後才完結的 session 一定 >= watermark，下次掃描必然涵蓋）。
  **只前進不後退**（`advance_scan_watermark`：new <= current 時 no-op 並回報
  現值——真實 scan 一律嘗試 advance，人工帶 `--since` 掃舊區間因此不會把
  watermark 往回拉）；dry-run 絕不推進（測試把關）。

scanner 無 `--since`／`--all-history` 時的 effective since ＝
**max(cutover, watermark)**，輸出明示來源（config cutover／bridge_meta
watermark）。窗口重疊無害：`event_id` 去重＋touch-only 語義保證冪等，
寧可保守重疊、不可跳漏。

**可拋棄重建語義不變**：watermark 與 bridge_sessions 同屬部署側 runtime
state——db 整個刪掉重建後 watermark 消失，scanner 退回 cutover 底線重掃
（重掃範圍變大但絕不越過 cutover），event_id 去重保證重掃無害。檢視指令：
`python3 hermes/bridge_state.py watermark [--db-path PATH]`。
