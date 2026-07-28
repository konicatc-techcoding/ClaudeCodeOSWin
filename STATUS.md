# STATUS — 現況快照

> **用途**:讓任何新 session(前台 Desktop 或 headless CoS)在 30 秒內跟上進度。
> **更新規則**:每次收工前更新本檔的四個區塊;只寫「現在是什麼、接下來做什麼」,
> 歷史細節與證據一律連結到權威文件(ROADMAP.md、docs/hermes-integration-roadmap.md、
> memory/),不在這裡重複展開。本檔永遠只反映「最新一次收工時」的狀態。

**最後更新**:2026-07-28

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

(2026-07-27~28,自上次快照 `116c296` 以來共 4 個 commit + 本收尾 commit,
皆已 push。)

- **服務控制寫入部分核准+落地**(`65f53ca`):四項拍板(核准實作、宿主併
  bridge 8787 白名單第二群組、白名單僅 worker/telegram 兩 service 不含 timer、
  僅服務層級不做 `wsl --terminate`);§2.4 start blocker 經查證解除(keepalive
  只保 distro,不拉回被 stop 的服務),stop 語意定案並明示於 UI。主 session
  經 UI 真實 restart 驗證通過(二次確認 → exit=0 → audit → MainPID 320→597
  → 燈號收斂)。安全檢查新增第 12 項(18 條斷言)。
- **keepalive 三天靜默停擺被抓到+補強落地**(`872a666`):restart 驗證前置
  檢查發現 `HermesWslKeepAlive` 自 07-24 事故日起失效(Last Result=1,
  restart-on-failure 用盡放棄)——常駐燈號紅燈首次抓到真實事故。當日拍板
  三項(第一階段 A+B 核准 15 分鐘、第二階段 watchdog+toast 暫緩、
  `wsl --shutdown` 會被復活知情接受),主 session 親自執行 XML 重註冊並
  實測:`wsl --terminate` → tick 零人工介入復活整條鏈。**兩個 Task Scheduler
  實測教訓**(已記 README 與 auto-memory):LogonTrigger 的 Repetition 要等
  下次登入才上膛(backstop 必須掛 TimeTrigger);RestartOnFailure 不保
  `schtasks /run` 手動實例。
- **個別服務燈號 + sidebar UI 調整**(`5527ec4`):ServiceControl 每列加
  個別燈(與聚合燈同源共享 store,單一 30 秒輪詢實測無加倍;暖機黃燈繼承
  data_resident 判斷)、sidebar 灰字調亮、操作鍵改 inline-SVG 圖示
  (aria-label 全文保留,二次確認維持全文字)。
- **總覽頁 systemd 誤報修正**(`2db94c3`):根因=`get_systemd_status()` 跑
  裸 `systemctl`,Windows 側必失敗 → 全顯「未安裝」。新增
  `dashboard/data_systemd_wsl.py`(複用 `_distro_state()` 守門不喚醒 distro、
  凍結常數、三分支誠實狀態);timers 改 `--output=json`(文字版右對齊單空格
  會把 2+ 空格切欄切壞——教訓已固定為負面測試)。順手修掉
  `includes("active")` 誤判 `inactive` 的舊 bug。實測總覽四卡全「運作中」、
  排程表三 timer 真實觸發時間。
- 測試終值:dashboard 218(+21)、webui 125/125、安全檢查 12/12。

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
  (`/cos` 混合模式)。
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
  `ANTHROPIC_API_KEY`(防重撿);`OPENROUTER_API_KEY` 為 nemocoding 必需不可移除。
- UI 欄位名稱可能再調整(使用者提出後隨時小改)。
- 舊項沿用:07-19 排程首次自動觸發結果待確認;「依任務類型自動選模型」規則引擎
  未實作;Hermes UI 設定維護(profile/allowlist 手改 config.yaml);Tavily key
  明文存放;WSL 部署複本需 `scripts/sync_to_wsl.sh` 手動同步。
- (低優先)bridge/PTY 屬安全敏感面,可考慮 `/code-review ultra` 補一道審查。

## 4. 下一步(可直接執行的第一步)

- **日常實際使用新 UI**(`webui/` 下 `npm run local` + `readonly-api`),
  累積觀察期經驗;觀察期滿拍板 Streamlit 退役。「Hermes 更新」頁 backup 組
  亮橙=有客製沒 push,立刻處理;sidebar 常駐燈紅=先查
  `schtasks /query /tn HermesWslKeepAlive`(自癒最壞 15 分鐘,超過就是
  WSL 本身的問題)。
- 次優先:排程表「未安裝→n/a」小措辭修正;拍板
  telegram-cos-realtime-proposal 或確認 bridge 三單元雙軌問題;
  env 變數移除(使用者手動);確認 07-19 排程首次自動觸發結果。
