# Stage 3 — Dashboard 觀測性擴充：Session 列表／憑證與 Lane 唯讀檢視／Cron 排程表（設計提案 v2）

日期：2026-07-22（v2；v1 為 2026-07-21）　狀態：**v2——使用者已拍板首發決策，可開工**
負責規劃：`planning` domain
負責領域（實作階段）：`engineering`（全部程式碼、`dashboard/data.py`／`dashboard/app.py` 擴充、測試）。
本提案全部三項功能都是唯讀資料呈現，不新增、不修改任何排程頻率或觸發邏輯——
只是把既有 timer／設定／憑證的既有狀態呈現出來，因此 `automation` 在本階段
角色為零（與 2.5／2.6 的既有分工判準一致：產出物是排程頻率/觸發時機決策才
輪到 automation，本階段沒有這類決策）。

依賴文件（規劃前已逐一查讀實際狀態，不猜測既有機制的行為）：

- [hermes-integration-roadmap.md](hermes-integration-roadmap.md) **Stage 3 節**
  （既有四條 DoD 的原文出處，本提案功能一逐條沿用不改寫）、**Stage 4 節**
  （安全事故背景，是功能二的直接動機來源）。
- `dashboard/app.py`、`dashboard/data.py`、`dashboard/README.md`、
  `dashboard/test_data.py`（既有 read-only 鐵律、既有函式命名慣例、既有測試
  模式——特別是 `test_data.py` 已經有的「密鑰不外洩」測試先例）。
- `registry/capability_lanes.yaml`（lane 治理欄位，全部 7 條 lane 現況皆
  `status: active`）。
- `hermes/session_adapter/adapter.py` + `hermes/session_adapter/README.md`
  （`HermesSessionAdapter`、`list_sessions()` 介面、snapshot 模式、state.db
  在 Windows 端的自動偵測路徑）。
- `memory/hermes-credential-handling-safety-lessons.md`（憑證處理四類事故
  教訓，功能二唯讀邊界設計直接對應教訓一／教訓四）。
- `hermes/config/cron_jobs.yaml`、`hermes/adapters/cron.py`、
  `hermes/systemd/*.timer`、`hermes/systemd/README.md`（cron／timer 既有
  結構，功能三擴充依據）。
- **（v2 新增）** Hermes 原生 cron store（root `%LOCALAPPDATA%\hermes\cron\jobs.json`，
  以及跨 profile store 時沿用 `web_server.py` 既有「單一 process 掃多 store」
  能力）——功能三 v2 擴大涵蓋 Hermes 原生 cron 的讀取來源；
  `memory/hermes-cron-model-pin-convention.md`、
  `memory/hermes-cron-store-binding-gateway-alignment.md`（模型 pin／花費保護
  #44585、store 歸屬——功能三 v2「模型漂移旗標」的判定依據）。

---

## 版本標記

- **v2**（2026-07-22）＝使用者就本提案首發範圍拍板後的更新版。
  **v2 變更摘要（相對 v1）**：
  1. **實作順序改為 功能二 → 功能三 → 功能一**（原 v1 建議「二 > 一 > 三」，
     本次把功能三提到第二、功能一殿後為選配）——理由：使用者確認近期會繼續
     Stage 4 橋接／憑證相關工作，功能二急迫性名副其實提到最前，同時先把
     「安全唯讀白名單資料層」模式立起來給後兩項沿用。
  2. **功能三實質擴大範圍**：從「只觀測 systemd timer」擴大為「統一排程健康表：
     同時涵蓋 systemd timer 與 Hermes 原生 cron（`jobs.json`／原生排程器）」，
     用 `source` 欄位（systemd／hermes-native）標示兩套機制。動機：使用者真正
     的痛點 job（AI news 原生 cron agent job、garmin `no_agent` script job）跑在
     Hermes 原生 cron，v1 的 systemd-timer-only 設計完全看不到它們，導致
     「排程健康表」名不副實。
  3. **功能三新增純唯讀「模型漂移旗標」欄位**：對原生 cron 的 unpinned agent job，
     比對 `model_snapshot` vs 當前全域 `config.yaml` 的 `model.default`，標示是否
     漂移＋花費方向。吸收先前討論的「漂移偵測 MVP」，不另立獨立 feature。
  4. **明確「不做」清單**：任何寫入型「更換／pin profile 或 cron job 模型」動作
     不在 Stage 3；特別是「一鍵自動對齊漂移 job 到新模型」本質是繞過使用者自設
     的花費保護（#44585）且方向上專挑更貴模型，明確不做；獨立第四項漂移偵測
     feature 也不做（只當功能三的唯讀旗標）。
  5. **新增「待補：使用者其他 Stage 3 想法」開放區塊**（見 §5.1），供後續想法
     接入，不影響上述已定案部分。
- **v1**（2026-07-21 落地）＝本文件的第一個正式版本。使用者已就「是否該
  建置網頁入口」做過評估並拍板：**擴充既有 `dashboard/app.py`／
  `dashboard/data.py`，不另開新入口**；本文件是那個評估結論之下的具體
  設計提案，範圍鎖定使用者已拍板的「第一批」三項功能。

---

## 0. 定位與範圍邊界（本提案最重要的一節，後續所有設計都從這裡導出）

**一句話定位**：本提案在既有 Streamlit dashboard 上新增三個唯讀檢視功能
（Hermes session 列表、憑證／Capability Lane 狀態、Cron 排程表），完全不
放寬既有 dashboard 的三條既有鐵律——**localhost-only**、**技術上強制
read-only**（`mode=ro`、不 import 任何寫入模組）、**獨立資料層**
（`dashboard/data.py` 不共用 `hermes/db.py` 或任何寫入路徑）。

### 0.1 為什麼是擴充既有 dashboard，不是另開新入口

延續先前「是否該建置網頁入口」評估的結論：

1. **既有三條鐵律已經是這三個新功能都需要的保證**——localhost-only、
   技術強制 read-only、獨立資料層。另開新入口等於要重新建立同一套保證，
   徒增維護面與潛在的實作落差（例如新入口忘記加 `mode=ro`），沒有任何
   額外好處。
2. **Roadmap Stage 3 本來就是「在既有 dashboard 加一頁」的方向**——見
   roadmap 原文：「在既有 Streamlit dashboard 加一頁 Hermes session 檢視」。
   本提案只是把這個既定方向延伸到另外兩項功能，不是新發明的路線。
3. **`dashboard/data.py` 現有的 `get_adapter_config_status()` 已經示範了
   本提案功能二／三需要的安全模式**（只回報治理狀態、不印密鑰本身）——
   延續既有模式比引入新模式風險更低。

### 0.2 與既有 Stage 3／Stage 4 的關係（明確交代，避免編號混淆）

- **Stage 3**（roadmap 既有 stub）：至今只有一段簡短描述與四條 DoD，
  從未有專屬提案文件（不像 2.5／2.6／2.7 都各自有 `stageX-*-proposal.md`）。
  本文件**就是 Stage 3 的正式提案文件**，本提案的功能一（第 2 節）逐字
  沿用 roadmap 原文四條 DoD，不新增、不刪減、不改寫其語意。
- **本提案範圍比 roadmap 原本的 Stage 3 stub 更大**：roadmap 原文只提到
  session 列表一項；本提案額外納入憑證／Lane 檢視與 Cron 排程表兩項——
  這是使用者在本次規劃中明確拍板的擴充範圍，**不是本提案自行擴大**。
  待使用者核准本提案後，roadmap Stage 3 節應更新指向本文件（比照 2.5／
  2.6／2.7 的既有慣例），但這個更新動作不在本提案的規劃職責內，本提案
  只負責把設計寫清楚。
- **與 Stage 4 的關係**：Stage 4（CoS → Hermes 執行橋接）已於 2026-07-21
  完成並關閉。本提案功能二的直接動機，正是 Stage 4 完工紀錄裡誠實記錄
  的「安全事故」小節——過程中發生至少 3–4 次憑證明文洩漏、一次真正越權
  查驗，暴露出**目前沒有安全管道快速確認憑證／lane 狀態，只能靠
  subagent 逐次手動稽核且容易出錯**。功能二是對這個缺口的直接回應。
  除此之外本提案與 Stage 4 無資料或程式碼依賴——Stage 4 已完成的
  `dispatch_domain.py`／`capability_lanes.yaml` 只是功能二的唯讀資料
  來源，本提案不修改、不依賴 Stage 4 的任何執行路徑。

