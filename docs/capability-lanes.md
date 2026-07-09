# Capability Lanes — registry 層的執行通道定義（v0.1）

日期：2026-07-09　狀態：**定稿（v1）**　負責領域：`engineering`（Stage 1 收斂產出）

機器可讀正本：[`registry/capability_lanes.yaml`](../registry/capability_lanes.yaml)。
比照既有慣例（`delegation_policy.md` ↔ `registry/delegation_policy.yaml`、
`memory-taxonomy.md` ↔ `registry/consolidation_policy.yaml`）：文件給人看理由，
yaml 給程式查參數。

## 1. 三個概念的分工

| 概念 | 定義 | 正本位置 |
|---|---|---|
| **Agent** | 擁有一個 domain 的**職責**（誰對這類任務負責） | `registry/agents.yaml`（+ `.claude/agents/*.md`） |
| **Capability** | 描述一種**工作型態**（這件事是哪種工作：複雜 coding、大量研究……） | `registry/model_router.yaml` 的 `routes` key |
| **Capability Lane** | 把 capability 對應到**實際執行通道**：provider／model 或 native route／Hermes profile／成本／風險／允許使用者／使用限制 | `registry/capability_lanes.yaml` |

一句話：Agent 決定「誰負責」，Capability 描述「是哪種工作」，Lane 描述「這種工作
可以走哪條通道去執行、代價與限制是什麼」。

## 2. 跟既有機制的關係（不新增第二套詞彙）

- **Capability 名稱的正本仍是 `model_router.yaml`**。lane 的 `capability` 欄位
  必須是那裡存在的 route key（`complex_coding`、`architecture_reasoning`、
  `claude_native`、`google_ecosystem`、`bulk_research`）。lane 不發明新的
  capability 名字。
- **`agents.yaml` 的 `default_capability` 機制完全不變**：subagent 查
  `default_capability` → 呼叫 `scripts/route_model.py <capability> <prompt-file>`
  → `route_model.py` 解析 `model_router.yaml` 執行。**`route_model.py` 不讀
  `capability_lanes.yaml`，執行邏輯零變更**。
- **每個既有 capability 都對應到至少一條 lane**：`claude_native` → `claude-native`、
  `architecture_reasoning` → `claude-architecture-reasoning`、`complex_coding` →
  `openrouter-gpt55-coding`（另有 Hermes 側的 reference lanes）、`bulk_research` →
  `openrouter-nemotron-bulk-research`（另有 Hermes 側 reference lanes）、
  `google_ecosystem` → `openrouter-gemini-google-ecosystem`。
- **一個 capability 可以有多條 lane**（例如 `complex_coding` 有 OpenRouter GPT-5.5
  一條 active lane，加上 nemocoding／gptcoding／codereviewer 三條 Hermes reference
  lane）——這正是引入 lane 這一層的原因：`model_router.yaml` 一個 capability 只能
  指一個 route，而系統實際上存在多條可能通道，需要一個地方把它們的成本、風險、
  允許使用者登記下來，而不是塞進 router 破壞它的單一映射語意。
- **一致性由測試保證**（`scripts/test_capability_lanes.py`）：
  - lane 的 `capability` 必須存在於 `model_router.yaml` 的 routes；
  - `execution: route_model` 且 provider=openrouter 的 lane，`model` 必須等於
    對應 route 的 `openrouter_model`（避免兩處漂移；要改 slug 改 router，lane 同步）;
  - `allowed_agents` 必須是 `agents.yaml` 裡存在的 agent id；
  - `fallback_lane` 必須指向本檔內存在的 lane id。

## 3. `execution` 三種型態

| execution | 意義 | 今天可用？ |
|---|---|---|
| `native` | 由目前的 Claude session 直接處理，不對外呼叫（= router 的 `via: native`） | 是 |
| `route_model` | 經 `scripts/route_model.py` 呼叫 OpenRouter（= router 的 `via: openrouter`） | 是 |
| `hermes_profile` | 對應一個實際存在的 Hermes profile 的工作通道；模型/工具由 profile 自己的 config 決定，本 registry 不重複記載 | 否——`status: reference`，沒有任何 runtime 自動走它；Stage 2 bridge 的 state 記錄（`selected_capability_lane`）會引用 lane id 做追蹤 |

