---
schema: claudecodeos.inbox.v1
source: hermes-bridge
created_at: 2026-07-25T00:11:27Z
usefulness: normal
usefulness_reason: 一批看似真實內容的技能異動（3 新增＋9 更新），非測試性質的管線健康檢查訊號；但通知本身只有技能名稱清單、無變更細節，先落地保留，整併時再判斷要不要並入 reference 或只留一筆觀察紀錄
sensitivity: none
---

# Hermes 技能自主學習偵測 — 技能庫異動通知

- 來源：Hermes 技能自主學習偵測管線（`hermes/adapters/hermes_bridge.py` → job queue → headless CoS，經 `hermes/adapter/invoke_cos.sh` 以 `claude -p` 觸發本次背景任務）
- 落地時間：本檔案 `created_at`（通知本身未附精確異動時間戳）

## 異動內容

**新增技能（3）**：
- `productivity/docx`
- `productivity/pdf`
- `productivity/xlsx`

**更新技能（9）**：
- `computer-use`
- `software-development/hermes-agent-skill-authoring`
- `software-development/simplify-code`
- `social-media/xurl`
- `productivity/nano-pdf`
- `productivity/ocr-and-documents`
- `productivity/powerpoint`
- `creative/songwriting-and-ai-music`
- `autonomous-ai-agents/hermes-agent`

## 判斷理由（是否為實質內容 vs. 純健康檢查訊號）

跟 2026-07-06 那次 `_hermes_bridge_test` 通知（見 `memory/inbox/.processed/2026-07-06T15-46-00Z-hermes-bridge-test-skill-change.md`）不同，這次判斷為**實質內容**，理由：

- 命名沒有底線開頭或 test 字樣，是正常的技能路徑命名，不符合 `docs/memory-taxonomy.md` §4.2 排除訊號第 1 條（測試 session／管線健康檢查）的樣式。
- 異動規模（3 新增＋9 更新，共 12 項）明顯超出單一測試技能變更的範圍，較像一次真實的技能庫批次更新。
- 新增的三個技能集中在同一子領域——`productivity/docx`、`productivity/pdf`、`productivity/xlsx`——像是在補齊 Office/PDF 文件處理能力；同批更新裡又包含同領域既有技能 `productivity/nano-pdf`、`productivity/ocr-and-documents`、`productivity/powerpoint`，方向一致，不像巧合。
- 更新清單中有兩項特別值得留意，因為不是一般內容技能：
  - `software-development/hermes-agent-skill-authoring`：技能撰寫規範本身被更新。
  - `autonomous-ai-agents/hermes-agent`：Hermes agent 自身的技能。
  兩者變更可能代表 Hermes 那邊的技能撰寫框架或 agent 行為本身有調整，跟 ClaudeCodeOS 的 Hermes 整合（`hermes/` 目錄、`docs/hermes-integration-roadmap.md`）間接相關，值得記錄但不代表 ClaudeCodeOS 這邊需要立即動作。

## 保留供 consolidation 判斷的重點

1. 這則通知只有技能名稱清單，沒有具體變更內容（改了什麼、為什麼改）。細節需要回 Hermes 那邊的技能庫（workspace 外部）查，或等後續通知補充；本檔案不臆測細節。
2. consolidate-memory 時可考慮：是否併入 `memory/reference_hermes_workspace.md`（記錄「Hermes 那邊有什麼」的既有慣例），或只需要在 `memory/project_v0_1_status.md` 記一筆「hermes-bridge 管線持續偵測到真實技能異動，非僅測試訊號」，視當時正本內容與篇幅取捨。
3. 若之後陸續看到同類「一批技能新增/更新」通知，可觀察 Hermes 那邊技能庫擴充的節奏與方向（這次看起來是文件處理能力＋技能撰寫規範本身），作為判斷 ClaudeCodeOS 是否需要跟進採用 Hermes 新技能的參考訊號。
</content>
