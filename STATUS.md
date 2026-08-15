# STATUS — 現況快照

> **用途**:讓任何新 session(前台 Desktop 或 headless CoS)在 30 秒內跟上進度。
> **更新規則**:每次收工前更新本檔的四個區塊;只寫「現在是什麼、接下來做什麼」,
> 歷史細節與證據一律連結到權威文件(ROADMAP.md、docs/hermes-integration-roadmap.md、
> memory/),不在這裡重複展開。本檔永遠只反映「最新一次收工時」的狀態。

**最後更新**:2026-08-15

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

(本次 session 一個 repo commit:`55e249d`(Streamlit 退役),已 push;另有兩項
repo 外變更(Hermes 全域 config、auto-memory)。起點=使用者問「還有什麼階段沒做」,
盤點後拍板三件事並全部完成。)

- **盤點結論**:Roadmap Stage 0–5 全數關閉,無開工中階段;剩餘全是遺留/待拍板
  議題(已整理成清單,見第 3 節,並建議下次由 `planning` 排成議程)。根 ROADMAP.md
  里程碑仍只到 `v0.1-beta`、技術債四條自 07-04 未對現況——文件面過時,未處理。
- **default 模型改回 `openai-codex/gpt-5.6-sol`(engineering,兩輪)**:
  `%LOCALAPPDATA%\hermes\config.yaml` `model:` 區塊改回與 gptcoding 一致
  (`gpt-5.6-sol` / `https://chatgpt.com/backend-api/codex`),並刪除 deepseek
  時期殘留的 `api_mode: chat_completions` 行;備份 `config.yaml.bak.20260815_182728`
  與 `_205855`。**零重啟**;憑證頁 `(global-root)` 交叉檢查燈**橙→綠**
  (entry_count=1、`last_status: ok`)。08-05~08-15 的 deepseek 插曲就此結束。
- **memory 應然表更新(knowledge)**:auto-memory `hermes-profile-intended-config`
  五列表格不動(改回後 default 列本來就對),補「插曲紀錄」與「換模型免重啟
  作業程序」兩段;待辦 (2) 改寫為 `OPENROUTER_API_KEY` **維持 nemocoding 必需、
  不可移除**(default 已不再依賴);待辦 (3) 結案——copilot 憑證經 `gh_cli`
  重生(gptcoding 08-03、nemocoding 07-30)屬已知行為非漂移。MEMORY.md hook 同步。
- **Streamlit 退役(engineering,`55e249d`)**:刪 `dashboard/app.py`、`test_app.py`
  (6 案例皆純 AppTest 渲染層,密鑰不外洩已由 `test_data`/`test_api` 覆蓋,不倒退);
  `requirements.txt` 移除 streamlit、`.claude/launch.json` 移除 8501;README×2/
  `WINDOWS_WSL_SETUP`/`ARCHITECTURE` 改指向 `start_webui_stack`;資料層四檔只改
  docstring 零邏輯;roadmap 兩份補退役註記。測試 dashboard 277→**271**(差額=刪掉
  的 Streamlit 測試)、hermes 491/scripts 107 不變、security_check **13/13**、
  webui typecheck+build 乾淨。**未 pip uninstall、未 sync WSL**。

## 3. 卡住/未決的問題

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

- **本次三項拍板皆已落地並驗證,無進行中作業**(default 改回 gpt-5.6-sol、
  memory 應然表更新、Streamlit 退役已 push)。
- **建議下一步:分派 `planning` 把第 3 節的「遺留/待拍板議題清單」排成一份帶
  優先序的議程提案**,再逐項拍板——目前沒有開工中的階段,下一個要做什麼是
  規劃問題,不是實作問題。
- **日常留意**:下一輪整點 cron / 首次 gptcoding 呼叫成功後,憑證頁 `gptcoding`
  黃燈應自動退綠(`last_status` 自癒);若久未退綠再查配額。
- **下次 `scripts/sync_to_wsl.sh` 時**順帶確認 WSL 側 `dashboard/app.py` 已被
  `--delete` 清掉。
- **換 hermes 模型的正確作業程序(08-05 實證、08-15 再走一次)**:改 `config.yaml`
  → **不用重啟任何服務** → 憑證頁按重整即見新值 → 想確認真的在跑,等下一輪
  整點 cron 或開**新** Telegram thread。橙燈=該 provider 在此 store 無憑證條目。
  正本已寫進 auto-memory `hermes-profile-intended-config`。
- **下次想升級 hermes**:按 fetch 鈕看落後數 → 說「升級 hermes」觸發
  `/hermes-upgrade`(兩次核准,其餘機械化;首跑先 `-DryRun`)。「Hermes 更新」頁
  backup 組亮橙=有客製沒 push,立刻處理;sidebar 常駐燈紅=先查
  `schtasks /query /tn HermesWslKeepAlive`(自癒最壞 15 分鐘)。
- **A1 已驗收結案**:日常留意首個多輪 named 對話的 episode 落地檔。
- **lane 通道已全通,可開始真實使用**:前台直接指定(「用 GPT 做 X」),Telegram
  入口亦可;累積真實使用觀察,供日後「依任務類型自動選模型」規則引擎的設計依據。
- `memory/inbox/` 有 2 個待整併檔(07-16 episode、07-31 skill catalog),下次
  daily-memory-check 或手動 `/consolidate-memory` 收。
