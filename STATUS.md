# STATUS — 現況快照

> **用途**:讓任何新 session(前台 Desktop 或 headless CoS)在 30 秒內跟上進度。
> **更新規則**:每次收工前更新本檔的四個區塊;只寫「現在是什麼、接下來做什麼」,
> 歷史細節與證據一律連結到權威文件(ROADMAP.md、docs/hermes-integration-roadmap.md、
> memory/),不在這裡重複展開。本檔永遠只反映「最新一次收工時」的狀態。

**最後更新**:2026-09-01

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
  遺留與待拍板議題(見第 3 節),下一個要開的規劃另議。
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

(涵蓋 08-28 至 09-01。**repo 端仍只有 STATUS 快照**——AIChain 實作全在版控之外的
`HermesWorkspace\HermesAgent\{AIChainOrchestrator,DailyAIChainResearchV2}`、
`HermesWorkspace\GptCoding\AIChainClaude` 與 `%LOCALAPPDATA%\hermes`,改完即生效、無 commit。
所有改動皆有 `.bak.2026082x` / `.bak.2026090x` 備份。)

### 排序層整治(08-28~08-31)——問題不在搜尋,在排序

- **intelligence 領域診斷**:08-28 的 801 筆原始採集裡,雙鴻擴產、世界先進擴產、力成獲博通
  包產能全都撈到了,**是 scorer 丟掉的**。三次獨立驗證(基本面素材全滅、緯穎舊文靠最低分保送、
  31 筆關稅新聞全滅而電競公關稿入選)。
- **Tier 0(1/3/4)**:重建 `company_aliases.yaml`(從 TWSE/TPEx OpenAPI 核對,修 10 組錯誤對映
  含 `Auras→榮成`實為雙鴻、`PTI→豐達`實為力成;新增 12 家);修 `media_credibility` 方向
  (原本「列入白名單=被懲罰」)並加 official/regulatory 豁免。**項目 3(刪 7 條聚合查詢)查證後
  停手**——席次會流向無分類桶,品質反而下降。項目 2(訊號組)使用者不做。
- **A11b + 兩個借自 Manus 腳本的排序設計**:新類別 `tw_ai_component_supply_chain` 置於類別列表
  **最前**(關鍵:分類是查詢執行順序的產物,放後面會被泛用查詢搶走);`taiwan_relevance` 接收
  死掉的 `memory` 0.15 權重、含 40 個環節詞;跨桶輪詢取件;`min_final_score: 0.40` 垃圾門檻。
  **台廠零組件 0/18 → 3/20**。
- **A11a**:跨日帳本 `state/item_ledger.json`(只記入選項、保留 14 天、排除窗 7 天)、
  無分類上限改為跨桶共用額度(4→1 席)、web_search 補分類(80 筆入選 0 筆,證明它們過去
  唯一入場券就是無競爭桶)。**同事件偵測評估後不做**——SK hynix 真同事件相似度 0.398,
  而「緯創個股概覽 vs 光寶科個股概覽」誤配是 0.892,**誤配是真陽性的 2.2 倍**。
- **報告可讀性**:新增「今日證據品質」一句話評級(門檻寫進 `report_rules.yaml`)、
  「台股開盤觀察」只留台股上市櫃且顯示中文名。
- **08-31 系統性掃描**:再修 10 個問題——5 組重複 canonical(`TSM` 是 `TSMC` 的子字串、
  `IBI`/`Hon_Hai`/`Apex`/`NanYaPCB`)、`Supermicro` 別名「超微」在台灣指 AMD(改「美超微」)、
  移除 `ase`/`tel` 裸別名(`'ase'` 命中 increased/based、`'tel'` 命中 intelligence,**每天約
  65 筆錯誤公司標記**)。

### 市場數據層(09-01)

- **法人買賣超接進 `market_gate`**:資料一直在抓(T86)但 gate 說 `unavailable`,是**被自己截成
  前 20 名**丟掉的。改為保留全上市 `by_ticker`(26KB,同一請求)。台廠零組件標的命中 1→5 檔。
  `hyperscaler_capex` 的 `tactical_bias_allowed` **首次翻成 true**(四項條件獨立成立);
  `ai_hardware_semiconductor` 從 unavailable 變 **contradicted**(台積電 +124 萬 vs 華邦電
  −3,881 萬,舊碼會讓量體小的一邊決定結論)。門檻寫進 `market_gate.yaml`。
- **報價名冊 6 → 55 檔**(49 上市 + 6 上櫃,`.TWO` 格式),順帶解掉 canonical→股號正式對照
  (55 個對到、0 歧義)。**擴大名冊讓判定更嚴不是更鬆**:`ai_hardware_semiconductor` 相對強度
  從 confirmed 變 contradicted,因為現在看得到南亞科 −0.18%、華邦電 −2.42%。
  途中拆掉一顆地雷:原本**任一檔報價失敗會讓整個 market_context 變空**。
