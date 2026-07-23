# STATUS — 現況快照

> **用途**:讓任何新 session(前台 Desktop 或 headless CoS)在 30 秒內跟上進度。
> **更新規則**:每次收工前更新本檔的四個區塊;只寫「現在是什麼、接下來做什麼」,
> 歷史細節與證據一律連結到權威文件(ROADMAP.md、docs/hermes-integration-roadmap.md、
> memory/),不在這裡重複展開。本檔永遠只反映「最新一次收工時」的狀態。

**最後更新**:2026-07-23

---

## 1. 目前所在階段

- **Stage 5(Web UI 遷移)進行中:P0 已完成並驗收,下一個 phase 是 P1**。
  2026-07-23 拍板:dashboard 以 AgentOSUI 範本為雛形**全面轉移**新 Web UI
  (推翻 stage3 提案 §0.1「不另開新入口」),提案正本:
  [docs/webui-migration-proposal.md](docs/webui-migration-proposal.md)(v2 已核准)。
- **Stage 3 三項觀測功能凍結**,設計正本仍為
  [docs/stage3-dashboard-observability-proposal.md](docs/stage3-dashboard-observability-proposal.md)
  §2–4,實作載體改為新 UI(Stage 5 P2 搬遷);既有 Streamlit dashboard 並行觀察期保留。
- 階段全貌:[docs/hermes-integration-roadmap.md](docs/hermes-integration-roadmap.md)
  (已新增 Stage 5 節)。部署環境不變(Windows/WSL2,bridge 排程在
  Windows Task Scheduler `HermesBridgeDaily`)。

## 2. 上一個 session 做了什麼

(2026-07-23,commit `bf2bfe2`,已 push)

- **Stage 5 立案並拍板**(planning 產出、使用者逐項核准):方案 B 全面轉移、
  Python 唯讀 API 資料層、純 Vite+React SPA、**bridge 核准為最小寫入例外**
  (使用者親定規格:四白名單操作、無任意 shell、PID ownership、重複啟動防護、
  localhost-only、audit log)、過渡期安全檢查 script;Streamlit 退役採並行觀察期;
  P3 寫入型功能各 gate 留待日後。拍板細節在 memory
  (`webui-migration-decisions`、`stage3-approval-pending-todos`)。
- **P0 完成**(engineering 實作):`webui/` 入 repo(範本剝離託管假設 grep 零命中、
  mock 清零、Vite SPA、npm audit 0 弱點)、`webui/scripts/bridge.mjs`(安全規格
  逐條測試 19/19)、`scripts/webui_security_check.py`(八項全 PASS)。實測發現:
  `hermes dashboard --status` 回報 PID 不可信(見 `webui/README.md`),bridge
  不依賴 CLI 狀態。使用者已目視驗收(iframe 內嵌 Hermes dashboard 正常)。
- 文件連動:roadmap 新增 Stage 5 節、stage3 提案加註載體變更。

## 3. 卡住/未決的問題

- **Stage 4 遺留**:`nous` token 撤銷待確認;「依任務類型自動選模型」規則引擎
  未實作。
- **07-19 排程首次自動觸發是否成功待確認**(Stage 2.7 後首次實跑驗證,見 auto-memory)。
- **Hermes UI 設定維護**:profile 建立與 Slack 頻道 allowlist 仍要手改
  config.yaml(未排程)。
- **Tavily key 明文存放待處理**(見 memory/hermes-tavily-key-plaintext-todo.md)。
- Windows repo 與 WSL 部署複本仍需 `scripts/sync_to_wsl.sh` 手動同步。
- (低優先)bridge 屬安全敏感變更,可考慮跑一次 `/code-review ultra` 補一道審查。

## 4. 下一步(可直接執行的第一步)

- **開工 Stage 5 P1**:依 [docs/webui-migration-proposal.md](docs/webui-migration-proposal.md)
  §4.2——Python 唯讀 API(包既有 `dashboard/data.py`)+ 新 UI 達成與 Streamlit
  五區塊功能對等;經使用者確認後分派 `engineering`。
- 次優先(可並行的小事):確認 `nous` token 是否已撤銷;確認 07-19 排程首次
  自動觸發結果。
