---
name: hermes-task-category-model-routing-preference
description: 「依任務類型/範疇自動選模型/lane」規則引擎的完整脈絡包——需求由來、現況機制實際值（agents/model_router/capability_lanes/route_model/dispatch_domain）、18 條既有模型選擇決策（事實上已是規則）、額度成本約束寫在哪裡與是否機器可讀、脈絡缺口。前置條件「Phase 2f」經 2026-09-03 查證指向一個不存在的里程碑，不要照著它繼續等
metadata:
  type: project
---

STATUS.md 掛最久的待拍板項之一。本檔是這個主題的**單一脈絡正本**：起草規則引擎的人讀這一份就夠，不必再去翻 registry、scripts 與其他記憶檔。2026-09-03 由 `knowledge` 彙整（讀檔查證）＋ `engineering` 唯讀查證（Phase 2f 一節）大幅擴充。

## 0. 需求由來與最有價值的四個判斷

使用者 2026-07-20 明確標記：他很在意「依任務的類型或範疇，規範該用哪個模型」，希望在適合開發的時機被提醒（不是當時就要做）。

起草時**必須保留**的四個判斷（2026-09-03 彙整產出，比事實清單本身更重要）：

1. **既有決策已經是「事實上的規則」。** 下方第 3 節那 18 條決策不是背景資料，而是這套規則引擎的實質內容——草案應該是把它們**形式化**，不是另起爐灶重新發明一套判準。
2. **真正「按任務性質選模型」的先例只有一條：D8。** 2026-07-22 晨報 job pin 回 `gpt-5.6-terra`，理由是「該 job 只做搜尋、不做分析，沿用舊模型即可」。這是全部記憶裡唯一一次以任務性質（搜尋 vs 分析）而非 domain 歸屬來選模型。**記憶檔沒說它可不可推廣，因此不應假設可推廣**——它可能只是一次個案判斷。
3. **D18「不強制路由」與「自動選模型引擎」有直接張力。** 2026-07-23 已拍板 Hermes 是 opt-in 執行後端、lane **不強制路由**（見 [[project_cos-hermes-division-of-labor]]）。一個「自動選模型」的引擎若設計成強制，會牴觸這條既有拍板。**規則是建議還是強制，尚未拍板**，草案必須明確處理，不能預設。
4. **額度／時段／exhausted 判讀／配額耗盡降級，全部沒有機器可讀形式。** 配額耗盡的「既有處理方式」實質上就是**使用者當場手改 `config.yaml`**（見 D13）。規則引擎讀不到這些約束，這直接限制它能自動化到什麼程度。

## 1. 前置條件的真相：「Phase 2f」指向一個不存在的里程碑

> ⚠️ **本節推翻本檔舊版寫的前置條件。看到「等 Phase 2f 完工」的人請先讀完這一節，不要繼續等。**

`engineering` 2026-09-03 唯讀查證結論：

- **「Phase 2f」在專案裡沒有權威定義。** 它是 2026-07-20/21 一次長 session 事後貼上的臨時編號，**在三份文件裡指三件不同的事**：
  - `registry/capability_lanes.yaml:298` → financialresearch profile 的憑證清理（**已完工 07-20**）
  - `docs/hermes-integration-roadmap.md:659`（併寫成「Phase 2e/2f」）→ 四條 lane 轉 active、補 `.claude/agents/*.md` 說明、ARCHITECTURE.md 加 5.1 節（**已完工 07-20**）
  - 本檔舊版引用的、使用者自己的括號註解 →「讓 subagent **真正呼叫** `dispatch_domain.py`」（**未完工**）
  - 使用者寫前置條件時用的是第三種意思，但那看起來是**把第二種（補 agent 說明）誤記成了「真正呼叫」**。