### 0.3 本提案範圍（第一批，三項，使用者已拍板鎖定，不可夾帶其他項目）

1. **Stage 3 — Hermes session 列表**（第 2 節）：沿用 roadmap 已寫死的
   四條 DoD，依賴已解（只依賴 `HermesSessionAdapter`，已完成），無 start
   blocker。
2. **憑證／Capability Lane 唯讀狀態檢視**（第 3 節，本提案設計重心）：
   套用 `get_adapter_config_status()` 已示範的安全模式，資料來源是
   `registry/capability_lanes.yaml`（純靜態治理資料）＋各 Hermes profile
   `auth.json` 的白名單欄位。
3. **統一排程健康表（systemd timer ＋ Hermes 原生 cron）＋模型漂移旗標**
   （第 4 節，**v2 擴大範圍**）：在 v1「既有 `get_adapter_config_status()`／
   `get_systemd_status()` 延伸」之上，v2 額外納入 Hermes 原生 cron
   （`jobs.json`）的讀取，用 `source` 欄位涵蓋兩套排程機制，並對原生 cron
   的 unpinned agent job 加一個純唯讀模型漂移旗標。純讀取，符合唯讀鐵律。

### 0.4 明確排除範圍（第二批，另案，不在本提案內展開設計）

- **Provider／模型使用量統計、Token 量化統計**：需要先決定 `jobs.db`
  schema 擴充與 `dispatch_domain.py` usage envelope 落地方式，屬於
  「第二批」。**本提案不含，待資料層決策後另案規劃**——本文件不展開
  細節，也不預先假設該資料層會長什麼樣子。
- **任務聊天視窗、開發預覽沙盒、更換 Hermes Profile 模型（寫入型設定）**：
  這三項**明確排除**，等使用者釐清範圍／是否接受打破 Dashboard 現行
  唯讀鐵律後再議。特別是「更換 Hermes Profile 模型」本質上是一個寫入
  操作，若要做，代表 dashboard 要第一次跨過「唯讀」這條線——這是一個
  需要使用者獨立、明確表態的決定，不是本提案可以順帶假設答案的事。
  本提案不展開這三項的任何設計。
- **（v2 明確補充）寫入型「更換／pin profile 或 cron job 模型」動作一律不在
  Stage 3**：特別是「一鍵自動對齊漂移 job 到新模型」——它本質上是繞過使用者
  自設的花費保護（#44585），且方向上專挑更貴模型（全域模型變動來源含
  auto-raise，通常往更貴跳），明確不做。功能三 v2 的模型漂移旗標**只偵測、
  只標示**，不提供任何修復動作。獨立的第四項漂移偵測 feature 也不做——漂移
  偵測只當功能三的一個唯讀欄位存在。

### 0.5 技術邊界重申（延續 Dashboard 既有設計原則，本提案不放寬）

1. **全部唯讀**：本提案三項功能全部只讀取，不新增任何寫入路徑、不新增
   任何「重跑」「核准」「修改設定」之類的操作按鈕。**（v2 重申）功能三的
   模型漂移旗標同樣只讀取比對、只顯示，絕不新增任何 pin／對齊模型的寫入
   按鈕。**
2. **SQLite 一律 `mode=ro`**：功能一沿用 `HermesSessionAdapter` 既有的
   `mode=ro` + `PRAGMA query_only=ON` 雙保險；功能二／三不涉及 SQLite
   （讀 YAML／JSON／呼叫 `systemctl`；功能三 v2 新增的原生 cron 讀取也是
   讀 `jobs.json` 這個 JSON 檔案，非 SQLite），不新增資料庫連線。
3. **不 import 任何有寫入能力的模組**：`dashboard/data.py` 現在不 import
   `hermes/db.py`，本提案新增的三個函式同樣不 import 任何寫入模組
   （`hermes/session_adapter/adapter.py` 的 `HermesSessionAdapter` 是
   唯讀類別，可以安全 import；`hermes/bridge_dispatch.py`／`hermes/db.py`
   等有寫入函式的模組，本提案不 import）。**（v2 重申）功能三讀 Hermes
   原生 cron 一律走「直接讀 `jobs.json` 檔案文字」的純檔案讀取路徑，
   不 import `cron.jobs` 的 `update_job` 等任何寫入函式。**
4. **憑證檢視頁面的硬性要求**（第 3 節詳述）：`access_token`／
   `refresh_token`／任何 token/key 欄位值**絕對不能**出現在網頁輸出裡
   ——這是硬性要求，不是建議，也是本提案能否安全落地的核心判準。

---

## 1. 現況盤點（2026-07-21，對照實際程式碼與設定檔，不猜測）

### 1.1 功能一（Hermes session 列表）現況

- `HermesSessionAdapter`（`hermes/session_adapter/adapter.py`）已完成、
  已有測試（`hermes/session_adapter/tests/test_adapter.py`），提供
  `list_sessions(source=None, since_epoch=None)`，回傳
  `claudecodeos.session.v1` normalized dict：`session_id`／
  `session_source`（`cli|tui|telegram|cron`）／`title`／`model`／
  `started_at`／`ended_at`／`end_reason`／`message_count`／`metadata`
  （`user_id`／`session_key`／`chat_id`／`chat_type`／`thread_id`／
  `parent_session_id`／`archived`）——**完全不含 `messages.content`**，
  message 內容只在 `iter_events()` 另一個方法才會被讀取，`list_sessions()`
  本身天然不外洩全文。
- state.db 路徑在 Windows 端自動偵測為 `%LOCALAPPDATA%\hermes\state.db`
  （`adapter.py` 第 153–163 行 `_default_db_path()`）——與 dashboard
  同樣運行在 Windows 端（`dashboard/README.md` 明文啟動指令是 Windows
  `.venv/Scripts/python.exe`），**沒有跨主機讀取的複雜度**，不需要
  透過 WSL 橋接。
- Read-only 保證已技術強制：`mode=ro` + `PRAGMA query_only=ON`；
  `snapshot=True` 模式處理「Hermes 正在運行、state.db 正被 WAL 寫入」
  時的鎖競爭（複製來源三檔＋fingerprint 一致性比對＋副本
  `quick_check`，`adapter.py` 第 22–33 行）。這正是 roadmap DoD 第 2 條
  「Windows Hermes 運行中仍可用」要求的機制，**已經存在，不需要新開發**。
- **依賴狀態**：只依賴 `HermesSessionAdapter`（已完成），無其他前置。
  **無 start blocker**，可以直接規劃實作步驟（第 2 節）。

### 1.2 功能二（憑證／Lane 狀態）現況

**`registry/capability_lanes.yaml`（純靜態治理資料，無安全疑慮）**：

- 現況 7 條 lane，全部 `status: active`（2 條 native lane：
  `claude-native`／`claude-architecture-reasoning`；4 條 `hermes_profile`
  lane：`hermes-nemocoding`／`hermes-gptcoding`／
  `hermes-financialresearch`／`hermes-intelligence`；OpenRouter 三條
  lane 已於 2026-07-20 全數移除，不存在殘留 reference lane）。
- 每條 lane 的欄位：`id`／`capability`／`execution`／`provider`／
  `model`（可為 `null`）／`hermes_profile`（選填）／`status`／
  `cost_tier`（`included|free|paid|unknown`）／`risk_tier`
  （`low|medium|high`）／`allowed_agents`／`intended_use`／
  `guardrails`——**這整份 YAML 已經 commit 進 git，本來就不是秘密**，
  不需要任何過濾即可直接呈現全部欄位。

**Hermes profile `auth.json`（含敏感欄位，需要嚴格過濾）**：

- 路徑（已於 Stage 0 系列查證，`docs/hermes-shared-storage-bootstrap.md`／
  `capability_lanes.yaml` 註記確認）：
  - Global-root：`%LOCALAPPDATA%\hermes\auth.json`
  - 各 profile：`%LOCALAPPDATA%\hermes\profiles\<profile>\auth.json`
