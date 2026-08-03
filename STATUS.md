# STATUS — 現況快照

> **用途**:讓任何新 session(前台 Desktop 或 headless CoS)在 30 秒內跟上進度。
> **更新規則**:每次收工前更新本檔的四個區塊;只寫「現在是什麼、接下來做什麼」,
> 歷史細節與證據一律連結到權威文件(ROADMAP.md、docs/hermes-integration-roadmap.md、
> memory/),不在這裡重複展開。本檔永遠只反映「最新一次收工時」的狀態。

**最後更新**:2026-08-04

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
  「未安裝」誤報已修正。**觀測面 stack 有一鍵啟動器**
  (`scripts/start_webui_stack`,冪等,桌面捷徑「AgentOS WebUI」),總覽頁
  頂部並排顯示服務控制(WSL systemd)與本機服務(8787/8801/5173/8799)
  狀態卡——開機後點捷徑即全套就緒(背景鏈本來就自動)。
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
- **升級預檢的 §10.1 燈號盲點已修(08-03,含追加拍板選項 b)**:新增 `follow`
  role(WSL origin=本機 Windows 路徑經正規化逐字比對才判定),「WSL 有沒有
  跟上 Windows」計入且**獨力驅動** WSL 整體燈(同 target 存在 follow 時
  upstream 降為僅供參考——per-topology 規則非硬編,拓撲變動自動回退)。
  三態語意:跟上=綠/落後 Windows=藍「該同步了」/領先或分歧=橙「異常」。
  live 驗證通過:WSL 卡片綠、主導標記在 follow 組、Windows 卡片零變化。
  正本:[webui-update-button-proposal.md](docs/webui-update-button-proposal.md) §10.1。
- Stage 3 四條 DoD 已透過 Stage 5 P2 在新載體達成;階段全貌見
  [docs/hermes-integration-roadmap.md](docs/hermes-integration-roadmap.md)。

## 2. 上一個 session 做了什麼

(2026-08-03~04,自上次快照 `d2007a5` 以來即本收尾 commit;主軸=升級預檢
follow role 落地(§10.1 燈號盲點修復)+ 0.19.1 受控 merge 定案並啟動。)

- **follow role 兩輪交付(engineering 分派,同一 agent 延續)**:
  第一輪=新增 `follow` role 偵測(`dashboard/data_update.py` 的
  `_canonical_path_key()`/`_is_windows_repo_url()`:remote URL 正規化後與
  `%LOCALAPPDATA%\hermes\hermes-agent` 逐字相等才判定,認不出退 `peer`
  安全側);第二輪=拍板**選項 b**——同 target 存在 follow 組時 upstream
  降為僅供參考(`_apply_follow_demotion()`,per-topology 非硬編),WSL
  整體燈由 follow 獨力驅動。前端僅型別/註解/fixture 動,顯示邏輯零改
  (本來就吃後端欄位)。
- **兩輪 live 驗證皆通過**(重啟 8799 經 launcher 冪等補缺):WSL 卡片
  綠「已是最新」、advice=「已跟上 Windows 整合 tip ✓」、主導標記在
  follow 組、官方組標「僅供參考」;Windows 卡片逐項零變化。
- **邊角決定(已追認)**:follow 組存在但 ref 查不到(灰)時仍降級
  upstream ⇒ 整體=灰(查詢失效本身要示警,不讓恆橙蓋掉)。
- 測試終值:dashboard 86(+23 vs baseline 63)、webui 137(+6)、
  typecheck 乾淨、security_check 12/12,零新增失敗,全程沙箱。
- **0.19.1 升級路線拍板**:不做按鈕(diverged repo 無一鍵更新,07-24
  教訓)、走受控流程五步——wrapup → engineering 隔離 worktree merge
  (產計畫文件,不碰 live)→ 使用者核准 → 主 session live 切換 →
  **立即 push 私有備份** → WSL ff-only。此次同時是階段二 blocker (a)
  的測試案例與 follow 燈號的首次實戰(merge 後 WSL 應轉藍、ff 後回綠)。

## 3. 卡住/未決的問題

- **防重演有一個結構性弱點(持續性風險,務必知道)**:`reset --hard origin/main`
  是 no-op 的前提是 `main == origin/main`。**一旦有客製 commit 沒 push 到私有
  備份,桌面 Install 鈕就會吃掉它們**——中和繫於「每次客製後都要 push」的人為
  紀律,不是結構保證。升級預檢已會在 `ahead > 0` 時亮橙告警。
