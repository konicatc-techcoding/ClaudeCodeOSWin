# Memory Taxonomy 與 Consolidation 政策 — v0.1

日期：2026-07-09　狀態：**定稿（v1）**　負責領域：`knowledge`（由互動式 CoS session 分派）

這份文件定義 ClaudeCodeOS 的三層記憶分類（Procedural / Semantic / Episodic），以及
「什麼東西、在什麼條件下、經過誰的把關，才能進入長期記憶正本」的 consolidation 政策。
它同時是 [hermes-integration-roadmap.md](hermes-integration-roadmap.md) Stage 1 DoD 3
要求的「session 匯入政策」定稿——Stage 2 的自動化 bridge 要編碼的就是這份規則。

**設計立場**：三層分類的概念啟發自外部的記憶架構模式（記憶依「怎麼做事／穩定事實／
有日期的事件」分層，事件先累積、達門檻才蒸餾成事實），但本文件**不是照抄任何外部架構**。
所有規則都用 ClaudeCodeOS 既有的機制表達：delegation、`memory/inbox/`、
`consolidate-memory` skill、正本寫入門檻。**本文件不新增任何寫入路徑或儲存機制**——
它只是給既有機制命名、補上觸發條件與把關規則。

機器可讀的參數（N 值、訊號清單、guardrail 動作）在
[`registry/consolidation_policy.yaml`](../registry/consolidation_policy.yaml)，
比照 `delegation_policy.md` ↔ `registry/delegation_policy.yaml` 的既有慣例：
文件給人看理由，yaml 給程式查參數，要調整數值改 yaml，不用改本文件。

---

## 1. 三層 Taxonomy 與既有檔案的對應

| 層 | 意義 | ClaudeCodeOS 對應 | 誰能寫 |
|---|---|---|---|
| **Procedural**（how to act） | 行為規則、技能、分派與路由政策——「系統怎麼做事」 | `.claude/agents/*.md`、`.claude/skills/**/SKILL.md`、`CLAUDE.md`、`registry/delegation_policy.yaml`、`registry/agents.yaml`、`registry/model_router.yaml`、`registry/consolidation_policy.yaml`、workflow 腳本（`hermes/adapters/` 等） | 版本控管（git）。變更走正常分類程序：政策／文件由互動式 CoS session 決策，程式碼變更分派 `engineering` |
| **Semantic**（durable facts） | 穩定事實、使用者偏好、專案脈絡、外部參照——「系統知道什麼」 | `memory/*.md` 正本（`user_*` / `project_*` / `feedback_*` / `reference_*`）＋ `memory/MEMORY.md` 索引 | **只有互動式 CoS session 或 consolidation pass**（`consolidate-memory` skill，由 `knowledge` 執行）。ARCHITECTURE.md 第 4 節的既有硬規則 |
| **Episodic**（dated events） | 有時間戳的事件與對話史——「系統經歷過什麼」 | Hermes `state.db`（**唯讀**，僅經 `HermesSessionAdapter` 取用）、`hermes/jobs.db`（job 生命週期）、`logs/`、`memory/inbox/.processed/`（整併稽核軌跡） | `state.db` 由 Hermes 擁有，ClaudeCodeOS **絕不寫入、絕不建第二份**；`jobs.db` 只由 `hermes/db.py` 寫；`logs/` 由各 runtime 元件寫 |

補充兩點：

- **`memory/inbox/` 不是第四層**。它是 Episodic → Semantic 的**過渡區**：內容已經是
  「發生過的觀察」（episodic 性質），但還沒通過整併門檻、尚未成為 durable fact。
  待處理／已處理／失敗的狀態由目錄位置表達（`inbox/` 本層＝待處理、`.processed/`、
  `.failed/`），這是唯一真相，不另設狀態欄位。
- **`feedback_*.md` 是 Semantic 裡的 Procedural 候補來源**：使用者對「怎麼做事」的糾正
  先以事實形式落在 Semantic 層，反覆出現後才升級成 Procedural（見第 3 節）。

## 2. 寫入權限規則（全部是既有硬規則，此處只是明文彙整）

以下每一條都引用既有文件，本文件不新增也不放寬任何一條：

1. `memory/*.md` 正本只有互動式 CoS session 或 consolidation pass 能編輯
   （ARCHITECTURE.md 第 4 節；`consolidate-memory` SKILL.md 前提）。
