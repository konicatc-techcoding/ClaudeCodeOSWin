---
name: ai-agent-adoption-landscape-2026-07
description: AI Agent adoption landscape snapshot (2026-07-29) — mainstream use-case ranking, representative products, deployment patterns (human-in-the-loop / single-agent dominance), failure modes; produced by intelligence domain via hermes-intelligence lane (gpt-5.6-terra, non-Claude)
metadata:
  type: snapshot
---

# AI Agent 最主流用法研究報告（2026-07-29）

由 intelligence domain 產出的長期脈絡快照，與 [ai_industry_landscape_2026-07.md](ai_industry_landscape_2026-07.md)（2026-07-06 的公司／模型／監管／資金快照）互補：那份看「供給側各家公司在做什麼」，本份看「需求側 agent 實際被怎麼用」。

**來源標注（執行證據）**：本報告由 `scripts/dispatch_domain.py` 經 `hermes-intelligence` lane 實際執行產出，**非 Claude native 生成**。

- 產出日期：2026-07-29
- execution_id: `ai-agent-research-20260729T120000`
- owner: intelligence｜category: market_research｜capability: bulk_research
- lane: `hermes-intelligence`｜profile: `intelligence`
- 頂層 envelope provider/model: `hermes` / `gpt-5.6-terra`
- 實際 usage 明細 provider/model: `openai-codex` / `gpt-5.6-terra`（session_id: `20260729_181253_e0a2f7`）
- exit_status: `success`｜cost_status: `included`（訂閱內，無額外計費）
- token 用量：input 45106／output 11758／cache_read 302592／reasoning 1648／total 359456，api_calls 12

---

觀察截至：2026-07-29；主要資料涵蓋 2025 下半年至 2026 年中

## 一、執行摘要

AI Agent 已從「聊天式輔助工具」走向能呼叫工具、存取企業資料、執行多步驟工作流的系統；但「大規模、無人值守的全自動代理」仍非企業部署主流。

若以企業實際導入廣度、可衡量 ROI、產品成熟度及開發者滲透率綜合排序，最主流的用途大致為：

1. 軟體開發／Coding agent
2. 客服與客戶互動 agent
3. 企業流程自動化／RPA 升級型 agent
4. 企業知識、文件與個人生產力 agent
5. 研究、分析與商業情報 agent
6. IT 運維、資安與資料工程 agent
7. 銷售、行銷與商務營運 agent
8. 消費者個人助理與交易／任務代辦 agent

核心判斷：

- 最成熟、最常見的 agent 化場景是 coding、客服、文件／知識工作，以及既有 RPA 工作流的 AI 化。
- 企業現況多屬「人機協作、自動化部分子流程」，而非完全授權 agent 自主完成高風險任務。
- 生產環境中最常見的架構仍是單一主 agent + 工具／資料檢索／規則流程；多 agent 常見於研究、軟體開發、複雜營運編排，但在正式生產系統的比例低於市場宣傳聲量。
- 真正的限制通常不是模型能否寫出一段答案，而是：可靠性、權限治理、例外處理、長流程狀態管理、評測、成本及組織流程重設。

---

## 二、採用最廣的 AI Agent 應用類別

> 排名是「企業與專業工作採用廣度」的綜合判斷，不是全球活躍使用者的精確市占排名。公開市場並沒有一套可直接比較各類 agent 實際部署量的統一統計。

| 排名 | 類別 | 為何居前 | 典型自主程度 |
|---|---|---|---|
| 1 | Coding agent | 開發者使用頻率高、成果易驗證、工具鏈成熟、ROI 可量化 | 中：可自動寫碼／測試／PR，但通常需人審 |
| 2 | 客服／面對客戶 agent | 高量、重複性高、既有 chatbot 與 CRM 基礎深 | 低至中：常處理標準案件、複雜案件轉人工 |
| 3 | 流程自動化／RPA agent | 企業已有 BPM、RPA、ERP／CRM 整合需求 | 中：明確流程可自動化，例外需人工 |
| 4 | 知識工作與個人生產力 agent | Copilot 類工具滲透快，適用所有白領職能 | 低：多為草擬、摘要、檢索、協作輔助 |
| 5 | Research／analysis agent | 金融、顧問、法務、策略、採購、競情需求旺盛 | 低至中：蒐集與初稿可自動，結論需審核 |
| 6 | IT Ops／資安／資料工程 agent | 事件量大、需跨工具調查，且資料結構化程度較高 | 中：告警分流、診斷、修復建議；高風險操作受控 |
| 7 | 銷售／行銷／營運 agent | CRM 資料與內容工作豐富，平台廠商積極整合 | 低至中：名單、內容、跟進、報表；發送與交易受控 |
| 8 | 個人消費者任務 agent | 曝光與使用者數可大，但可靠完成率與交易授權限制多 | 低至中：規劃、資訊彙整較成熟；跨站交易較早期 |

