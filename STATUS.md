# STATUS — 現況快照

> **用途**:讓任何新 session(前台 Desktop 或 headless CoS)在 30 秒內跟上進度。
> **更新規則**:每次收工前更新本檔的四個區塊;只寫「現在是什麼、接下來做什麼」,
> 歷史細節與證據一律連結到權威文件(ROADMAP.md、docs/hermes-integration-roadmap.md、
> memory/),不在這裡重複展開。本檔永遠只反映「最新一次收工時」的狀態。

**最後更新**:2026-07-25

---

## 1. 目前所在階段

- **Stage 5(Web UI 遷移)功能面全部完成:P0–P3 四個 phase 皆已交付並經使用者驗收**。
  新 Web UI(`webui/`,Vite+React)已可用:總覽/ClaudeCode CLI(PTY 終端機)/Jobs/
  成本/Memory/Logs/Hermes Sessions/憑證·Lane 狀態/Hermes Dashboard,搭配唯讀 API
  (`dashboard/api.py`,127.0.0.1:8799)與 PTY server(127.0.0.1:8801)。
  提案正本:[docs/webui-migration-proposal.md](docs/webui-migration-proposal.md)、
  [docs/webui-pty-terminal-proposal.md](docs/webui-pty-terminal-proposal.md)(皆已核准並實作)。
  另已追加兩個唯讀觀測面:**WSL 常駐服務狀態燈**(`/api/resident-status`)與
  **Hermes 更新升級預檢**(`/api/update-precheck`,雙基準、零執行鈕)。
- **Streamlit 並行觀察期自 2026-07-24 起算**(觀察一個自然使用週期後決定退役;
  期間 `dashboard/app.py` 零改動、標 deprecated)。
- **Hermes-agent repo 兩側(Windows/WSL)已對齊且防重演已落地**:兩側 HEAD 皆
  `970118870`、逐 byte 相同,`origin` 各自指向私有備份/本機 Windows 路徑,
  使 `reset --hard origin/main` 退化為 no-op。詳見
  [docs/wsl-regraft-plan.md](docs/wsl-regraft-plan.md) 與 auto-memory
  `hermes-agent-repo-work`。
- Stage 3 四條 DoD 已透過 Stage 5 P2 在新載體達成;階段全貌見
  [docs/hermes-integration-roadmap.md](docs/hermes-integration-roadmap.md)。

## 2. 上一個 session 做了什麼

(2026-07-24~25,自上次快照 `9537575` 以來共 6 個 commit + 本收尾 commit。
前兩項是 07-24~25 中間 session 的成果,先前從未進入本快照,一併補記。)

**先前未記載的兩項**

- `3871129`:WSL 常駐服務狀態(`dashboard/data_resident.py` +
  `/api/resident-status`)與 webui 狀態燈;另產出兩份**待拍板**草案——
  [webui-service-control-proposal](docs/webui-service-control-proposal.md)(重啟/關閉鍵)、
  [telegram-cos-realtime-proposal](docs/telegram-cos-realtime-proposal.md)(`/cos` 混合模式)。
- `b81f54f`:**Hermes 更新升級預檢(階段一,唯讀)**——`dashboard/data_update.py`
  雙基準比較 + `/api/update-precheck` + webui「Hermes 更新」view(零執行鈕),
  並落地 2026-07-24 updater 事故的 firebreak(私有備份 repo、remote 重整)。
  提案正本:[docs/webui-update-button-proposal.md](docs/webui-update-button-proposal.md)。

**本次 session**

- **WSL hermes-agent re-graft**(`654242d`/`7599d99`):調查證明 WSL **零獨有
  commit**,故採方案 A 直接對齊而非 merge(merge 會讓「兩側一致」從可證明退化
  成靠人相信)。主 session 親自執行,約 12 分鐘、**Telegram bot 停機 0 分鐘**;
  驗證 **V1–V15 全過**(含 Slack allowlist 負面 fail-closed、ledger 冪等)。
  計畫與執行紀錄:[docs/wsl-regraft-plan.md](docs/wsl-regraft-plan.md)。
- **07-24 事故沙箱清理**(非 repo 變更):`hermes-remerge-wt`(git worktree,
  已 `worktree remove`)+ venv + temp home,回收約 265MB;branch
  `integration/v0.19.0-custom` 刻意保留為歷史錨點。
  工具面教訓:Windows 清 Python venv 遇 `.pyd` 卡住(image section),
  PowerShell `Remove-Item` 刪不掉但 Git Bash `rm` 可以。
