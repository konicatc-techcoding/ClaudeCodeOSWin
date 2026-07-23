---
name: project-agentos-console-phasing
description: 使用者拍板照此推進的「AgentOS Dashboard」大願景分期方向——從 Stage 3 被動唯讀儀表板到主動互動控制台的類別轉變、兩 app 並存架構、已定案分期骨架與明確取捨；2026-07-22 更新：對話入口最終改用現成互動式 `claude` CLI（見 [[project_cos-conversation-entry-point]]），自建 Console 對話頁（路 A 薄殼／SDK 常駐宿主）一併擱置，Stage 3 唯讀監控軌不變
metadata:
  type: project
---

來源：2026-07-22 互動式前台 CoS session（使用者提出「AgentOS Dashboard」三區式管理後台大願景，經 `planning` 評估後拍板此分期方向）。與既有 Stage 3 dashboard 設計提案文件直接相關：`docs/stage3-dashboard-observability-proposal.md`（唯讀 Streamlit 觀測性擴充，v2 已可開工；文件不在 memory，但本則多處引用其唯讀鐵律與功能編號）。

> **2026-07-22 校準（對話入口最終定論，優先於本檔舊敘述）**：**對話入口改用現成互動式 `claude` CLI（在 repo root 開，本身就是 CoS）＋ 語音用 Windows Win+H**，不自建 web 對話頁。**先前「AgentOS Console 對話頁 MVP 走路 A 薄殼包 headless」的方向已被推翻並擱置**；路 B（SDK 常駐宿主）與全套重功能更往後擱置，只在「CLI 撞到具體缺口且使用者確實需要」時才重啟。連帶「headless `--resume` 撐 web 多輪對話」spike **不再是待辦**（被 CLI 決策取代，非還要做）。**Stage 3 唯讀監控／三區外殼／iframe 那條軌不受影響，照原計畫。** 完整決策見 [[project_cos-conversation-entry-point]]。以下各段中凡涉及「對話頁 MVP／路 A／路 B／對應 spike」者，一律以本校準為準。

使用者的願景輪廓：一個三區式管理後台——**topbar 全域狀態**＋**左側 sidebar 導覽**（對話頁／監控頁／Hermes Dashboard 頁）＋**主內容區**。

## 定性判斷（關鍵，決定架構走向）

1. **這是一次「類別轉變」，不是 Stage 3 的放大版**：從 Stage 3 的**被動唯讀儀表板** → **主動互動控制台**（主操作入口）。因此它是**獨立於 Stage 3 的、更大的新階段**，暫稱「AgentOS Console」。

2. **不打破 Stage 3 的唯讀鐵律去合併**：Stage 3 的唯讀監控原封成為本願景「監控頁面」的內容。正確架構是**兩個 app 並存**——唯讀 Streamlit 監控 ＋ 若要做的獨立互動 console（讀寫、獨立後端，**絕不共用 `dashboard/data.py`**）。

3. **監控頁與 Stage 3 高度重疊，別重造**：願景監控頁的
   - (b)「所有 profile 掛載的模型」≈ Stage 3 功能二（憑證／Capability Lane 唯讀狀態）；
   - (c)「cron 數量／名稱／內容」≈ Stage 3 功能三（統一排程健康表）；
   - (a)「CoS／token 用量」部分靠既有成本 tab ＋ 第二批 token 統計（見下方 Stage 3.6）。

4. **核心工程缺口——（自建）願景對話頁隱含「第三種 CoS」**：一個由 Claude Agent SDK 撐起來的**常駐、可從 web 驅動、多輪、可串流、可觀測 subagent、可攔授權的 CoS session 宿主**。現有只有 (a) Desktop 互動 CoS（無可程式化 IPC，web 驅動不了）與 (b) headless 一次性 `claude -p`，**兩者都不是**這個宿主。
   - **2026-07-22 校準**：這一整段的前提是「要自建 web 對話頁」。既然對話入口改用現成互動式 CLI（[[project_cos-conversation-entry-point]]），**這個「第三種 CoS 宿主」缺口在現行方向下不需要被填**——現成 CLI 已原生提供串流／互動授權／多輪／subagent 顯示。本點僅在未來「CLI 撞到具體缺口、要重啟自建 console」時才重新相關。

## 分期骨架（穩定，已拍板照此推進）

