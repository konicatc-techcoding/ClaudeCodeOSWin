# STATUS — 現況快照

> **用途**:讓任何新 session(前台 Desktop 或 headless CoS)在 30 秒內跟上進度。
> **更新規則**:每次收工前更新本檔的四個區塊;只寫「現在是什麼、接下來做什麼」,
> 歷史細節與證據一律連結到權威文件(ROADMAP.md、docs/hermes-integration-roadmap.md、
> memory/),不在這裡重複展開。本檔永遠只反映「最新一次收工時」的狀態。

**最後更新**:2026-07-23

---

## 1. 目前所在階段

- **v0.1-beta 之後、Hermes 整合軌進行中**。Stage 0–2.7 與 Stage 4 全部完成,
  **下一個待開工階段是 Stage 3(Dashboard Hermes Session 檢視頁/觀測性收尾)**,
  提案 v2 已寫好:[docs/stage3-dashboard-observability-proposal.md](docs/stage3-dashboard-observability-proposal.md)。
- 階段全貌與各 stage 驗收證據:[docs/hermes-integration-roadmap.md](docs/hermes-integration-roadmap.md)
  (「建議優先順序」一節是排序正本)。
- 部署環境:Windows/WSL2(repo 在 Windows 原生路徑,常駐服務在 WSL2 systemd;
  bridge 排程已改交 **Windows Task Scheduler `HermesBridgeDaily`**,見 commit `7ef8b7a`)。

## 2. 上一個 session 做了什麼

(2026-07-23,單一收尾 commit)

- **建立收工快照機制**:新增本檔 `STATUS.md`(第一版即填入實況,非空模板)、
  新增 `/wrapup` skill(`.claude/skills/wrapup/SKILL.md`:收工時更新四區塊並
  commit 收尾)、修改 `CLAUDE.md` 加入「開場先讀 STATUS.md、收工用 /wrapup、
  期中 commit 不動 STATUS.md」指引。
- **拍板的慣例**:STATUS.md 更新粒度綁「收工」不綁「每次 commit」;觸發靠
  明確指令 /wrapup,CLAUDE.md 指引要求在使用者以其他說法表達收工時主動建議跑它。
- 上上次 session(2026-07-21~22)的重點——bridge 排程移交 Windows Task Scheduler
  `HermesBridgeDaily`(`7ef8b7a`)、Stage 3 提案 v2(`d874cff`)、Stage 4 完成——
  已記錄於 docs/hermes-integration-roadmap.md 與 memory/,不在此重複。

## 3. 卡住/未決的問題

- **Stage 4 遺留**:`nous` token 撤銷待確認;「依任務類型自動選模型」規則引擎
  未實作(現有架構只到 domain 層級 `default_capability`,無法按任務型自動選 lane)。
- **07-19 排程首次自動觸發是否成功待確認**(Stage 2.7 通知排程化後的第一次
  實跑驗證,見 auto-memory)。
- **Hermes UI 設定維護**:profile 建立與 Slack 頻道 allowlist 目前要手改
  config.yaml,希望改成 UI 勾選/下拉維護(未排程)。
- **Tavily key 明文存放待處理**(見 memory/hermes-tavily-key-plaintext-todo.md)。
- Windows repo 與 WSL 部署複本是兩份獨立複本,設定變更要靠
  `scripts/sync_to_wsl.sh` 手動同步——改了部署側相關檔案記得同步。

## 4. 下一步(可直接執行的第一步)

- **開工 Stage 3**:從
  [docs/stage3-dashboard-observability-proposal.md](docs/stage3-dashboard-observability-proposal.md)
  的提案 v2 開始——第一步是走一次提案核准流程(確認範圍與拍板項),核准後分派
  `engineering` 實作。
- 次優先(可並行的小事):向使用者確認 `nous` token 是否已撤銷;確認 07-19
  排程首次自動觸發結果。
