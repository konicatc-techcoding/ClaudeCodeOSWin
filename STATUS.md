# STATUS — 現況快照

> **用途**:讓任何新 session(前台 Desktop 或 headless CoS)在 30 秒內跟上進度。
> **更新規則**:每次收工前更新本檔的四個區塊;只寫「現在是什麼、接下來做什麼」,
> 歷史細節與證據一律連結到權威文件(ROADMAP.md、docs/hermes-integration-roadmap.md、
> memory/),不在這裡重複展開。本檔永遠只反映「最新一次收工時」的狀態。

**最後更新**:2026-07-24

---

## 1. 目前所在階段

- **Stage 5(Web UI 遷移)功能面全部完成:P0–P3 四個 phase 皆已交付並經使用者驗收**。
  新 Web UI(`webui/`,Vite+React)已可用:總覽/ClaudeCode CLI(PTY 終端機)/Jobs/
  成本/Memory/Logs/Hermes Sessions/憑證·Lane 狀態/Hermes Dashboard,搭配唯讀 API
  (`dashboard/api.py`,127.0.0.1:8799)與 PTY server(127.0.0.1:8801)。
  提案正本:[docs/webui-migration-proposal.md](docs/webui-migration-proposal.md)、
  [docs/webui-pty-terminal-proposal.md](docs/webui-pty-terminal-proposal.md)(皆已核准並實作)。
- **Streamlit 並行觀察期自 2026-07-24 起算**(觀察一個自然使用週期後決定退役;
  期間 `dashboard/app.py` 零改動、標 deprecated)。
- Stage 3 四條 DoD 已透過 Stage 5 P2 在新載體達成;階段全貌見
  [docs/hermes-integration-roadmap.md](docs/hermes-integration-roadmap.md)。

## 2. 上一個 session 做了什麼

(2026-07-23~24,commits `f1b9104`/`3da90ae`/`0bbd6c1` + 本收尾 commit)

- **P1**(`f1b9104`):唯讀 API(stdlib、GET-only、CORS/405/import guard、
  `redact.py` 三道掃描正本)+新 UI 五區塊與 Streamlit 對等。
- **P2**(`3da90ae`):Stage 3 三項觀測功能搬入新 UI(憑證/Lane 白名單檢視、
  統一排程健康表+模型漂移旗標、Hermes sessions);目視修正(字級+2、表格對齊、
  model 欄改「實際生效模型」)。
- **憑證清理實戰**(非 repo 變更):以 P2 憑證頁稽核發現殘留(gemini exhausted×2、
  nous 三處 ok、多 profile 庫存),使用者依 runbook 用官方 CLI 清理,五 profile
  對齊應然配置(見 auto-memory `hermes-profile-intended-config`);
  **教訓:profile store 切換靠 `HERMES_HOME`,`HERMES_PROFILE` 無關**。
- **P3**(`0bbd6c1`):PTY 終端機「ClaudeCode CLI」view(獨立 server 8801、
  token+Origin 雙層授權、spawn 凍結、單 session+idle 終止、audit 不落 transcript;
  node-pty 命中 win32 prebuilt 免建置工具);使用者實測打字/中文通過;
  按鈕改 Claude 橘、字體 15px。
- 文件連動(roadmap Stage 5 完成證據、兩份提案標實作完成)隨本收尾 commit 收。

## 3. 卡住/未決的問題

- **Streamlit 退役決策**:待並行觀察期(2026-07-24 起)滿一個自然使用週期後拍板。
- **nous 撤銷收尾**:本機憑證已清;**服務端撤銷待使用者到 Nous Portal 操作**。
- **env 變數清理待使用者手動**:建議移除 `GEMINI_API_KEY`/`GOOGLE_API_KEY`/
  `ANTHROPIC_API_KEY`(防重撿);`OPENROUTER_API_KEY` 為 nemocoding 必需不可移除。
- 本地 master 領先 origin 多個 commit,**尚未 push**(push 需使用者確認)。
- UI 欄位名稱可能再調整(使用者提出後隨時小改)。
- 舊項沿用:07-19 排程首次自動觸發結果待確認;「依任務類型自動選模型」規則引擎
  未實作;Hermes UI 設定維護(profile/allowlist 手改 config.yaml);Tavily key
  明文存放;WSL 部署複本需 `scripts/sync_to_wsl.sh` 手動同步。
- (低優先)bridge/PTY 屬安全敏感面,可考慮 `/code-review ultra` 補一道審查。

## 4. 下一步(可直接執行的第一步)

- **日常實際使用新 UI**(`webui/` 下 `npm run local` + `readonly-api`),
  在觀察期內累積使用經驗,發現問題隨時回報修正;觀察期滿拍板 Streamlit 退役。
- 次優先:push 到 origin(待確認);nous Portal 服務端撤銷+env 變數移除
  (使用者手動);確認 07-19 排程首次自動觸發結果。
