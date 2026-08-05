# STATUS — 現況快照

> **用途**:讓任何新 session(前台 Desktop 或 headless CoS)在 30 秒內跟上進度。
> **更新規則**:每次收工前更新本檔的四個區塊;只寫「現在是什麼、接下來做什麼」,
> 歷史細節與證據一律連結到權威文件(ROADMAP.md、docs/hermes-integration-roadmap.md、
> memory/),不在這裡重複展開。本檔永遠只反映「最新一次收工時」的狀態。

**最後更新**:2026-08-05(第五次收工,同日第二次)

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

(本次 session 兩個 commit:`b5a402b`(A+B 主交付)、`797b26e`(exhausted 降黃),
皆已 push;起點=使用者把
全域 default 模型換成 `openrouter/deepseek-v4-flash-0731` 後「哪裡都看不出來」,
主軸=**由此追出觀測面第三軸缺口並補上**,附帶四項實測釐清。)

- **實測推翻「換模型要重啟 gateway」**:`agent.log` 15:27 那筆 client
  是**舊 gateway 進程(改 config 後、重啟前)**用新模型建的,證明
  `_load_gateway_config()` 的 mtime-keyed 快取一改檔即失效。**換 default
  模型零重啟即生效**;啟動時 `self.config` 那份快照不影響模型解析路徑。
  (使用者仍實跑了一次 restart,無害,狀態指紋/綠燈確認正常。)
- **釐清憑證軸 ≠ 模型軸**:憑證頁 `providers` 欄來自 `auth.json`(此 store
  存了哪些 provider 的憑證),跟 `config.yaml` 的 `model.provider` 是兩件事,
  改模型不動它是**正確行為不是 bug**。同名不同軸即誤解成因 → 催生本次交付。
- **A+B 交付(engineering,9 檔)**:A=每列補生效模型三欄(判定邏輯抽單一處,
  lane 表複用,global config 只讀一次);B=交叉檢查結構化燈號
  (`credential_model_consistency`)+ `last_status=exhausted` 紅標。
  **auth.json 不存在/壞掉時模型區塊照樣顯示**(`default` profile 即此情形)。
  測試:dashboard 84→**98**、全套 274 passed/1 skipped、webui 149→**155**、
  typecheck+build 乾淨、security_check **13/13 未減項**;§3.2 白名單六欄
  一字未擴充,並加回歸釘子鎖住。全程唯讀,未碰 bridge 白名單。
- **live 驗證通過**:8799 重啟後 `(global-root)` 如實顯示
  `openrouter / deepseek-v4-flash-0731 / 繼承全域`,交叉檢查**橙**
  (`entry_count=0`);六列燈號各就各位。**這格橙燈揭露的是真實隱形依賴——
  global-root 憑證池無 openrouter 條目,default 靠 `OPENROUTER_API_KEY`
  環境變數在跑**(nemocoding 那筆的 source 即 `env:OPENROUTER_API_KEY`)。
- **deepseek 新 default 驗證結案**:16:04 / 17:05 兩輪整點 cron 皆
  `finish_reason=stop`、`end_reason=cron_complete`,`session_model_usage`
  有實際 token 計費。同時證實 15:04 那輪失敗純屬 codex 配額。
- **codex 配額耗盡(非設定問題)**:15:04 cron HTTP 429
  `usage_limit_reached`(plan_type=plus),**約 08-08 中午恢復**;
  `gptcoding` 憑證 `last_status` 已標 `exhausted`。三條 codex lane
  (gptcoding/financialresearch/intelligence)期間形同停用——**使用者明示不在意**。
- **nous 日誌噪音查明並拍板不動**:`config.yaml` **沒有 `auxiliary:` 段**、
  全檔無 `nous` 字串;18 個 auxiliary 子任務全是內建預設 `provider: auto`。
  nous 是 `agent/auxiliary_client.py` 內建 auto 鏈的**第 3 順位**(Hermes Agent
  出自 Nous Research),07-23 清憑證=登出,鏈仍在 → 每輪 cron 探測失敗數次後
  標 unhealthy 跳過,主線不受影響。**沒有殘留設定可清**,消噪只能反向 pin
  provider(等於放棄 fallback,不划算)。知情接受。
