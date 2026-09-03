# STATUS — 現況快照

> **用途**:讓任何新 session(前台 Desktop 或 headless CoS)在 30 秒內跟上進度。
> **更新規則**:每次收工前更新本檔的四個區塊;只寫「現在是什麼、接下來做什麼」,
> 歷史細節與證據一律連結到權威文件(ROADMAP.md、docs/hermes-integration-roadmap.md、
> memory/),不在這裡重複展開。本檔永遠只反映「最新一次收工時」的狀態。

**最後更新**:2026-09-03

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
- **Streamlit 已於 2026-08-15 退役(`55e249d`)**:`dashboard/app.py`/`test_app.py`
  移除、streamlit 相依與 8501 launch 設定移除,**新 Web UI 是唯一觀測面**;
  Stage 5 至此無任何剩餘事項。資料層(`dashboard/data*.py`/`api.py`)全數保留。
- **Roadmap 上 Stage 0–5 全數關閉**,目前沒有「開工中」的階段;剩下的是階段
  遺留與待拍板議題(見第 3 節),**已有帶優先序的開工順序**(2026-09-03 由 `planning` 產出,
  補掉 08-15 起掛著的議程項):清場(0) → 止血(1) → 校正脈絡(2) → 規則引擎(3)
  → 集中拍板(4) → 執行(5) → 收尾(6),序列執行。
- **Hermes-agent 兩側已升 v0.19.1 且對齊(08-04,首次完整走受控升級流程)**:
  兩側 HEAD 皆 `aa65ff286`(= 客製 tip × 官方 tag `v2026.7.30` 受控 merge,
  engineering 隔離 worktree 產出),live 驗證全過(doctor/allowlist 負面
  fail-closed/message-key dedup/multiplexer 5 profiles),main+整合 branch+
  rescue tag `rescue/pre-0191-20260803` 皆已 push 私有備份,防重演基準回位。
  WSL ff-only 同步、服務零停機。順帶修掉 live 現存 Slack 帶附件送出必炸 bug。
  計畫正本 [docs/hermes-0191-merge-plan.md](docs/hermes-0191-merge-plan.md)
  (含 §11 測試洩漏事故記錄);踩坑清單在 auto-memory `hermes-agent-repo-work`。
  **升級後穩態:Windows 對官方 orange(落後 934/帶 15 客製)屬預期常態**。
- **升級預檢的 §10.1 燈號盲點已修(08-03,含追加拍板選項 b)**:新增 `follow`
  role(WSL origin=本機 Windows 路徑經正規化逐字比對才判定),「WSL 有沒有
  跟上 Windows」計入且**獨力驅動** WSL 整體燈(同 target 存在 follow 時
  upstream 降為僅供參考——per-topology 規則非硬編,拓撲變動自動回退)。
  三態語意:跟上=綠/落後 Windows=藍「該同步了」/領先或分歧=橙「異常」。
  **三態已於 0.19.1 升級中實戰驗證(綠→藍→綠完整循環)**;橙態 badge 文字
  已 per-role 貼合(08-04 第二輪)。
  正本:[webui-update-button-proposal.md](docs/webui-update-button-proposal.md) §10.1。
- **觀測面兩項新能力(08-04)**:(1) **gateway pid 活性驗證**——常駐燈與預檢
  service 欄不再盲信 `gateway_state.json`,以 OpenProcess(QUERY_LIMITED)+
  `start_time` 指紋(與 hermes lifecycle ledger 同源)驗 pid 死活,fail-open
  防檢查器自身故障誤報;起因=gateway 曾死一天半而燈號一直顯示「就緒」的
  真實事故,現最壞 75 秒轉紅。(2) **〔重新整理遠端資訊〕fetch 按鈕**——
  第四個寫入例外(bridge 8787 第三群組):四條凍結 fetch 指令、零參數、
  無 --prune 純加法、per-remote fail-loud、audit 逐條;預檢 `?fresh=1`
  cache-bust,按完即見新數字。實按驗證四條全過。security_check 12→13 項。
