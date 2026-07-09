目前狀態總結
一、已完成的架構決策
Runtime 核心

Chief of Staff（CoS）只做「決策・分派・整合」，實際工作交給五個 domain subagent（intelligence / engineering / automation / knowledge / planning，全部 active）
delegation_policy.yaml 強制分派：任務先分類（direct_categories vs delegated_categories），不讓模型自己判斷「任務很小就自己做」；含跨領域依賴（planning → knowledge）
Model Router 是 capability-based：subagent 只請求「能力」（如 complex_coding），不直接點名模型；registry/agents.yaml 的 default_capability 是每個領域的唯一真相來源，不寫死在 subagent 檔案裡。目前：intelligence→bulk_research（nemotron，OpenRouter 免費層）、engineering→complex_coding（GPT-5.5）、automation/knowledge/planning→claude_native
記憶系統

memory/inbox/ 只能新增、不能改；只有互動式 CoS session 或 consolidate-memory skill 能寫入 memory/*.md 正本——兩邊寫入路徑物理隔離，不是靠自律
Hermes 背景執行

SQLite job queue（queued/running/completed/failed/dead_letter，retry 指數退避、dead-letter、reaper、session resume 24h TTL、cost_usd 統計）
三個 event source adapter（Telegram polling、Cron、RSS）都刻意設計成無狀態，排程/觸發時機全部交給部署層排程器，adapter 本身只負責「產生 job」
常駐服務由部署層排程器管理（worker + telegram + cron-daily-memory-check + rss + bridge），部署層跟 Runtime 解耦（worker.py/db.py 完全不知道排程器存在）。目前 live runtime 是 WSL2 systemd（`hermes/systemd/`）；launchd 為 macOS 時期 legacy，僅留參考（2026-07-09 更新）
Dashboard

Streamlit，localhost-only、read-only——read-only 是技術強制的（獨立資料層、mode=ro 開 SQLite，不 import hermes/db.py），不是只靠程式碼自律
專案位置

從 ~/Documents/Claude Code/ClaudeCodeOS 搬到 ~/dev/ClaudeCodeOS（避開 macOS TCC 保護資料夾），已 trust、已驗證
里程碑：v0.1-alpha（Runtime 核心）→ v0.1-beta（補完 Cron/RSS/Dashboard）都已用 git tag 標記。

二、目前檔案結構
ClaudeCodeOS/
├── CLAUDE.md、ARCHITECTURE.md、ROADMAP.md、delegation_policy.md
├── INTEGRATION_TEST.md、SAT_REPORT.md          ← 測試證據文件
├── .claude/
│   ├── agents/{intelligence,engineering,automation,knowledge,planning}.md
│   ├── skills/consolidate-memory/SKILL.md
│   ├── settings.json                            ← headless Bash/WebSearch 白名單
│   └── launch.json                               ← Streamlit preview 設定
├── registry/
│   ├── agents.yaml            ← 領域清單 + default_capability
│   ├── delegation_policy.yaml ← 分類規則
│   └── model_router.yaml      ← capability → 模型對照
├── memory/
│   ├── MEMORY.md、project_v0_1_status.md
│   └── inbox/（.processed/、.failed/）
├── scripts/
│   ├── route_model.py（+ test）、requirements.txt
├── hermes/
│   ├── db.py、worker.py（+ test）
│   ├── adapter/invoke_cos.sh
│   ├── adapters/{telegram,cron,rss}.py（各有 test）
│   ├── config/{cron_jobs.yaml, rss_feeds.yaml}（進版控）、telegram.json（gitignore，含密鑰）
│   ├── state/（gitignore，telegram offset、rss seen）
│   ├── launchd/（4 個 plist + install/uninstall.sh）
│   └── jobs.db（gitignore）
├── dashboard/
│   ├── app.py、data.py（+ test）
└── logs/hermes/（gitignore，worker/adapter log + 每個 job 一個 log）

九十個左右的檔案裡，81 個單元測試全部通過（hermes/、dashboard/、scripts/ 底下都有）。

三、已知待解決的問題
技術債（ROADMAP.md 已記錄）

scripts/route_model.py 的例外處理仍不完整（code review 當時列出的其餘發現，只修了路徑邊界檢查那一項）
scripts/requirements.txt 沒有鎖定版本
headless 模式下，.claude/settings.json 的 Bash 白名單以外的指令仍會被擋（目前夠用，非完整方案）
這次會話新發現、還沒處理的

這個 session 裡 Preview 工具（preview_start）綁定在舊路徑 ~/Documents/Claude Code/ClaudeCodeOS，不會因為搬家而改變，重試無效——要用瀏覽器看 Dashboard，要嘛在新資料夾開一個新 session，要嘛用我手動起的 server 自己開瀏覽器連
Chrome 擴充功能（claude-in-chrome）這次也連不上，原因不明，可能是暫時性的
新工作目錄第一次使用時會撞到 Claude Code 的 workspace trust 機制（已解決，但值得記住：以後這個專案如果再搬家，一樣要先跑一次 claude 互動式接受 trust）
規劃中、尚未動工

Model Router 的 MCP server 版本（目前 script adapter 就夠用）
Dashboard 要不要開放除了 Telegram 以外的投遞管道，還沒討論