- 現存的 Windows Hermes profile（`codereviewer` 已於 2026-07-20 Phase 2a
  隨使用者拍板移除，不應再出現在任何列舉裡）：`default`／`gptcoding`／
  `nemocoding`／`financialresearch`／`intelligence`——共 5 個。
- 結構依 `capability_lanes.yaml` 各 lane 內大量稽核註記交叉確認：
  `credential_pool.<provider>` 是該 provider 的憑證清單（每個 profile
  可能對同一 provider 有多筆，例如 Stage 4 稽核時 `gptcoding`／
  `nemocoding`／`financialresearch` 的 `openai-codex` 都曾一度有兩筆）；
  每筆 entry 至少含 `id`／`priority`／`last_status`／`last_refresh`／
  `source`／`label` 這類治理欄位，**以及** `access_token`／
  `refresh_token` 等敏感欄位（教訓一實際印出過 `nous` provider 的完整
  JWT、Tavily API key）。此外可能有頂層 `providers` 清單（僅 provider
  名稱字串）與 `suppressed_sources`（抑制標記，記錄哪個 provider 的哪個
  來源被抑制，內容本身不含 token，但本提案仍要求以白名單方式讀取，不
  假設安全）。
- **誠實標註**：以上結構是**交叉比對既有文件與 `capability_lanes.yaml`
  稽核註記後的最佳理解**，`planning` domain 本身**沒有、也不應該**去讀
  真實 `auth.json` 驗證精確 schema——直接讀憑證檔案本身就是教訓一要避免
  的行為，規劃階段不該為了「確認一下結構」去踩這個雷。**engineering
  在第 3.3 節實作的第一步，必須用白名單／程式化方式（例如只印
  `json.load()` 後的頂層 key 名稱清單，不印任何 value）獨立驗證精確
  schema**，本節描述的欄位名稱是設計依據，不是保證完全精確的最終規格。

**事故背景**（`memory/hermes-credential-handling-safety-lessons.md`，
本提案功能二的直接動機）：

- **教訓一**：通用 `Read` 工具沒有欄位過濾能力，只要讀取憑證檔案，內容
  就會完整進入對話紀錄——至少 3–4 次因此印出明文 token（`nous` JWT、
  Tavily key 兩次、`intelligence`／`gptcoding` 憑證各一次）。**這是工具
  限制，不是操作失誤**——對應本提案設計：頁面本身必須用程式化白名單
  讀取，不能給操作者任何「順手看一下原始檔案」的管道。
- **教訓四**：官方指令看似沒生效時，subagent 自行改用內部函式繞過，
  且回報時掩蓋此事。**這條教訓提醒本提案的邊界要「唯讀」到連
  「嘗試修復」的衝動都沒有空間存在**——本頁面只呈現狀態，不提供任何
  「一鍵清除」「重新登入」之類的操作入口，即使技術上可行，也刻意不做
  （對應第 0.4 節排除的寫入型功能）。
- 現況遺留：`nous` provider JWT 殘留憑證清理**截至 2026-07-21 仍未完成
  且暫停中**（見該 memory 文件「共通處置現況」）——這正說明了「快速
  安全確認憑證狀態」這個能力現在還不存在，只能靠人工逐次稽核，本提案
  要補的就是這個缺口。

### 1.3 功能三（Cron／Timer 排程表）現況

- `hermes/config/cron_jobs.yaml` 目前只定義一筆 job
  （`daily-memory-check`），且**明文設計為「只定義 job 名字與 prompt，
  不定義排程時間」**——排程時間寫在部署層的 `.timer` 檔（`OnCalendar=`）。
  `get_adapter_config_status()` 現有的 `cron` 區塊只回報
  `job_count`（設定檔裡定義了幾個 job），**不含任何排程時間資訊**。
- 實際排程時間定義在 `hermes/systemd/*.timer`（`OnCalendar=` 欄位，
  static、commit 進 git、非秘密），現況共 6 組 service+timer 對：
  `hermes-rss`（每 30 分鐘）、`hermes-cron-daily-memory-check`
  （每天 08:00）、`hermes-bridge-scanner`（每天 08:05）、`hermes-bridge`
  （每天 08:10）、`hermes-bridge-pipeline`（每天 08:15，Stage 2.7b 新增，
  **尚未 enable／部署**）、`hermes-bridge-notifier`（每天 08:25，同上，
  **尚未部署**）；另有 2 個常駐 service 無 timer（`hermes-worker`／
  `hermes-telegram`，`KeepAlive` 型態，「排程」的概念不適用）。
- `get_systemd_status()`（`dashboard/data.py`）現況：跑
  `systemctl --user list-units --all --type=service,timer`，只回報
  `active_state`／`sub_state`／`load_state`，篩選條件是 unit 名稱含
  `hermes-`——**目前完全不含 schedule expression、不含下次觸發時間、
  不含「上次執行成功或失敗」這種細緻結果**，只有粗粒度的
  `active`／`inactive` 二元狀態。
- `app.py` 現有的 `SYSTEMD_LABELS` 只挑 4 個 unit（`hermes-worker`／
  `hermes-telegram`／`hermes-cron-daily-memory-check.timer`／
  `hermes-rss.timer`）在總覽頁做極簡摘要，**沒有涵蓋 bridge 系列三個
  timer**，也沒有任何「排程表」形式的完整列表。
- **（v2 補充）Hermes 原生 cron（`jobs.json`）現況**：使用者實際在跑、
  且最常出問題的排程 job（AI news 這類原生 cron agent job、garmin 這類
  `no_agent` script job）**不走 systemd timer，而是跑在 Hermes 原生
  cron 排程器**，其 job 定義與執行狀態記錄在 `%LOCALAPPDATA%\hermes\cron\jobs.json`
  （跨 profile 時各 store 有各自的 `jobs.json`）。v1 的功能三只觀測
  systemd timer，**完全看不到這批原生 cron job**——這是 v2 擴大範圍要
  補的核心缺口（見第 4 節）。原生 cron 的 unpinned agent job 每次觸發跟隨
  全域 default 模型，建立時記一個 `model_snapshot` 當 drift tripwire；
  觸發時若「有 snapshot ＋該軸未 pin ＋現值 != snapshot」，花費保護
  （#44585）會 fail-closed（raise RuntimeError、不發 inference）——這正是
  使用者「換全域模型後隔天才被動發現某個 job 壞掉」痛點的機制來源
  （詳見 `memory/hermes-cron-model-pin-convention.md`）。

---

## 2. 功能一設計 — Stage 3 Hermes Session 列表

### 2.1 Definition of Done（沿用 roadmap 原文四條，逐字不改寫）

1. Dashboard 新頁面列出 Hermes sessions（含 source／title／時間／
   message_count）。
2. Windows Hermes 運行中時頁面仍可用（snapshot／immutable 讀取路徑）。
3. read-only 與既有 dashboard 同等級技術強制（`mode=ro`／`immutable=1`，
   不 import 寫入層）。
4. 不外洩敏感內容——列表層預設不渲染 `messages.content` 全文，或明確
   標註風險後由使用者決定。

### 2.2 資料層設計

新增 `dashboard/data.py::get_hermes_sessions(source=None, limit=200) -> list[dict]`：

```python
def get_hermes_sessions(source: str | None = None, limit: int = 200) -> list[dict]:
    """唯讀列出 Hermes sessions。snapshot=True——Hermes 運行中時仍可讀
    （WAL 鎖競爭由 HermesSessionAdapter 內部處理，見 adapter.py 第 22-33 行）。
    找不到 state.db（環境沒裝 Hermes 或路徑不同）回傳 []，不噴例外。
    """
```

- 內部 `import` `hermes/session_adapter/adapter.py` 的
  `HermesSessionAdapter`（唯讀類別，符合第 0.5 節「可安全 import」的
  判準——它不是 `hermes/db.py`）。
- 一律 `HermesSessionAdapter(snapshot=True)`：不假設 Hermes 有沒有在跑，
  一律走 snapshot 路徑（DoD 第 2 條的直接實作方式），呼叫完
  `adapter.close()` 清掉 temp 目錄（沿用既有 context manager
  `__enter__`/`__exit__`）。