- **升級流程已 skill 化(08-04 第三輪)**:`/hermes-upgrade` skill + 兩支冪等
  script(`scripts/hermes_apply_upgrade.ps1 -Tip <sha>` Windows live 切換
  八步全程含 S7 驗證與 push、`scripts/hermes_sync_wsl.ps1` WSL ff 跟上)。
  結構上只允許 ff(無 reset)、失敗停在原地印 rollback、冪等可重跑;pins
  維護在 `scripts/hermes_extra_pins.txt`、回歸測試 `scripts/tests/`(沙箱
  43 案例)。真實環境 `-DryRun` 試跑通過(零寫入、冪等 skip 正確)。
  下次升級=按 fetch 鈕 → 任一 session 說「升級 hermes」→ 兩次核准。
- **觀測面第三軸補齊(08-04/05):憑證頁的「模型軸」**。原本只有憑證軸
  (`auth.json`)與 named lane 模型軸(`profiles/*/config.yaml`),**全域/default
  的模型設定沒有任何欄位照得到**——換 default 模型後整個 UI 零變化。現在憑證
  治理頁每列(含 `(global-root)`)並列兩軸:生效 provider/model/來源
  (`_effective_model_fields()` 單一判定處,lane 表改呼叫同一支)+ 交叉檢查燈。
  欄位正名「憑證 provider」vs「生效模型 provider」——**撞名正是誤解成因**。
  **四燈語意(橙 > 黃 > 綠,gray=略過檢查)**:橙=生效 provider 在該 store 憑證
  條目數為 0(可能靠環境變數)、黃=有條目但配額耗盡(暫時、會自癒)、綠=正常。
- Stage 3 四條 DoD 已透過 Stage 5 P2 在新載體達成;階段全貌見
  [docs/hermes-integration-roadmap.md](docs/hermes-integration-roadmap.md)。

## 2. 上一個 session 做了什麼

(2026-09-03,單一 session,主題是**系統面盤點與開工順序規劃**——規劃層推進一大格,
執行層零推進。本次唯一的檔案變更是 `memory/` 兩個檔;webui 那四項未 commit 變更
第四次刻意未帶入。)

### 系統面完整盤點(28 項,分六類)

- 依第 3 節與**實際開檔核對 ROADMAP.md** 產出:需拍板 7、可直接動手 11(合計約半天)、
  結構性風險 4、觀察待觸發 5、文件過時 1 份、工作樹雜項 2。
- **核對出 ROADMAP.md 四處與現實脫節**:開頭仍寫「Windows 側 bridge／Task Scheduler
  是未來選項、**尚未實作**」(實際已上線數月且是主要 runtime,最會誤導新 session 的一句)、
  Milestone 表停在 `v0.1-beta`(2026-07-04)、「下一步」段落過時、「已知技術債」四項從未複查。
- 判讀:**系統面沒有任何「做到一半」的實作,全部卡在決策層**。

### 開工順序規劃(planning 產出)

- 七批次序列,核心判斷是 **A 類 7 項不該是 7 個 session**,而是 1 個起草 + 1 個集中拍板。
  拍板排第四不是拖延——前三批要先給它乾淨的工作樹、正確的 runtime 脈絡與草案。
- **建議 6 項直接結案**(28→22,尚未生效,要等批次 4 拍板):更新按鈕階段二判死
  (0.19.1 實戰已否證其價值前提)、Lane `provider` 正名改為文件註記、黃燈精準度降級為
  觀察項、lane session 觀測缺口下半關閉、keepalive watchdog 補「三個月內再發生一次
  WSL 靜默死亡即升級」的重啟條件、白天撞 WAL 關閉待辦。
- 產出兩份規劃 artifact(盤點清單、開工順序),**不在版控內**。

### 模型路由脈絡包落地(knowledge)

- `memory/hermes-task-category-model-routing-preference.md` 由 14 行擴為約 190 行
  (+214/-6):18 條既有決策表(「什麼情況 → 選了什麼 → 為什麼」)、現況機制實際值、
  額度約束的「寫在哪裡／機器可讀與否」判定、三個現存陷阱、15 項脈絡缺口。
- 四個最有價值的判斷寫在檔案最前面:**既有決策已經是事實上的規則**(草案該形式化而非
  另起爐灶)、**D8 是唯一「按任務性質選模型」的先例且不應假設可推廣**、**D18「不強制
  路由」與自動選模型引擎有直接張力**、**額度約束全無機器可讀形式**(配額耗盡的既有
  處理方式實質上就是使用者當場手改 `config.yaml`)。