- **更新按鈕提案校正至 v1.1 + 階段一補完**(`11ecbcc`):經證據鏈查明
  **07-24 事故的觸發源不是自動排程,是 Hermes Desktop in-app updater 的
  Install 鈕(GUI 一鍵)**——這推翻提案原本的前提,並改變防重演的設計目標
  (不是停掉排程,而是讓那顆鈕即使被按也不會毀)。同時修正端點/模組名、
  停機面(4 個觸發面而非 3 個)、新增「中和失效條件」一節。
  階段一補上 live 版本字串(`v0.19.0 upstream 3910ab28 + local 97011887
  (+12 carried commits)`),來源全走唯讀 git,**刻意不執行 hermes CLI**
  (其 banner 會寫 `.update_check`,會把「看狀態」變成「動狀態」),
  並以 security check 判準鎖死這條捷徑。測試:dashboard 107、webui 92/92、
  安全檢查 11/11。

## 3. 卡住/未決的問題

- **防重演有一個結構性弱點(持續性風險,務必知道)**:`reset --hard origin/main`
  是 no-op 的前提是 `main == origin/main`。**一旦有客製 commit 沒 push 到私有
  備份,桌面 Install 鈕就會吃掉它們**——中和繫於「每次客製後都要 push」的人為
  紀律,不是結構保證。升級預檢已會在 `ahead > 0` 時亮橙告警。
- **更新按鈕階段二(寫入型 ff 執行鈕)已拍板延後**,卡在兩件事:
  (a) **沒有測試案例**——07-25 re-graft 後 WSL 對 `origin/main` 是 `0/0`,
  原定首測場景「WSL 落後、ff 追 Windows」已不存在;要有用例需先讓 Windows 側
  受控 merge 官方一次(現落後 295),那是獨立的高風險作業。
  (b) **宿主與白名單未拍板**——推薦掛在 `webui-service-control-proposal` 的
  寫入部分,但該案仍待核准,且其建議白名單**不含 timer**,與階段二需要操作
  timer 直接衝突,**兩案白名單範圍必須合併拍板**。
- **兩份草案待拍板**:[webui-service-control-proposal](docs/webui-service-control-proposal.md)
  (服務重啟/關閉鍵)、[telegram-cos-realtime-proposal](docs/telegram-cos-realtime-proposal.md)
  (`/cos` 混合模式)。
- **升級預檢的兩個已知邊界**(皆為取捨非疏漏,已記於提案與 docstring):
  (a) §10.1 燈號盲點——WSL 的 `origin`(本機路徑)被判 `peer` 不計入整體燈,
  「WSL 有沒有跟上 Windows」這條反而不驅動燈號,需新增 role 才能修,待獨立評估;
  (b) live 版本字串取自 HEAD 的 `pyproject.toml`,**不涵蓋「merge 後忘記重跑
  `pip install -e`」**的依賴落後情形,要涵蓋需另案做「依賴同步狀態」欄位。
- **Streamlit 退役決策**:待並行觀察期(2026-07-24 起)滿一個自然使用週期後拍板。
- **env 變數清理待使用者手動**:建議移除 `GEMINI_API_KEY`/`GOOGLE_API_KEY`/
  `ANTHROPIC_API_KEY`(防重撿);`OPENROUTER_API_KEY` 為 nemocoding 必需不可移除。
- 本地 master 領先 origin **11 個 commit**(不含本收尾),**尚未 push**(需使用者確認)。
- UI 欄位名稱可能再調整(使用者提出後隨時小改)。
- 舊項沿用:07-19 排程首次自動觸發結果待確認;「依任務類型自動選模型」規則引擎
  未實作;Hermes UI 設定維護(profile/allowlist 手改 config.yaml);Tavily key
  明文存放;WSL 部署複本需 `scripts/sync_to_wsl.sh` 手動同步。
- (低優先)bridge/PTY 屬安全敏感面,可考慮 `/code-review ultra` 補一道審查。

## 4. 下一步(可直接執行的第一步)

- **日常實際使用新 UI**(`webui/` 下 `npm run local` + `readonly-api`),
  在觀察期內累積使用經驗,發現問題隨時回報修正;觀察期滿拍板 Streamlit 退役。
  新增的「Hermes 更新」頁可順帶當防重演的偵測面——若哪天 backup 組亮橙,
  代表有客製沒 push,那正是需要立刻處理的訊號。
- 次優先:push 到 origin(待確認,現領先 11+);兩份草案擇一開始拍板
  (建議先 service-control,因為它同時解開更新按鈕階段二的宿主依賴);
  env 變數移除(使用者手動);確認 07-19 排程首次自動觸發結果。
  (nous 已結案:使用者 2026-07-24 拍板從未使用過,本機清除即可,不追服務端撤銷。)