- **`Phase 2c` 這個編號全 repo 零命中**；phase 編號從未出現在任何 commit message，只活在檔案註解與 roadmap 內文。是**流水帳式編號，不是規劃里程碑**。
- **前置條件後半（「四條 lane 有一段時間真實使用經驗」）明確不成立。** `logs/dispatch_domain/` 共 **14 筆**執行記錄：07-20 四筆 Phase 2d smoke test、07-21 五筆 financialresearch/gptcoding 調試（3 敗：timeout 300s／empty_output／registry_error）、07-29 兩筆前台 e2e、08-02 一筆、08-03 兩筆 A1 capture test。成功率 **11/14**。**最後一筆 2026-08-03，至今整整一個月零執行。14 筆全部帶明確 `--lane` override，`lane_override=None` 的自動選路徑一次都沒被走過。全部是驗證／測試性質，沒有任何一筆是自然發生的日常任務。**
- **真正的死結是循環依賴**：「工具存在但沒人用」從來沒被排進任何 phase。要累積使用經驗，得先有「什麼情況該走 lane」的判準——而那正是規則引擎本身。**等使用經驗再做規則引擎，是等不到的。**
- `scripts/dispatch_domain.py` 檔頭 docstring 措辭過時：「接線是 Phase 2」（2a–2h 全做完卻沒有一個 phase 真的做接線）、第 2 行「v0.1（Phase 1）」標籤從未更新。但「**不被 CoS／worker.py 呼叫**」那句**今天依然字面為真**——與 STATUS.md「非 Claude lane 端到端驗證有效」不矛盾，兩者描述不同層次（能不能跑 vs 有沒有被自動接線）。
- 全 repo grep 確認：`dispatch_domain.py` **無任何自動呼叫點**，只有 `.claude/agents/engineering.md:20-29`、`.claude/agents/intelligence.md:20-23`、`delegation_policy.md:71` 的 prompt 層文字說明，以及 `scripts/test_dispatch_domain.py` 的 unit test。

## 2. 現況機制的實際值（2026-09-03 讀檔查證）

### 2.1 五個 domain 的 `default_capability`（`registry/agents.yaml`）

| domain | status | triggers | depends_on | default_capability |
|---|---|---|---|---|
| intelligence | active | manual, rss | — | `claude_native` |
| engineering | active | manual | — | `claude_native` |
| automation | active | cron, webhook, manual | — | `claude_native` |
| knowledge | active | manual, cron | — | `claude_native` |
| planning | active | manual | `[knowledge]` | `claude_native` |

**五個全部都是 `claude_native`。** 檔頭註記：這是「唯一的真相來源——subagent 檔案本身不寫死 capability 名稱」，且「只是預設，任務內容明顯不適合預設能力時，subagent 可以換成別的 capability」。

**核心落差**：`default_capability` 只到 domain 層級，無法表達「同一個 domain 底下，A 類任務用 Hermes lane、B 類任務用 Claude native」。

### 2.2 `registry/model_router.yaml`

`default: claude`；`routes` 共 **4 條，全部 `via: native`、`model: claude`**：

| capability key | via | 備註 |
|---|---|---|
| `architecture_reasoning` | native | 命名保留給工程/CoS 情境 |
| `claude_native` | native | 給五個 domain 用 |
| `complex_coding` | native | 原走 OpenRouter GPT-5.5，07-20 移除。**保留 key 只因兩條 active lane 引用它做 capability 標記**，無 domain 指向它 |
| `bulk_research` | native | 原走 OpenRouter Nemotron 免費層，同日移除。保留 key 同上理由 |

- `google_ecosystem` 已整個刪除。
- **這一層沒有 fallback 機制**（無 fallback 欄位）。fallback 只存在於 `capability_lanes.yaml` 的選填 `fallback_lane`，而目前**沒有任何 lane 填了它**。

### 2.3 `scripts/route_model.py`

- 介面：`route_model.py <capability> <prompt-file|->`，恰好 2 個位置參數。需專案 `.venv`（PyYAML）。
- **支援的 capability 不是白名單**：`resolve_route()` 若 capability 不在 routes 裡，**靜默回退** `{"model": default, "via": "native"}`——打錯名字不會報錯。
- prompt 路徑邊界：必須在專案根目錄內（`Path.relative_to(ROOT)`），否則 `sys.exit`（防讀 SSH key/.env）。
- `via == native` → 只 print 一行提示、exit 0。**它不真的呼叫任何模型。**
- `via != native` → `sys.exit` 明確報錯，訊息含「**不要假設有 fallback**」。