- `memory/MEMORY.md` 索引同步一行,把三個警訊直接放進索引本身,讓下次 recall 掃索引即可見。

### ★ Phase 2f 查證(engineering 唯讀)——推翻了一個假前提

- **「Phase 2f」在專案裡沒有權威定義**。它是 07-20/21 一次長 session 事後貼上的臨時編號,
  **在三份文件裡指三件不同的事**(`capability_lanes.yaml:298` financialresearch 憑證清理／
  roadmap:659 lane 轉 active+補文件／使用者自己註解的「讓 subagent 真正呼叫」)。
  `Phase 2c` 全 repo 零命中;編號從未進過任何 commit message。
- **前置條件後半明確不成立**:`logs/dispatch_domain/` 共 14 筆、成功率 11/14、
  **最後一筆 2026-08-03,至今整整一個月零執行**、**14 筆全部帶明確 `--lane` override,
  自動選路徑一次都沒被走過**、全部是驗證/測試性質,沒有一筆是自然發生的日常任務。
- **真正的死結是循環依賴**:「工具存在但沒人用」從來沒被排進任何 phase;要累積使用經驗
  得先有「什麼情況該走 lane」的判準,而那正是規則引擎本身。
- 附帶澄清:`dispatch_domain.py` docstring 的「不被 CoS／worker.py 呼叫」**今天依然
  字面為真**(全 repo 無自動呼叫點),與 STATUS 的「端到端驗證有效」不矛盾——兩者描述
  不同層次(人工觸發跑得通 vs 沒有自動呼叫點)。

## 3. 卡住/未決的問題

- **★ 模型路由規則引擎:兩個必須先拍的板(09-03 新增,阻斷批次 3)**:
  (a) **循環依賴**——「工具存在但沒人用」從未被排進任何 phase,要累積使用經驗得先有
  判準,判準就是引擎本身;**不該再等一個不存在的 Phase 2f 里程碑**。
  (b) **規則是建議還是強制**——2026-07-23 已拍板「Hermes = opt-in 執行後端,**不強制
  路由**」,一個「自動選」的引擎要不要覆蓋這條,尚未拍板。這一題會反過來改寫其他議題
  的形狀,**必須先於批次 4 的其他議程**。脈絡正本已備齊在
  `memory/hermes-task-category-model-routing-preference.md`。
- **`scripts/dispatch_domain.py` docstring 措辭過時(09-03 新增)**:檔頭「接線是
  Phase 2」與第 2 行「v0.1(Phase 1)」標籤從未更新——2a–2h 全做完,卻沒有一個 phase
  真的做接線。建議併入批次 2(脈絡校正)與 ROADMAP 一起修。
- **phase 編號體系本身不可信(09-03 新增)**:2a–2h 是流水帳式事後編號,`2c` 不存在、
  `2e`/`2f` 一號多義、從未進 commit message。**日後引用 phase 編號前先查證語意**。

- **★ cron + claude_cli 至今仍未驗收(09-02、09-03 兩次排程皆已跑過,無人確認)**。憑證解析已唯讀查證為高信心,
  但真實 cron 環境沒跑過。**若失敗**:錯誤在 `AIChainOrchestrator\logs\<run_id>_aichain_claude_daily_auto.log`
  的 stderr;**回滾一行**——`AIChainClaude\00_CONFIG\claude_provider.yaml` 改回
  `provider: anthropic_api`,立即生效不必重啟。
- **故障模式已改變**:分析失敗的原因從「API 回錯誤」變成「**訂閱額度不足**」。
  **在 08:00 前後大量使用 Claude Code 會跟晨報搶額度。**(本次 session 就撞到兩次用量上限。)
- **美股報價日期錯亂 bug(已診斷、使用者決定先不修)**:Yahoo 會回傳 `close: null` 的交易日 bar,
  而 `market_context_collector.py:248-253` 濾掉 null 後**用位置取值**,整個交易日無聲消失。
  三個獨立錯位放大它:`market_time` 來自 `meta` 不跟著位移、`volume` 又是第三套過濾
  (實測 08-31 的 NVDA 是「週四價格 + 週五成交量 + 週五時間戳」)。**影響 `us_ai_chain`/
  `tw_previous_close`/`macro` 三個 bucket**,09-01 報告的「費半重挫、Marvell 暴跌 12.34%」
  方向是錯的(實際 NVDA +1.49%、MRVL −2.29%)。**是既有 bug 非近期引入**(備份檔有同一段邏輯,
  且備份 mtime 晚於出錯的執行)。⚠️ **若日後砍掉 RSS 蒐集,市場數據會成為唯一量化證據,
  這個 bug 就從技術債升級為上線阻斷項——兩個決定綁在一起。**
