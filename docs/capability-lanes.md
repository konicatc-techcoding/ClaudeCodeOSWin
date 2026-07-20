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
  必須是那裡存在的 route key（`architecture_reasoning`、`claude_native`、
  `complex_coding`、`bulk_research`）。lane 不發明新的 capability 名字。
  （2026-07-20：原本還有 `google_ecosystem`，隨 OpenRouter provider 路徑一併
  移除，已不是有效 capability 名稱。）
- **`agents.yaml` 的 `default_capability` 機制完全不變**：subagent 查
  `default_capability` → 呼叫 `scripts/route_model.py <capability> <prompt-file>`
  → `route_model.py` 解析 `model_router.yaml` 執行。**`route_model.py` 不讀
  `capability_lanes.yaml`，執行邏輯零變更**。
- **每個既有 capability 都對應到至少一條 lane**：`claude_native` → `claude-native`、
  `architecture_reasoning` → `claude-architecture-reasoning`、`complex_coding` →
  `hermes-nemocoding`／`hermes-gptcoding`、`bulk_research` →
  `hermes-financialresearch`／`hermes-intelligence`。
  （2026-07-20：`complex_coding`／`bulk_research` 原本還各自多一條 OpenRouter
  route_model lane（`openrouter-gpt55-coding`／`openrouter-nemotron-bulk-research`），
  但 `OPENROUTER_API_KEY` 自系統建成以來從未真正設定過，這兩條路徑實務上從未
  打通；使用者拍板全部移除 OpenRouter provider 相關路徑後，這兩個 capability
  現在各自只剩 Hermes 側的 lane，`complex_coding`／`bulk_research` 這兩個
  capability key 本身在 `model_router.yaml` 保留下來，純粹是因為這幾條
  Hermes lane 仍引用它們做 capability 標記，不是因為還有 route_model 通道在用。
  `google_ecosystem` 原本只有 `openrouter-gemini-google-ecosystem` 一條 lane，
  沒有其他依賴，整個移除。）
- **一個 capability 可以有多條 lane**（例如 `complex_coding` 目前有
  nemocoding／gptcoding 兩條 Hermes lane——原本還有 codereviewer，該 profile
  已於 2026-07-20 Phase 2a 稽核時經使用者拍板移除，對應 lane 也一併下線；
  nemocoding／gptcoding 兩條已於同日 Phase 2d 通過真實端對端 smoke test 轉為
  `active`，見 `registry/capability_lanes.yaml`）——這正是引入
  lane 這一層的原因：`model_router.yaml` 一個 capability 只能
  指一個 route，而系統實際上存在多條可能通道，需要一個地方把它們的成本、風險、
  允許使用者登記下來，而不是塞進 router 破壞它的單一映射語意。
- **一致性由測試保證**（`scripts/test_capability_lanes.py`）：
  - lane 的 `capability` 必須存在於 `model_router.yaml` 的 routes；
  - `execution: route_model` 且 provider=openrouter 的 lane，`model` 必須等於
    對應 route 的 `openrouter_model`（避免兩處漂移；要改 slug 改 router，lane 同步）；
    2026-07-20 起 `model_router.yaml` 已無任何 `via=openrouter` 的 route，這條規則
    暫時沒有實際案例可測，但邏輯保留給未來若重新加回某個 route_model provider 時用；
  - `allowed_agents` 必須是 `agents.yaml` 裡存在的 agent id；
  - `fallback_lane` 必須指向本檔內存在的 lane id。

## 3. `execution` 三種型態

