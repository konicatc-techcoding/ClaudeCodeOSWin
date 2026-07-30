---
name: consolidate-memory
description: Sweep memory/inbox/ into this project's canonical memory/*.md files — merge duplicates, resolve conflicts with existing entries, keep memory/MEMORY.md's index in sync, and archive processed inbox files. Use when memory/inbox/ has pending files to fold into long-term memory, or when explicitly asked to consolidate or organize memory.
---

# Consolidate Memory — v0.1

這是 Claude Code OS 專案自己的 memory 整併流程，只處理這個專案底下的 `memory/`，不是外部通用的記憶系統。

## 前提

- 只有這個 skill（在互動式 CoS session 之外）被允許修改 `memory/*.md` 正本檔案，見 `../../../ARCHITECTURE.md` 第 4 節、`../../../delegation_policy.md`。
- 呼叫這個 skill 的應該是 `knowledge` subagent。
- inbox 檔案是「已經發生的觀察」（背景任務寫入的原始素材），不是你自己去蒐集的——不要在這個流程裡新增任何不是從 inbox 或既有正本來的內容。
- 觸發時機（N-gate）、useful 判定與敏感內容 guardrails 見 `../../../docs/memory-taxonomy.md` 第 4 節（參數在 `../../../registry/consolidation_policy.yaml`）。inbox 檔案若帶 `claudecodeos.inbox.v1` frontmatter，用它的 `usefulness`／`sensitivity` 欄位輔助下面第 3、4 步的判斷；無 frontmatter 的檔案照本流程原樣處理。

## 流程

1. **列出待處理檔案**：`memory/inbox/` 底下、不在 `.processed/` 或 `.failed/` 子目錄裡的檔案，依檔名（含時間戳）排序。沒有檔案就直接回報「沒有待整併的內容」，不用往下做。
2. **讀取現況**：讀 `memory/MEMORY.md`（索引）跟目前所有 `memory/*.md` 正本，了解已經記錄過什麼，避免重複。
3. **逐檔判斷歸屬**：對每個 inbox 檔案，決定：
   - 併入既有的某個 `memory/<type>_<topic>.md`（型別見下方「型別判斷」），或建立一個新的。
   - 判斷這個內容是否已經被既有正本涵蓋（重複）——重複的話跳過，不重寫。
   - 判斷是否讓既有記憶「過期」（例如新資訊推翻舊資訊）——過期的話更新既有檔案，不要兩份並存。
4. **格式不合法的檔案**：內容看不出屬於哪個型別、或明顯不是給記憶系統用的雜訊，不要硬塞進正本。搬到 `memory/inbox/.failed/`，並在回報裡列出原因。
5. **更新索引**：`memory/MEMORY.md` 裡每個正本檔案一行，格式：`- [標題](檔名) — 一句話說明`，跟這次異動同步（新增/更新/移除都要反映）。
6. **歸檔**：處理成功的 inbox 檔案搬到 `memory/inbox/.processed/`，保留原檔名（含時間戳），作為稽核軌跡。**這一步要在正本寫入成功之後才做，不要先搬再寫**——避免處理到一半中斷時，內容既沒進正本、也從 inbox 消失。
7. **回報**：整併了幾個檔案、建立/更新了哪些正本、跳過幾個重複、失敗幾個並說明原因。

## 型別判斷（跟 memory/*.md 的命名慣例對齊）

- `user_*.md`：關於「誰在用這個系統、他的角色/偏好/知識背景」
- `project_*.md`：進行中的工作、目標、決策、期限，會隨時間變動
- `feedback_*.md`：使用者對「怎麼做事」給過的糾正或肯定
- `reference_*.md`：外部系統的指標（bug 追蹤在哪、資料在哪個服務）

如果一個 inbox 檔案同時符合多種型別，拆成多筆，不要塞進同一個型別硬湊。如果完全看不出型別，走上面第 4 步的「格式不合法」路徑。

## Retention review（2026-07-30 拍板）