### 2.4 `scripts/dispatch_domain.py` 的角色與分工

`delegation_policy.md` 結尾寫：「`engineering` subagent 內部才會視情況呼叫 `route_model.py <capability>`，或（任務明顯不適合預設能力時）改用 `dispatch_domain.py` 選路到 Hermes lane」。**程式碼層面的實況**：

- 分工：`route_model.py` = capability→model 解析器（現況只會回 native）；`dispatch_domain.py` = **Domain Execution Router**（選通道＋執行＋回傳穩定 JSON envelope）。後者匯入前者重用路徑邊界檢查，且 `execution=native|route_model` 的 lane 是**再 subprocess 呼叫 route_model.py**。
- 參數：必填 `--owner --category --prompt-file --execution-id`；選填 `--capability`（覆蓋 default_capability）、`--lane`、`--timeout`（預設 600s）、`--hermes-bin`、`--log-dir`。
- 選路：capability = `--capability` or agent 的 `default_capability`；lane = `--lane`，否則自動從「capability 相符＋`status: active`＋owner 在 `allowed_agents`」的候選取**第一條**（`candidates[0]`，順序＝YAML 檔案順序）。
- **關鍵事實（規則引擎的核心缺口）**：`--category` **只被原樣寫進 JSON envelope 與 log，完全不參與 lane 選擇**。argparse help 原文即「原樣記錄，不重新驗證」。
- 因此「任務明顯不適合預設能力時改走 Hermes lane」＝**人／subagent 自己判斷後手動傳 `--capability complex_coding --lane hermes-gptcoding`**。沒有任何自動判斷。
- fallback **程式碼存在**（主 lane 非 success 且有 `fallback_lane` → 執行 fallback，記 `fallback_success`/`fallback_failed`），但 registry 零使用，**目前是死路徑**。
- exit_status 詞彙表（若要做重試/降級，這是既有失敗分類）：`success | fallback_success | fallback_failed | timeout | profile_not_found | hermes_not_found | empty_output | bad_usage_json | nonzero_exit | prompt_too_long | isolation_error | wslpath_error | registry_error | prompt_path_error`。
- 硬限制：`MAX_HERMES_PROMPT_CHARS = 20_000`（hermes `-z` 的 prompt 直接進 argv；Windows 命令列上限約 32,767）。
- Hermes 執行檔解析（2026-07-29 憑證單一存放拍板）：`--hermes-bin` > WSL 內走凍結常數 `WINDOWS_HERMES_INTEROP_PATH`（`/mnt/c/Users/razer/AppData/Local/hermes/hermes-agent/venv/Scripts/hermes.exe`）> PATH > `~/.local/bin/hermes`。**WSL 側 hermes 零 profile**，落到它一律 `profile_not_found`。

### 2.5 目前實際存在的 lane（`registry/capability_lanes.yaml`，schema `claudecodeos.capability_lanes.v1`）

共 **6 條，全部 `status: active`**：

| lane id | capability | execution | provider | model 欄 | hermes_profile | cost_tier | risk_tier | allowed_agents |
|---|---|---|---|---|---|---|---|---|
| `claude-native` | claude_native | native | anthropic | null | — | included | low | automation, knowledge, planning, engineering, intelligence |
| `claude-architecture-reasoning` | architecture_reasoning | native | anthropic | null | — | included | low | engineering |
| `hermes-nemocoding` | complex_coding | hermes_profile | hermes | null | nemocoding | free | medium | engineering |
| `hermes-gptcoding` | complex_coding | hermes_profile | hermes | null | gptcoding | included | medium | engineering |
| `hermes-financialresearch` | bulk_research | hermes_profile | hermes | null | financialresearch | included | **high** | intelligence |
| `hermes-intelligence` | bulk_research | hermes_profile | hermes | null | intelligence | included | medium | intelligence |

