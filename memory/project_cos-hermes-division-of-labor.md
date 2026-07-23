---
name: project-cos-hermes-division-of-labor
description: 2026-07-23 拍板的 CoS ↔ Hermes 架構分工定調（乙案）——記憶正本＋recall＝ClaudeCodeOS；Hermes＝餵進 CCOS 記憶的「來源之一」（唯讀單向 bridge）＋ opt-in 執行後端（經 profile 存取非 Claude 模型）。原「Hermes 當記憶層」意圖據此 reframe。含四來源匯一正本、執行去重（預設 CoS native）、別再造/必須擁有清單、roadmap（bridge 部署排 recall-first 之後第一位）
metadata:
  type: project
---

來源：2026-07-23 互動式前台 CoS session（草稿由 `planning` 評估、使用者拍板）。本則是「Hermes 與 ClaudeCodeOS 誰負責記憶、誰負責執行」這個問題的**架構分工定調**（乙案），供日後任何「要不要讓 Hermes 當記憶正本／要不要建反向橋／要不要強制路由 lane」的討論直接引用。

## 核心 reframe

原始意圖「把 Hermes 當記憶層」調整為：

- **記憶正本 + recall = ClaudeCodeOS（CCOS）**。
- **Hermes = 餵進 CCOS 記憶的「來源之一」+ opt-in 執行後端**，不是記憶正本。
- **資料流單向**：Hermes → CCOS（設計如此）。bridge **唯讀、絕不寫回 Hermes**。

這不是願景降級，是對齊既有資料流的物理事實。

## 為什麼調整（已查證約束）

bridge 是設計上單向唯讀。要反向讓 CoS 產出進 Hermes 記憶，只有兩條路，都不可取：

1. **寫 Hermes 的第三方 DB**——違反「絕不寫 Hermes 原始資料」核心規則，且脆弱。
2. **全走會留 `ended_at` 的 Hermes session**——吃模型／成本代價，且**仍抓不到 CoS 自己的推理**。

故「Hermes 當記憶正本」這條路乾淨地補不起來。既然反向補不起來，正確定調就是：記憶正本留在 CCOS，Hermes 只作來源與執行後端。

## 記憶去重——四來源匯一正本（關鍵）

四條寫入路徑，最終**全部經 consolidation 併入同一份 CCOS 正本**（`memory/*.md`）：

1. **互動 CoS 產出** → 直接寫 `memory/*.md` 正本。
2. **headless CoS 產出** → `memory/inbox/`。
3. **lane 執行結果** → 經回傳 JSON envelope → inbox（**不必碰 Hermes 記憶**）。
4. **Hermes 自己的 session** → 唯讀 bridge → inbox。

- 四者全經 consolidation 併入**同一正本**，由 recall-first（`delegation_policy.md` 決策程序步驟 1.5，見 [[project_recall-first-mvp]]）檢索。
- **CoS recall 只查 CCOS 正本，不去查 Hermes 的 FTS5**——Hermes FTS5 留給 Hermes 自己用。這是讀側的乾淨切分：recall 面只有一個真相來源。

## 執行去重

- **預設 = CoS native**（Opus、Claude Code loop）。
- **Hermes lane = opt-in**，只在任務**確實需要「非 Claude 模型」**時才用；**不強制路由**；**不為沒有「非 Claude 模型理由」的領域建 lane**。
- Hermes 真正差異化的執行價值 = **經 profile 憑證存取非 Claude 模型**。除此之外的 agentic loop／工具／編排能力，Claude Code 已經有，不靠 Hermes。

## 別再造 / 必須擁有

- **別再造**：agentic loop、工具、subagent 編排、互動授權 UI（Claude Code 有）；非 Claude 執行（Hermes lane 有）；反向 CCOS→Hermes 記憶橋；FTS5／embeddings（現規模不需要）。
- **必須擁有**：治理（delegation policy）、recall-first、記憶正本、consolidation、「什麼進哪裡」的判斷。這五項是 CCOS 這一層不可外包的核心。

## Roadmap

1. **拍板本決策並落檔**（本次，2026-07-23）。
2. **完成 Hermes→CCOS bridge 部署**（scanner／importer episode 化 + 目前未部署的 timer）＝真正能動的記憶整合，排 **recall-first 之後第一位**。
3. **lane 維持 opt-in**。
4. **不追反向橋、不強制路由**。
5. 先前提的「Hermes oneshot 留不留可捕捉 session」spike **可不做**——lane 結果已能經 envelope 進 inbox，不需要靠留 session 來回收 lane 產出。

## How to apply

- 有人再提「把 Hermes 當記憶正本／記憶層」時，先引用本則：記憶正本＋recall 在 CCOS，Hermes 是來源之一（唯讀單向）＋ opt-in 執行後端；反向橋設計上補不起來（寫第三方 DB 違規＋脆弱、全走 session 吃成本又抓不到 CoS 推理）。
- 有人再提「建 CCOS→Hermes 反向記憶橋」或「讓 CoS recall 去查 Hermes FTS5」時：不做。recall 只查 CCOS 正本。
- 有人再提「把某領域強制路由到 Hermes lane／給沒有非 Claude 理由的領域建 lane」時：不做。lane 是 opt-in，只為「確實要非 Claude 模型」的任務存在。
- 記憶整合的下一步排序：recall-first（已落地）→ **Hermes bridge 部署（scanner/importer episode 化＋timer）** 排第一位。

## 相關記憶

- [[project_recall-first-mvp]] — recall-first 讀側 MVP；本則指定 recall 只查 CCOS 正本、四來源匯一正本後由步驟 1.5 檢索，兩則互為讀寫兩側的分工。
- [[project_cos-conversation-entry-point]] — 對話入口用現成 CLI；本則的「互動 CoS 產出直接寫正本」與其「互動式 session 不被 Hermes bridge 捕捉」的查證一致（Claude Code session 存 `~/.claude/`，Hermes bridge 只讀自己的 `state.db`）。
- [[reference_hermes_workspace]] — Hermes workspace 是外部系統、正本在 Hermes 側 `state.db`；本則定調它相對 CCOS 是「記憶來源之一（唯讀）」而非正本。
- [[project_v0_1_status.md]] — v0.1 領域狀態與 bridge 端到端驗證現況；roadmap 第 2 項（bridge 部署）承接該檔的 bridge 進度。
- ARCHITECTURE.md §4.2.1（文件，非 memory）：反向橋接是 Domain Execution Router、資料流不對稱——本則是該不對稱性的分工定調。
- docs/memory-taxonomy.md §7（文件，非 memory）：治理結論「記憶正本＝CCOS、Hermes＝來源之一、recall 只查 CCOS 正本、四來源匯一正本、不建反向橋」。