### 1. Coding agent（最具實際生產力感受）

代表產品／框架：GitHub Copilot（含 coding agent）、Cursor、Anthropic Claude Code、OpenAI Codex / ChatGPT Codex 類工作流、Google Gemini Code Assist / Jules、Amazon Q Developer；開源／框架：Aider、Continue、Cline、OpenHands、SWE-agent、Devin 類自主開發系統。

實務限制：常見失敗包括誤解需求、修改範圍過大、測試看似通過但破壞邊界情境、依賴不存在 API、產生安全或授權漏洞。METR 於 2025 年對資深開源開發者的實驗發現，使用當時 AI 工具的受試者完成任務時間反而增加約 19%（提醒：benchmark／主觀感受不能直接代替真實生產力評估）。

### 2. 客服／customer-facing agent（部署量大，自主權通常受限）

代表產品：Salesforce Agentforce、Microsoft Copilot Studio / Dynamics 365、Google Customer Engagement Suite / Vertex AI Agent Builder、Zendesk AI、Intercom Fin、Ada、Sierra、Decagon、Cognigy、Kore.ai、Genesys Cloud AI、ServiceNow AI Agents。

最成熟的是低風險、標準化、可由知識庫回答的案件；高價值客訴、退款例外、法律／合規、醫療、金融建議、帳戶安全事件多數仍升級人工。

### 3. RPA 式流程自動化（從固定腳本走向理解非結構化資料）

代表產品：UiPath Agentic Automation、Automation Anywhere、Microsoft Power Automate + Copilot Studio、ServiceNow AI Agents、SAP Joule / SAP Business AI、Oracle AI Agents、Workday Illuminate Agents、n8n／Zapier Agents／Make／Pipedream。

模式：規則／工作流引擎定義可做範圍 → 模型處理文件理解／分類／抽取／例外判斷 → 高風險動作（付款、主檔修改、合約核准、刪除資料）設人工核准。

### 4. 企業知識／個人生產力 agent（使用最廣，但多屬 agent-assisted work）

代表產品：Microsoft 365 Copilot、Google Workspace with Gemini、ChatGPT Enterprise、Claude Enterprise、Notion AI / Notion Agent、Atlassian Rovo、Glean、Slack AI、Zoom AI Companion。

多數任務由使用者發起、輸出為草稿／摘要／建議，真正外部動作（寄信、改權限、發起付款、更新正式紀錄）受權限與確認機制限制。

### 5. Research／analysis agent（需求強，人類驗證需求也最高）

代表產品／框架：OpenAI Deep Research 類能力、Google Gemini Deep Research、Perplexity Enterprise / Deep Research、Microsoft Researcher／Copilot 研究工作流、Claude Research、Palantir AIP、Hebbia、AlphaSense/FactSet/Bloomberg/S&P Global 等垂直情報平台；框架：LangChain/LangGraph、LlamaIndex、CrewAI、AutoGen、Semantic Kernel、Haystack。

核心風險：引用很多來源不等於結論正確，容易把摘要、二手轉述、推論與事實混淆；高影響情境須保留原始來源、日期版本、證據強度、事實與推論邊界、人工 sign-off。

### 6. IT Ops／資安／資料工程 agent（高價值、受控自動化）

代表產品：Microsoft Security Copilot、CrowdStrike Charlotte AI、Palo Alto Networks Cortex/Prisma、ServiceNow ITOM/ITSM AI Agents、Datadog Bits AI、Dynatrace Davis AI、Splunk AI Assistant、Elastic AI Assistant、GitHub Copilot、Databricks Assistant、Snowflake Cortex、dbt 生態工具。

寫入／設定變更／停機／封鎖帳號／刪除資源等動作多採預先核准 runbook、最小權限、change ticket、人工批准、完整稽核軌跡。

### 7. 銷售／行銷／營運 agent

代表產品：Salesforce Agentforce、HubSpot Breeze、Microsoft Dynamics 365 Copilot、Adobe Experience Platform Agent Orchestrator、Oracle/SAP/Zoho CRM-ERP AI agent 功能、Jasper/Writer/Copy.ai/Clay。ROI 比客服和 coding 更難精確量化。