除了 inbox 整併之外，本 skill 還承擔記憶生命週期的「出口」檢視（設計正本：`../../../docs/memory-lifecycle-proposal.md` §2.3；拍板結果：汰選＝全自動歸檔＋豁免清單、升格＝MEMORY.md 索引分層 C-a）。**只在呼叫端明確要求做 retention review 時執行**（daily-memory-check 的排程 prompt 依 `review_interval_days` 決定要不要帶這個要求），一般 inbox 整併不順便做。執行者同樣是 `knowledge` subagent。

步驟：

1. **讀參數**：`../../../registry/consolidation_policy.yaml` 的 `retention:` 區塊——`stale_threshold_days`、`review_interval_days`、豁免清單 `exempt`。區塊不存在就回報「retention 政策尚未配置」並跳過整個 review，不用預設值硬跑。
2. **讀 recall log 並統計**：讀 `../../../logs/recall_log.jsonl`（append-only JSONL，每行含 `ts`／`entry`／`result`／`hit_ids`／`task_hint`）。注意**兩側可能各一份**（Windows 前台寫本地、WSL headless 寫部署複本側）——若 sync reverse-merge 尚未把兩側併起來，統計時要把拿得到的每一份都讀進來取**併集**。對每個 `memory/*.md` 正本統計：被 `hit_ids` 提及的總次數、最後一次 recall 時間。
3. **冷啟動保護（先判斷，不足就停）**：log 覆蓋天數＝今天 − log 裡最早一筆的 `ts`。覆蓋天數 < `stale_threshold_days` 時**不做任何汰選**，回報「log 覆蓋 N 天，未達門檻 M 天，本次只做升格檢視」。理由：recall log 是方向性下限（prompt 層埋點 best-effort），覆蓋不足時「零 recall」不可信。
4. **汰選＝直接歸檔**：對「`stale_threshold_days` 內零 recall」且**不在豁免清單**的正本檔：
   - 豁免判定（符合任一即永不歸檔）：內容屬拍板決策（substantive_decision 型）、安全／事故教訓、`feedback_*` 全型別。`reference_*` 與一般 `project_*` 可汰選。
   - 歸檔動作：把正本檔搬到 `memory/.archive/`（目錄不存在就建立，保留原檔名），並從 `memory/MEMORY.md` 索引移除該行。**不是刪除**——要回復就把檔案搬回 `memory/` 並補回索引一行。
   - 全自動執行、不逐條詢問（拍板檔位），但**回報中必須完整列出本次歸檔清單**（檔名＋最後 recall 時間或「log 期間零 recall」＋log 覆蓋起算日），供使用者事後知悉與反悔搬回。
5. **升格＝索引分層（C-a）**：recall 高頻的條目（參考 `skill_promotion.min_recall_reuse` 的精神，log 期間 recall ≥ 3 次即算高頻），在 `memory/MEMORY.md` 索引裡集中到最前面的「高頻」區段（區段不存在就建立）；其餘條目維持原有分組。正本檔不動、不搬家——recall-first 先讀索引，排序本身就是長期記憶優先權。另外：同一解法／程序 recall 複用達 `min_recall_reuse` 次的，照既有規則列為 SKILL.md 升級候補（人在迴路，不自動建 skill）。
6. **回報**：log 覆蓋天數、統計了幾條正本、歸檔清單（或「冷啟動保護生效、未汰選」）、高頻區段異動、SKILL 候補清單。

邊界（本節專屬）：

- 只動 `memory/*.md` 正本、`memory/MEMORY.md` 索引與 `memory/.archive/`——不寫 `logs/recall_log.jsonl`（那是 recall 埋點的輸出，本流程唯讀）。
- 豁免清單條目即使整個 log 期間零 recall 也不進歸檔，連候選都不列。
- 歸檔動作與 inbox 整併互不混用：`.archive/` 收的是**正本**，`.processed/`／`.failed/` 收的是 inbox 素材，不要交叉搬。

## 邊界

- 不要為了「有東西可以整併」而發明內容——沒有 inbox 素材、也在既有正本找不到需要更新的地方，就是「沒事做」，如實回報。
- 不要刪除既有正本裡跟這次 inbox 內容無關的部分。
- 不要處理 `memory/inbox/.processed/` 或 `.failed/` 底下的檔案——那些已經是這個流程自己的輸出，不是待處理項目。