- **Stage 3（已規劃，唯讀）**：功能二／三／一 → 直接變願景的「監控頁面」，零額外成本。細節見 `docs/stage3-dashboard-observability-proposal.md`（v2 已可開工）。
- **Stage 3.5（便宜、CP 值最高）**：用 Streamlit 把三區外殼（topbar 全域狀態＋sidebar 導覽＋main 區）佈局起來，Stage 3 唯讀視圖放進監控頁。topbar 全域狀態多可從既有資料推導（systemctl 狀態、jobs.db running 數、最後同步時間）。約 Stage 3 成本拿到 80% 的「管理後台體感」。
- **Stage 3.6（第二批，中等）**：token／用量統計（需 jobs.db schema 擴充 ＋ dispatch usage envelope 落地）。對應 Stage 3 提案 §0.4 明確排除的「第二批」。
- **Hermes dashboard iframe 嵌入**：需 engineering 小 spike 把關（port 穩定性、X-Frame-Options／CSP framing 限制）。
- **~~AgentOS Console（互動對話頁）~~ → 擱置（被現成 CLI 取代）**：先前設想的獨立正規前端（React／Svelte 之類）＋ Agent SDK 常駐宿主。**2026-07-22 起，日常對話入口改用現成互動式 `claude` CLI（[[project_cos-conversation-entry-point]]），此「自建對話頁」階段（含路 A 薄殼與路 B SDK 宿主）一併擱置**，只在 CLI 撞到具體缺口才重啟討論。三區外殼／監控頁／iframe 那條軌**不受此影響**、照原計畫。

## 明確決定

- **砍掉語音／STT**：localhost 單人鍵盤場景，ROI 差。
- **topbar 的 CoS 狀態要誠實**：Desktop 互動 CoS 無 IPC 可探活，topbar 只可靠顯示 worker／gateway／佇列狀態，**不假裝知道 Desktop session 死活**。
- **必要 spike（已隨 MVP 技術路徑評估校準，見下方「MVP 技術路徑」段）**：
  1. **Hermes dashboard iframe 嵌入**（小）——三區外殼／iframe 那條線的把關，**仍有效、不變**（監控軌，與對話入口決策無關）。
  2. **~~headless `--resume` 撐 web 多輪對話 ＋ headless turn 授權邊界 spike~~ → 不再是待辦（2026-07-22 校準）**：這原是自建對話 MVP（路 A）開工前的前置關卡。**對話入口既已改用現成 CLI（[[project_cos-conversation-entry-point]]），路 A 擱置，此 spike 被 CLI 決策取代、不用做了**（不是「還沒做」，是「不需要做」）。
  3. **~~Agent SDK 常駐 CoS session 宿主 PoC~~（中大）→ 更往後押後**：只在未來「CLI 撞到具體缺口、要重啟自建 console（路 B）」時才需要。現行方向下用不到。

## 使用者對對話頁的價值主張（已記錄為動機）

想要一個輕量「直接對話入口」承接研究／簡單開發類任務，避免 Claude Code Desktop session 過載。`planning` 認為在這個聚焦用途下此價值主張成立。

## ~~AgentOS Console MVP 技術路徑（路 A）~~ → 已擱置（2026-07-22 對話入口改用現成 CLI）

> **本節整段已被 [[project_cos-conversation-entry-point]] 推翻，保留為歷史脈絡。** 現行結論：**對話入口採現成互動式 `claude` CLI ＋ Win+H，不自建對話頁**；下述「路 A 薄殼包 headless」與其前置 spike 一併擱置／不再是待辦。之所以放棄自建路 A：現成 CLI 本身就原生內建路 A 要重造的一切（串流、互動式權限授權、多輪有狀態 session、subagent 顯示），自建薄殼只是造一個比 CLI 更差的 client。以下保留當時對路 A 的評估，供未來若真要重啟自建 console 時參考。

**（歷史）技術路徑傾向曾定：走「路 A —— 薄殼包 headless（`claude -p --resume`）」，不是正式 SDK 常駐宿主。** 但可行性吊在一個前置 spike（見下）。**此傾向已於 2026-07-22 被「改用現成 CLI」取代，路 A 不再推進。**

**路 A 為何可行（關鍵使能事實）**：headless 支援 `--resume <session_id>`，且 Hermes 現在已用「thread_id → last session_id，還熱就 `--resume`」處理 Telegram 多輪續接。所以 web 多輪對話用薄殼可行：每則使用者訊息 → `claude -p --resume <session_id> "<訊息>" --output-format json`，解析 envelope 貼回頁面，下一則續 resume 同一 session_id。
- **這修正了定性判斷第 4 點「對話頁每一項都吊在 SDK 常駐宿主」的說法**——對縮小後的 MVP，`--resume` 是便宜的使能點，成本從先前評估的「大階段」降到**數天級**。
- **路 B（正式 SDK 常駐宿主）對這個聚焦 MVP 是殺雞用牛刀，押後**；等 MVP 用出具體痛點（沒串流難受、要中途 pause/cancel）再升級。

