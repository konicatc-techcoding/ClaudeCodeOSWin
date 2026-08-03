---
schema: claudecodeos.inbox.v1
source: hermes-bridge
created_at: 2026-07-31T00:11:35Z
usefulness: normal
usefulness_reason: 延續 reference_hermes_skill_catalog.md 對 Hermes 技能異動的觀察序列；本批「更新」清單與 07-27 批次逐項完全相同（25 項、順序一致），是支持「目錄層級批次刷新／週期性重索引」假說的第二個樣本，值得併入既有正本強化結論
sensitivity: none
---

## 2026-07-31 批次：1 新增＋25 更新

- **新增（1）**：`software-development/project-governance-design`
- **更新（25）**：software-development ×6（`node-inspect-debugger`、`plan`、`python-debugpy`、`requesting-code-review`、`spike`、`test-driven-development`）、`smart-home/openhue`、research ×2（`blogwatcher`、`llm-wiki`）、productivity ×2（`airtable`、`teams-meeting-pipeline`）、`note-taking/obsidian`、`mlops/huggingface-hub`、media ×2（`gif-search`、`songsee`）、`github/codebase-inspection`、creative ×2（`ascii-art`、`sketch`）、autonomous-ai-agents ×3（`claude-code`、`codex`、`opencode`）、apple ×4（`apple-notes`、`apple-reminders`、`findmy`、`imessage`）

**關鍵觀察**：這 25 項「更新」清單與 [reference_hermes_skill_catalog.md](../reference_hermes_skill_catalog.md) 記錄的 07-27 批次**逐項、逐序完全相同**。同一組 25 個技能在四天內第二次被標記為「更新」，且與 07-27 一樣跨 11 個子領域、無主題聚焦性。這比單一「全更新無新增」樣本更強地指向：Hermes 側存在會定期重新標記整個目錄（或某個未知子集）為「已更新」的機制（例如版本欄位/中繼資料重寫、批次重新索引），而非這 25 個技能各自真的有內容變動——若真是內容異動，四天內兩次幾乎相同範圍的變動不合常理。

新增的 `software-development/project-governance-design` 本身是唯一的實質訊號，其餘可視為雜訊候選。

**建議動作（供 knowledge 整併時參考）**：
1. 若能取得 Hermes 側技能檔案的實際 mtime 或內容 hash，可驗證這 25 項是否真的內容未變（僅中繼資料被重寫）；本次通知本身不含變更細節，無法在這裡確認。
2. 若第三次通知再出現相同或高度重疊的「更新」清單，可考慮依 [docs/memory-taxonomy.md](../../docs/memory-taxonomy.md) §4.2 新增一條排除訊號規則：「與前一批次完全重複的更新項目視為批次刷新雜訊，不逐項深究，只記新增項目」。