2. 背景（headless）任務只能在 `memory/inbox/` **新增**檔案，不能編輯既有檔案、
   不能碰正本（ARCHITECTURE.md 第 4 節、CLAUDE.md 邊界節）。
3. Hermes 絕不直寫 `memory/*.md`；Hermes 資料進入記憶的唯一路徑是
   `HermesSessionAdapter`（技術上強制 read-only：`mode=ro` + `PRAGMA query_only=ON`，
   見 `hermes/session_adapter/README.md`）。
4. 不建立第二份 `state.db`、不寫入 Hermes 原始資料
   （hermes-integration-roadmap.md 既定前提）。
5. Procedural 層（agents / skills / registry）是共享版本控管資產，CoS 與 Hermes
   都是使用者不是擁有者（ARCHITECTURE.md 第 3 節）；變更一律走 git 與分派程序。

## 3. 資料流：Episodic → Semantic → Procedural

```
  Episodic 層                          過渡區                        Semantic 層
┌─────────────────────┐
│ Hermes state.db      │─ adapter to-inbox ──┐
│ （唯讀，經 adapter） │                      ▼
├─────────────────────┤               memory/inbox/  ── consolidate-memory ──▶ memory/*.md 正本
│ jobs / RSS / cron /  │─ headless CoS ──────┘   （N-gate ＋ guardrails，        （＋ MEMORY.md 索引）
│ Telegram 背景任務    │   新增 inbox 檔案         見第 4 節）                          │
└─────────────────────┘                                                              │
                                                                                      ▼
                                                                            Procedural 候補回報
                                                                    （skill／policy 變更建議，見下）
```

- **Episodic → 過渡區**：兩條既有路徑。(a) `knowledge` 對指定 Hermes session 呼叫
  adapter 的 `to-inbox` CLI（Stage 1 手動路徑）；(b) headless 背景任務（含未來
  Stage 2 的 session bridge）在 `memory/inbox/` 新增檔案。兩條都受第 4 節的
  匯入 guardrails 約束——**把關點在落地前的呼叫端**，因為 adapter 明確不過濾內容。
- **過渡區 → Semantic**：唯一路徑是 `consolidate-memory` skill（去重、衝突處理、
  型別歸檔、索引同步、`.processed/` 歸檔），本政策不重新發明合併邏輯，只定義
  「何時觸發」與「哪些內容不該走到這一步」。
- **Semantic → Procedural（升級）**：consolidation 過程中若發現**反覆出現的操作模式**
  ——同一類糾正在 `feedback_*.md` 累積 3 次以上、同一段人工步驟在多個 session 重複、
  某個 project 事實實質上是一條行為規則——`knowledge` 在整併回報中列為
  **Procedural 候補**（建議變成哪個 skill／哪份 registry policy 的哪一條）。
  **升級不自動發生**：由 CoS 整合回報後，政策文件變更由互動式 session 決策，
  程式碼／skill 實作分派 `engineering`。這保持 Procedural 層的變更全部走 git 與
  delegation 程序，跟第 2 節第 5 條一致。

## 4. Consolidation 政策

### 4.1 觸發規則（N-gate）

原則：**事件先累積、達門檻才整併**——不是每筆 inbox 檔案都立刻蒸餾進正本。
每次 consolidation 都要全文重讀正本與索引，逐筆觸發是浪費；但累積太久，
記憶滯後又會讓 `planning` 等依賴 `knowledge` 的領域拿到過期脈絡。

觸發條件（滿足任一即觸發，參數正本在 `registry/consolidation_policy.yaml`）：

1. **累積門檻**：`memory/inbox/` 待處理檔案中，`usefulness` 非 `low` 的累積達
   **N = 5**（`trigger.min_useful_pending`）。
2. **時間上限**：最舊的待處理檔案超過 **7 天**（`trigger.max_pending_age_days`），
   即使不足 N 也觸發——避免低流量期長尾滯留。
3. **人工觸發**：互動式 session 明確要求整併時無條件執行（`trigger.manual_always`），
   維持既有行為不變。