**路 A 的已知限制（誠實記錄，對「研究/簡單開發、丟出去等結果」可接受）**：
- 無 token 串流（每輪阻塞、只有 spinner）；
- 每輪冷啟延遲（process spawn ＋ 載入 CLAUDE.md）；
- 要管理 session_id（過期、避免同 session 並發 resume）。

**MVP 定位邊界（講死）**：定位在「研究/分析/不需提權的簡單任務」。headless 下未白名單動作是 fail-closed 被擋、**不跳授權卡片**，所以需要寫入/提權的任務沿用既有 Desktop（互動授權）/Telegram 路徑，**MVP 不自建 web 授權卡片**（避免在 headless 上開「無人把關卻能寫」的洞）。

**（歷史）前置關卡 → 已不再是待辦（2026-07-22）**：原本路 A 開工前要做的 engineering spike——驗證「headless `--resume` 能否支撐 web 多輪對話」＋「單一 headless turn 的授權邊界」。**既然改用現成 CLI、路 A 擱置，此 spike 被 CLI 決策取代，不用做了。** 僅在未來重啟自建 console 時才可能重新相關。（歷史 spike 內容備查：`--resume` 續接語意在快速多輪 web loop 下穩不穩、JSON envelope 是否可靠帶回 assistant 回覆、每輪延遲、並發控制、Write/Edit vs Bash 白名單在 headless turn 的實際行為。）

**價值主張定性補充（已被 CLI 決策超越）**：此輕量對話入口為「真實但中等的便利價值」。**最終解不是自建路 A，而是直接用現成互動式 `claude` CLI（在 repo root 開就是 CoS），成本近乎零、且原生具備串流／互動授權／多輪／subagent 顯示**——比任何自建薄殼都好。使用情境劃分（即時互動→CLI；非同步外包/遠端→Telegram-headless）見 [[project_cos-conversation-entry-point]]。

## How to apply

- 這是**已採納的 roadmap 方向**，不是開放討論——分期骨架與上列「明確決定」可直接引用。
- 涉及監控頁時，先對照 Stage 3 提案的功能二／三／一，**別重造**已規劃的唯讀視圖。
- 任何要在 dashboard 上做**寫入型**互動的想法，屬於獨立的 AgentOS Console 階段，走獨立後端／獨立安全模型，**不得混進或放寬唯讀 Streamlit 監控 app**（Stage 3 提案 §0.4／§0.5 的唯讀鐵律持續有效）。
- **對話入口已定案用現成互動式 `claude` CLI（在 repo root 開）＋ Win+H 語音**，見 [[project_cos-conversation-entry-point]]。**自建對話頁（路 A 薄殼包 headless、路 B SDK 常駐宿主）與其前置 spike 一併擱置／不再是待辦**，只在「CLI 撞到具體缺口且使用者確實需要」時才重啟討論。有人再提「做 web 對話 MVP／路 A／SDK 宿主」時，先引用該則決策，別把本檔舊的「路 A MVP」段落當現行方向。

## 相關記憶

- [[project_cos-conversation-entry-point]] — **對話入口最終定論**（2026-07-22）：改用現成互動式 `claude` CLI ＋ Win+H，自建對話頁（路 A／SDK 宿主）擱置。本檔中所有「對話頁 MVP／路 A」敘述以該則為準；本檔 Stage 3 唯讀監控／三區外殼／iframe 軌不受影響。
- [[project_agent-os-web-ui-timing-deferred]] — 先前對 Web UI 開發時機的擱置討論與 API/Domain-first 判斷準則；本則是那個問題在「監控頁走 Streamlit 便宜先行、互動 console 押後」方向上的具體化推進。
- [[hermes-cron-model-pin-convention]]、[[hermes-cron-store-binding-gateway-alignment]] — 監控頁 (c) cron 檢視與 Stage 3 功能三的模型漂移旗標判定所依賴的底層機制。
- Stage 3 唯讀 dashboard 設計提案（文件，非 memory）：`docs/stage3-dashboard-observability-proposal.md`。