- **「ChatGPT 08:00 前產出」尚未驗證**:手上唯一樣本是 **12:25** 產出的(搜尋時間窗結束 12:24,
  內文有上午 11 點的新聞)。整個新架構壓在這個假設上。**連續觀察 3-5 天之前不應砍掉 RSS。**
- **Slack 投遞靜默失敗仍在**:`send_ai_report_to_slack()` 各失敗路徑只 `print(stderr); return`,
  外層 try/except 再吞一次,包裝腳本還硬編 `delivery: slack`。屬階段 3 範圍,本次未動。
- **`average_volume` 名不副實**:欄位名是三個月均量,實際填的是 5 日視窗前 4 根的平均。
  任何「爆量/量縮」判斷不可靠。長期存在,與本次改動無關。
- **待使用者決定的三個下市/不存在條目**:`Great_Wall`(名冊查無)、`SPIL`(矽品已併入日月光)、
  `Chilisin`(奇力新已併入國巨)。目前 0 命中無立即危害,建議移除但未動。
- **明天(08-28)第一次自動執行的三個觀察點**:① Telegram 那行 `sources: relay ?/? -
  feeds 9/23 - tavily ?` 是否正常出現(這會驗掉「wrapper end-to-end 未跑過真實 cron」);
  ② 18 筆入選裡有沒有出現散熱/電源/機構件/網通(**A10 的真正驗收**——查詢有了不等於
  進得了報告,18 個名額要競爭);③ **Slack `#ai-chainresearch` 實際有沒有收到**(系統不會
  主動告訴你,見 S1)。另 `original_relay_url` 欄位可在 `structured/20260828.json` 目視確認。
- **cron 這個月只跑了 14 天(應約 26 天)且無人察覺**,08-18→08-25 有整整一週空窗。
  今天做的告警只能報「跑了但品質差」,**「跑都沒跑」它不會出聲**——那要 B2 的新鮮度
  看門狗(獨立於這條鏈之外的檢查)。
- **使用者要求記下的未來規劃**:想試把分析改走 `claude_cli`(訂閱制)**看品質會不會更好**
  ——注意**動機是品質不是省錢**,故 08-27 那份以省錢為前提、結論「不建議改」的評估
  **不適用**。正本 `memory/project_aichain-claude-cli-provider-trial.md`,含技術前置事實
  與必踩的坑。等前置項目完成後再規劃;**與 B1 的先後順序需另外拍板**(先切 v2 會讓 schema
  更嚴格,屆時換 provider 的 JSON 通過率風險更高)。
- **AIChain 晨報:仍未修的缺陷(08-27 更新,已解決者已移除)**:
  - **A8 與 A12 有強制依賴,不可只做 A8**:Tavily 的觸發條件是「官方 feed 失敗或抓到
    0 筆」,所以**修好 23 條 feed 反而會讓全文覆蓋率從 6/18 掉到 3/18**。必須先讓
    Tavily 脫離 feed 健康度綁定。
  - ~~Tavily 全靜默失敗~~ → **2026-08-27 已解決**(蒐集層健康度告警)。**但雙 key 輪替
    尚未實作**——使用者有兩把 1000/月 的 key,目前 code 只讀一把、無輪替邏輯,也未確認
    兩把是否同帳號共用額度池。
  - **Slack 送達無驗證**(A3):投遞失敗被 try/except 吞掉,包裝腳本還硬編
    `print("delivery: slack")`。Telegram 說 completed ≠ 報告有送到。
  - **`thesis_updates` 的信心變化反映抽樣而非世界**:08-25 與 08-26 入選 18 筆的
    **URL 交集 = 0**,樣本 100% 不重疊;而本該解決此事的 `thesis_registry` 是空的
    (只由 v2 的 `thesis_memory_updates` 填充,cron 走 v1)。在 B1 切 v2 前,「二階效應」
    段的信心上調/下調只能當敘事語氣讀。
  - **`categories.yaml` 6 條公司↔產業對應寫錯**(榮成=紙器包裝、台光電=CCL、華新科=
    MLCC、世芯/創意=ASIC、南亞科=DRAM、錸德=光碟片),年份寫死 2024/2025,缺
    GB300/Vera Rubin 與奇鋐/雙鴻/台達電/日月光等主力標的。這是報告裡「標籤抽取錯誤」
    的真正源頭。