### 8. 個人助理／消費者任務／交易 agent（聲量大，成熟度不均）

代表產品：ChatGPT、Google Gemini、Microsoft Copilot、Claude、Perplexity、Apple Intelligence/Siri、Rabbit、browser-use 類工具與各種 browser agent。受限於身分驗證與付款授權、CAPTCHA 與網站介面變動、隱私與帳號權限、不可逆操作風險、任務失敗責任歸屬。

---

## 三、企業最常見的部署模式

### 1. Human-in-the-loop 是主流，非過渡期例外

三層治理模型：

| 模式 | 說明 | 常見場景 |
|---|---|---|
| 建議型／Copilot | Agent 只產生答案、草稿、摘要、建議；人決定是否採用 | 知識工作、研究、行銷、coding |
| 人工核准型 agent | Agent 可規劃與執行低風險步驟；外部／高風險動作前請人核准 | RPA、客服退款、IT Ops、採購、資料更新 |
| 受限自動化 | 在明確政策、固定工具與低風險資料範圍內自行完成任務 | FAQ 客服、案件分類、文件擷取、告警分流、例行資料同步 |

Microsoft 2025 Work Trend Index（31 國、31,000 名工作者調查）：46% 領導者表示公司正在使用 agent 全自動化某些工作流；81% 領導者預期未來 12-18 個月 agent 將中度或廣泛整合。（提醒：這是領導者自述採用狀態，不等於所有工作流已無人監督。）

### 2. 單一 agent 仍比 multi-agent 更常見於正式系統

單一主 agent + 工具／檢索／工作流是目前主流架構，優點是易追蹤測試、失敗面小、可保留傳統 workflow 確定性，適合客服、coding、個人生產力、文件處理、受控 RPA、IT 服務台。

Multi-agent（planner/executor/reviewer、researcher/verifier/writer、coder/tester/reviewer、supervisor/specialist）在研究型、複雜軟體開發、跨系統編排較常見，但成本與狀態同步複雜度更高，錯誤責任更難定位，且可能放大幻覺傳遞。

不同類別的架構傾向：

| 類別 | Human-in-the-loop 傾向 | 單一／多 agent 傾向 |
|---|---|---|
| Coding | PR、merge、production deploy 多有人審 | 單一 coding agent 為主；複雜任務才 planner/tester/reviewer |
| 客服 | 高；例外、退款、敏感案件轉人工 | 多為單 agent + CRM／知識庫／路由規則 |
| RPA／企業流程 | 高；金流、主檔、合約等需核准 | workflow 中可有多專家，但核心通常是編排器 |
| 研究／分析 | 很高；結論、引用與決策需人工 | 多 agent 較常見，但 verifier 須有獨立證據規則 |
| 個人生產力 | 使用者天然在迴路中 | 以單 agent 為主 |
| IT Ops／資安 | 高；破壞性或安全動作需核准 | 可採調查／修復角色拆分，權限隔離重要 |
| 行銷／銷售 | 中；品牌、承諾、對外發送常需核准 | 單 agent + CRM／內容工具較普遍 |

---

## 四、主要瓶頸與失敗模式

### 技術面

1. **幻覺、引用錯誤與證據鏈斷裂**——把不存在的文件、API、規則、數字講得可信；混淆版本／日期／管轄區／客戶資料；將推論寫成事實。高風險於法務、醫療、金融、投資、政策、合規、客服承諾。緩解：強制引用可點回原文、事實/推論/未知分欄、以檢索結構化資料優先、高影響結論人工覆核。
2. **長任務規劃與狀態管理失敗**——前段做對後段遺忘目標或約束，例外後重複嘗試或偏離任務，難以判定任務真正完成，長時間執行後上下文遺失、決策矛盾。是「demo 能跑」與「生產可靠執行數小時／數天」之間的主要落差。
3. **工具呼叫與 UI 操作不可靠**——API 參數/日期格式錯誤、讀錯資料表或環境、網頁改版導致 browser agent 失敗、未處理 rate limit/timeout/重試冪等性、失敗重試造成重複寫入。緩解：優先版本化 API 而非脆弱 UI automation、寫入操作用 idempotency key、重要動作包進確定性 workflow、保存 execution trace。
4. **權限過大與 prompt injection**——可讀 Email/文件/CRM/雲端硬碟/外部網站的 agent 可能被文件內惡意指令誘導、網頁內容 prompt injection、資料外洩、未授權對外發送、超範圍操作。緩解：最小權限、對外發送/付款/刪除/權限變更設人工核准、隔離不可信外部內容與系統指令、工具 allowlist 與稽核日誌。
5. **評測不足**——許多企業只測「回答看起來好不好」，未測端到端任務成功率、高風險錯誤率、升級人工率、平均處理時間與重工、成本/每成功任務、在新資料與例外情境下的穩定性。