**N = 5 的理由**：目前 inbox 實際流量很低（v0.1 至今 `.processed/` 只有 2 筆），
`daily-memory-check` cron 每天 08:00 會檢查一次。N = 1 等於逐筆整併，每次都付
全文重讀成本；N 太大（如 20）以現有流量要累積數週，違反 7 天上限的精神。
5 大約對應「正常使用一週內的累積量」，跟時間上限互相呼應。Stage 2 bridge 上線後
流量會變，屆時只調 yaml 數值，不改流程。

**跟既有機制的銜接**：`daily-memory-check` 每天喚醒的 headless CoS 依這三條判斷
「今天要不要分派 `knowledge` 跑 consolidate-memory」；判斷本身是 orientation read
（讀 inbox 檔名與 frontmatter），不觸碰正本。

### 4.2 Useful 判定標準（哪些內容值得進 inbox／正本）

**正面訊號**（含任一即視為 useful）：

- 含**實質決策**：拍板的取捨、被接受或否決的方案、含理由的方向選擇
- 含**新事實**：環境眉角、系統狀態變化、實測得到的限制或行為（例如「WSL 側讀
  live state.db 會被互斥擋下」這類）
- 含**使用者偏好或糾正**：對「怎麼做事」的明確表達（→ `feedback_*` 型別）
- 含**跨 session 可重用的結論**：下次遇到同類問題可以直接引用的答案
- 含**外部參照**：資料在哪個系統、追蹤在哪個服務（→ `reference_*` 型別）

**排除訊號**（符合任一即不匯入；已在 inbox 的由 consolidate-memory 移 `.failed/`）：

- **純測試 session**：title 或內容是測試標記（`STAGE0-OK`、`_hermes_bridge_test`
  型健康檢查訊號、底線開頭＋test 字樣的命名模式）。這類訊號本身「管線可動」的
  事實已經記錄在正本，重複出現不需要每次深究——這是既有結論的政策化。
- **指令試誤**：大量 tool 呼叫但沒有得出結論的除錯過程（結論若有價值，摘結論即可，
  過程留在 Episodic 層的 state.db／logs 就好）
- **單純閒聊**：對應 delegation policy 的 `general_conversation`，無領域任務內容
- **內容過短**：低於 `usefulness.min_content_chars`（預設 200 字元）或 session
  訊息數低於 `usefulness.min_session_messages`（預設 4），且不含上述任何正面訊號
- **重複**：內容已被既有正本涵蓋（這條本來就是 consolidate-memory 流程第 3 步）

判定時機有兩個：**落地前**（`knowledge` 挑 session、或 headless CoS 決定要不要寫
inbox 時）優先過濾，這是主要防線；**整併時**（consolidate-memory 流程第 4 步的
「格式不合法」路徑）作為第二道網。

### 4.3 Guardrails

| 情境 | 判定 | 動作 | 把關者 |
|---|---|---|---|
| 測試 session／管線健康檢查 | 4.2 排除訊號第 1 條 | 不落地 inbox；已在 inbox 的移 `.failed/` 並在回報註明 | 落地前：呼叫端（headless CoS 或 knowledge）；整併時：consolidate-memory |
| 一般雜訊（試誤、閒聊、過短） | 4.2 其餘排除訊號 | 同上 | 同上 |
| **敏感內容**（見下） | 內容含 credentials／API token／密碼、健康資料、財務個資 | **headless：一律拒絕匯入該 session（fail-closed），不落地、不節錄，僅在 job log 記「依政策略過＋session_id」**；**互動式：人工確認**——遮罩後匯入或整段放棄，由使用者當場拍板 | headless：呼叫端程式規則；互動式：使用者本人 |

敏感內容規則的細節：

- **為什麼 fail-closed**：adapter 明確不過濾 `messages.content`（README 的既定立場），
  責任在落地前的判斷層。headless 模式沒有人可以確認，所以預設答案是「不匯入」，
  寧可漏記一筆也不讓金鑰進入 git 版本控管的正本。漏掉的 session 永遠還在
  Episodic 層（state.db），互動式 session 隨時可以人工補匯。
- **遮罩規則**（僅互動式路徑可用）：金鑰／token／密碼類**整段移除**，不做部分遮罩
  （`sk-...xxxx` 這種留頭尾的形式仍可能協助比對，一律換成 `[REDACTED:credential]`）。
  健康／財務個資可保留「存在此類討論」的事實層級描述，具體數值與細節移除。
  遮罩後的內容必須自足——讀者不需要回去看原文才能理解。