- **稽核 artifact 有一處錯誤待更正**:B3 段寫「relay URL 解析只成功 3/18,是既有能力
  沒發揮」——實際上**該功能從未存在**(`resolved_count` 只是在數有幾筆不是轉址)。真正
  的槓桿是 `content_enricher`(對直連 100% 成功、對 Google News 轉址 100% 失敗)。

- **防重演有一個結構性弱點(持續性風險,務必知道)**:`reset --hard origin/main`
  是 no-op 的前提是 `main == origin/main`。**一旦有客製 commit 沒 push 到私有
  備份,桌面 Install 鈕就會吃掉它們**——中和繫於「每次客製後都要 push」的人為
  紀律,不是結構保證。升級預檢已會在 `ahead > 0` 時亮橙告警。
- **遺留/待拍板議題清單(08-15 盤點;09-03 已由 `planning` 排成帶優先序的七批次順序,
  本項僅留作索引)**:
  待拍板=telegram-cos-realtime 草案(併 Telegram 出口格式缺口)、headless session
  記憶失效機制、更新按鈕階段二「還做不做」、launcher `-Restart`、「依任務類型
  自動選模型」規則引擎(需 planning 起草);小修=lane session 觀測缺口下半、
  預檢「依賴同步狀態」欄位、排程表 `n/a` 措辭、憑證頁三燈 CSS、bridge 三單元
  雙軌確認;文件=根 ROADMAP.md 里程碑/技術債過時。
  (**「Stage 0.5 殘項是否早已過時」已於 09-03 查證後結案移除**——三項殘項全數過時:
  ① financialresearch 自啟腳本→`multiplex_profiles: true`,named profile 不再各自起
  gateway,腳本本身無存在意義;② 三個殭屍 Startup vbs→兩個 Startup 目錄實地檢查
  已無任何 vbs;③ sticky profile 緩解→唯一的自動化呼叫路徑 `scripts/dispatch_domain.py`
  已硬性帶 `--profile`(:598,docstring:18 明文禁止依賴 sticky),慣例另記於
  `memory/hermes-profile-sticky-vs-ephemeral.md`。)
  各項細節仍散在下方條目,清單只是索引。
- **`gptcoding` 憑證 `last_status` 仍顯示 `exhausted`(last_refresh 08-02)**:
  該欄只在 lane 被實際呼叫時更新,不代表配額仍耗盡;下次 gptcoding lane 真實
  呼叫成功即自癒、憑證頁黃燈退綠。不需動作,只是別誤讀。
  (**觸發:下一次真實呼叫 gptcoding lane 時順帶確認**——若呼叫成功但 `last_status`
  仍是 `exhausted`,那就不是「設計如此」而是欄位更新有 bug,改開 engineering 修;
  若在 2026-12-31 前都沒有任何真實呼叫發生,直接結案刪除本項——一條一整季沒人用的
  lane,它的憑證燈號本來就不值得追。)
- **`dashboard/data_stage3.py:76` 裸 `import data_systemd_wsl`**(08-15 engineering
  附帶觀察):從 repo 根 `import dashboard.data_stage3` 會 `ModuleNotFoundError`,
  現行 api.py 在 `dashboard/` 目錄下起所以沒事;之後有人從 repo 根 import 會踩到。
  一行可修,未排。
- **Streamlit 退役後 WSL 部署複本尚未同步**:`app.py`/`test_app.py` 要等下次
  `scripts/sync_to_wsl.sh`(rsync `--delete`)才會在 WSL 側消失;venv 內 streamlit
  套件刻意未 uninstall(無害)。
