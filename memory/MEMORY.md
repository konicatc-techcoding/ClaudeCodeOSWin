# Shared Memory — Claude Code OS

這是整個系統的長期記憶正本。互動式 Chief of Staff session 與 consolidation pass 才能編輯這裡的檔案；背景任務只能寫進 `inbox/`。細節見 [ARCHITECTURE.md](../ARCHITECTURE.md) 第 4 節。記憶分層與 consolidation 政策（觸發門檻、useful 判定、敏感內容 guardrails、inbox frontmatter 約定）見 [docs/memory-taxonomy.md](../docs/memory-taxonomy.md)。

- [AI 產業動態 2026-07 快照](ai_industry_landscape_2026-07.md) — intelligence domain 於 2026-07-06 蒐集的各公司模型/策略/監管/資金動態快照，供長期脈絡查閱
- [Hermes workspace 專案指標](reference_hermes_workspace.md) — 使用者在 Hermes workspace 的自有專案（ResearchHelper 等）的存在與最後觀察狀態；正本在 Hermes 側 state.db
- [v0.1 領域狀態](project_v0_1_status.md) — `v0.1-beta` 已達成、capability-based model routing 已完成；`hermes_bridge.py` 技能同步管線已端到端驗證可動；原版在 `~/dev/ClaudeCodeOS`（macOS）；2026-07-07 起 Windows/WSL2 複本 `ClaudeCodeOSWin` 也已完整跑起來（`~/dev/ClaudeCodeOSWin`，5 個常駐服務全裝）；含 workspace trust（每次搬新路徑都會重現）／Preview 工具綁定等環境眉角
