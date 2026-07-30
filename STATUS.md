# STATUS — 現況快照

> **用途**:讓任何新 session(前台 Desktop 或 headless CoS)在 30 秒內跟上進度。
> **更新規則**:每次收工前更新本檔的四個區塊;只寫「現在是什麼、接下來做什麼」,
> 歷史細節與證據一律連結到權威文件(ROADMAP.md、docs/hermes-integration-roadmap.md、
> memory/),不在這裡重複展開。本檔永遠只反映「最新一次收工時」的狀態。

**最後更新**:2026-07-29

---

## 1. 目前所在階段

- **Stage 5(Web UI 遷移)功能面全部完成:P0–P3 四個 phase 皆已交付並經使用者驗收**。
  新 Web UI(`webui/`,Vite+React)已可用:總覽/ClaudeCode CLI(PTY 終端機)/Jobs/
  成本/Memory/Logs/Hermes Sessions/憑證·Lane 狀態/Hermes Dashboard,搭配唯讀 API
  (`dashboard/api.py`,127.0.0.1:8799)與 PTY server(127.0.0.1:8801)。
  提案正本:[docs/webui-migration-proposal.md](docs/webui-migration-proposal.md)、
  [docs/webui-pty-terminal-proposal.md](docs/webui-pty-terminal-proposal.md)(皆已核准並實作)。
  另已追加兩個唯讀觀測面:**WSL 常駐服務狀態燈**(`/api/resident-status`,
  含個別服務燈號)與**Hermes 更新升級預檢**(`/api/update-precheck`,雙基準、
  零執行鈕);以及**第三個寫入例外——服務控制鍵**(bridge 8787 白名單第二
  群組,僅 worker/telegram × start/stop/restart,已核准並經真實 restart
  驗證,提案正本
  [docs/webui-service-control-proposal.md](docs/webui-service-control-proposal.md) v1.1)。
  總覽頁/排程表的 systemd 欄位已改經 WSL 唯讀探測(`dashboard/data_systemd_wsl.py`),
  「未安裝」誤報已修正。
- **WSL keepalive 已補強自癒**:`HermesWslKeepAlive` 曾於 07-24~27 靜默停擺
  三天(常駐燈號首次抓到真實事故);現有 TimeTrigger PT15M backstop,死了
  最壞 15 分鐘自動拉回(實測零人工介入復活整條鏈)。提案正本
  [docs/wsl-keepalive-hardening-proposal.md](docs/wsl-keepalive-hardening-proposal.md) v1.1。
- **非 Claude lane 通道已於兩個入口端到端驗證有效(07-29)**:前台四條 lane
  皆通(gptcoding/nemocoding 實測);headless(Telegram 入口)經五層修復後
  首次完成真實非 Claude 任務(intelligence → `hermes-intelligence` lane →
  gpt-5.6-terra,產出已整併進 memory 正本)。架構拍板:**lane 憑證單一存放
  於 Windows 側 hermes,WSL 經 interop 呼叫 Windows hermes.exe**
  (`scripts/dispatch_domain.py` 平台感知解析 + wslpath 轉譯)。
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

(2026-07-29,自上次快照 `0e3761b` 以來共 4 個 commit + 本收尾 commit;
主軸=把「headless 用非 Claude 模型」從失敗修到端到端通,逐層共修五關。)

- **lane 通道前台實測**:gptcoding(gpt-5.6-sol,訂閱內 $0)與 nemocoding
  (nemotron 免費層)一次通;發現 `OPENROUTER_API_KEY` 實際依賴 profile
  credential pool 而非呼叫端 shell env。