- **`model` 欄全部為 `null` 且是刻意的**：「模型與工具設定由該 profile 自己的 config 決定，本檔不重複記載、不宣稱」。實際生效模型只能讀 `%LOCALAPPDATA%\hermes\profiles\<name>\config.yaml`。
- 既有的 `intended_use`（規則形式化時可直接引用）：`claude-native` ＝本來就該 Claude 原生處理的領域工作；`claude-architecture-reasoning` ＝工程/CoS 架構推理（**guardrail：automation/knowledge/planning 不得借用**）；`hermes-nemocoding`／`hermes-gptcoding` ＝ Hermes 側 coding profile；`hermes-financialresearch` ＝財務研究，**「產出常含財務個資訊號，落地判斷從嚴」，headless 匯入一律 fail-closed，互動式才可遮罩後匯入**；`hermes-intelligence` ＝情報蒐集。
- 共通 guardrails：對 Hermes 一律唯讀不寫入；session 內容進記憶一律走 `memory/inbox/`；**自動化呼叫 Hermes CLI 一律明確 `--profile`**。
- `hermes-codereviewer` 已於 2026-07-20 Phase 2a 移除，使用者拍板不留。

### 2.6 OpenRouter 移除現況（別誤列，也別漏看）

**作為「ClaudeCodeOS 直連通道」已完全移除**（`route_model.py` 的 `call_openrouter`、三條 route、三條 `openrouter-*` lane）。**但**：

1. **`nemocoding` Hermes profile 本身仍是 openrouter provider**（`nvidia/nemotron-3-ultra-550b-a55b:free`）——走 `hermes-nemocoding` lane 實際打的就是 OpenRouter。移除的是直連，不是 Hermes 內部的 OpenRouter。`OPENROUTER_API_KEY` 被記為「nemocoding 必需、**不可移除**」。
2. `dispatch_domain.py:validate_lane()` 對 `execution=route_model` 仍檢查 `via == "openrouter"`（死驗證，見第 5 節陷阱）。
3. 函式名仍叫 `execute_native_or_openrouter()`。
4. 字串殘留於：`ARCHITECTURE.md`、`dashboard/data_stage3.py`、`docs/capability-lanes.md`、`docs/hermes-integration-roadmap.md`、`INTEGRATION_TEST.md`、`WINDOWS_WSL_SETUP.md`、`scripts/test_*.py`、[[hermes-tavily-key-plaintext-todo]]、[[project_v0_1_status]]。

### 2.7 既有 category 詞彙表（`registry/delegation_policy.yaml`）

- `direct_categories`：`meta_system_question`、`clarification`、`synthesis`、`orientation_read`、`general_conversation`、`local_diagnosis`
- `delegated_categories`：`code_change`／`code_review`／`debugging_testing` → engineering、`external_research` → intelligence、`knowledge_management` → knowledge、`planning_prioritization` → planning、`recurring_workflow` → automation
- `delegation_policy.md` 明文區隔兩軸：「**delegation policy 決定『哪個領域負責』，model router 決定『該領域內部呼叫哪個模型』。兩者不互相取代**」。

## 3. 既有的模型選擇決策（「什麼情況 → 選了什麼 → 為什麼」）

**這 18 條就是事實上已在運作的規則。草案該把它們形式化。**

