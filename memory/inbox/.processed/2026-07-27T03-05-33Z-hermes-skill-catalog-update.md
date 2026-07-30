---
schema: claudecodeos.inbox.v1
source: hermes-bridge
created_at: 2026-07-27T03:05:33Z
usefulness: normal
usefulness_reason: 一批全數為「更新」的技能異動（25 項、跨 11 個子領域），規模與跨度都超出單一測試訊號；但全數是更新、無新增，且橫跨領域之廣可能代表目錄層級的批次刷新（例如 metadata 格式版本調整）而非逐一內容異動，細節需 consolidation 時再判斷
sensitivity: none
---

# Hermes 技能自主學習偵測 — 技能庫異動通知

- 來源：Hermes 技能自主學習偵測管線（`hermes/adapters/hermes_bridge.py` → job queue → headless CoS，經 `hermes/adapter/invoke_cos.sh` 以 `claude -p` 觸發本次背景任務）
- 落地時間：本檔案 `created_at`（通知本身未附精確異動時間戳）

## 異動內容

**更新技能（25，無新增）**：

- `software-development/node-inspect-debugger`
- `software-development/plan`
- `software-development/python-debugpy`
- `software-development/requesting-code-review`
- `software-development/spike`
- `software-development/test-driven-development`
- `smart-home/openhue`
- `research/blogwatcher`
- `research/llm-wiki`
- `productivity/airtable`
- `productivity/teams-meeting-pipeline`
- `note-taking/obsidian`
- `mlops/huggingface-hub`
- `media/gif-search`
- `media/songsee`
- `github/codebase-inspection`
- `creative/ascii-art`
- `creative/sketch`
- `autonomous-ai-agents/claude-code`
- `autonomous-ai-agents/codex`
- `autonomous-ai-agents/opencode`
- `apple/apple-notes`
- `apple/apple-reminders`
- `apple/findmy`
- `apple/imessage`

## 判斷理由（是否為實質內容 vs. 純健康檢查訊號）

跟 2026-07-06 的 `_hermes_bridge_test`（純測試訊號，見 `memory/inbox/.processed/`）與
2026-07-25 的技能異動（見 `memory/inbox/2026-07-25T00-11-27Z-hermes-skill-catalog-update.md`，
3 新增＋9 更新、集中在文件處理子領域）都不同：

- 命名無底線開頭或 test 字樣，不符合 `docs/memory-taxonomy.md` §4.2 排除訊號第 1 條的樣式，
  不能直接歸類為測試/健康檢查訊號。
- 但這次規模（25 項，超過 2026-07-25 那次的兩倍）與跨度（11 個子領域：
  software-development、smart-home、research、productivity、note-taking、mlops、media、
  github、creative、autonomous-ai-agents、apple）都明顯更大，且**全數是更新、沒有任何新增**——
  跟上次「新增聚焦同一子領域＋相關既有技能同步更新」的敘事型異動不同，比較像是目錄層級的
  批次刷新（例如技能中繼資料格式、版本欄位、或 Hermes 技能撰寫規範調整後的連動更新），
  而非個別技能內容各自變動。這點無法從通知本身確認，留待 consolidation 時視 Hermes
  workspace 側的實際差異再判斷。
- 三項與 ClaudeCodeOS／Hermes 整合直接相關，值得特別留意：
  - `autonomous-ai-agents/claude-code`：Claude Code 本身的技能，內容可能影響本專案對
    Claude Code 使用方式的既有假設。
  - `autonomous-ai-agents/codex`、`autonomous-ai-agents/opencode`：同一分類下的相鄰
    agent 工具技能同批更新，跟 `claude-code` 一起看，較像該分類技能撰寫規範被統一調整，
    呼應上面「批次刷新」的推測。

## 保留供 consolidation 判斷的重點

1. 通知本身只有技能名稱清單與新增/更新分類，無變更細節；「是否為目錄層級批次刷新」
   的推測需要回 Hermes 技能庫（workspace 外部）核對才能確認，本檔案不臆測細節。
2. consolidate-memory 時可考慮：是否併入 `memory/reference_hermes_workspace.md`，
   或在 `memory/project_v0_1_status.md` 補一筆「hermes-bridge 出現首次大規模（25 項）
   純更新型異動，跨度遠超先前批次，疑似目錄層級刷新」的觀察。
3. 若後續通知持續出現這種「全數更新、無新增、跨多子領域」的樣式，可能代表 Hermes 那邊
   有週期性的技能目錄重建/重新索引機制，屆時可考慮是否要新增一條排除訊號規則
   （避免每次都當作實質內容逐項深究）——但目前只有一次樣本，不足以下這個結論，先如實記錄。
</content>