Hermes profile lanes 的立場沿用既有硬規則：ClaudeCodeOS 對 Hermes 資料**唯讀**
（`HermesSessionAdapter`，`mode=ro`）；Hermes session 內容進入長期記憶的唯一路徑是
`memory/inbox/` ＋ consolidation 政策（[memory-taxonomy.md](memory-taxonomy.md)
4.2 useful 判定／4.3 guardrails）。lane 登記不改變任何寫入權限。

## 4. 欄位定義

| 欄位 | 必填 | 說明 |
|---|---|---|
| `id` | ✔ | lane 唯一識別，kebab-case。外部記錄（如 Stage 2 bridge state 的 `selected_capability_lane`）引用它 |
| `capability` | ✔ | `model_router.yaml` routes 的 key |
| `execution` | ✔ | `native` \| `route_model` \| `hermes_profile` |
| `provider` | ✔ | `anthropic` \| `openrouter` \| `hermes` |
| `model` | ✔（可為 null） | 實際模型 slug；native 與 hermes_profile lane 為 `null`（前者不對外呼叫，後者由 profile config 決定） |
| `hermes_profile` | execution=hermes_profile 時必填 | 實際存在的 Hermes profile 名稱 |
| `status` | ✔ | `active`（今天就可走）\| `reference`（描述性登記，尚未接線） |
| `cost_tier` | ✔ | `included`（訂閱內）\| `free` \| `paid` \| `unknown`（僅限 reference lane，成本未盤點前不瞎編） |
| `risk_tier` | ✔ | `low` \| `medium` \| `high`——資料外流面與內容敏感度的治理判斷（例：送 prompt 出外部 API 至少 medium；常含財務個資的通道 high） |
| `allowed_agents` | ✔ | 允許使用這條 lane 的 domain agent id 清單（`agents.yaml`）。「允許」≠「預設」——預設仍看 `default_capability` |
| `intended_use` | ✔ | 一句話：這條 lane 該用在什麼工作 |
| `guardrails` | ✔ | 使用限制清單。用詞沿用既有政策：敏感內容定義與 fail-closed 規則引用 memory-taxonomy 4.3；inbox-only 寫入引用 ARCHITECTURE.md 第 4 節 |
| `fallback_lane` | 選填 | 此 lane 不可用（API key 缺失、服務中斷）時退到哪條 lane。目前所有 openrouter lane 都退 `claude-native`，與 router 的 `fallback_order`（claude 第一位）方向一致 |

## 5. 使用方式（現況）

- **subagent**：行為不變——查 `default_capability`、呼叫 `route_model.py`。
  想改走非預設通道時，lane 的 `allowed_agents`／`cost_tier`／`guardrails` 是
  判斷依據（例如 engineering 想用 GPT-5.5 以外的通道，先查這裡有沒有登記、
  允不允許）。
- **CoS**：分派時不需要讀本檔（分派看 delegation policy 與 agents.yaml）；
  只有在需要回答「這類工作可以走哪些通道、代價是什麼」的 meta 問題時查閱。
- **Stage 2 bridge（未來）**：處理狀態記錄的 `selected_capability_lane` 欄位
  引用 lane id（見 [memory-bridge-state.md](memory-bridge-state.md)），讓
  「這個 session 是哪條通道產生的工作」可追蹤。

## 6. 不做的事

- 不修改 `route_model.py`（本版只加了讀取/驗證 schema 的測試）。
- 不讓任何 runtime 自動依 lane 切換模型——那是之後（若有需要）的獨立決策。
- 不在 lane 裡重複記載 Hermes profile 的模型/工具設定——避免第二份會漂移的真相。