- **更新按鈕階段二(寫入型 ff 執行鈕)維持延後,但 blocker (a) 正在解**:
  宿主依賴(service-control 寫入部分)已落地。剩兩個 blocker:(a) **沒有
  測試案例**——**0.19.1 受控 merge 已啟動(見 §4),完成後即有首個案例**;
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
- **A1 驗收已結案(08-03,紀錄正本=提案 §7)**:唯一留給自然流量的是
  首個多輪 named episode 落地檔的目視(render 有單測釘格式)。
- **白天手動跑 bridge 掃描可能撞 WAL**(08-02 觀察):忙時段 WSL 經
  /mnt/c 對主 db 快照會輸給高頻寫入(fail-loud 三次重試放棄,設計行為);
  每日 08:05 排程時段實績皆成功。排程時段也開始撞再議重試參數。
- **retention 冷啟動中**:recall log 自 07-30 起算,覆蓋滿 90 天(約 10 月底)
  前 retention review 只做升格不汰選——這是設計,不是故障。
- **keepalive 第二階段(watchdog+toast)拍板暫緩**:殘餘風險=WSL 本身壞掉
  時 tick 靜默重試,只剩 webui 紅燈被動面(知情接受;真發生再升級)。
- **bridge-scanner/pipeline/notifier 的 systemd 單元**:repo 有、WSL 沒裝
  (實際跑 Windows Task Scheduler)——排程表顯示「未安裝」是真實狀態。
  是刻意雙軌還是遺留待確認,順帶決定 repo 內三個 unit 檔去留。
- **排程表小措辭**:未安裝列的觸發欄顯示「無法查詢」,嚴格應為 `n/a`
  (沒裝不是查不到)。一句話可修,未排。
- **升級預檢的已知邊界**(取捨非疏漏,已記於提案與 docstring):
  (a) ~~§10.1 燈號盲點~~ **已修(08-03,見 §1)**;遺留一個小切片:follow
  橙態的 `light_text` 沿用共用表「帶客製 diverge——需人工受控 merge」,與
  follow 真正語意(WSL 不該有 Windows 沒有的 commit)不貼,需 per-role
  light_text 結構小改,未排;
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

- **【進行中】0.19.1 受控 merge——第 2 步:engineering 隔離 worktree 調查**
  (`git fetch upstream` → 確認 0.19.1 tag → 隔離 worktree merge 客製 tip
  `970118870` × 上游、解衝突、沙箱全套測試 → 產計畫文件,仿
  [wsl-regraft-plan](docs/wsl-regraft-plan.md),不碰 live)。後續:使用者
  核准 → 主 session live 切換(安全 tag/停 gateway/reset/`pip install -e`/
  受控重啟/doctor+allowlist 負面+message-key 驗證)→ **立即 push 私有備份**
  → WSL `ff-only`+`pip install -e`(bot 不用停,07-25 實證)。過程中順帶
  驗收 follow 燈號三態(merge 後 WSL 藍 → ff 後綠)。
- **日常實際使用新 UI**(`webui/` 下 `npm run local` + `readonly-api`),
  累積觀察期經驗;觀察期滿拍板 Streamlit 退役。「Hermes 更新」頁 backup 組
  亮橙=有客製沒 push,立刻處理;sidebar 常駐燈紅=先查
  `schtasks /query /tn HermesWslKeepAlive`(自癒最壞 15 分鐘,超過就是
  WSL 本身的問題)。
- **A1 已驗收結案**:日常留意首個多輪 named 對話的 episode 落地檔
  (frontmatter/tool 縮減目視即可)。
- **launcher `-Restart` 選項待答**:若要「一鍵重啟整套 stack」,給
  launcher 加參數+第二個桌面捷徑即可(已向使用者提出,未答)。
- **lane 通道已全通,可開始真實使用**:前台直接指定(「用 GPT 做 X」),
  Telegram 入口亦可;named profile 對話現在會自動進記憶(最壞 3 天),
  累積真實使用觀察,供日後「依任務類型自動選模型」規則引擎的設計依據。
- 次優先:拍板 telegram-cos-realtime-proposal(**併入 Telegram 出口格式
  缺口一起議**);排程表「未安裝→n/a」小措辭修正;bridge 三單元雙軌
  確認;env 變數移除(使用者手動)。