### 組織、治理與經濟面

1. **信任與責任歸屬**——錯誤由誰負責、誰批准權限、如何稽核決策、客戶/監管機關能否接受，這解釋了為何 human-in-the-loop 在高影響流程仍是預設模式。
2. **資料品質與系統整合**——資料過時/重複/矛盾/缺欄、缺乏可安全呼叫的 API、身分權限與主資料不一致，會使 agent 導入只是把既有混亂自動化。
3. **ROI 不清、成本波動與「PoC 墓場」**——成本不只 token，還包括應用開發整合、資料治理與權限設計、評測監控人工審核、安全法遵稽核、例外處理流程重設、供應商切換成本。Gartner 2025 預測到 2027 年底超過 40% 的 agentic AI 專案可能因成本上升、商業價值不明或風險控制不足被取消（分析機構預測，非已發生實績）。
4. **人才與營運模式不足**——需要 AI product owner、流程設計與 domain expert、資料/平台工程與資安、可靠性工程與 eval、變革管理與員工培訓、界定「何時由人接手」的營運團隊。

---

## 五、對企業採用的實務建議（摘要）

1. 優先選擇高頻、窄範圍、可驗證、低不可逆風險的任務（客服 FAQ／案件分類、文件擷取、開發輔助、企業知識搜尋、IT 告警摘要、CRM 資料整理）；不宜一開始全自動化付款、法律結論、人事決策、生產環境破壞性操作、對外正式承諾、高敏感資料跨系統存取。
2. 先建立可觀測的單 agent 系統（明確目標契約、工具 allowlist、權限隔離、可重播 trace、成功率與錯誤分類、失敗轉人工、成本延遲監控），再評估是否需要 multi-agent。
3. 把人工審核設計成產品能力——只在高風險/低信心/例外/不可逆動作升級，並將審核結果回流成規則與測試案例。
4. 用「任務成功」而非「聊天品質」衡量成效：完整任務成功率、人工介入率、重大錯誤率、平均處理時間、每成功任務成本、滿意度、是否確實減少重工/backlog。

---

## 六、主要資料來源

1. Microsoft, 2025 Work Trend Index — "The Year the Frontier Firm Is Born"（31 國、31,000 名工作者調查，2025/2/6–3/24）— https://www.microsoft.com/en-us/worklab/work-trend-index/2025-the-year-the-frontier-firm-is-born
2. McKinsey, "The State of AI: How organizations are rewiring to capture value"（2025）— https://www.mckinsey.com/capabilities/quantumblack/our-insights/the-state-of-ai-how-organizations-are-rewiring-to-capture-value
3. METR, "Early-2025 AI experienced open-source developer study" — https://metr.org/blog/2025-07-10-early-2025-ai-experienced-os-dev-study/
4. Gartner, 2025 agentic AI project cancellation forecast — https://www.gartner.com/en/newsroom/press-releases/2025-06-25-gartner-predicts-over-40-percent-of-agentic-ai-projects-will-be-canceled-by-end-of-2027
5. NIST AI Risk Management Framework — https://www.nist.gov/itl/ai-risk-management-framework
6. Anthropic Economic Index — https://www.anthropic.com/economic-index

## 七、主要不確定性與研究限制

1. 沒有統一的「AI Agent」定義——有些報告把帶工具調用的 Copilot 算作 agent，有些只計可自主規劃、多步執行、長時間運作的系統，不同調查的採用率不能直接相加或比較。
2. 供應商調查通常偏向意向與自述，「公司使用 agent 自動化 workflow」未必代表企業級全面部署。
3. 產品能力迭代極快，2025 年初、年底與 2026 年中的 agent 能力和成本結構差異可能很大，尤其 coding agent 與 browser/computer-use agent 變化最快。
4. 公開數據對「真正生產成效」不足，較容易取得試點/採用意圖/使用者數與案例宣傳，較少有可獨立驗證的長期 ROI、事故率、人工介入率與總持有成本資料。
5. 本報告的採用排序屬綜合判斷（平台成熟度、企業案例密度、工作流適配度、公開調查訊號），不應視為精確市場市占表。
