# STATUS — 現況快照

> **用途**:讓任何新 session(前台 Desktop 或 headless CoS)在 30 秒內跟上進度。
> **更新規則**:每次收工前更新本檔的四個區塊;只寫「現在是什麼、接下來做什麼」,
> 歷史細節與證據一律連結到權威文件(ROADMAP.md、docs/hermes-integration-roadmap.md、
> memory/),不在這裡重複展開。本檔永遠只反映「最新一次收工時」的狀態。

**最後更新**:2026-07-30

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
- **記憶生命週期已完整閉環(07-30 拍板+落地,提案正本
  [docs/memory-lifecycle-proposal.md](docs/memory-lifecycle-proposal.md) v1.1)**:
  進=bridge episode capture 擴充到 **named profile**(A1,已部署、cutover
  `2026-07-30T09:15:55Z`,tool 輸出縮減至 500 字元/則、敏感偵測在縮減前);
  用=recall 統計(`scripts/log_recall.py` → `logs/recall_log.jsonl`,決策
  程序步驟 1.5 埋點);升=高頻條目 MEMORY.md 索引分層;出=90 天零 recall
  自動歸檔 `memory/.archive/`(豁免:拍板決策/事故教訓/feedback;冷啟動
  保護=log 覆蓋滿 90 天前不汰選)。檢視掛既有 daily-memory-check,零新排程。
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

(2026-07-30,自上次快照 `565970c` 以來共 5 個 commit + 本收尾 commit,
皆已 push;主軸=named profile session 讀取實證 → 記憶生命週期拍板到
全面落地。)

- **named profile session 讀取路徑實證**:跨庫 FTS 定位使用者的 gptcoding
  Telegram 對話(「Gemini 即時翻譯字幕系統設計」,在 WSL 側主 state.db、
  `profile_name` 欄標記;live db 用快照複製避鎖),蒸餾入正本
  `memory/project_live-translate-vmix-caption-design.md`(`986bb1e`,標注
  設計討論尚未開工)。
- **記憶生命週期提案+四項拍板**(planning 起草,
  [memory-lifecycle-proposal](docs/memory-lifecycle-proposal.md) v1.1):
  汰選=**全自動歸檔**(豁免:拍板決策/事故教訓/feedback;冷啟動保護)、
  A1 核准+先查證(inactivity 72h 維持)、升格=C-a 索引分層、B1 recall
  log 核准(接受方向性下限)。關鍵盤點:default profile 的自動蒸餾**本來
  就存在**,真缺口=named 全漏/recall 零統計/記憶只進不出。
- **第一批落地**(`105eedb`):`scripts/log_recall.py`(11 測試)+決策程序
  步驟 1.5 埋點+`consolidation_policy.yaml` `retention:` 區塊+
  daily-memory-check prompt 第 4 步+SKILL.md retention 指引;已 sync 兩側。
- **A1 落地**(`55b6639`):bridge 三件組+adapter 多 profile 擴充——
  namespace `hermes/<profile>:`啟用(裸形式恆等 default 零 migration)、
  主 db 依 `profile_name` 分流+四顆 profile db 納掃、per-profile cutover、
  WAL symlink 修正、tool 縮減(500 字元/則,敏感偵測在縮減前)。
  查證發現:WSL `~/.hermes/state.db` 是 symlink 指向 Windows 主 db(兩側
  同一顆);lane session 83–94% 是 tool 輸出。
- **部署中抓到既有 bug 並修**(`b315d71`):reconcile 的 cursor_recovery
  是無條件空重放,健康與否無從分辨——改為只在 cursor 真落後時動作,
  「跑兩次第二次歸零」成為機械判準(WSL 實證通過)。
- **A1 上線**(`8860034`):runbook §7 步驟 1–9 全過,四 profile cutover=
  `2026-07-30T09:15:55Z`,兩側 dry-run 皆零歷史湧入(49 筆歷史 episode
  正確被擋)。
- 測試終值:hermes 483、scripts 107、皆綠。

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
- **lane session 觀測缺口(07-29 新增;07-30 已解一半)**:讀取路徑已
  實證(profile state.db 快照複製+FTS 搜尋,手動可挖)且 A1 上線後
  named session 會自動進記憶;剩 webui「Hermes Sessions」view 仍只讀
  預設 profile 的呈現層缺口。待議,優先級降。
- **A1 首輪觀察待做(07-30 新增,runbook §7 步驟 10)**:明日 08:05 首輪
  真實掃描後檢視 named episode 的 frontmatter(`hermes/<profile>:`
  event_id、`source_profile:`)與 consolidation 對 lane 雜訊(tool 縮減後)
  的抵抗力;不夠再議提案拍板項 5(摘要步)。
- **retention 冷啟動中**:recall log 自 07-30 起算,覆蓋滿 90 天(約 10 月底)
  前 retention review 只做升格不汰選——這是設計,不是故障。
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
- **A1 首輪觀察(明日 08:05 後的第一件事)**:見 §3「A1 首輪觀察待做」——
  下個 session 開場若已過首輪窗口,主動檢視並回報。
- **lane 通道已全通,可開始真實使用**:前台直接指定(「用 GPT 做 X」),
  Telegram 入口亦可;named profile 對話現在會自動進記憶(最壞 3 天),
  累積真實使用觀察,供日後「依任務類型自動選模型」規則引擎的設計依據。
- 次優先:拍板 telegram-cos-realtime-proposal(**併入 Telegram 出口格式
  缺口一起議**);排程表「未安裝→n/a」小措辭修正;bridge 三單元雙軌
  確認;env 變數移除(使用者手動)。
