# Memory Bridge State — Stage 2 session bridge 的處理狀態記錄（定義，不實作）

日期：2026-07-09　狀態：**格式定稿（v1）／實作待 Stage 2**　負責領域：`engineering`

這份文件定義未來 Stage 2 session bridge（[hermes-integration-roadmap.md](hermes-integration-roadmap.md)
Stage 2）的「處理狀態記錄」格式：bridge 每偵測到一個 Hermes 新完結 session，
要記下「處理到哪、判定成什麼、為什麼」，用於**去重（idempotent）**與**可追蹤性**。
機器可讀的 schema 正本在 [`registry/bridge_state_schema.yaml`](../registry/bridge_state_schema.yaml)
（`claudecodeos.bridge_state.v1`）。

**本文件只定義格式，不實作 bridge、不安裝排程**——那是 Stage 2 的工作。

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
4. bridge 是 headless 管線的一部分，寫入權限沿用既有規則：**只能在
   `memory/inbox/` 新增檔案，不能編輯既有檔案、不能碰 `memory/*.md` 正本**
   （ARCHITECTURE.md 第 4 節）。

## 2. 欄位摘要

完整定義（型別、必填、enum 值）見 `registry/bridge_state_schema.yaml`。摘要：

| 欄位 | 意義 |
|---|---|
| `session_id` | Hermes session id（沿用 `claudecodeos.session.v1`） |
| `source_profile` | 來源 Hermes profile（等同指出來源 db） |
| `session_source` | Hermes 的 `sessions.source`（cli/tui/telegram/cron…） |
| `status` | `discovered` \| `skipped` \| `to_inbox` \| `imported` \| `failed` \| `needs_review` |
| `memory_type` | `procedural` \| `semantic` \| `episodic` \| `none`（三層 taxonomy 的初步歸類） |
| `useful_chat` | memory-taxonomy 4.2 useful 判定結果（bool） |
| `selected_capability_lane` | 對應的 lane id（`registry/capability_lanes.yaml`，選填） |
| `decision_reason` | 一句話：為什麼是這個 status |
| `inbox_file` | 落地的 inbox 檔案路徑（to_inbox/imported 時必填） |
| `processed_at` | 最後狀態變更時間（UTC ISO 8601） |
| `error` | failed 時必填，不得含 session 敏感內容 |
| `event_id` / `event_id_range` | 去重依據，沿用 adapter 的 `hermes:<session_id>[:<rowid>]` 慣例 |

## 3. 狀態機與既有政策的對應

```
 discovered ──依 4.2/4.3 判定──▶ skipped（略過：排除訊號，或敏感內容 fail-closed）
     │                        ▶ needs_review（留給互動式 session 人工確認）
     │                        ▶ failed（error 必填；重跑靠 event_id 去重）
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
  `.failed/`，taxonomy 第 5 節既有立場）；bridge state 的 `imported` 只是
  對照目錄位置回填的**追蹤快取**，兩者不一致時以目錄位置為準。
- **去重**：`event_id`（session 層級 `hermes:<session_id>`）是 idempotency key
  ——同一 session 重跑 bridge 不重複 enqueue、不重複落地（Stage 2 DoD 2 的
  「恰好一次」就靠這個欄位＋adapter `open(mode="x")` 的檔案層級保證）。
  訊息層級的 `event_id_range` 跟 inbox frontmatter（`claudecodeos.inbox.v1`）
  的同名欄位互相對照。

## 4. 儲存載體（建議，**Stage 2 決策**）

三個候選，共同前提：都在 ClaudeCodeOS 側、都不進 git（runtime state 不是 registry
內容）、都不碰 Hermes：

| 載體 | 優點 | 缺點 |
|---|---|---|
| `hermes/jobs.db` 新 table | 沿用既有 `hermes/db.py` 的 SQLite 習慣與備援；一個 db 好觀測 | 把「job 生命週期」跟「bridge 簿記」耦合在同一 schema；dashboard 的 read-only 查詢層要跟著加 |
| **獨立小 SQLite（`hermes/state/bridge_state.db`）— 建議** | 符合 `hermes/state/` 既有定位（「adapter 自己維護的執行狀態」，跟 rss_seen.json 同類）；schema 演進不影響 jobs.db；可做 UNIQUE(event_id) 硬性去重 | 多一個檔案 |
| `hermes/state/bridge_state.jsonl`（append-only） | 最簡單、天生 append | 去重要全檔掃描；狀態更新（discovered→to_inbox）變成多筆記錄要 last-wins 解讀，容易出錯 |

建議 Stage 2 採**獨立小 SQLite**：bridge 的核心需求是「UNIQUE 約束的去重」與
「單筆狀態更新」，SQLite 直接給，jsonl 要自己搭；而 jobs.db 該保持只屬於 job queue。
**最終選擇在 Stage 2 實作前拍板，不在本文件定案。**

## 5. 與 Stage 2 DoD 的對應

- DoD 2「恰好一次」→ `event_id` 去重 + 檔案層級 `open(mode="x")`。
- DoD 2「明確記錄依政策略過」→ `status=skipped` + `decision_reason`
  （敏感 fail-closed 時 job log 僅記「依政策略過＋session_id」，taxonomy 4.3 既有規則）。
- DoD 3「bridge 自身狀態存放於 ClaudeCodeOS 側」→ 第 4 節載體皆滿足。
- Stage 3 dashboard 觀測「匯入了什麼、略過了什麼」→ 直接讀這份 state（read-only）。