- **憑證頁殘留邊界(三項皆小,知情接受)**:(a) 同頁 Capability Lane 治理表的
  `provider` 欄**未跟著正名**(屬 registry 語意,刻意不動)——若兩表並置仍會
  混淆再開小改;(b) **黃燈只看「整個 store 有無 exhausted 條目」,不區分耗盡的
  是否為生效 provider 的條目**(偏保守、寧可誤黃);目前六個 store 皆單一
  provider 不觸發,要更精準需再拍板;(c) 文字色與色點的對應方式三燈不一致
  (橙點 `#fb923c`/橙字 `#f0a24b`、綠字中性灰、黃字與黃點同為 `#fbbf24`),
  黃字併排時亮一階——化妝品級,一行 CSS 可統一,已向使用者提出未處理。
- **更新按鈕階段二(寫入型 ff 執行鈕):blocker (a) 已解,但價值待重估**:
  0.19.1 升級提供了首個端到端案例——同時實證 WSL ff 段實際只有三條指令、
  零停機,**按鈕的價值比原提案設想小**。剩 blocker (b):白名單不含 timer
  (07-27 拍板),要做需回 service-control 提案擴充重審。建議下次議程
  帶「還做不做」一起議,而非只議「怎麼做」。
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
  (**觸發:下一次 `memory/inbox/` 出現 named profile 的 session 匯入檔時,整併前
  順手目視一次即結案**——不必為它單獨排 session。**到期:2026-10-28**(與 retention
  冷啟動同日);屆時若一次 named episode 都沒落地,那要問的不是「格式對不對」而是
  「named profile 到底有沒有在被使用」,本項改結案並改記為 lane 使用率問題。)
- **白天手動跑 bridge 掃描可能撞 WAL**(08-02 觀察):忙時段 WSL 經
  /mnt/c 對主 db 快照會輸給高頻寫入(fail-loud 三次重試放棄,設計行為);
  每日 08:05 排程時段實績皆成功。排程時段也開始撞再議重試參數。
- **retention 冷啟動中**:recall log 自 07-30 起算,覆蓋滿 90 天前 retention review
  只做升格不汰選——這是設計,不是故障。
  (**到期:2026-10-28**(07-30 + 90 天)。屆時 daily-memory-check 的 retention review
  應首次進入「可汰選」狀態;若 10-28 之後跑過一輪仍回報「冷啟動保護生效」,代表
  recall log 有斷檔或兩側未併集,要查 `logs/recall_log.jsonl` 最早一筆 `ts`。)
- **keepalive 第二階段(watchdog+toast)拍板暫緩**:殘餘風險=WSL 本身壞掉
  時 tick 靜默重試,只剩 webui 紅燈被動面(知情接受;真發生再升級)。
- **bridge-scanner/pipeline/notifier 的 systemd 單元**:repo 有、WSL 沒裝
  (實際跑 Windows Task Scheduler)——排程表顯示「未安裝」是真實狀態。
  是刻意雙軌還是遺留待確認,順帶決定 repo 內三個 unit 檔去留。
- **排程表小措辭**:未安裝列的觸發欄顯示「無法查詢」,嚴格應為 `n/a`
  (沒裝不是查不到)。一句話可修,未排。
- **升級預檢的已知邊界**(取捨非疏漏,已記於提案與 docstring):
  (a) ~~§10.1 燈號盲點~~ 已修(08-03);~~light_text 小切片~~ 已修(08-04);
  (b) live 版本字串取自 HEAD 的 `pyproject.toml`,**不涵蓋「merge 後忘記重跑
  `pip install -e`」**的依賴落後情形,要涵蓋需另案做「依賴同步狀態」欄位——
  更新頁待辦清單目前僅剩此項(加上階段二價值重估,見上)。
- **0.19.1 升級遺留觀察項**:(a) S7-6 cron 排程送達一輪待自然觸發確認
  (`_deliver_result` 新增 relay fail-closed 閘門,觀察無誤閘)。**已逾期一個月**
  (08-04 升級至今無人回報誤閘)。(**到期:2026-09-30**,屆時若仍無任何「該送沒送」
  的回報,即視為閘門無誤閘、本項結案;若要提前確認,查 hermes 側 cron job 的
  `last_delivery_error` 是否出現 relay 相關拒絕,一次查詢即可定案。)
  (b) 本機重跑上游測試套件前必看計畫文件 §11 的 `--ignore` 清單
  (三組測試會沙箱洩漏:dashboard unified launch/update-flow/pty)。