- 找不到 `state.db`（`FileNotFoundError`）→ 回傳 `[]`，比照
  `jobs_db_exists()` 的既有容錯慣例（`dashboard/app.py` 已有「jobs.db
  尚未建立」的警告呈現先例，這裡沿用同一種「缺檔不是例外」的設計語言）。
- `limit` 參數：`list_sessions()` 本身不支援分頁，函式內部取全部結果後
  在 Python 層依 `started_at` 反排序取前 `limit` 筆（沿用
  `get_recent_jobs()` 的 `limit` 語意）。

### 2.3 UI 設計

- `app.py` 新增一個 tab `"Hermes Sessions"`（沿用既有 `st.tabs()` 模式，
  插入既有 5 個 tab 之後）。
- 表格欄位：`session_id`／`session_source`／`title`／`model`／
  `started_at`／`ended_at`／`message_count`——**不含 `metadata` 內的
  `session_key`／`chat_id` 等欄位**（這些是路由用中繼資料，非本頁面
  觀測目的所需，減少不必要的資訊暴露面，即使它們本身不是憑證）。
- 篩選器：依 `session_source`（`cli|tui|telegram|cron`）下拉篩選，沿用
  既有 Jobs 頁籤的篩選器 UI 模式（`st.selectbox`）。
- **DoD 第 4 條的具體實作**：預設**不**顯示任何訊息內容欄位（列表層
  本來就不含 `content`，`list_sessions()` 回傳的 dict 天然不含這個欄位，
  UI 層也不會額外呼叫 `iter_events()`）。**明確決定：v1 不做「點進去看
  對話全文」的功能**——`iter_events()` 會讀 `messages.content`，這正是
  DoD 第 4 條要求「明確標註風險後由使用者決定」的分支；本提案選擇
  **不做**這個分支（比照第 0.4 節「任務聊天視窗」排除範圍的精神——
  session 全文檢視本身就有内容可能含敏感資訊的疑慮，留待使用者未來
  明確要這個功能時才评估）。列表頁本身即完整滿足 DoD 四條。

### 2.4 測試策略

- 沿用 `dashboard/test_data.py` 既有模式：暫存 state.db fixture（可用
  `hermes/session_adapter/tests/test_adapter.py` 既有的 fixture 建置
  方式），對 `get_hermes_sessions()` 斷言：
  1. 正常情境回傳 normalized 欄位、`content` 欄位不存在於任何回傳
     的 dict（遞迴檢查整個回傳結構不含 `content` 這個 key）。
  2. state.db 不存在時回傳 `[]`，不噴例外。
  3. `source` 篩選正確。
- `dashboard/test_app.py`：用既有 `AppTest` 模式對新 tab 跑一次，確認
  不噴例外。

### 2.5 風險

| 風險 | 緩解 |
|---|---|
| Hermes 運行中讀取 state.db 撞到 WAL 鎖 | `snapshot=True`（已有機制，DoD 第 2 條原生涵蓋） |
| 誤加欄位間接外洩訊息片段 | UI 層欄位白名單明確列舉（第 2.3 節），不用 `st.json()` 整個 dict 硬塞 |
| state.db 路徑在非 Windows 開發環境不存在 | 沿用既有「缺檔不噴例外」慣例（第 2.2 節） |

---

## 3. 功能二設計 — 憑證／Capability Lane 唯讀狀態檢視（本提案設計重心）

### 3.1 動機（重申，呼應第 1.2 節事故背景）

Stage 4 過程中反覆發生憑證處理事故（教訓一～四），核心問題之一是
**「沒有安全管道快速確認憑證/lane 狀態」**——每次要確認「這個 profile
的這個 provider 憑證還在不在、上次刷新是什麼時候」，都要靠 subagent
臨場用 `Read` 或 CLI 手動查，容易一不小心就把完整 token 印進對話紀錄。
本功能的目的是**提供一個技術上就不可能外洩秘密值的唯讀檢視**，讓「確認
憑證治理狀態」這件事不再需要碰觸原始憑證檔案的完整內容。

### 3.2 唯讀邊界（核心設計——這是本功能能否安全落地的關鍵，逐條列死）

**可以顯示的欄位（allowlist，白名單制，不是黑名單制）**：

- `registry/capability_lanes.yaml` 的**全部欄位**（`id`／`capability`／
  `execution`／`provider`／`model`／`hermes_profile`／`status`／
  `cost_tier`／`risk_tier`／`allowed_agents`／`intended_use`／
  `guardrails`）——這份檔案已 commit 進 git，本來就是公開治理資料，
  不需過濾。
- 每個 Hermes profile `auth.json` 的 `credential_pool.<provider>` 陣列
  裡，**每筆 entry 只抽取以下欄位，其餘一律捨棄，不論欄位叫什麼名字**：
  `id`／`priority`／`last_status`／`last_refresh`／`provider`／
  `source`／`label`。
- 頂層 `providers`（若存在）：只顯示 provider 名稱字串清單，不展開任何
  entry 內容（entry 內容一律走上面那條白名單邏輯）。
- `auth.json` 本身的存在與否、檔案 `mtime`——純中繼資訊，可顯示。
- 每個 provider 底下的 entry **筆數**（例如「`openai-codex`：2 筆
  憑證」）——這是治理上最常需要的「有沒有共用憑證」判斷依據，數字本身
  不是秘密。

**絕對不能顯示的欄位（不論用什麼理由，沒有例外）**：

- `access_token`／`refresh_token`／`id_token`／任何名稱包含
  `token`／`key`／`secret`／`credential`（不分大小寫）子字串的欄位值
  ——即使白名單機制理論上不會抽到這些欄位，**仍然加一層防呆**（見下方
  「雙重防護」）。
- JWT 原始字串或其解碼後的 payload 內容（教訓三的越權事件正是「解碼
  JWT payload 擷取身分資訊」——本功能連「安全地解碼看內容」這個選項都
  不提供，因為那正是教訓三定義的越權行為型態，不留這個技術可能性）。
- `suppressed_sources` 的原始值本身（雖然目前理解它只是抑制標記，不含
  token，但**本提案採取保守立場**：這個欄位不在白名單內，若未來需要
  顯示，需要另外評估後才加入白名單，不預設安全）。
- `.env`／任何非 `auth.json` 的憑證檔案內容——本功能範圍嚴格限定
  `capability_lanes.yaml` ＋各 profile `auth.json` 的白名單欄位，不
  擴及 Hermes home 底下其他任何檔案。

**雙重防護（defense in depth，即使白名單已經足夠，仍加這一層）**：

1. **資料層只建構新 dict，不傳遞原始 dict**：讀取函式解析
   `auth.json` 後，**必須**逐欄位手動組出一個全新的、只含白名單欄位的
   dict 回傳——**不能**先把整份原始 JSON load 進一個變數再企圖用某種
   `del`／filter 方式從裡面挑，因為那種寫法一旦漏改一行，原始 dict 裡
   還留著完整秘密值的參照就可能被後面的程式碼（現在或未來的修改）不小心
   傳給 UI 層。正確寫法是「只從原始資料讀取白名單欄位、組進新物件」，
   原始的完整 dict 讀完立刻超出函式作用域、不外流。
2. **輸出前二次掃描**：回傳給 UI 層之前，對整個最終回傳結構做一次
   遞迴的字串內容檢查——任何字串值若長度超過某個門檻（例如 100 字元）
   且看起來像 base64／JWT（`eyJ` 開頭）或高熵字串，直接以
   `"[REDACTED-SUSPECT-SECRET]"` 取代並記一筆警告（不是丟例外中斷
   整頁——寧可某個治理欄位被誤判擋掉，也不要放過一個真正的秘密值）。
   這條防線的目的是**防止白名單本身寫錯**（例如把 `access_token`
   誤植成白名單允許的欄位名稱）時還有最後一道防呆。
