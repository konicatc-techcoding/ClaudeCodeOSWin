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

## 邊界

- 不要為了「有東西可以整併」而發明內容——沒有 inbox 素材、也在既有正本找不到需要更新的地方，就是「沒事做」，如實回報。
- 不要刪除既有正本裡跟這次 inbox 內容無關的部分。
- 不要處理 `memory/inbox/.processed/` 或 `.failed/` 底下的檔案——那些已經是這個流程自己的輸出，不是待處理項目。