| # | 情況 | 選了什麼 | 為什麼 | 日期 | 來源 |
|---|---|---|---|---|---|
| D1 | ClaudeCodeOS 直連外部 provider | 全改 `claude_native`，移除 OpenRouter 路徑 | `OPENROUTER_API_KEY` 自建成從未真正設定，三條路徑從未打通 | 07-20 | `model_router.yaml` 檔頭 |
| D2 | `complex_coding`／`bulk_research` key 要不要一起刪 | **保留 key、拔掉 OpenRouter 專屬欄位** | 四條 active hermes lane 仍引用它們；刪掉會讓 `validate_lane()` 當場失敗，牴觸「不動 hermes lane」的範圍界線 | 07-20 | 同上 |
| D3 | `google_ecosystem` | **整個 key 刪除** | 無 lane 引用、無 domain 指向，零殘留參照 | 07-20 | 同上 |
| D4 | 架構推理 vs 一般領域工作（同為 native，行為相同） | 刻意分成兩個 capability 名稱 | 命名邊界：`architecture_reasoning` 保留給工程/CoS，**不借給其他領域** | — | `model_router.yaml` + lane guardrail |
| D5 | Hermes lane 憑證放哪 | **單一存放於 Windows 側**，WSL 經 interop 呼叫 `hermes.exe` | WSL 側 hermes 零 profile，帶 `--profile` 一律失敗 | 07-29 | `dispatch_domain.py` docstring |
| D6 | intelligence 的真實情報任務（headless/Telegram 入口） | `hermes-intelligence` lane → **gpt-5.6-terra**（非 Claude） | 首次端到端驗證非 Claude 模型任務可行；產出已整併進正本 | 07-29 | STATUS.md、[[ai_agent_adoption_landscape_2026-07]] |
| D7 | Hermes 原生 cron 的 **agent job** 建立時 | **一律 pin `provider`+`model` 兩軸** | unpinned job 跟隨當下全域 default；漂移時花費保護 fail-closed（RuntimeError、完全不發 inference），**硬行為、不可關閉、不可降級成 warn** | 07-22 | [[hermes-cron-model-pin-convention]] |
| **D8** | 已壞掉的晨報 job 要 pin 到哪個模型 | **pin 回舊模型 gpt-5.6-terra**（不跟進新的 gpt-5.6-sol） | 「該 job **只做搜尋、不做分析**，沿用舊模型即可」——**現存唯一一條真正按任務性質選模型的先例；是否可推廣，記憶檔沒說，不應假設** | 07-22 | 同上 |
| D9 | 純 script job（`no_agent=True`，如 garmin/aichain） | **不需要 pin** | 不做 inference，不受花費保護 guard 影響 | — | 同上 |
| D10 | 臨時用某個 profile 跑指令 | `hermes -p <name>`（per-command），**不要** `profile use` | 後者持久 sticky、無 TTL、無自動還原 | 07-22 | [[hermes-profile-sticky-vs-ephemeral]] |
| D11 | 建立「會被實際執行」的 cron job 前 | 先 `hermes profile use default` 再 create，建完 `cron status`+`cron list` 驗證 | 只有 default(global root) 有 running ticker；named profile 無自己的 ticker → job 保證孤兒、永不觸發、**且不報錯** | — | [[hermes-cron-store-binding-gateway-alignment]] |
| D12 | 五個 Hermes profile 的應然 provider+model | default→openai-codex/gpt-5.6-sol；financialresearch→openai-codex/gpt-5.6-luna；gptcoding→openai-codex/gpt-5.6-sol；intelligence→openai-codex/gpt-5.6-terra；nemocoding→openrouter/nemotron:free | 使用者拍板，稽核/漂移判斷一律以此表為準 | 07-23 拍板，08-15 確認不變 | auto-memory `hermes-profile-intended-config` |
| D13 | codex 配額耗盡（429 `usage_limit_reached`, plus） | 全域 default **暫改** openrouter / `deepseek/deepseek-v4-flash-0731` | 配額耗盡的應急降級——**由使用者當場手改 config.yaml，沒有寫成任何規則** | 08-05 | 同上 |
| D14 | 上述暫改要不要留 | **改回** openai-codex / gpt-5.6-sol | 使用者拍板；備份 `config.yaml.bak.20260815_182728` | 08-15 | 同上 |
| D15 | AIChain 每日分析要不要從 `anthropic_api` 改走 `claude_cli`（訂閱制） | **改**（09-01 階段 1 已上線） | **動機是品質不是省錢**；08-27 那份以省錢為前提、結論「不建議改」的評估**明確不適用** | 記於 08-27，09-01 上線 | [[project_aichain-claude-cli-provider-trial]] |
| D16 | AIChain 要不要改走 Hermes lane（路線 B） | **已排除** | prompt 當 argv 傳，121KB 是 Windows argv 上限的 3.7 倍；可用 lane 都是 OpenAI Codex 非 Claude、schema 通過率未知、該額度 08-05 曾耗盡 | 08-27 | 同上 |
| D17 | 品質不佳的 web search 調研 | 改用 ChatGPT 排程調研 + Claude Code 分析 | 當日實例：管線只抓到三則媒體轉載，ChatGPT 抓到 NVIDIA/聯發科官方聯合發布本身 | 09-01 | STATUS.md |
| **D18** | 記憶正本歸屬／lane 要不要強制路由 | 記憶正本+recall ＝ ClaudeCodeOS；**Hermes ＝ opt-in 執行後端**；**不強制路由**；不建反向橋 | 乙案拍板——**與「自動選模型引擎」有直接張力，見第 0 節第 3 點** | 07-23 | [[project_cos-hermes-division-of-labor]] |

