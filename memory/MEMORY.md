# Shared Memory — Claude Code OS

這是整個系統的長期記憶正本。互動式 Chief of Staff session 與 consolidation pass 才能編輯這裡的檔案；背景任務只能寫進 `inbox/`。細節見 [ARCHITECTURE.md](../ARCHITECTURE.md) 第 4 節。記憶分層與 consolidation 政策（觸發門檻、useful 判定、敏感內容 guardrails、inbox frontmatter 約定）見 [docs/memory-taxonomy.md](../docs/memory-taxonomy.md)。

- [AI 產業動態 2026-07 快照](ai_industry_landscape_2026-07.md) — intelligence domain 於 2026-07-06 蒐集的各公司模型/策略/監管/資金動態快照，供長期脈絡查閱
- [Hermes workspace 專案指標](reference_hermes_workspace.md) — 使用者在 Hermes workspace 的自有專案（ResearchHelper 等）的存在與最後觀察狀態；正本在 Hermes 側 state.db
- [Hermes Tavily key 明碼待修](hermes-tavily-key-plaintext-todo.md) — gptcoding profile config.yaml 的 tavily URL 內嵌明碼 API key，待改成 env 注入
- [Hermes 憑證處理安全教訓](hermes-credential-handling-safety-lessons.md) — 2026-07-20～21 分派 Hermes profile 憑證工作時的三類事故（讀檔案印明碼、廣泛進程掃描、越權碰其他 profile）；分派類似任務前必讀，教訓要直接寫進分派指令
- [任務類型→模型路由規則（使用者在意，未排程）](hermes-task-category-model-routing-preference.md) — 現有架構只到 domain 層級，尚無「依任務類型/範疇選 lane」的決策引擎，Phase 2f 完工後適合重提
- [Agent OS 驗收測試偏好](feedback_agent-os-acceptance-testing-preference.md) — 使用者偏好先跑單筆完整鏈路（scanner/importer/enqueue）確認正常，再做批次測試
- [Telegram session 生命週期管理構想（擱置）](project_telegram-session-lifecycle-management-idea.md) — 使用者提出後主動喊停，暫不建排程/掃描/歸檔，已留存判定維度供之後人工決定是否推進
- [Agent OS Web UI 開發時機（擱置未拍板）](project_agent-os-web-ui-timing-deferred.md) — 何時開始設計 Web UI 尚無定論，已留存重提時的判斷準則
- [v0.1 領域狀態](project_v0_1_status.md) — `v0.1-beta` 已達成、capability-based model routing 已完成；`hermes_bridge.py` 技能同步管線已端到端驗證可動；原版在 `~/dev/ClaudeCodeOS`（macOS）；2026-07-07 起 Windows/WSL2 複本 `ClaudeCodeOSWin` 也已完整跑起來（`~/dev/ClaudeCodeOSWin`，5 個常駐服務全裝）；含 workspace trust（每次搬新路徑都會重現）／Preview 工具綁定等環境眉角