- **headless 呼叫鏈五層修復**(`5ddd6e7`/`77de4a8` + WSL 側 settings):
  (1) intelligence.md 補 Bash 工具+跨平台路徑+route_model 過時描述校正;
  (2) WSL 部署複本過舊——親自跑 `sync_to_wsl.sh --apply`(停/啟服務由
  使用者經 webui 服務控制鍵操作,新功能首次實戰);(3) dispatch_domain
  hermes 解析加 `~/.local/bin` fallback(非 login shell PATH 問題);
  (4) **架構拍板:WSL 經 interop 呼叫 Windows hermes.exe**(憑證單一存放;
  平台感知解析+凍結 interop 路徑+wslpath 轉譯,WSL 實測 exit 0);
  (5) WSL 側 `.claude/settings.json` 加兩條最窄 Bash 白名單(僅
  dispatch_domain 入口;settings 為 sync 排除項,故直接改 WSL 側)。
  headless 誤診糾正兩則:WSL venv 其實是好的;「寫不進 memory 正本」是
  架構防線正常運作,不開權限。
- **sync script 順序缺口修正**(`36f2c58`):部署前 dry-run 抓到 Phase 1
  合併會把已 consolidation 的檔案重新塞回 Windows inbox——重排為先清理後
  合併,dry-run 輸出忠實等於 apply 行為。
- **Telegram 入口端到端里程碑 + 整併**(`3f11e3b`):使用者從 Telegram 重發
  原任務,intelligence 經 `hermes-intelligence` lane 以 gpt-5.6-terra 完成
  研究(12 次 API 呼叫,訂閱內 $0);knowledge 跑 consolidate-memory 把
  產出整併進正本(新 snapshot + skill-catalog reference,inbox 清空)。
- **過程中確認的 session resume 行為**:同 thread 24h 內 resume 同一
  session(`hermes/db.py`),bot 兩度以過時世界觀回覆——已列入 §3 待辦。
- 測試終值:scripts 96(dispatch_domain 51)、安全檢查 12/12。

## 3. 卡住/未決的問題

- **防重演有一個結構性弱點(持續性風險,務必知道)**:`reset --hard origin/main`
  是 no-op 的前提是 `main == origin/main`。**一旦有客製 commit 沒 push 到私有
  備份,桌面 Install 鈕就會吃掉它們**——中和繫於「每次客製後都要 push」的人為
  紀律,不是結構保證。升級預檢已會在 `ahead > 0` 時亮橙告警。
- **更新按鈕階段二(寫入型 ff 執行鈕)維持延後,但已解開一半**:宿主依賴
  (service-control 寫入部分)已落地。剩兩個 blocker:(a) **沒有測試案例**
  ——需先讓 Windows 側受控 merge 官方一次(現落後 295,獨立高風險作業);
  (b) 白名單**不含 timer**(07-27 拍板),階段二要用 timer 需回
  service-control 提案擴充重審。
- **一份草案待拍板**:[telegram-cos-realtime-proposal](docs/telegram-cos-realtime-proposal.md)
  (`/cos` 混合模式)。**建議議程順帶納入 07-29 新發現的 Telegram 出口
  格式缺口**(見下項)。
- **Telegram 出口格式缺口(07-29 新增)**:CoS 輸出 GitHub markdown,
  Telegram 不渲染,使用者看到原始 `##`/`**` 符號。修法兩層次:便宜版=
  invoke 包裝層對 Telegram 來源任務指示純文字輸出;完整版=adapter 端
  markdown→Telegram HTML 轉換。建議併入 telegram-cos-realtime-proposal 拍板。
- **headless session 記憶無失效機制(07-29 新增,已兩次實際發作)**:
  同 thread 24h 內 `--resume` 同一 session(`hermes/db.py` sessions 表),
  環境在 session 外被修復後,舊 session 仍自信輸出過時結論(bot 兩度以
  已修復/已刪除的舊狀態回覆)。修法方向:Telegram 端 `/reset` 指令清
  thread session、或環境變更時主動失效。待議。