- **TPEx 法人買賣超端點打通(尚未接)**:前一輪 8 個候選全 520 是猜錯路徑家族;正解是
  `www.tpex.org.tw/www/zh-tw/insti/dailyTrade?type=Daily&sect=EW&date=YYYY/MM/DD&response=json`,
  917 列、口徑與 T86 一致、六檔上櫃股全在內、與 T86 合併零碰撞。**評估後 Yahoo 買賣超排行
  判定不可用**(只有外資非三大法人、硬上限 100 名、且系統性漏掉非外資主導的賣壓)。

### ★ 架構轉向:分析改走訂閱制(09-01)

- **背景**:使用者判斷現有 web search 調研品質不佳(當日實例:管線抓到三則媒體轉載,
  而 ChatGPT 抓到 NVIDIA/聯發科**官方聯合發布**本身,管線完全漏掉),決定改用 ChatGPT
  排程調研 + Claude Code 分析,讓 API 費用歸零。
- **可行性評估(含一次實測)**:`claude_cli` provider 已實作,但實測發現**兩處新的不對等**——
  ① claude.exe 在 text 模式把 JSON 包在 markdown fence 裡,而 `claude_cli` 分支漏了剝 fence
  (`anthropic_api` 分支有),不修 100% 失敗;② **前次 state 未載入**(只給路徑不給內容),
  模型明說「尚無前次分析狀態可供比較」,跨日迭代會靜默失效。
  **實測證明 packet 不必內嵌**(`--add-dir` 讓模型自己 Read 可行)。
- **階段 1 已上線**:`provider.py` 抽出共用 `_build_provider_prompt()`、補 fence 正規化、
  嵌入 `latest_state.json`、加嚴格輸出約束;`claude_provider.yaml` 切 `provider: claude_cli`
  (絕對路徑 + `--add-dir`,timeout 1200)。**途中修掉一個自己引入的 regression**:
  `subprocess.run(text=True)` 未指定 encoding,cp950 遇 `Jalapeño` 的 `ñ` 直接爆;
  且同一 bug 另一半會讓 stdout **靜默變成 None**(解碼失敗在 reader thread 被吞掉)。
  驗證:schema valid、無 fence、`key_insights` 5(修前 8)、`active_themes` 7(修前 4/API 10)、
  305 秒、46 測試全過。
- **cron 環境憑證查證(唯讀)**:四條環境鏈全通——cron 用 `os.environ.copy()` 只刪不重建
  (`USERPROFILE` 保留、`ANTHROPIC_API_KEY` 被刪)、gateway 由排程工作以 `razer/Interactive`
  身分啟動、`claude.exe` 走 `os.homedir()` 有 `USERPROFILE` + process token 雙保險且不看 `HOME`。
  **附帶好消息**:cron 會刪掉 API key,保證走訂閱不可能誤用 API 計費。

## 3. 卡住/未決的問題

- **★ 09-02 08:00 是第一次「cron + claude_cli」**,尚未驗證。憑證解析已唯讀查證為高信心,
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
- **工作樹有四項與本次 session 無關的未 commit 變更**(`webui/src/App.tsx`、
  `webui/src/globals.css`、`agentos-ui-patch/`、`CLAUDE-CODE-PROMPT.md`,webui
  typography patch),本次收尾 commit 刻意未帶入,待使用者決定去留。

- **防重演有一個結構性弱點(持續性風險,務必知道)**:`reset --hard origin/main`
  是 no-op 的前提是 `main == origin/main`。**一旦有客製 commit 沒 push 到私有
  備份,桌面 Install 鈕就會吃掉它們**——中和繫於「每次客製後都要 push」的人為
  紀律,不是結構保證。升級預檢已會在 `ahead > 0` 時亮橙告警。
- **遺留/待拍板議題清單(08-15 盤點,建議由 `planning` 排成一份帶優先序的議程)**:
  待拍板=telegram-cos-realtime 草案(併 Telegram 出口格式缺口)、headless session
  記憶失效機制、更新按鈕階段二「還做不做」、launcher `-Restart`、「依任務類型
  自動選模型」規則引擎(需 planning 起草);小修=lane session 觀測缺口下半、
  預檢「依賴同步狀態」欄位、排程表 `n/a` 措辭、憑證頁三燈 CSS、bridge 三單元
  雙軌確認、Stage 0.5 殘項是否早已過時;文件=根 ROADMAP.md 里程碑/技術債過時。
  各項細節仍散在下方條目,清單只是索引。