- 匯入後的 inbox 檔案以 frontmatter `sensitivity` 欄位記錄處理狀態（見第 5 節），
  consolidate-memory 整併時據此決定正本內容的詳略。

## 5. Inbox 檔案 frontmatter 約定（`claudecodeos.inbox.v1`）

給 `memory/inbox/` 新增檔案的**建議** YAML frontmatter。定位：輔助 consolidation
判斷（4.1 的 N-gate 數 usefulness、4.3 的 sensitivity 稽核），**不是必要條件**——
無 frontmatter 的檔案照 consolidate-memory 既有流程處理（內容型別判斷本來就不依賴
frontmatter），既有 `.processed/` 檔案不回溯補寫。

```yaml
---
schema: claudecodeos.inbox.v1
source: hermes-session        # hermes-session | hermes-bridge | rss | telegram | cron | manual
session_id: 20260630_183709_063b4e40   # 有對應 Hermes session 時必填，否則省略
event_id_range: "hermes:<session_id>:1..102"   # 選填，對應 claudecodeos.event.v1 的去重 key 範圍
created_at: 2026-07-09T08:00:00Z       # 落地時間（UTC ISO 8601）
usefulness: normal            # high | normal | low —— 落地者依 4.2 標準的自評
usefulness_reason: 一句話說明為什麼值得留（或為什麼存疑）
sensitivity: none             # none | masked | blocked-partial
                              # none = 未偵測到敏感內容
                              # masked = 互動式路徑遮罩後匯入（4.3）
                              # blocked-partial = session 部分內容被整段移除
---
（正文：session 摘要或觀察內容，格式沿用既有 inbox 慣例）
```

- 跟 adapter 的銜接：`session_id` 直接沿用 `claudecodeos.session.v1` 的欄位；
  `event_id_range` 沿用 `claudecodeos.event.v1` 的 `event_id` 格式，讓重複匯入
  可以對照去重（adapter 的 `open(mode="x")` 已保證檔案層級 idempotent，這個欄位
  是內容層級的稽核輔助）。
- `consolidation` 狀態**刻意不設欄位**：待處理／已處理／失敗由目錄位置
  （`inbox/`、`.processed/`、`.failed/`）表達，是唯一真相，避免雙重狀態不同步。
- headless 落地的檔案：`sensitivity` 只可能是 `none`（4.3 的 fail-closed 規則下，
  headless 遇到敏感訊號根本不落地）；`masked`／`blocked-partial` 只會出現在
  互動式路徑產出的檔案。

## 6. 與既有規則的關係

本政策**沒有**與任何既有規則衝突，全部是在既有機制上補「觸發條件與把關」：

- ARCHITECTURE.md 第 4 節的寫入門檻：原樣沿用（第 2 節逐條引用）。
- `consolidate-memory` skill：合併邏輯不變；本政策只回答「何時跑」（4.1）與
  「哪些內容不該進來」（4.2／4.3），對應 skill 流程第 1 步之前與第 4 步的判斷依據。
- `delegation_policy.yaml`：consolidation 的分派歸屬不變（`knowledge_management` →
  `knowledge`）；`daily-memory-check` 的 N-gate 判斷屬 orientation read。
- hermes-integration-roadmap.md：本文件即 Stage 1 DoD 3 的政策定稿；
  Stage 2 bridge 的「雜訊控制」與「headless 依政策略過」直接引用 4.2／4.3。

## 7. Recall-first：consolidation 的讀側對應

§3／§4 定義的是**寫側**——事件怎麼從 Episodic 蒸餾進 Semantic、再標出 Procedural 候補。**recall-first 是對應的讀側**：CoS 收到任務、動手或分派之前，先主動檢索既有記憶與 skill，有相似就複用、沒有才從頭來。兩者互補、方向相反，共用同一批 artifact，**不新增任何寫入路徑**（recall 只讀）。