**其他相關條目**（非決策但起草時需納入）：[[hermes-credential-handling-safety-lessons]]（四類憑證事故）、[[hermes-tavily-key-plaintext-todo]]（gptcoding config.yaml 內嵌明碼 Tavily key）、[[project_v0_1_status]]（model routing「完成」只到 capability 層）、[[project_live-translate-vmix-caption-design]]（07-30 一次「設計討論類任務走 Hermes gptcoding」的實際樣本）。另：各 `.claude/agents/*.md` 都寫「任務內容明顯不適合預設能力時可以換 capability」，但**「明顯不適合」從未定義**——那正是規則引擎要填的空缺。

## 4. 額度與成本約束：**寫在哪裡、規則引擎讀不讀得到**

| 約束 | 寫在哪裡 | 機器可讀？ |
|---|---|---|
| lane 成本/風險分級 `cost_tier`（included/free/paid/unknown）、`risk_tier` | `registry/capability_lanes.yaml` 每條 lane | **是**（唯一機器可讀的成本欄位） |
| 實際成本訊號 `usage.cost_status`（included/estimated） | `logs/dispatch_domain/<id>.usage.json` + JSON envelope | **是**，但**執行後才有**，不是決策前輸入 |
| cron 刪 API key：`os.environ.copy()` 只刪不重建（`USERPROFILE` 保留、`ANTHROPIC_API_KEY` 被刪）→「保證走訂閱不可能誤用 API 計費」 | Hermes cron 執行程式碼（`%LOCALAPPDATA%\hermes\hermes-agent`，**版控外**）；本 repo 只有 STATUS.md 散文 | **否** |
| 訂閱額度競爭：「故障模式從『API 回錯誤』變成『訂閱額度不足』」「**08:00 前後大量使用 Claude Code 會跟晨報搶額度**」 | **只在 STATUS.md 散文裡** | **否——純人腦/文件** |
| AIChain provider 選擇（`provider: claude_cli`、絕對路徑 command、`--add-dir`、timeout 1200；回滾＝改回 `anthropic_api`，一行、立即生效不必重啟） | `AIChainClaude\00_CONFIG\claude_provider.yaml`（**版控外**） | 是，但在外部專案 |
| `gptcoding` 憑證 `last_status: exhausted`（last_refresh 08-02） | Hermes `auth.json`（外部）；**判讀規則**「該欄只在 lane 被實際呼叫時更新，不代表配額仍耗盡，下次成功即自癒 → 既有處理方式是不動作、只是別誤讀」寫在 STATUS.md 與 auto-memory | 欄位可讀，**判讀規則只存在於文件/人腦** |
| 配額耗盡的應急處理程序（改全域 config.yaml 的 `model:` 區塊；**不用重啟**，`_load_gateway_config()` mtime-keyed 快取；確認生效要等下輪整點 cron 或開**新** Telegram thread——舊 thread 24h 內 resume 舊 session） | auto-memory `hermes-profile-intended-config` | **否**（程序在記憶檔） |
| Tavily 雙 key 未輪替（兩把 1000/月，code 只讀一把，未確認是否同帳號共用額度池） | AIChain 蒐集層（外部）+ STATUS.md | **否**（是缺陷不是機制） |
| 憑證頁四燈語意：**橙 > 黃 > 綠，gray 自成一類**。gray ＝生效 provider 查不到／`auth.json` 不存在或壞掉 → 無從比對，**即使有 exhausted 條目也維持 gray**；橙＝生效 provider 在此 store 無憑證條目（靠環境變數在跑），**已是橙者不因 exhausted 降為黃**；黃＝本來判綠但本 store 有 exhausted 條目（08-05 拍板，理由是「綠燈 + 紅色 exhausted 條目」的張力會讓人漏看）；綠＝有條目且無 exhausted。另輸出 `exhausted_entry_count` | `dashboard/data_stage3.py` 約 324-370 行 | **是** |
| 兩軸判定：「**憑證 provider**」（auth.json）vs「**生效模型 provider**」（config.yaml）——**欄位撞名正是誤解成因**，08-04/05 才正名。`_effective_model_fields()` 是唯一判定處：profile 有 `model.default` → source `profile`；否則 fallback 全域 → `global`；都查不到 → `unknown` + `UNKNOWN_MODEL_TEXT`（fail-soft）。`(global-root)` 傳 `profile_cfg=None`，一律落 `global`（語意正確非 fallback）。native lane → `"(native session)"` | `dashboard/data_stage3.py:168`（`_effective_model_fields`）、`:196`（`_annotate_effective_models`） | **是** |
| 黃燈已知不精準：**只看「整個 store 有無 exhausted 條目」，不區分耗盡的是否為生效 provider 的條目**（偏保守、寧可誤黃）。六個 store 皆單一 provider 不觸發；要更精準**需再拍板** | STATUS.md「憑證頁殘留邊界 (b)」 | 知情接受的缺陷 |