- **`gptcoding` 憑證 `last_status` 仍顯示 `exhausted`(last_refresh 08-02)**:
  該欄只在 lane 被實際呼叫時更新,不代表配額仍耗盡;下次 gptcoding lane 真實
  呼叫成功即自癒、憑證頁黃燈退綠。不需動作,只是別誤讀。
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
  (a) ~~§10.1 燈號盲點~~ 已修(08-03);~~light_text 小切片~~ 已修(08-04);
  (b) live 版本字串取自 HEAD 的 `pyproject.toml`,**不涵蓋「merge 後忘記重跑
  `pip install -e`」**的依賴落後情形,要涵蓋需另案做「依賴同步狀態」欄位——
  更新頁待辦清單目前僅剩此項(加上階段二價值重估,見上)。
- **0.19.1 升級遺留觀察項**:(a) S7-6 cron 排程送達一輪待自然觸發確認
  (`_deliver_result` 新增 relay fail-closed 閘門,觀察無誤閘);
  (b) 本機重跑上游測試套件前必看計畫文件 §11 的 `--ignore` 清單
  (三組測試會沙箱洩漏:dashboard unified launch/update-flow/pty)。
- **升級 script 尚未實戰**:DryRun 與沙箱矩陣全過,但完整寫入路徑
  (真 merge/pip/build/gateway 重啟/push)要等下次官方升級首跑驗證;
  skill 已註明首跑先 `-DryRun`。
- UI 欄位名稱可能再調整(使用者提出後隨時小改)。
- 舊項沿用:07-19 排程首次自動觸發結果待確認;「依任務類型自動選模型」規則引擎
  未實作;Hermes UI 設定維護(profile/allowlist 手改 config.yaml);Tavily key
  明文存放;WSL 部署複本需 `scripts/sync_to_wsl.sh` 手動同步(07-29 已實跑
  兩輪,流程順;注意 `.claude/settings*.json` 是 sync 排除項,WSL 側權限
  白名單要單獨維護)。
- (低優先)bridge/PTY 屬安全敏感面,可考慮 `/code-review ultra` 補一道審查。

## 4. 下一步(可直接執行的第一步)

- **① 09-02 早上先驗收 cron + claude_cli 的第一次真實執行**(見第 3 節第一項)。
  約 08:08 出報告,看 Slack `#ai-chainresearch` 有沒有東西即可。
- **② ChatGPT 調研路線的分階段計畫**(engineering 評估產出,依序):
  - **階段 0｜觀測(零成本)**:ChatGPT 排程改到 08:00 前,連續 **3-5 個工作日**只看檔案
    幾點出現。整個架構壓在這個假設上,目前唯一樣本是 12:25 產出的。
  - **階段 1｜費用歸零** ✅ **已完成上線**(見第 2 節)
  - **階段 2｜ChatGPT 檔當「補充」證據,不取代 RSS**(1.5-2 天):新寫
    `chatgpt_research_reader.py`(沿用 `ManusResearchLoadResult` 契約,markdown parser,
    **檔名日期須嚴格等於執行日**、交叉驗第一行標題、多檔匹配視為異常)。
    檔案在 `C:\Users\razer\Documents\ChatGPT DailyStockResearch\`(**外層無日期子資料夾**),
    命名如 `AI產業鏈調研_2026-09-01.md`。並行比較兩週再決定要不要砍 RSS。
  - **階段 3｜fail-closed + 告警**(0.5-1 天):`verify_manus_packet_inclusion()` 狀態分支反轉、
    07:30 預檢 + Telegram、reader 加 60 秒×3 重試、**cron 從 `0 8` 挪到 `30 8`**、
    順手修 Slack 靜默失敗。
  - **階段 4｜砍 RSS**(0.5 天但風險最高):三個前提都要滿足——階段 2 跑滿兩週且資料證實、
    Yahoo 位移 bug 已修、實跑確認 `--market-context` 單獨模式下 `daily/{date}.md` 仍 ≥1000 bytes。
- **③ 已查證可做、尚未排的**:
  - **接 TPEx 法人買賣超**(約 3 小時)——端點已實測打通,接完六檔上櫃股才有流向,
    `tw_ai_component_supply_chain` 才有機會被真正評估
  - **修 Yahoo 報價位移 bug**(2.5-3.5 小時)——timestamp 對齊取值 + 防呆告警 +
    順手修價量錯位;建議一併開始保存 Yahoo 原始回應到 `raw/`,否則下次同類事故查不出來
- **原有的規劃面待辦**(08-15 起未動):分派 `planning` 把第 3 節的遺留議題排成帶優先序的議程。
  目前仍無開工中的 roadmap 階段。
- **工作樹有四項與 AIChain 無關的未 commit 變更**(`webui/src/App.tsx`、`webui/src/globals.css`、
  `agentos-ui-patch/`、`CLAUDE-CODE-PROMPT.md`,webui typography patch),三次收尾 commit
  都刻意未帶入,**待使用者決定去留**。
- `memory/inbox/` 有 2 個待整併檔,下次 daily-memory-check 或手動 `/consolidate-memory` 收。