- **升級 script 尚未實戰**:DryRun 與沙箱矩陣全過,但完整寫入路徑
  (真 merge/pip/build/gateway 重啟/push)要等下次官方升級首跑驗證;
  skill 已註明首跑先 `-DryRun`。
- UI 欄位名稱可能再調整(使用者提出後隨時小改)。
- 舊項沿用(**「07-19 排程首次自動觸發結果待確認」已於 09-03 結案移除**:掛了一個半月
  無人確認,且已被更強的證據取代——WSL `systemctl --user list-timers` 顯示
  `hermes-cron-daily-memory-check` / `hermes-bridge` / `hermes-rss` 三個 timer
  今日(09-03)08:00 / 08:10 / 15:00 皆正常觸發、next_run 持續推進,排程鏈長期運作
  已是既成事實;「首次那一輪」的個別結果已無回溯價值也無證據可查):
  「依任務類型自動選模型」規則引擎
  未實作;Hermes UI 設定維護(profile/allowlist 手改 config.yaml);Tavily key
  明文存放;WSL 部署複本需 `scripts/sync_to_wsl.sh` 手動同步(07-29 已實跑
  兩輪,流程順;注意 `.claude/settings*.json` 是 sync 排除項,WSL 側權限
  白名單要單獨維護)。
- (低優先)bridge/PTY 屬安全敏感面,可考慮 `/code-review ultra` 補一道審查。

## 4. 下一步(可直接執行的第一步)

**照批次順序走,序列執行(ROADMAP「不同時開兩個能力」)。**

- **批次 0｜清場——✅ 已完成(2026-09-03)**,四項全數結案,`git status` 乾淨:
  ① webui 那四項未 commit 變更(擱置 19 天)——`App.tsx`/`globals.css` 加上收尾修復
  隨 commit `ae1eff1` 上線(156/156 綠、typecheck 零錯誤、零新增 `!important`);
  規格與落地經驗整理成
  [docs/webui-typography-proposal.md](docs/webui-typography-proposal.md)
  (含三個規格書沒寫到的坑與兩項未解);素材包 `agentos-ui-patch/` 與根目錄逐字
  複本 `CLAUDE-CODE-PROMPT.md` 已移出 repo 至
  `~/dev/_archive/agentos-ui-patch-20260815/`(MD5 一致性驗證後刪除複本)。
  **engineering 批次的前置已解除。**
  ② `memory/inbox/` 已清空(commit `3d14da8`),兩份歸檔進 `.processed/`;
  ③ Stage 0.5 三項殘項查證後全數過時、已結案移除(見第 3 節);
  ④ 觀察類 5 項全補上到期日/觸發條件,其中 07-19 首次自動觸發直接結案。
- **批次 1｜止血**:把「預檢 `ahead>0` 亮橙」升級為主動保護(未 push 的客製 commit 不能被
  桌面 Install 鈕靜默吃掉);順手修 `dashboard/data_stage3.py:76` 裸 import。
  **這是清單裡唯一會造成不可逆損失的項目。**
- **批次 2｜脈絡校正**:ROADMAP.md 四處翻修 + 技術債四項判定,**併入 `dispatch_domain.py`
  docstring**。要在拍板之前做完。
- **批次 3｜規則引擎**:性質已改變,**起草前先拍板**(見第 3 節第一項)。脈絡已備齊。
- **批次 4｜集中拍板(60–90 分)**:議程第 0 項=模型路由的循環依賴與「建議 vs 強制」;
  接著 `/cos`+Telegram 出口格式(合併議)、headless 記憶失效(傾向做)、launcher `-Restart`、
  systemd 雙軌、以及 6 項建議結案的確認。**避開 07:30–08:30**(搶晨報訂閱額度)。
- **批次 5｜執行拍板結果**;**批次 6｜低優先收尾**(三項 UI 小修打包、Hermes UI 設定維護
  獨立 session、Tavily key、bridge/PTY `/code-review ultra`)。

**與 AIChain 那條線的插隊項**(不在批次內,但時效已過):先確認 09-02/09-03 兩次
`cron + claude_cli` 的實際結果——看 Slack `#ai-chainresearch` 有沒有東西;失敗證據在
`AIChainOrchestrator\logs\<run_id>_aichain_claude_daily_auto.log` 的 stderr,回滾一行
(`claude_provider.yaml` 改回 `provider: anthropic_api`,即時生效)。