- **`exhausted` 降黃拍板+落地(`797b26e`)**:四燈優先序**橙 > 黃 > 綠**
  (gray 維持不變——該情形計數本身不可信,不據以升級告警)。規則正本在
  `_credential_model_consistency()` 的 `_out()`:附加 exhausted 文案後只把
  green 改判 yellow,orange 走同一條路但不被改寫。前端沿用既有黃 `#fbbf24`
  (= `ResidentStatus` 同一顆),未自創 token。測試 dashboard 274→277、
  webui 155→156、security_check 13/13;live 驗證 `gptcoding` green→yellow。
- STATUS 的「env 變數清理待使用者手動」條目依使用者要求刪除(第 3、4 節各一處)。

## 3. 卡住/未決的問題

- **防重演有一個結構性弱點(持續性風險,務必知道)**:`reset --hard origin/main`
  是 no-op 的前提是 `main == origin/main`。**一旦有客製 commit 沒 push 到私有
  備份,桌面 Install 鈕就會吃掉它們**——中和繫於「每次客製後都要 push」的人為
  紀律,不是結構保證。升級預檢已會在 `ahead > 0` 時亮橙告警。
- **memory 應然表已過期,待 `knowledge` 更新(08-05 新增,優先)**:
  `hermes-profile-intended-config` 的 default 列仍寫 `openai-codex/gpt-5.6-sol`,
  實際已是 `openrouter/deepseek-v4-flash-0731`——不更新,下次憑證稽核會把新值
  誤判成漂移。同一則的待辦 (2) 也要改寫:**`OPENROUTER_API_KEY` 從「nemocoding
  必需」升級為「default 主線必需」**(global-root 憑證池無 openrouter 條目)。
  **STATUS 的 env 條目已刪,memory 是這條約束的唯一正本**。順帶把
  「07-23 清乾淨的 copilot 憑證會經 `gh_cli` 重生」(gptcoding 08-03、
  nemocoding 07-30 各一筆)寫進去——那是舊待辦「觀察是否有 entry 重生」的答案。
- **codex 配額約 08-08 中午恢復後的決策**:屆時要決定 default 改回
  `openai-codex/gpt-5.6-sol` 還是續用 deepseek;三條 codex lane 會自動復原
  (不需動作)。決定後才做上面那條 memory 更新,免得改兩遍。
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
- **Streamlit 退役決策**:待並行觀察期(2026-07-24 起)滿一個自然使用週期後拍板。
- UI 欄位名稱可能再調整(使用者提出後隨時小改)。
- 舊項沿用:07-19 排程首次自動觸發結果待確認;「依任務類型自動選模型」規則引擎
  未實作;Hermes UI 設定維護(profile/allowlist 手改 config.yaml);Tavily key
  明文存放;WSL 部署複本需 `scripts/sync_to_wsl.sh` 手動同步(07-29 已實跑
  兩輪,流程順;注意 `.claude/settings*.json` 是 sync 排除項,WSL 側權限
  白名單要單獨維護)。
- (低優先)bridge/PTY 屬安全敏感面,可考慮 `/code-review ultra` 補一道審查。

## 4. 下一步(可直接執行的第一步)

- **本次交付已 live 驗證完畢,無進行中作業**(含 `exhausted` 降黃,已拍板落地)。
- **08-08 中午 codex 配額恢復後**:決定 default 改回 `gpt-5.6-sol` 或續用
  deepseek → 決定後才分派 `knowledge` 更新 memory 應然表(免得改兩遍)。
  **在那之前 `OPENROUTER_API_KEY` 絕對不能移除**——default 主線靠它。
- **換 hermes 模型的正確作業程序(本次實證)**:改 `config.yaml`(CLI 或
  手改)→ **不用重啟任何服務** → 憑證頁按重整即見新值 → 想確認真的在跑,
  等下一輪整點 cron 或開**新** Telegram thread(舊 thread 24h 內會 resume
  舊 session)。憑證頁那格橙燈=該 provider 在此 store 無憑證條目,靠環境變數。
- **0.19.1 已全程完成**。日常留意:S7-6 cron 送達一輪
  (下次排程自然觸發時看 Slack 是否正常);Windows 卡片橙(落後 934)
  是升級後預期常態。**下次想升級:按 fetch 鈕看落後數 → 說「升級
  hermes」觸發 `/hermes-upgrade`**(兩次核准,其餘機械化;首跑先
  `-DryRun`)。
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
  確認。