3. **實作與測試都不得用 `Read`／`cat`／無欄位過濾的方式碰觸真實
   `auth.json`**（教訓一直接對應）：engineering 實作時驗證 schema，
   一律用「只印頂層 key 名稱清單」「只印某個 entry 的 key 名稱清單」
   這種程式化、漸進式窄化的方式（例如
   `python -c "import json; print(list(json.load(open(p)).keys()))"`），
   絕不對著真實憑證檔案做一次完整 `json.dumps(..., indent=2)` 式的
   全量印出，即使只是為了「確認結構」。測試資料一律用假 fixture（帶假
   token 字串如 `"FAKE_TOKEN_abc123"`），不得使用真實憑證片段。

### 3.3 資料層設計

新增兩個函式：

```python
def get_capability_lane_status() -> list[dict]:
    """直接回傳 registry/capability_lanes.yaml 的 lanes 全部欄位——
    這份檔案本身無安全疑慮，不需過濾。"""

def get_hermes_credential_status() -> dict:
    """回傳每個已知 Hermes profile（含 global-root）的憑證治理狀態，
    只含白名單欄位（見提案 §3.2）。任何一個環節找不到檔案／解析失敗，
    該 profile 標記為 {"auth_json_exists": False} 或 {"error": "..."},
    不中斷其他 profile 的呈現。"""
```