- **lane session 觀測缺口(07-29 新增)**:webui「Hermes Sessions」view
  只讀預設 profile,**看不到 lane profile(intelligence/gptcoding 等)的
  session**;lane 執行的完整 transcript 在
  `%LOCALAPPDATA%\hermes\profiles\<name>\` 的 state.db,目前無現成觀測
  介面(envelope result 與 usage.json 可得,中間過程要現場挖)。待議。
- **keepalive 第二階段(watchdog+toast)拍板暫緩**:殘餘風險=WSL 本身壞掉
  時 tick 靜默重試,只剩 webui 紅燈被動面(知情接受;真發生再升級)。
- **bridge-scanner/pipeline/notifier 的 systemd 單元**:repo 有、WSL 沒裝
  (實際跑 Windows Task Scheduler)——排程表顯示「未安裝」是真實狀態。
  是刻意雙軌還是遺留待確認,順帶決定 repo 內三個 unit 檔去留。
- **排程表小措辭**:未安裝列的觸發欄顯示「無法查詢」,嚴格應為 `n/a`
  (沒裝不是查不到)。一句話可修,未排。
- **升級預檢的兩個已知邊界**(皆為取捨非疏漏,已記於提案與 docstring):
  (a) §10.1 燈號盲點——WSL 的 `origin`(本機路徑)被判 `peer` 不計入整體燈,
  「WSL 有沒有跟上 Windows」這條反而不驅動燈號,需新增 role 才能修,待獨立評估;
  (b) live 版本字串取自 HEAD 的 `pyproject.toml`,**不涵蓋「merge 後忘記重跑
  `pip install -e`」**的依賴落後情形,要涵蓋需另案做「依賴同步狀態」欄位。
- **Streamlit 退役決策**:待並行觀察期(2026-07-24 起)滿一個自然使用週期後拍板。
- **env 變數清理待使用者手動**:建議移除 `GEMINI_API_KEY`/`GOOGLE_API_KEY`/
  `ANTHROPIC_API_KEY`(防重撿)。`OPENROUTER_API_KEY`:07-29 實測 shell
  unset 時 nemocoding 仍成功(key 已在 profile credential pool),「不可
  移除」可能可放寬——但 pool 的 source 標記仍是 `env:OPENROUTER_API_KEY`,
  未確認 pool 是否會回頭讀 env 前,先保守不動。
- UI 欄位名稱可能再調整(使用者提出後隨時小改)。
- 舊項沿用:07-19 排程首次自動觸發結果待確認;「依任務類型自動選模型」規則引擎
  未實作;Hermes UI 設定維護(profile/allowlist 手改 config.yaml);Tavily key
  明文存放;WSL 部署複本需 `scripts/sync_to_wsl.sh` 手動同步(07-29 已實跑
  兩輪,流程順;注意 `.claude/settings*.json` 是 sync 排除項,WSL 側權限
  白名單要單獨維護)。
- (低優先)bridge/PTY 屬安全敏感面,可考慮 `/code-review ultra` 補一道審查。

## 4. 下一步(可直接執行的第一步)

- **日常實際使用新 UI**(`webui/` 下 `npm run local` + `readonly-api`),
  累積觀察期經驗;觀察期滿拍板 Streamlit 退役。「Hermes 更新」頁 backup 組
  亮橙=有客製沒 push,立刻處理;sidebar 常駐燈紅=先查
  `schtasks /query /tn HermesWslKeepAlive`(自癒最壞 15 分鐘,超過就是
  WSL 本身的問題)。
- **lane 通道已全通,可開始真實使用**:前台直接指定(「用 GPT 做 X」),
  Telegram 入口亦可;累積幾次真實 lane 任務的品質觀察,供日後「依任務
  類型自動選模型」規則引擎的設計依據。
- 次優先:拍板 telegram-cos-realtime-proposal(**併入 Telegram 出口格式
  缺口一起議**);排程表「未安裝→n/a」小措辭修正;bridge 三單元雙軌
  確認;env 變數移除(使用者手動)。