- **檢索哪幾層（依「一次命中省下多少重想」排序）**：Procedural（`.claude/skills/**/SKILL.md`、`CLAUDE.md`、registry policies）優先——命中代表「有現成程序可直接執行」；其次 Semantic（`memory/*.md` + `MEMORY.md` 索引）——「有可複用的既有決策/事實」；Episodic（`inbox/.processed`、logs、state.db 唯讀）最後且多半不需要，因為它通常已被 consolidation 蒸餾進 Semantic。MVP 只查 Procedural + Semantic。
- **對 consolidation 的要求**：recall 越有用，consolidation 產出品質越關鍵（垃圾進→召回無用）。因此 `consolidate-memory` 的「索引同步」步驟應維護 **recall-友善的 MEMORY.md 索引**（每條帶關鍵字 + 一句「這條回答什麼問題」）。這是對既有步驟的補充，不是新機制。
- 決策程序面的強制/可稽核規則見 [delegation_policy.md](../delegation_policy.md)「決策程序」步驟 1.5；機器可讀參數見 [registry/delegation_policy.yaml](../registry/delegation_policy.yaml) 的 `recall` 區塊。

### 7.1 recall 複用 → skill 升級判準

§3「Semantic → Procedural 升級」已定義升級管線（consolidation 時列 Procedural 候補、人在迴路、skill 實作分派 `engineering`）。recall-first 在既有升級訊號（如「同類糾正累積 3 次」）之外，新增一個訊號：

- **某個解法/程序被 recall 召回並複用達 N 次（預設 3，見 `registry/consolidation_policy.yaml` 的 `skill_promotion.min_recall_reuse`）** → `knowledge` 在 consolidation 回報中列為 SKILL.md 候補。反覆被召回複用，正是「照做不用重想」該沉澱成 skill 的訊號。
- 升級仍不自動發生：CoS 確認後，skill 撰寫分派 `engineering`，走 git + delegation（與 §3 一致）。
- **複用次數怎麼算**：MVP 階段無自動計數器，由 `knowledge` 在 consolidation 時從 inbox／回報中人工盤點「這解法最近被當答案端出幾次」即可；未來若上 recall 腳本再談自動計數（非 MVP 範圍）。

### 7.2 記憶正本歸屬：CCOS 是正本、Hermes 是來源之一（2026-07-23 分工定調）

§7／§7.1 講的是讀側「怎麼檢索」；本節補的是讀側**檢索誰**這個更前置的歸屬問題——**記憶正本與 recall 的家在 ClaudeCodeOS（CCOS），不在 Hermes**。這是 2026-07-23 使用者拍板的 CoS↔Hermes 架構分工定調（完整理由見 [memory/project_cos-hermes-division-of-labor.md](../memory/project_cos-hermes-division-of-labor.md)），此處只把跟本 taxonomy 相關的治理結論明文化：

- **記憶正本＝CCOS**：`memory/*.md` + `MEMORY.md` 索引（Semantic 層，§1）是唯一正本；recall（§7）只查這裡。
- **Hermes＝來源之一，不是正本**：Hermes 的 session（`state.db`，Episodic 層）只是**餵進 CCOS 記憶的來源之一**，經唯讀 bridge → `inbox/` → consolidation 進正本（§3 已有的 Episodic→Semantic 路徑）。它與其他三條來源地位對等，不享有正本地位。
- **CoS recall 只查 CCOS 正本，不查 Hermes 的 FTS5**：Hermes 側的全文檢索留給 Hermes 自己用；CCOS 的 recall 面只有一個真相來源（§7 檢索的 Procedural + Semantic 都在 CCOS）。這與 §7 MVP「只查 Procedural + Semantic、Episodic 多半不需要」的立場一致。
- **四來源匯一正本**：互動 CoS 產出（直接寫正本）、headless CoS 產出（→inbox）、**lane 執行結果（經回傳 JSON envelope→inbox，不必碰 Hermes 記憶）**、Hermes 自己的 session（唯讀 bridge→inbox）——四者全經 consolidation（§4）併入**同一份 CCOS 正本**。§3 資料流圖的兩條既有入 inbox 路徑，在此明確擴充為涵蓋 lane envelope 這條。
- **不建反向橋（CCOS→Hermes 記憶）**：反向要嘛寫 Hermes 第三方 DB（違反第 2 節第 3／4 條「絕不直寫、絕不建第二份」）、要嘛全走留 `ended_at` 的 Hermes session（吃成本且抓不到 CoS 自己的推理），兩條都補不起來。故資料流維持單向 Hermes→CCOS，與 ARCHITECTURE.md §4.2.1 的「資料流不對稱」呼應。