**綜合**：規則引擎目前**能讀到**的只有 `cost_tier`/`risk_tier`（靜態標籤）、`_effective_model_fields()`/四燈（唯讀觀測，且在 dashboard 而非 registry）、執行後的 `usage.cost_status`。**額度競爭、時段衝突、exhausted 判讀、換模型程序，全部無機器可讀形式。**

## 5. 三個現存陷阱（起草時必須處理）

1. **未知 capability 的行為不一致**：`route_model.py` **靜默回退 native**（打錯名字不報錯），`dispatch_domain.py:resolve_capability()` **明確報 `registry_error`**。同一件事兩種行為，取決於走哪個入口。
2. **`validate_lane()` 的死驗證**：對 `execution=route_model` 仍要求 `route.via == "openrouter"` 且 `lane.model == route.openrouter_model`，但所有 route 都已是 native。目前無害（零條 `execution: route_model` 的 lane），但**若規則引擎新增這類 lane（例如未來接回某個外部 provider），會當場撞上**。
3. **`cost_tier` 不隨 profile config 漂移同步**：`hermes-financialresearch` 的 `free` 判定曾因 profile provider 由 openrouter 改成 openai-codex 而**失效並改記 included**。registry 不會自動同步，**`cost_tier` 的新鮮度不可信任為即時值**。

## 6. 脈絡缺口（起草時必須當未知數，不可假設）

**查不到的**

