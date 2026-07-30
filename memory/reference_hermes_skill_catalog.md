---
name: hermes-skill-catalog-observations
description: Observed changes in the Hermes-side skill catalog (external to ClaudeCodeOS), as reported by the hermes-bridge skill-change detection pipeline — batch contents, interpretation, and the emerging "catalog-level batch refresh" pattern to watch
metadata:
  type: reference
---

Hermes 側技能庫（workspace 外部，正本在 Hermes 那邊）的異動觀察紀錄。來源是 Hermes 技能自主學習偵測管線（`hermes/adapters/hermes_bridge.py` → job queue → headless CoS）。通知本身只有技能名稱清單與新增/更新分類，**沒有變更細節**（改了什麼、為什麼改）——細節需回 Hermes 技能庫查證，本檔不臆測。

管線首次端到端驗證（2026-07-06，測試訊號 `_hermes_bridge_test`）記錄在 [project_v0_1_status.md](project_v0_1_status.md)；本檔只記「實質內容」的異動批次。

## 異動批次紀錄

### 2026-07-25 批次：3 新增＋9 更新，聚焦文件處理能力

- **新增（3）**：`productivity/docx`、`productivity/pdf`、`productivity/xlsx`
- **更新（9）**：`computer-use`、`software-development/hermes-agent-skill-authoring`、`software-development/simplify-code`、`social-media/xurl`、`productivity/nano-pdf`、`productivity/ocr-and-documents`、`productivity/powerpoint`、`creative/songwriting-and-ai-music`、`autonomous-ai-agents/hermes-agent`
- **解讀**：判定為實質內容（非測試訊號）——命名正常、規模超出單一測試變更；新增集中在 Office/PDF 文件處理，同批更新又含同領域既有技能（nano-pdf、ocr-and-documents、powerpoint），像是在補齊文件處理能力。兩項特別值得留意：`hermes-agent-skill-authoring`（技能撰寫規範本身被更新）與 `autonomous-ai-agents/hermes-agent`（Hermes agent 自身技能）——可能代表 Hermes 側技能撰寫框架或 agent 行為有調整，與本專案的 Hermes 整合間接相關，但不需要 ClaudeCodeOS 立即動作。

### 2026-07-27 批次：25 項純更新、無新增，跨 11 個子領域（疑似目錄層級批次刷新）

- **更新（25）**：software-development ×6（`node-inspect-debugger`、`plan`、`python-debugpy`、`requesting-code-review`、`spike`、`test-driven-development`）、`smart-home/openhue`、research ×2（`blogwatcher`、`llm-wiki`）、productivity ×2（`airtable`、`teams-meeting-pipeline`）、`note-taking/obsidian`、`mlops/huggingface-hub`、media ×2（`gif-search`、`songsee`）、`github/codebase-inspection`、creative ×2（`ascii-art`、`sketch`）、autonomous-ai-agents ×3（`claude-code`、`codex`、`opencode`）、apple ×4（`apple-notes`、`apple-reminders`、`findmy`、`imessage`）
- **解讀**：規模（25 項，07-25 批次的兩倍以上）與跨度（11 個子領域）都明顯更大，且**全數是更新、沒有任何新增**——與 07-25「新增聚焦同一子領域＋相關技能同步更新」的敘事型異動不同，比較像目錄層級批次刷新（例如技能中繼資料格式、版本欄位、或技能撰寫規範調整後的連動更新），而非個別技能內容各自變動。此推測未經 Hermes 側核對，不視為已確認。三項與本專案直接相關值得留意：`autonomous-ai-agents/claude-code`（可能影響本專案對 Claude Code 使用方式的既有假設）、`codex`／`opencode`（同分類相鄰工具同批更新，呼應「該分類撰寫規範被統一調整」的批次刷新推測）。

## 觀察模式與後續判斷依據

1. Hermes 側技能庫擴充方向（截至目前觀察）：文件處理能力補齊（07-25）＋技能撰寫規範/agent 分類的連動調整（07-25、07-27 皆有跡象）。可作為判斷 ClaudeCodeOS 是否需要跟進採用 Hermes 新技能的參考訊號。
2. 若後續通知持續出現「全數更新、無新增、跨多子領域」的樣式，可能代表 Hermes 側有週期性的技能目錄重建/重新索引機制，屆時可考慮在 `docs/memory-taxonomy.md` §4.2 新增一條排除訊號規則（避免每次都當實質內容逐項深究）。**目前只有一次樣本（07-27），不足以下結論**，先如實記錄。