- **profile 清單來源**：不是硬編死清單，而是動態掃描
  `%LOCALAPPDATA%\hermes\profiles\` 底下的子目錄名稱（目錄名稱本身不
  敏感，可以安全列舉）——這樣未來新增/移除 profile（例如
  `codereviewer` 那次的移除）不需要改 dashboard 程式碼。額外固定包含
  global-root（`%LOCALAPPDATA%\hermes\auth.json`）一項，標記為特殊的
  `"(global-root)"` 條目。
- **路徑常數**：`HERMES_HOME = Path(os.environ.get("LOCALAPPDATA", "")) / "hermes"`
  ——這是 dashboard/data.py **第一次**讀取 repo 之外、`ROOT` 以外的
  路徑。必須明確處理 `LOCALAPPDATA` 環境變數不存在的情況（非 Windows
  開發環境）→ 整個函式回傳空 dict 或明確標記「此環境無法查詢」，不噴
  例外（比照 `get_launchd_status()`／`get_systemd_status()` 既有的
  「環境不支援就回傳空結果」慣例）。
- 每個 profile 的讀取邏輯：
  1. 檢查 `auth.json` 是否存在，不存在 → `{"auth_json_exists": False}`。
  2. 存在 → `json.load()` 解析；解析失敗（壞 JSON）→
     `{"auth_json_exists": True, "error": "設定檔格式錯誤"}`（沿用
     `get_adapter_config_status()` 對 `telegram.json` 壞 JSON 的既有
     處理模式），**不嘗試用其他方式讀取內容去除錯**。
  3. 解析成功 → 對 `credential_pool` 底下每個 provider，逐筆抽取白名單
     欄位（第 3.2 節「雙重防護」第 1 點的組新 dict 寫法），額外附上
     `entry_count`。
  4. 頂層 `providers`（若存在）→ 只取 provider 名稱清單。
  5. 對整個組出來的 profile-level 結果跑第 3.2 節「雙重防護」第 2 點的
     輸出前掃描。

### 3.4 UI 設計

- `app.py` 新增一個 tab `"憑證／Lane 狀態"`。
- 區塊一：`st.dataframe(pd.DataFrame(get_capability_lane_status()))`——
  直接呈現 lane 治理表格（id/capability/provider/hermes_profile/status/
  cost_tier/risk_tier/allowed_agents/intended_use）。
- 區塊二：對每個 profile（含 global-root）用 `st.expander` 展開顯示
  該 profile 各 provider 的白名單欄位表格（`entry_count`／`id`／
  `priority`／`last_status`／`last_refresh`／`source`／`label`）。
- 頁面最上方固定顯示一行**不可移除**的警語文字：「本頁僅顯示治理層級
  的中繼資訊（有沒有設定、設定了幾筆、上次更新時間），絕不顯示
  token／key 等憑證值本身」——比照現有 Adapter 設定狀態區塊已有的
  類似說明文字慣例（`app.py` 第 107 行）。

### 3.5 Definition of Done

1. `get_capability_lane_status()`／`get_hermes_credential_status()` 兩個
   函式皆有單元測試，測試資料為假 fixture（不得使用真實憑證片段）。
2. **核心安全測試（比照 `test_data.py` 既有的 telegram bot_token 不外洩
   測試先例，本測試優先級最高）**：對一份含假秘密值（`access_token`：
   `"FAKE_SECRET_abcdef123456"`、`refresh_token`：另一組假值）的 fixture
   `auth.json`，斷言 `get_hermes_credential_status()` 的**整個**回傳
   結構（遞迴字串搜尋，包含所有巢狀 dict/list 裡的每一個字串值）都
   **不包含**這兩個假秘密值的任何子字串。
3. 對「白名單欄位以外還有其他未知欄位」的 fixture entry（模擬未來
   schema 新增欄位的情境）斷言：未知欄位不會出現在輸出中（allowlist
   語意驗證，而不是只驗證已知的敏感欄位被擋）。
4. 第 3.2 節「雙重防護」第 2 點的輸出前掃描邏輯有獨立測試：故意在白名單
   欄位裡塞一個看起來像 JWT 的假字串（模擬白名單寫錯的情境），斷言
   仍被攔下顯示為 `[REDACTED-SUSPECT-SECRET]`。
5. `LOCALAPPDATA` 環境變數不存在／profile 目錄不存在／`auth.json` 壞
   JSON 三種情境皆有測試，函式不噴例外。
6. `dashboard/test_app.py` 對新 tab 跑 `AppTest`，確認不噴例外、且用
   同樣的「不含假秘密值子字串」斷言掃過整個渲染輸出（比照既有
   `test_app.py` 對 bot_token 的既有测试模式，擴大掃描範圍到新 tab）。
7. engineering 實作前**必須**先用第 3.2 節「雙重防護」第 3 點描述的
   安全方式（程式化、只印 key 名稱、不印完整內容）對真實環境的
   `auth.json` 做一次 schema 驗證，把驗證結果（僅限欄位名稱清單，不含
   任何值）記錄在 PR 描述或 commit message 裡，作為「本節第 1.2 節描述
   的 schema 假設已驗證/有落差」的證據。

### 3.6 測試策略（重申，因為這是本提案安全性最關鍵的一環）

- 所有測試 fixture 一律用**假造**的 token 字串（清楚標示
  `FAKE_`／`TEST_` 前綴），不使用、不衍生自任何真實憑證片段。
- 測試斷言的**主要工具**是「遞迴掃描整個回傳結構／整個渲染文字輸出，
  搜尋已知假秘密字串是否出現」，而不只是「檢查白名單欄位存在」——
  後者只驗證了正向情境（有欄位就顯示），沒有驗證負向情境（沒列在白
  名單的欄位真的被擋下），兩種斷言都要有。
- CI／本機測試執行時，**不得**把任何 fixture 檔案命名或放置在容易與
  真實 `hermes/config/` 或 `%LOCALAPPDATA%\hermes\` 混淆的路徑——沿用
  `test_data.py` 既有的 `tempfile.mkdtemp()` 隔離模式。

### 3.7 風險

| 風險 | 影響 | 緩解 |
|---|---|---|
| 白名單漏列導致敏感欄位意外顯示 | 憑證外洩，安全事故重演 | 雙重防護（組新 dict＋輸出前掃描，§3.2）；DoD 第 2/3/4 項測試專門針對這個風險設計 |
| `auth.json` 實際 schema 與本提案理解有落差 | 白名單欄位名稱對不上，可能漏擋或誤擋 | DoD 第 7 項強制要求 engineering 實作前先安全驗證 schema |
| 讀取路徑跨出 repo（`%LOCALAPPDATA%`）引入新的環境依賴 | 非 Windows／非本機環境下功能不可用 | 比照既有 `get_launchd_status()` 慣例：環境不支援時回傳空結果，不噴例外，頁面顯示「無法查詢」 |
| 未來 Hermes 版本升級改變 `auth.json` schema | 白名單機制可能整批失效（找不到預期欄位）或者未預期新欄位含秘密值卻被誤判安全 | Allowlist（而非 blocklist）設計本身是主要防線——即使 schema 改變，新欄位預設不顯示，不會「預設外洩」；輸出前掃描是第二層防線 |

---

## 4. 功能三設計 — 統一排程健康表（systemd timer ＋ Hermes 原生 cron）＋模型漂移旗標

> **v2 實質範圍變更標注**：v1 的功能三只觀測 systemd timer（6 組 timer 驅動
> 任務）。v2 依使用者 2026-07-22 拍板，將功能三擴大為「統一排程健康表」，
> **同時涵蓋 systemd timer 與 Hermes 原生 cron（`jobs.json`／原生排程器）**，
> 並對原生 cron 的 unpinned agent job 新增一個純唯讀的「模型漂移旗標」欄位。
> 以下 4.1–4.7 為據此改寫後的完整設計；systemd timer 部分沿用 v1 既有設計不變，
> v2 新增的是「Hermes 原生 cron 讀取層」與「模型漂移旗標」，兩者皆純唯讀。

### 4.1 現有基礎與擴充方向

現有 `get_adapter_config_status()` 的 `cron` 區塊只回報
`job_count`；`get_systemd_status()` 已能取得 `hermes-*` unit 的
`active_state`／`sub_state`／`load_state`。本功能是這兩者的自然延伸：
**把「有沒有裝、裝了在跑沒、上次跑得怎樣、下次何時跑」整合成一張完整
表格**，不需要新的資料庫連線。

**（v2 核心擴充動機）為什麼要納入 Hermes 原生 cron**：使用者真正的痛點 job
——AI news（原生 cron agent job）、garmin（`no_agent` script job）——**不走
systemd timer，而是跑在 Hermes 原生 cron 排程器**。v1 的 systemd-timer-only
設計完全看不到它們，會讓一張叫「排程健康表」的頁面漏掉使用者最在意、最常壞
的那批 job，名不副實。因此 v2 把原生 cron（`jobs.json`）也納入，並用 `source`
欄位（`systemd`／`hermes-native`）明確標示兩套機制，讓表格涵蓋「這個系統
到底有哪些排程、各自健不健康」的全貌。

**範圍界定**：本表格涵蓋**兩套排程機制**的排程任務：

- **(A) systemd timer 驅動的任務（原 v1 範圍，`source="systemd"`）**：涵蓋
  現況 6 組 timer 對，含狹義透過 `hermes/adapters/cron.py`＋
  `hermes/config/cron_jobs.yaml` 這條路徑的 `daily-memory-check`，也包含
  `hermes-bridge-scanner`／`hermes-bridge`／`hermes-bridge-pipeline`／
  `hermes-bridge-notifier` 這幾個直接呼叫對應 Python 腳本的 timer。
- **(B) Hermes 原生 cron 的排程 job（v2 新增，`source="hermes-native"`）**：
  涵蓋 `jobs.json` 裡註冊的 agent job（如 AI news）與 script job（`no_agent`，
  如 garmin）。

常駐服務（`hermes-worker`／`hermes-telegram`，無 timer）不納入本表格——它們是
`KeepAlive` 型態，「排程」「下次觸發」的概念不適用，繼續留在既有總覽頁的服務
狀態區塊。

### 4.2 資料層設計

擴充 `dashboard/data.py::get_cron_schedule_table() -> list[dict]`，回傳一張
統一表格，每筆帶 `source` 欄位。內部由兩條**彼此獨立**的讀取路徑合併，
一邊不可用不影響另一邊。

**路徑 A — systemd timer（沿用 v1 設計，`source="systemd"`）**：

1. **靜態排程表達式（來源：repo 內 `hermes/systemd/*.timer` 檔案文字，
   git 版本控制、非秘密）**：對每個 `.timer` 檔案，parse `[Timer]` 區塊
   的 `OnCalendar=` 值與對應的 `Unit=`（找不到 `Unit=` 則用同名
   `<name>.service` 推斷）。這一步**不呼叫 `systemctl`**，純粹讀 repo
   內文字檔——即使目標環境完全沒裝 systemd，這一層資訊永遠可得。
2. **部署狀態（來源：既有 `get_systemd_status()`，本函式呼叫既有函式，
   不重複實作 `systemctl --user list-units` 呼叫）**：對每個 timer 對應
   的 `.timer` 與 `.service` unit，取得 `active_state`／`sub_state`。
3. **新增一次 subprocess 呼叫**：
   `systemctl --user list-timers --all --no-legend`，取得 `NEXT`／
   `LAST` 欄位（下次觸發時間／上次觸發時間），比照
   `get_systemd_status()` 既有的容錯模式——`FileNotFoundError`／
   `TimeoutExpired`／`OSError` 一律回傳 `{}`，不噴錯。只保留 `UNIT`
   含 `hermes-` 的行。
4. **「上次執行結果」的判斷**：不新增第三次 subprocess 呼叫，直接複用
   步驟 2 已取得的 `.service` unit 的 `sub_state`——systemd 對 oneshot
   service 的既有語意：成功執行完畢後 `sub_state` 回到
   `dead`／`exited`（視版本），失敗則進入 `failed`，**這個既有語意本身
   就是「上次執行結果」**，不需要額外查詢。

**路徑 B — Hermes 原生 cron（v2 新增，`source="hermes-native"`）**：

1. **資料來源**：Hermes 原生 cron store —— root 的
   `%LOCALAPPDATA%\hermes\cron\jobs.json`；跨 profile store 時，沿用既有
   dashboard「單一 process 掃多 store」能力（即 `web_server.py` 的
   `use_cron_store` 模式）逐一讀取各 store 的 `jobs.json`。**純檔案讀取
   （`json.load()`），不呼叫任何 Hermes 寫入路徑、不 import `cron.jobs`
   的 `update_job` 等寫入函式**（符合 §0.5 唯讀鐵律）。
2. **每個 job 抽取**：`job_name`（job id／name）、`schedule_expr`（cron
   表達式）、以及原生 cron 排程器記錄的執行狀態欄位 `last_status`／
   `last_error`／`next_run_at`（以及上次執行時間，若有記錄）。實際欄位
   名稱以 engineering 用安全方式（只印 key 名稱、不印敏感 value）驗證
   `jobs.json` schema 為準（見 DoD）；本節欄位是設計依據，非最終規格。
3. **對應到統一表格欄位**：
   - `deployed`：對原生 cron 恆為 `True`（存在於 `jobs.json` 即代表已註冊）。
   - `timer_active`：以 job 的 `enabled` 狀態，或原生排程器是否在跑
     （`hermes-worker` 是否 active）呈現。
   - `last_result`：取 `last_status`／`last_error`。
   - `next_trigger`：取 `next_run_at`。
   - `last_trigger`：取上次執行時間（若原生 cron 有記錄，否則「從未觸發」）。

**模型漂移旗標（v2 新增，只對 Hermes 原生 cron 的 unpinned agent job）**：

- 對每個原生 cron 的 **agent job**（`no_agent` 為 `False`／未設），若該 job
  有 `model_snapshot`、且模型軸未 pin，則比對 `model_snapshot` vs 當前全域
  `config.yaml` 的 `model.default`：
  - 相同 → `model_drift: "aligned"`。
  - 不同 → `model_drift: "DRIFTED"`，並附 `drift_cost_direction`：以
    lane／`cost_tier` 或已知 provider 定價方向判斷「更貴／更便宜／相同／
    未知」（**純標示**，供使用者判斷，不做任何自動修復）。
- **script job（`no_agent: True`，如 garmin）不做 inference、不受 #44585
  花費保護影響**，`model_drift` 一律標為 `"n/a"`，不納入漂移判斷。
- **動機**：使用者痛點是「換全域模型後隔天才被動發現某個 unpinned job 因
  fail-closed 壞掉」。全域模型漂移是**低頻事件**（使用者說主要在新增 profile
  的測試期才改模型），但一旦讀進原生 cron 資料，這個比對是**零邊際成本的
  搭便車**，能把「下次觸發會 fail-closed」的 job 在觸發前就在表上標出來，
  正面消滅「隔天才發現」的被動性。
- **純唯讀重申**：本旗標只「偵測並標示」，**絕不提供任何「一鍵對齊／pin
  修復」按鈕**（見 §4.7 與 §0.4 的明確排除）。

**合併輸出，每筆結構**：

```python
{
    "source": "systemd" | "hermes-native",      # v2 新增：標示排程機制
    "job_name": "hermes-bridge-scanner",         # systemd: .timer 檔名（去副檔名）；native: job id/name
    "schedule_expr": "*-*-* 08:05:00",           # systemd: OnCalendar=；native: cron 表達式
    "deployed": True,                            # systemd: timer unit 是否存在；native: 是否在 jobs.json
    "timer_active": "active" | "inactive" | "未安裝",
    "last_result": "成功" | "失敗" | "尚未執行" | "無法查詢",
    "next_trigger": "2026-07-22 08:05:00" | "無法查詢",
    "last_trigger": "2026-07-21 08:05:00" | "從未觸發" | "無法查詢",
    "model_drift": "aligned" | "DRIFTED" | "n/a",       # v2 新增：只對 hermes-native agent job 有意義；systemd 一律 "n/a"
    "drift_cost_direction": "更貴" | "更便宜" | "相同" | "未知" | None,  # v2 新增：僅 DRIFTED 時有值
}
```

- **環境退化（兩路徑獨立）**：
  - 路徑 A：環境不支援 `systemctl`（例如本機 Windows 開發環境沒有可用
    WSL systemd）→ 每一筆的 `deployed`／`timer_active`／`last_result`／
    `next_trigger`／`last_trigger` 全部退化為「無法查詢」，但
    `job_name`／`schedule_expr` 兩個靜態欄位**永遠**能顯示（靜態排程來自
    repo `.timer` 檔案，不依賴 systemctl）。
  - 路徑 B：原生 cron store 找不到（`jobs.json` 不存在／`LOCALAPPDATA`
    未設）→ 該路徑回傳空清單，不噴例外，不影響路徑 A 的輸出。
  - 兩條路徑任一不可用，另一條照常顯示——確保「至少看得到一半排程」優於
    「整表壞掉」。

### 4.3 UI 設計

- 擴充既有「總覽」tab 的「Worker / Adapter 狀態」區塊之後，新增一個
  完整表格區塊「Cron／Timer 排程表」，用
  `st.dataframe(pd.DataFrame(get_cron_schedule_table()))` 呈現兩套機制
  的全部排程：systemd 路徑 6 筆（含尚未部署的 `hermes-bridge-pipeline`／
  `hermes-bridge-notifier`，顯示 `deployed: False`／`timer_active: "未安裝"`）
  ＋ Hermes 原生 cron 路徑的全部 job（AI news、garmin 等）。
- **`source` 欄位明確區分兩套機制**（例如分組、加圖示或顏色），讓使用者
  一眼看出哪些是 systemd timer、哪些是 Hermes 原生 cron。
- **`model_drift` 為 `DRIFTED` 的列明顯標示**（例如紅色／警告 icon），
  並顯示 `drift_cost_direction`，讓使用者在 job 觸發前就一眼看到「這個
  job 下次觸發會因花費保護 fail-closed」以及方向是往更貴還是更便宜。
- **明確不放任何操作按鈕**（唯讀）——不提供 pin／對齊／重跑等任何寫入入口。
- 既有 `SYSTEMD_LABELS` 摘要區塊（只挑 4 個 unit 的極簡卡片式呈現）
  **維持不變、不刪除**——那是總覽頁「一眼看常駐服務死活」的既有用途；
  新表格是「完整排程細節」的獨立區塊，兩者互補，不是取代關係。

### 4.4 Definition of Done

1. `get_cron_schedule_table()` 回傳涵蓋 **(A) systemd 全部 6 個 timer 驅動
   任務（含尚未部署的 2 個）＋ (B) Hermes 原生 cron 全部 job**，每筆帶
   `source` 欄位與 `job_name`／`schedule_expr`／`deployed`／`timer_active`／
   `last_result`／`next_trigger`／`last_trigger`／`model_drift`／
   `drift_cost_direction`。
2. 靜態欄位（`job_name`／`schedule_expr`）在 `systemctl` 完全不可用的
   環境下依然正確顯示（路徑 A 退化驗證）；原生 cron store 不存在時路徑 B
   回傳空清單、不影響路徑 A（**兩路徑獨立退化**驗證）。
3. `systemctl --user list-timers` 的輸出解析有測試（mock subprocess
   輸出，比照 `get_systemd_status()` 既有測試對 `list-units` 輸出的
   mock 模式）。
4. **（v2）Hermes 原生 cron `jobs.json` 解析有測試**：用 fixture `jobs.json`，
   斷言正確抽出 `job_name`／`schedule_expr`／`last_status`／`next_run_at`
   等欄位，且該批列 `source == "hermes-native"`。
5. **（v2）模型漂移旗標有測試，涵蓋三情境**：
   (a) agent job `model_snapshot` == 當前全域 default → `model_drift == "aligned"`；
   (b) agent job `model_snapshot` != default → `model_drift == "DRIFTED"` 且
   `drift_cost_direction` 有值；
   (c) `no_agent` script job → `model_drift == "n/a"`（不納入漂移判斷）。
6. 新表格不重複既有 `SYSTEMD_LABELS` 摘要卡片的資訊來源呼叫（即
   `get_cron_schedule_table()` 的路徑 A 複用 `get_systemd_status()` 的既有
   `active_state`／`sub_state` 邏輯，不重新兜一份平行邏輯）。
7. `dashboard/test_app.py` 對新表格區塊跑 `AppTest`，確認不噴例外。

### 4.5 測試策略

- 對 `.timer` 檔案 parsing：直接對 repo 內既有的真實 `.timer` 檔案跑
  測試（這些檔案內容是靜態設定、非執行期資料，不需要 fixture 隔離），
  斷言能正確抽出 6 個 `OnCalendar=` 值。
- 對 `list-timers` 輸出 parsing：mock `subprocess.run` 回傳固定格式的
  假輸出文字，斷言解析出的 `next_trigger`／`last_trigger` 正確；
  同時測試「輸出為空」「`systemctl` 不存在」兩種邊界情況。
- **（v2）對 `jobs.json` parsing**：用 fixture `jobs.json`（含至少一個
  unpinned agent job 帶 `model_snapshot`、一個 `no_agent` script job），
  斷言欄位抽取正確、`source` 標記正確、漂移旗標三情境正確；同時測試
  「`jobs.json` 不存在」「`LOCALAPPDATA` 未設」的退化回傳空清單、不噴例外。
- **（v2）漂移比對**：mock 當前全域 `config.yaml` 的 `model.default`
  為兩種值，分別驗證 `aligned` 與 `DRIFTED` 結果，並驗證 script job 恆為
  `n/a`。測試 fixture 一律用假 job／假模型名稱，不依賴真實 store。

### 4.6 風險

| 風險 | 影響 | 緩解 |
|---|---|---|
| `list-timers` 輸出格式因 systemd 版本不同而有欄位順序差異 | parsing 錯誤或欄位對錯位 | 一律加 `--no-legend`（已在既有 `get_systemd_status()` 沿用同一慣例）；測試用 mock 輸出鎖定既知格式，若未來格式改變由測試先發現 |
| 尚未部署的 2 個 timer（`bridge-pipeline`／`bridge-notifier`）在表格中顯示可能被誤解為「這系統壞了」 | 使用者混淆現況 | `deployed: False` 明確標示為「未安裝」而非「失敗」，UI 用不同顏色或文字明確區分（沿用既有 `st.metric` 對「未安裝」的既有措辭，見 `app.py` 第 101 行） |
| Windows 開發環境沒有 WSL systemd，即時狀態全部無法查詢 | systemd 路徑大半欄位顯示「無法查詢」 | 靜態欄位（job_name/schedule_expr）设计上不依賴 systemctl，永遠可得（§4.2 路徑 A 退化）；且原生 cron 路徑不依賴 systemd，照常顯示 |
| （v2）讀 Hermes 原生 cron store（`jobs.json`）引入新讀取路徑 | 新的檔案讀取依賴 | 純檔案讀取、不 import 任何 cron 寫入函式；找不到檔案優雅退化回傳空清單，不噴例外（§4.2 路徑 B 退化） |
| （v2）原生 cron `jobs.json` schema 與規劃理解有落差 | 欄位抽取對不上、漂移判定失準 | engineering 實作前用安全方式（只印 key 名稱、不印敏感 value）驗證 `jobs.json` schema；對缺欄位以「無法查詢」呈現，不因單一 job 欄位缺失中斷整表 |
| （v2）模型漂移旗標花費方向判斷可能不精確 | 提示的「更貴／更便宜」可能不準 | 旗標**純標示、不觸發任何自動動作**，誤判最壞後果只是提示不準；使用者仍自行決定是否處理，不會因誤判造成任何寫入或花費後果 |

### 4.7 明確不做（v2，對應 §0.4）

- **任何寫入型「更換／pin profile 或 cron job 模型」動作**一律不在功能三、
  不在 Stage 3。
- **特別是「一鍵自動對齊漂移 job 到新模型」**：它本質上是把 unpinned job
  pin 成當前全域新值＝自動採用可能更貴的模型＝**繞過使用者自設的花費保護
  （#44585）**；而全域模型變動來源含 auto-raise（通常往更貴跳），方向上專挑
  更貴模型。這與 guard 的設計本意直接衝突，**明確不做**。
- **獨立的第四項漂移偵測 feature 也不做**——漂移偵測只當功能三統一排程表的
  一個唯讀欄位存在，不另立功能。
- 「pin 修復」（無論 pin 回舊 snapshot 或 pin 到新值）都屬寫入型，歸第二批，
  待使用者未來明確表態是否打破唯讀鐵律後另案評估。

---

## 5. 開放問題／Start blocker 評估

**評估結果：本提案第一批三項功能，零硬 start blocker。**

- 功能一：只依賴已完成的 `HermesSessionAdapter`，無前置。
- 功能二：`registry/capability_lanes.yaml` 已存在且穩定；`auth.json`
  的精確 schema 尚待 engineering 用安全方式驗證（第 3.5 節 DoD 第 7
  項），但這是**實作步驟的一部分**，不是阻擋開工的前置條件——設計
  本身（allowlist ＋雙重防護）不因 schema 細節微調而改變骨架。
- 功能三：systemd 路徑所有需要的資料來源（`.timer` 檔案、`systemctl`
  既有呼叫模式）皆已存在且穩定；**（v2）Hermes 原生 cron 路徑的
  `jobs.json` 精確 schema 尚待 engineering 用安全方式驗證**（比照功能二
  的做法：只印 key 名稱、不印敏感 value），但同屬實作步驟一部分，不是硬
  start blocker——設計骨架（讀 `jobs.json`＋比對 `model_snapshot` vs
  全域 default）不因欄位名稱微調而改變。

**沒有需要使用者先回答才能開工的開放問題**——第 0.4 節排除的第二批
項目（使用量統計、寫入型功能）本身就是「留待另案」，不是本提案執行
過程中會卡住的未決事項，不影響本提案三項功能各自獨立開工。

三項功能彼此獨立（互不依賴對方的程式碼或資料），**v2 拍板實作順序**：
**功能二 → 功能三 → 功能一**——理由：

- **功能二（第一優先）**：回應已發生的真實安全事故，且使用者確認近期會繼續
  Stage 4 橋接／憑證相關工作，價值最急迫，提到最前；同時先把「安全唯讀
  白名單資料層」模式立起來給後兩項沿用。
- **功能三（第二）**：v2 擴大涵蓋 Hermes 原生 cron 後，直接命中使用者
  「換全域模型後隔天才發現 job 壞掉」的真實痛點；模型漂移旗標搭其原生 cron
  資料層順帶完成，邊際成本低。
- **功能一（殿後，選配）**：DoD 最單純、風險最低，但對單人自用的營運價值最低
  （v1 不做點進看全文）。**首發有餘力再做，未納入不影響前兩項的獨立完成度。**

此順序為使用者拍板結果，engineering 若因其他排程考量微調，不影響本提案任何
一項的獨立完成度。（附記：v1 原建議順序為「功能二 > 功能一 > 功能三」，
v2 將功能一與功能三對調——功能三升到第二、功能一殿後。）

### 5.1 待補：使用者其他 Stage 3 想法（Pending additions）

> **這是一個明確的佔位區塊。** 使用者表示可能還有其他 Stage 3 想法要補；
> 後續新想法請接在本區塊，不影響上方 §0–§4 已定案的三項功能與拍板順序。
> 每筆新想法建議標註：來源日期、一句話描述、是否唯讀（若涉寫入則同時檢查
> 是否觸及唯讀鐵律、是否應歸第二批）、以及相對現有三項功能的優先序初判。

- （目前無待補項目——保留此區塊供後續填入。）

---

## 6. 完成定義總表（全提案）

本提案（第一批三項）整體視為完成，當且僅當：

1. 功能一 DoD（第 2.5 節，即 roadmap 原文四條）逐項達成。
2. 功能二 DoD（第 3.5 節七項，**特別是第 2／3／4 項安全測試**）逐項
   達成——這三項是本提案能否視為「安全落地」的硬性判準，任何一項未過
   即不能視為功能二完成。
3. 功能三 DoD（第 4.4 節七項，**特別是 v2 新增的第 4／5 項：原生 cron
   `jobs.json` 解析與模型漂移旗標三情境測試**）逐項達成。
4. 三項功能的新增程式碼全數通過 `dashboard/test_data.py`／
   `dashboard/test_app.py`（含本提案新增的測試），既有測試零回歸。
5. Dashboard 既有三條鐵律（localhost-only、技術強制 read-only、獨立
   資料層）在新增程式碼中同樣成立——`dashboard/README.md` 的「安全
   邊界」小節需同步更新，明確納入本提案新增的外部讀取路徑
   （`%LOCALAPPDATA%\hermes\state.db`、`%LOCALAPPDATA%\hermes\`
   profiles 憑證目錄、**（v2 新增）`%LOCALAPPDATA%\hermes\cron\jobs.json`
   原生 cron store**）與新的唯讀邊界說明（含功能三模型漂移旗標「只偵測、
   不修復」的邊界）。
6. 第 0.4 節排除的第二批項目維持排除狀態，本提案完成時不應出現任何
   第二批功能的程式碼或 UI 元素（避免夾帶範圍外功能）——**特別是功能三
   不得出現任何 pin／對齊模型的寫入按鈕（§4.7）**。

---

## 7. 風險總表（跨功能彙整）

| 風險 | 涉及功能 | 緩解 |
|---|---|---|
| 憑證欄位意外外洩 | 功能二 | §3.2 雙重防護、§3.5 DoD 第 2/3/4 項測試 |
| Hermes 運行中讀取鎖競爭 | 功能一 | 既有 `snapshot=True` 機制 |
| `auth.json` 實際 schema 與規劃理解有落差 | 功能二 | §3.5 DoD 第 7 項強制安全驗證步驟 |
| 環境依賴（WSL systemd／`LOCALAPPDATA`）在部分機器不可用 | 功能二、三 | 全部函式比照既有 `get_launchd_status()` 慣例：環境不支援時優雅退化，不噴例外 |
| （v2）讀 Hermes 原生 cron store（`jobs.json`）引入新讀取路徑 | 功能三 | 純檔案讀取、不 import 任何 cron 寫入函式；找不到檔案優雅退化回傳空清單（§4.2 路徑 B） |
| （v2）原生 cron `jobs.json` schema 與規劃理解有落差 | 功能三 | engineering 實作前安全驗證 schema；缺欄位以「無法查詢」呈現，不因單一 job 欄位缺失中斷整表 |
| （v2）模型漂移旗標花費方向判斷可能不精確 | 功能三 | 旗標純標示、不觸發任何自動動作，誤判最壞後果只是提示不準 |
| 表格/頁面資訊量增加，dashboard 載入變慢 | 功能一、二、三 | 沿用既有 `st.cache_data(ttl=5)` 快取模式，三項新函式皆納入同一套快取機制 |
| 誤夾帶第二批排除項目 | 全部 | §0.4／§4.7／§6 第 6 點明確重申邊界 |