| execution | 意義 | 今天可用？ |
|---|---|---|
| `native` | 由目前的 Claude session 直接處理，不對外呼叫（= router 的 `via: native`） | 是 |
| `route_model` | 經 `scripts/route_model.py` 解析 capability 執行（= router 的對應 `via`）。2026-07-20 起 `model_router.yaml` 沒有任何 `via=openrouter` 的 route，這個 execution 型態目前沒有實際案例在用（`route_model.py` 也已移除 OpenRouter 呼叫邏輯），機制本身保留給未來若需要 | 是（但目前無 lane 使用） |
| `hermes_profile` | 對應一個實際存在的 Hermes profile 的工作通道；模型/工具由 profile 自己的 config 決定，本 registry 不重複記載 | 是——v0.1（`scripts/dispatch_domain.py`，Domain Execution Router Phase 1）起，`hermes_profile` lane 可以透過明確 `--lane` opt-in 被真的執行（`hermes -z --profile <name>`）；Phase 2d（2026-07-20）對現有四條 `hermes-*` lane 各跑過一次真實端對端 smoke test 並全部通過，四條均已由 `reference` 轉 `active`，因此自動選路徑（`status: active` 才會被納入候選）現在也可能選到它們，不只限明確 `--lane`（詳見 `registry/capability_lanes.yaml` 對應段落）。Stage 2 bridge 的 state 記錄（`selected_capability_lane`）會引用 lane id 做追蹤 |

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
| `provider` | ✔ | `anthropic` \| `openrouter` \| `hermes`（`openrouter` 目前無任何 lane 使用，2026-07-20 三條 OpenRouter lane 已移除，enum 值保留給未來） |
| `model` | ✔（可為 null） | 實際模型 slug；native 與 hermes_profile lane 為 `null`（前者不對外呼叫，後者由 profile config 決定） |
| `hermes_profile` | execution=hermes_profile 時必填 | 實際存在的 Hermes profile 名稱 |
| `status` | ✔ | `active`（今天就可走）\| `reference`（描述性登記，尚未接線） |
| `cost_tier` | ✔ | `included`（訂閱內）\| `free` \| `paid` \| `unknown`（僅限 reference lane，成本未盤點前不瞎編） |
| `risk_tier` | ✔ | `low` \| `medium` \| `high`——資料外流面與內容敏感度的治理判斷（例：送 prompt 出外部 API 至少 medium；常含財務個資的通道 high） |
| `allowed_agents` | ✔ | 允許使用這條 lane 的 domain agent id 清單（`agents.yaml`）。「允許」≠「預設」——預設仍看 `default_capability` |
| `intended_use` | ✔ | 一句話：這條 lane 該用在什麼工作 |
| `guardrails` | ✔ | 使用限制清單。用詞沿用既有政策：敏感內容定義與 fail-closed 規則引用 memory-taxonomy 4.3；inbox-only 寫入引用 ARCHITECTURE.md 第 4 節 |
| `fallback_lane` | 選填 | 此 lane 不可用（API key 缺失、服務中斷）時退到哪條 lane。2026-07-20：原本三條 OpenRouter lane 都退 `claude-native`（與 `model_router.yaml` 舊的 `fallback_order` 方向一致），這三條 lane 已移除；目前 registry 內沒有任何 lane 設定 `fallback_lane`，`fallback_order` 欄位也已從 `model_router.yaml` 移除（未被程式碼引用，只剩單一 `claude` 模型時已無意義）。dispatch_domain.py 的 fallback 機制本身沒有拿掉，只是暫無實際案例會觸發 |

## 5. 使用方式（現況）

- **subagent**：行為不變——查 `default_capability`、呼叫 `route_model.py`。
  想改走非預設通道時，lane 的 `allowed_agents`／`cost_tier`／`guardrails` 是
  判斷依據（例如 engineering 想用 hermes-nemocoding 以外的通道，先查這裡有沒有
  登記、允不允許）。
- **CoS**：分派時不需要讀本檔（分派看 delegation policy 與 agents.yaml）；
  只有在需要回答「這類工作可以走哪些通道、代價是什麼」的 meta 問題時查閱。
- **Stage 2 bridge（未來）**：處理狀態記錄的 `selected_capability_lane` 欄位
  引用 lane id（見 [memory-bridge-state.md](memory-bridge-state.md)），讓
  「這個 session 是哪條通道產生的工作」可追蹤。

## 6. 不做的事

- 不修改 `route_model.py`（本版只加了讀取/驗證 schema 的測試）。
- 不讓任何 runtime 自動依 lane 切換模型——那是之後（若有需要）的獨立決策。
- 不在 lane 裡重複記載 Hermes profile 的模型/工具設定——避免第二份會漂移的真相。