1. ~~Phase 2f 是否完成~~ → **已由第 1 節解答：這個里程碑不存在。**
2. ~~四條 hermes lane 的真實使用經驗~~ → **已由第 1 節解答：14 筆全為驗證/測試，08-03 後零執行，自動選路徑從未被走過。**
3. `hermes-financialresearch` 與 `hermes-nemocoding` 是否曾在真實（非測試）任務被選用——找不到記錄。
4. `gptcoding` 憑證 exhausted 是否已自癒——記載停在 08-15。
5. **Claude 訂閱額度的實際數字**（每期上限、重置時間、與晨報的重疊量）——只有定性敘述，零量化。
6. AIChain 09-02 08:00 第一次「cron + claude_cli」的驗收結果（STATUS.md 標「尚未驗證」；log 在版控外的 `AIChainOrchestrator\logs\`）。這直接影響 D15 是否穩定成立。
7. **各 Hermes profile 的 tool 設定與能力差異**——registry 刻意不記載，config 在 `%LOCALAPPDATA%` 外部。「哪條 lane 擅長什麼」除了 profile 名字外沒有結構化依據。

**互相不一致的**

8. 第 5 節三個陷阱（未知 capability 行為不一致、死驗證、cost_tier 漂移）。
9. **`fallback_lane`：有能力但無政策**——程式碼完整實作 fallback 執行與狀態碼，schema 也定義了欄位，registry 卻 0 使用。要啟用還是視為不存在，需拍板。

**只存在於人的記憶／散文的**

10. **「任務內容明顯不適合預設能力時可以換 capability」的判準從未定義**——這正是要填的空缺，也意味著**除了 D8 一例外，沒有既有樣本可供校準**。
11. 「08:00 前後別大量用 Claude Code」只是一句話，無任何機制執行。
12. exhausted 的正確判讀方式（「不代表仍耗盡」）只在文件裡——直接讀 `auth.json` 的引擎會誤判。
13. **配額耗盡時該降級到哪條 lane**：08-05 那次是使用者手動換全域 default 到 deepseek，沒寫成規則，也沒寫進 `fallback_lane`。
14. **規則正本該放哪個檔案**——使用者當初列為待答核心問題之一，至今無答案。
15. ~~`memory/inbox/` 兩份未整併檔案可能含模型選擇事實~~ → **已解（2026-09-03 整併完成，inbox 清空）：兩份都不含模型選擇事實，本脈絡包無缺口。** 逐檔查證結果：(a) `20260731_hermes-skill-catalog-update.md` 是 Hermes 側技能目錄異動通知，與 lane／模型路由無關（併入 [[reference_hermes_skill_catalog]]）；(b) `hermes_session_20260716_*.md`（34KB、67 則訊息）主題是「AI news cron job 的 Slack 投遞為何沒送達」，全文 **`lane`／`capability`／`dispatch_domain` 零命中**，唯一與模型沾邊的是一張 07-16 的 profile→模型快照（default=`gpt-5.6-terra`、codereviewer=`gpt-5.6-sol`、financialresearch=nemotron、gptcoding=gpt-5.6-terra），且已被第 3 節既有決策表與 auto-memory 的應然配置表涵蓋、更新（併入 [[hermes-cron-store-binding-gateway-alignment]] 的 `[SILENT]` 排查層）。**結論本身有用：即使是使用者親自操作 Hermes 的長 session，也沒有留下任何「按任務性質選模型／選 lane」的痕跡——這與第 1 節「自動選路徑從未被走過」互相印證，缺口 10（判準無既有樣本）依然成立。**

## 7. How to apply（重提與起草時怎麼接手）

- **前置條件已作廢**：不要再等「Phase 2f」。第 1 節證明它指向一個不存在的里程碑，且「累積使用經驗」與「規則引擎」互為循環依賴——**等經驗是等不到的**。是否現在動工，改由使用者直接拍板。
- 走 `planning` domain 起草設計提案（比照本專案 Stage 2.5/2.6/2.7 的既有慣例）。使用者當初列的三個核心問題仍然有效：
  1. 規則顆粒度要到多細（domain / category / 單一任務關鍵字）
  2. 規則正本放哪個 registry 檔案
  3. 要不要跟 `delegation_policy.yaml` 的任務分類共用同一套 category 定義
  另補一題（2026-09-03）：**規則是建議還是強制**（D18 張力，見第 0 節）。
- 起草的實質工作是**把第 3 節那 18 條形式化**，不是另起爐灶。
- 使用者原始警告仍適用：**不要在其他不相關任務裡順便夾帶這個功能，範圍會失控。**

## 關聯

- [[project_cos-hermes-division-of-labor]] — D18「不強制路由」拍板，與本主題有直接張力
- [[hermes-cron-model-pin-convention]] — D7/D8/D9；Hermes 原生 cron 的模型 pin 與花費保護（與 CoS/domain 側的 lane 決策是不同層次，別混談）
- [[hermes-cron-store-binding-gateway-alignment]] — D11；cron job 的 store 歸屬/gateway 對齊
- [[hermes-profile-sticky-vs-ephemeral]] — D10；`-p` per-command vs `profile use` sticky
- [[project_aichain-claude-cli-provider-trial]] — D15/D16；本主題的一個具體個案
- [[hermes-credential-handling-safety-lessons]]、[[hermes-tavily-key-plaintext-todo]]、[[project_v0_1_status]]、[[reference_hermes_workspace]]
