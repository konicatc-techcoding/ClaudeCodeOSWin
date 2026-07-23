# Web UI 遷移提案 — 以 AgentOSUI 範本為雛形全面轉移 Dashboard（v2）

日期：2026-07-23（v2；v1 同日稍早）　狀態：**v2 已核准——2026-07-23 使用者拍板，可開工**
負責規劃：`planning` domain
負責領域（實作階段）：`engineering`（全部程式碼、範本剝離、唯讀 API、bridge 最小
寫入例外實作、過渡期安全檢查 script、UI 接線、測試）；
`automation` 在 P0–P2 角色為零（無任何排程頻率/觸發決策）；P3 若日後核准個別寫入
功能，屆時再依該功能性質重新分工。

依賴文件（規劃前已逐一查讀實際狀態，不猜測既有機制的行為）：

- 範本本體：`C:\Users\razer\Documents\AgentOSUI-ClaudeCode-Reference-2026-07-23\`
  （`web-ui/` 全目錄、`AGENT.md`、`ARCHITECTURE.md`、四區塊架構圖 PNG——
  盤點結論見 §1.1）。
- [stage3-dashboard-observability-proposal.md](stage3-dashboard-observability-proposal.md)
  v2（**其 §2–§4 功能/安全設計是本提案 P2 搬遷時的設計正本，繼續有效**；
  其 §0.1「不另開新入口」拍板已於 2026-07-23 被使用者推翻，見 §0.1）。
- `dashboard/app.py`、`dashboard/data.py`、`dashboard/README.md`、
  `dashboard/test_data.py`（既有三鐵律的技術強制實作與測試先例）。
- `hermes/session_adapter/adapter.py`（`HermesSessionAdapter` snapshot 機制，
  P2 功能一的既有依賴）。
- `memory/hermes-credential-handling-safety-lessons.md`（憑證四類事故教訓，
  P2 功能二與本提案全部憑證邊界的依據）。
- `memory/hermes-gateway-init-slow.md`（gateway 啟動約 3.5 分鐘才寫狀態檔
  ——P0 的 bridge timeout 驗證項直接來自這條教訓）。

---

## 版本標記

- **v2**（2026-07-23）＝使用者就 v1 §9 待拍板項逐項拍板後的**定稿版，已核准
  可開工**。**v2 變更摘要（相對 v1）**：
  1. **資料層拍板：選項 A**（Python 唯讀 API 包既有 `data.py`）——§2.2。
  2. **框架剝離拍板：選項 b**（降級純 Vite + React SPA，剝掉 vinext/wrangler）
     ——§2.3。
  3. **Bridge 拍板：推翻 v1「P0 唯讀化」預設，現在即核准為最小寫入例外**。
     使用者親定的安全規格一字不漏納入本提案（§4.1 P0 第 7 項、§5.4），
     作為硬性 DoD。
  4. **過渡期缺口拍板：先做最小安全檢查 script**（唯讀檢查＋輸出報告，
     不自動修改系統），排入 P0 尾段（其驗證標的多為 bridge 性質，需在
     bridge 入 repo 後才有標的）；P2 完整安全檢查功能完成後由正式版本取代
     ——§4.1 P0 第 8 項、§4.3 P2 DoD 第 5 項。
  5. **未被推翻的預設照舊**：Streamlit 退役採並行觀察期；P3 各功能 gate
     （Chat 派工／Job Retry／Session 管理）留待日後，未核准前零程式碼。
  6. §9 由「待拍板項清單」改為「拍板結果記錄」。
- **v1**（2026-07-23 稍早）＝第一個正式版本（草案）。來源：使用者就「是否以
  AgentOSUI 範本為雛形開發 dashboard」的評估拍板了三點（見 §0.1），v1 是該
  拍板之下的具體遷移設計，含 §9 待拍板項——現已全數有答案（見上方 v2 變更
  摘要與 §9）。

---

## 0. 定位與範圍邊界（本提案最重要的一節，後續所有設計都從這裡導出）

**一句話定位**：把 dashboard 觀測功能從既有 Streamlit（`dashboard/app.py`）
全面轉移到以 AgentOSUI 範本為雛形的新 Web UI，分四個可獨立驗收的 phase
（P0 剝離與落地驗證 → P1 唯讀資料層與既有功能對等 → P2 Stage 3 三項功能
搬遷 → P3 寫入型功能獨立評估），**P0–P2 全程維持既有三條鐵律**
（localhost-only、技術強制 read-only、獨立資料層），在新架構下逐條重建
技術強制（§3）；**唯一的核准例外是 bridge process 控制**（2026-07-23
拍板為最小寫入例外，安全規格見 §5.4）。

### 0.1 本提案從何而來（2026-07-23 使用者拍板，逐點照錄）

1. **方案 B 全面轉移**：推翻 Stage 3 提案 v2 §0.1「擴充既有 Streamlit
   dashboard，不另開新入口」的既有拍板；dashboard 觀測功能全面轉移到
   以範本為雛形的新 Web UI。
2. **Stage 3 凍結**：三項功能（session 列表／憑證與 Lane 檢視／統一排程
   健康表）不在 Streamlit 開工，等新 UI 方向定案後搬進新 UI；**提案 v2
   §2–§4 的功能與安全設計仍是搬遷時的設計正本**，不重新發明。
3. **寫入型功能現在就啟動獨立評估**：Chat 派工、Job Retry、Session 管理
   納入本提案評估（§5），**但核准 gate 獨立——不隨遷移案自動核准**。

同日第二次拍板（針對 v1 §9 待拍板項）的結果照錄於 §9，其中 bridge 一項
推翻了 v1 的預設方案（詳見 §5.4）。

### 0.2 與 Stage 3 提案 v2 的關係（明確交代，避免兩份文件互相矛盾）

- v2 的 **§0.1 前提被推翻**（不另開新入口 → 全面轉移新入口），但 v2 的
  **§2（session 列表）、§3（憑證/Lane 白名單與雙重防護）、§4（統一排程
  健康表＋模型漂移旗標）設計內容全部有效**，本提案 P2 逐字沿用其 DoD 與
  安全設計，只把「UI 落點」從 Streamlit tab 換成新 Web UI 頁面、把「資料
  層曝露方式」從 Streamlit 直接呼叫換成唯讀 API（§2、§3）。
- v2 的 **§0.4 排除清單**（任務聊天視窗、寫入型操作等）在 v2 語境下是
  「明確排除、留待使用者獨立表態」；使用者已於 2026-07-23 表態「啟動獨立
  評估」——因此這批項目**轉入本提案 §5**。其中 **bridge process 控制已
  核准**（最小寫入例外，§5.4）；Chat 派工／Job Retry／Session 管理維持
  「未核准前不寫任何程式碼」（gate 留待日後，§9 第 6 項）。v2 §4.7
  「一鍵自動對齊漂移 job」等因繞過花費保護（#44585）而明確不做的項目，
  **在本提案中繼續明確不做**（§0.4）。
- 對 v2 文件本身加註「§0.1 拍板已於 2026-07-23 被推翻，功能設計仍有效」
  與 roadmap 立新 stage 屬文件連動（§7），與本提案 v2 定稿同批執行。

### 0.3 本提案範圍

- **P0**：範本剝離託管假設＋Windows 落地驗證＋bridge 最小寫入例外實作＋
  過渡期最小安全檢查 script（§4.1）。
- **P1**：唯讀資料層（Python read-only API 包既有 `data.py`，已拍板選項 A）
  ＋新 UI 達成與既有 Streamlit dashboard 的功能對等（§4.2）。
- **P2**：Stage 3 三項功能依 v2 設計正本搬入新 UI（§4.3），順序沿用 v2
  拍板：功能二 → 功能三 → 功能一；完成後以正式安全檢查取代過渡期 script。
- **P3**：寫入型功能（Chat 派工／Job Retry／Session 管理）的安全邊界設計
  與獨立核准 gate（§5）——**gate 已拍板留待日後**；bridge process 控制
  已提前核准並移入 P0 實作（§5.4）。

### 0.4 明確排除範圍（不論哪個 phase，一律不做）

- **對外網路曝露**：任何非 `localhost`／`127.0.0.1` 的 bind、反向代理、
  tunnel、對外部署一律不做。
- **Cloudflare 實際部署與 ChatGPT Apps 託管**：範本的 Cloudflare Worker／
  D1／R2／`.openai/hosting.json`／`chatgpt-auth.ts` 是託管假設，P0 全數
  剝離，不保留「未來上雲」的殘留設定。
- **一鍵自動對齊漂移 job 到新模型**（沿用 v2 §4.7）：繞過花費保護
  （#44585），不做；漂移旗標維持只偵測、只標示。
- **未核准的 P3 功能（Chat 派工／Job Retry／Session 管理）在各自 gate
  核准前的任何程式碼**：評估≠核准，gate 前連「先把按鈕做出來但 disabled」
  都不做——UI 上不出現未核准功能的入口。（bridge process 控制不在此列
  ——它已於 2026-07-23 核准，見 §5.4。）
- **任意 shell command API**：bridge 即使已核准為寫入例外，也**不得**提供
  任意 shell command API（使用者親定規格，§5.4）——白名單以外的任何
  process 操作都不存在技術入口。
- **憑證值顯示**：`access_token`／`refresh_token`／任何 token/key/secret
  欄位值在任何 phase、任何層（API 回應、UI 渲染、log）都絕對不出現——
  這條在新架構下的技術強制見 §3.4。

### 0.5 三鐵律重申（本提案的硬性前提）

既有 Streamlit dashboard 的三條鐵律，在新架構下**不是沿用而是重建**——
因為範本骨架天生不滿足（bridge 有 spawn/kill 寫入路徑、無資料層）。
重建方案是 §3 整節；P0–P2 每個 phase 的 DoD 都包含對應的鐵律驗證項。

1. **localhost-only**：所有服務（含 bridge）只 bind `127.0.0.1`，CORS
   白名單只允許本機 origin。
2. **技術強制 read-only**：唯讀不是靠自律，是靠「寫入能力在技術上不存在」
   ——SQLite `mode=ro`、API 只有 GET、不 import 任何寫入模組。**唯一
   例外是 bridge 的白名單 process 操作**（2026-07-23 核准，§5.4）——
   例外範圍固定四種操作，且「其他設定寫入功能仍維持唯讀，後續再個別
   審核」（使用者原句）。
3. **獨立資料層**：UI 不直接開任何資料庫/憑證檔案，只透過唯讀 API；
   唯讀 API 只透過 `dashboard/data.py`（及其同等唯讀模組）取數。

---

## 1. 現況盤點（2026-07-23，對照實際檔案，不猜測）

### 1.1 範本盤點（已實際讀過範本全部關鍵檔案）

- **底層來源**：OpenAI `site-creator-vinext-starter`（`package.json` 名稱
  可證），為 ChatGPT Apps／Codex 託管範本：`.openai/hosting.json`（帶
  `appgprj_` project id）、`app/chatgpt-auth.ts`（讀 `oai-authenticated-user-*`
  headers）、`worker/index.ts`（Cloudflare Worker 入口，D1/R2 binding）、
  wrangler/Miniflare 本機模擬、Drizzle ORM（`db/schema.ts` **刻意留空**）。
- **技術棧**：Node.js ≥ 22.13、Next.js 16 + React 19、`vinext`
  （Next-on-Vite）、Tailwind 4、wrangler 4。
- **改造層（AgentOS Control Center）**：單一 `app/page.tsx`（`"use client"`
  單頁元件）三個 view（對話／監控／Hermes Dashboard）＋
  `scripts/agentos-local.mjs` 的 Local Bridge（bind `127.0.0.1:8787`，
  POST 後 spawn `hermes dashboard --host 127.0.0.1 --port 9119 --no-open`，
  90 秒 timeout 等待就緒，UI 以 iframe 內嵌；另提供 stop 端點呼叫
  `hermes dashboard --stop`）。
- **關鍵事實一：Chat 與 Monitor 兩個 view 全部是硬編假資料**——假 profile
  表、假排程、假 token 用量、假版本號、假 agent 數。範本自己在 composer
  下方註明「任務送出將在 Bridge 串接後啟用」。真正可運作的只有
  「bridge spawn ＋ iframe 內嵌 Hermes 原生 dashboard」一條路徑。
- **關鍵事實二：Bridge 是寫入路徑**——能 spawn/kill process。其 origin
  檢查（`^http://(localhost|127\.0\.0\.1):\d+$`）與固定指令白名單（只允許
  那一條 `hermes dashboard` 指令）是可取的既有安全設計，但「啟動/終止
  process」本身不是唯讀行為——**已於 2026-07-23 由使用者核准為最小寫入
  例外**，安全規格見 §5.4，P0 依規格實作（§4.1 第 7 項）。
- **範本附帶文件的效力**：`AGENT.md` 是通用 coding 行為守則，與 UI 無關；
  `ARCHITECTURE.md` 是**本專案 2026-07-04（macOS/launchd 時代）架構文件的
  過期複本**，只能當願景參考，不得當現況真相。四區塊架構圖
  （Chat／Sessions／Jobs／System）是長期願景，其中 Chat 派工、Session
  「管理」、Job「Retry」皆為寫入操作，歸 §5 處理。

### 1.2 本專案現況

- **既有 Streamlit dashboard**（`dashboard/app.py` + `data.py`）：
  localhost-only、`mode=ro` 技術強制唯讀、不 import `hermes/db.py`、
  密鑰不外洩有專門測試（`test_data.py`／`test_app.py`）。功能五區塊：
  總覽（worker/adapter/systemd 狀態、job 統計、domain 狀態）、Jobs
  （列表＋單筆 detail＋log）、成本、Memory（inbox 計數＋正本清單）、
  Logs（tail）。**在 P2 驗收前，這個 dashboard 維持現狀可用**；P2 後
  進入並行觀察期（已拍板，§9 第 4 項）。
- **Stage 3 v2 已完成的設計資產**（凍結的是「在 Streamlit 開工」，不是
  設計本身）：`get_hermes_sessions()`／`get_capability_lane_status()`／
  `get_hermes_credential_status()`／`get_cron_schedule_table()` 四個唯讀
  函式的完整設計、§3.2 憑證白名單＋雙重防護、漂移旗標三情境測試設計、
  各功能 DoD——P2 全部沿用。
- **凍結的代價與過渡處置（已拍板）**：v2 功能二的動機是**已發生的真實
  憑證安全事故**（至少 3–4 次明文洩漏）；Stage 3 凍結意味著「安全確認
  憑證狀態」的缺口將持續到 P2 交付。使用者已拍板過渡處置：**P0 先做
  最小安全檢查 script**（§4.1 第 8 項），P2 完整安全檢查功能完成後由
  正式版本取代。
- **環境事實**：本機為 Windows 10；Hermes gateway 啟動後約 3.5 分鐘才寫
  狀態檔（`memory/hermes-gateway-init-slow.md`）——範本 bridge 的 90 秒
  timeout 是否適用 `hermes dashboard` 指令，P0 必須實測（§4.1）。
- **規劃邊界聲明**：本提案規劃過程未讀取任何真實 `auth.json`／憑證檔案；
  憑證結構假設全部沿用 v2 §1.2 的交叉比對結論，精確 schema 仍依 v2
  §3.5 DoD 第 7 項由 engineering 以安全方式（只印 key 名稱）驗證。

---

## 2. 目標架構與兩個架構級決策（v2：兩項皆已拍板）

### 2.1 目標架構（P2 完成時的形態）

```
瀏覽器（僅本機）
  └─ 新 Web UI（純 Vite/React SPA，bind 127.0.0.1）
       ├─ fetch → 唯讀 API service（Python，bind 127.0.0.1，只有 GET）
       │            └─ dashboard/data.py（既有＋v2 新增函式；mode=ro、
       │               白名單、不 import 寫入模組——鐵律的真正落點）
       ├─ fetch → Local Bridge（127.0.0.1:8787；最小寫入例外，僅四種
       │            白名單操作：啟動 Hermes Dashboard／health／重新載入
       │            與停止「由 AgentOS 啟動的」process；audit log；§5.4）
       └─ iframe → Hermes 原生 dashboard（127.0.0.1:9119）

（P3 若日後核准）其他寫入 API：獨立 process、獨立 port，與唯讀 API
物理分離（§5.2）——目前 gate 留待日後，零程式碼。
```

### 2.2 資料層架構決策 — ✅ 已拍板（2026-07-23）：選項 A

**拍板結果：選項 A——Python 唯讀 API service 包既有 `dashboard/data.py`**
（v1 推薦案，使用者採納）。

新增一個極簡 HTTP service（`dashboard/api.py`，建議 FastAPI 或 stdlib
`http.server`，依 engineering 評估依賴面後擇一），每個 endpoint 一對一
對應 `data.py` 的既有/規劃函式，只回傳 JSON。

落選方案（選項 B：TypeScript 重寫全部讀取器）與當初的取捨理由保留如下，
供未來回顧：

1. **三鐵律已在 Python 端技術強制且有測試資產**：`mode=ro`、不 import
   寫入模組、密鑰不外洩測試全部現成；選 B 等於在新語言重建全部保證，
   且「重建時漏一條」正是 v2 §0.1 原本反對開新入口的核心風險——入口
   決定已改，但這條風險論證仍然成立，用 A 把它中和掉。
2. **`HermesSessionAdapter` 的 snapshot 機制是 Python 專屬**（WAL 鎖競爭
   處理、fingerprint 比對、`quick_check`）：選 B 需要在 TS 重寫這整套，
   而它是 v2 功能一 DoD 第 2 條「Hermes 運行中仍可用」的機制本體。
3. **v2 §3.2 憑證白名單＋雙重防護可原樣移植**：組新 dict、輸出前掃描、
   遞迴假密鑰斷言測試——設計與測試模式直接沿用，不需翻譯到另一個語言
   再重驗一次。
4. **單一真相**：`hermes/db.py` schema 變動時只需同步 `data.py` 一處
   （既有已知限制），不會出現 Python/TS 兩份平行讀取邏輯漂移。

A 的代價（誠實列出，拍板時已知悉）：多一個本機常駐/隨用 process；UI
依賴 Python venv 存在。兩者對單人本機工具都可接受。

### 2.3 UI 框架剝離深度決策 — ✅ 已拍板（2026-07-23）：選項 b

**拍板結果：選項 b——降級為純 Vite + React SPA**（v1 推薦案，使用者採納）：
移除 Next/vinext/wrangler/worker/drizzle，`page.tsx` 遷移為一般 React 元件
（遷移成本低，因為它本來就是 client component）。本機工具不需要
SSR/Worker；供應鏈面大幅縮小；bridge 腳本邏輯獨立保留（依 §5.4 規格改寫）。

落選方案（選項 a：保留 vinext/wrangler 骨架、只移除 D1/R2/OpenAI 設定）
不採——供應鏈與維護面照單全收，且範本的 UI 實質是一個 `"use client"`
單頁元件＋全域 CSS，完全沒用到 Next 的 SSR/RSC/route handler。

---

## 3. 三鐵律在新架構的技術強制方案

### 3.1 localhost-only

- 唯讀 API：socket bind 寫死 `127.0.0.1`，不提供 host 參數化（不是
  「預設 localhost 但可改」，是**沒有改的入口**）。
- UI dev server／靜態伺服：啟動腳本寫死 `--host 127.0.0.1`。
- Bridge：同樣 bind 寫死 `127.0.0.1`（使用者安全規格「localhost-only
  限制」的必備項，§5.4）。
- CORS：唯讀 API 與 bridge 只對 `^http://(localhost|127\.0\.0\.1):\d+$`
  回 `Access-Control-Allow-Origin`（沿用範本 bridge 已示範的 origin 檢查
  模式）；非白名單 origin 一律 403。
- 測試：對 API 與 bridge 以偽造 `Origin: http://evil.example` 請求，
  斷言 403。

### 3.2 技術強制 read-only

- **HTTP 層**：唯讀 API 只註冊 GET route；任何 POST/PUT/DELETE/PATCH
  一律 405——用框架層全域攔截實作，不是逐 endpoint 記得擋。
- **模組層**：`dashboard/api.py` 只准 import `dashboard/data.py`（及
  stdlib/框架），**不 import** `hermes/db.py`、`hermes/bridge_dispatch.py`、
  `cron.jobs` 等任何含寫入函式的模組——比照 `data.py` 既有原則。
- **import guard 測試**：新增測試斷言 `dashboard/api.py` 的 import 集合
  不含已知寫入模組清單（靜態檢查 `sys.modules` 或 AST），把「未來有人
  順手 import」從自律問題變成測試紅燈。
- **SQLite 層**：維持 `data.py` 既有 `mode=ro`（jobs.db）與
  `HermesSessionAdapter` 的 `mode=ro` + `PRAGMA query_only=ON`（state.db）。
- **唯一例外——bridge（§5.4）**：bridge 是獨立的小型 process，**不在**
  唯讀 API process 內；其寫入能力固定為四種白名單 process 操作，不碰
  任何資料檔案、不提供任意 shell command API。唯讀 API 的 import guard
  與 405 攔截**不因 bridge 例外而放寬分毫**。
- 測試：對唯讀 API 發 POST 斷言 405；沿用 `test_data.py` 的
  `test_readonly_connection_rejects_writes` 既有先例。

### 3.3 獨立資料層

- UI（TS 端）**零**檔案/資料庫直接存取：不裝任何 SQLite/檔案讀取依賴，
  取數只有 `fetch` 唯讀 API 一條路——寫入能力在 UI 層技術上不存在
  （bridge 呼叫是 process 控制，不是資料存取）。
- 範本的 `db/`、`drizzle*`、`worker/index.ts` 的 D1 binding 於 P0 移除
  （§4.1），杜絕「UI 端自己長出資料層」的技術可能性。

### 3.4 v2 §3.2 憑證白名單＋雙重防護的移植方式

- **原樣落在 Python 端**：白名單抽取（組新 dict、原始 dict 不出函式
  作用域）、輸出前遞迴掃描（`eyJ` 開頭/高熵長字串 →
  `[REDACTED-SUSPECT-SECRET]`）全部在 `data.py` 的
  `get_hermes_credential_status()` 內完成——API 層拿到的已是淨化後結構。
- **新增第三道防線（API 序列化前掃描）**：唯讀 API 在把任何 endpoint
  的回應序列化為 JSON 前，統一再跑一次同一個遞迴掃描函式（共用同一份
  實作，不是複製貼上）——防的是「未來新增 endpoint 忘了走白名單函式」
  的情境。
- **UI 層規則**：憑證頁禁止泛型 JSON dump（等價於 v2 §2.5 對
  `st.json()` 的禁令），欄位白名單明確列舉。
- **測試**：v2 §3.5 DoD 第 1–6 項原樣移植，掃描範圍擴大到「API 回應
  全文」與「UI 渲染輸出」兩層（fixture 一律假 token，`FAKE_`/`TEST_`
  前綴，`tempfile` 隔離，不得置於易與真實 `%LOCALAPPDATA%\hermes\`
  混淆的路徑）。

---

## 4. 分階段路線與各階段 DoD

四個 phase 各自可獨立驗收；P0→P1→P2 有依賴順序，P3 與 P2 無程式碼依賴
且 gate 已拍板留待日後（§5、§9）。

### 4.1 P0 — 範本剝離＋Windows 落地驗證＋bridge 最小寫入例外＋過渡期安全檢查 script

**內容**：

1. 範本 `web-ui/` 複製進 repo（建議 `webui/` 目錄，與 `dashboard/` 並存）。
2. 剝離託管假設：移除 `.openai/hosting.json`、`app/chatgpt-auth.ts`、
   `worker/index.ts` 的 D1/R2 binding、`drizzle*`、`examples/d1/`；依
   §2.3 拍板結果執行框架降級（**已拍板選項 b：純 Vite + React SPA**）。
3. Node.js ≥ 22.13 安裝；`npm install` 前對 lockfile 做供應鏈檢視
   （依賴清單審閱＋`npm audit`），結果記錄於 commit message 或 PR 描述。
4. **清除全部 mock**：`page.tsx` 的 Monitor/Chat 假資料（假 profile 表、
   假排程、假 token 量、假版本號、假 agent 數）全部移除；未接線的區塊
   在 P0 階段直接不呈現（不是顯示假數字，也不是灰掉的假介面）。
5. Windows 驗證：bridge 腳本 spawn `node_modules/.bin/*`（或降級後等效
   指令）在 Windows 的 `.cmd` shim 問題實測修正；`hermes` 指令
   （`HERMES_BIN`）在本機的實際可用性驗證。
6. **慢啟動實測**：實測 `hermes dashboard` 從冷啟動到可回應的耗時，
   對照 gateway 3.5 分鐘慢啟動教訓，確認 bridge 90 秒 timeout 是否
   足夠；不足則調整 timeout 並記錄實測值。
7. **Bridge 最小寫入例外實作（2026-07-23 使用者拍板核准，推翻 v1 的
   「P0 唯讀化」預設方案）**：bridge 保留 process 控制能力，依下列
   使用者親定安全規格實作——**本規格一字不漏納入，為 P0 硬性 DoD**：
   - 僅允許固定白名單操作：啟動 Hermes Dashboard、查詢 health、重新
     載入與停止「由 AgentOS 啟動的」Hermes process。
   - 不得提供任意 shell command API。
   - 必備：PID/process ownership 驗證、重複啟動防護、localhost-only
     限制、audit log。
   - 其他設定寫入功能仍維持唯讀，後續再個別審核。
8. **過渡期最小安全檢查 script（2026-07-23 拍板新增）**：純唯讀檢查＋
   輸出報告，**不自動修改系統**；排在本 phase 尾段——其驗證標的多為
   bridge 性質，需在 bridge 入 repo（第 7 項完成）後才有標的。至少
   驗證八項：**localhost-only、固定指令白名單、禁止任意 shell 參數、
   PID ownership、重複啟動防護、CORS、敏感資料暴露、audit log**。
   建議落點 `scripts/`（如 `scripts/webui_security_check.py`），輸出
   人可讀報告；**P2 完整安全檢查功能完成後由正式版本取代**（§4.3
   DoD 第 5 項）。

**DoD**：

1. 新 UI 在 Windows 本機一鍵啟動（`npm run local` 或等效），bind 僅
   `127.0.0.1`。
2. repo 內 `webui/` 無任何 Cloudflare／OpenAI 託管殘留（grep
   `oai-`／`appgprj`／`d1_databases`／`r2_buckets` 零命中）。
3. iframe 內嵌 Hermes 原生 dashboard 實測可用（經 bridge 白名單操作
   啟動後，UI 內正常顯示）。
4. 畫面上零 mock 假資料（人工核對＋`page.tsx` 內硬編數字清除）。
5. **Bridge 安全規格四條（第 7 項）逐條達成且逐條有對應測試或檢核**：
   (a) 只存在四種白名單操作的端點，無其他操作入口；(b) 無任意 shell
   command API——指令與參數皆寫死，不接受呼叫端傳入的指令/參數字串；
   (c) stop/reload 僅作用於「由 AgentOS 啟動的」process（PID/process
   ownership 驗證有測試）；重複啟動防護有測試（已在線時再啟動＝no-op
   或明確拒絕，不產生第二個 process）；bind 僅 `127.0.0.1`；(d) 每次
   操作寫一筆 audit log（時間、操作、結果）且有測試。
6. **過渡期安全檢查 script（第 8 項）對 P0 交付物執行，八項檢查全部
   通過並產出報告**；script 本身純唯讀有檢核（不含任何寫入/修改呼叫）。
7. 供應鏈檢視紀錄存在；`npm audit` 無未處置的 high/critical。
8. Node 版本需求、啟動指令、慢啟動實測值、bridge 安全規格與 audit log
   位置寫入 `webui/README.md`。

### 4.2 P1 — 唯讀資料層＋既有功能對等

**內容**：

1. 依 §2.2 拍板結果（**選項 A**）建立唯讀 API（`dashboard/api.py` 包
   `data.py`），實作 §3.1–§3.3 全部技術強制與測試。
2. Endpoint 一對一對應 `data.py` 既有函式：status counts、recent jobs、
   job detail、cost summary、systemd status、memory inbox counts、
   memory files、domain status、adapter config status、log tail。
3. 新 UI 實作與既有 Streamlit dashboard **對等**的五區塊：總覽／Jobs／
   成本／Memory／Logs（對等清單以 `dashboard/README.md`「內容」節逐項
   核對）。
4. API 層第三道憑證掃描（§3.4）隨 API 骨架一起落地（雖然 P1 endpoints
   理論上不含憑證資料，防線先立起來給 P2 用）。

**DoD**：

1. 對等清單逐項打勾：既有 Streamlit 五區塊的每一項資訊在新 UI 都能看到。
2. §3.1 CORS/403 測試、§3.2 405 測試、import guard 測試、既有
   `test_data.py` 全數通過，零回歸。
3. `bot_token` 不外洩測試移植到 API 層（對 adapter config endpoint 回應
   全文斷言不含假 token fixture 值）。
4. API 只 bind `127.0.0.1` 且無 host 參數化入口（code review 檢核項）。
5. 既有 Streamlit dashboard 在本 phase 期間維持可用、零改動。

### 4.3 P2 — Stage 3 三項功能搬遷（設計正本：v2 §2–§4）

**內容**：

1. 依 v2 拍板順序實作：**功能二（憑證/Lane）→ 功能三（統一排程健康表
   ＋漂移旗標）→ 功能一（session 列表）**。
2. 資料層四函式（`get_capability_lane_status`／
   `get_hermes_credential_status`／`get_cron_schedule_table`／
   `get_hermes_sessions`）依 v2 §2.2／§3.3／§4.2 設計原樣實作在
   `data.py`，經唯讀 API 曝露，新 UI 呈現。
3. v2 全部安全設計原樣執行：§3.2 白名單＋雙重防護（含 engineering 實作
   前以安全方式驗證 `auth.json`／`jobs.json` schema、只印 key 名稱）、
   功能一不渲染 `messages.content`、功能三漂移旗標只標示不修復。
4. UI 呈現對應 v2 的 UI 設計節（§2.3／§3.4／§4.3），Streamlit 專屬語彙
   （`st.tabs`／`st.expander`／`st.dataframe`）轉譯為對等 React 元件，
   欄位白名單與警語文字逐字沿用。
5. **正式安全檢查取代過渡期 script**：P2 的完整安全測試套件（三層假密鑰
   斷言＋鐵律測試）落地後，過渡期 script 正式退役或併入測試套件。

**DoD**：

1. v2 功能一 DoD 四條、功能二 DoD 七項（**安全測試第 2/3/4 項為硬性
   判準**）、功能三 DoD 七項逐項達成——驗收即對照 v2 原文，不另立標準。
2. 憑證掃描斷言覆蓋三層：`data.py` 回傳結構、API 回應全文、UI 渲染輸出
   （UI 層以 build 後的 rendered HTML 測試或元件測試實作，比照範本既有
   `tests/rendered-html.test.mjs` 的測試形態）。
3. v2 §6 完成定義總表第 4–6 點同等成立（測試零回歸、README 安全邊界
   更新、不夾帶未核准功能的任何程式碼或 UI 元素）。
4. 既有 Streamlit dashboard 標記 deprecated（README 註記），進入
   **並行觀察期**（已拍板，§9 第 4 項）：並行一個自然使用週期、期間
   Streamlit 零維護只讀，觀察期滿後再實際移除。
5. **過渡期安全檢查 script 由正式版本取代**：取代前後檢查涵蓋面不縮水
   （P0 的八項檢查每一項都能對應到正式測試/檢查的具體條目），取代動作
   （退役或併入）明確記錄。

### 4.4 P3 — 寫入型功能（gate 已拍板留待日後，見 §5）

本 phase 在本提案內只交付 §5 的評估與邊界設計。**2026-07-23 拍板：三項
功能（Chat 派工／Job Retry／Session 管理）的 gate 全部留待日後**——
未核准前零程式碼、UI 不出現入口。bridge process 控制已提前核准並移入
P0 實作（§5.4）。

---

## 5. 寫入型功能獨立評估（P3——每項獨立核准 gate）

### 5.1 總則

- **評估 ≠ 核准**：本節存在的目的是把安全邊界先想清楚，不是預告一定會做。
- **每項功能一個獨立 gate**：2026-07-23 拍板現況——**(4) bridge process
  控制已核准**（最小寫入例外，移入 P0）；**(1)(2)(3) 三項 gate 留待日後**，
  未核准前零程式碼。
- **共同原則**：寫入能力與唯讀層物理隔離（§5.2）；每個寫入動作留
  audit 記錄；寫入面最小化（只做白名單動作，不做泛用操作台）。

### 5.2 隔離架構（任何一項核准後的共同前提）

- 寫入功能一律放在**獨立的寫入 process**（獨立 port、獨立模組），
  與唯讀 API 分離：唯讀 API 的 import guard（§3.2）繼續保證它永遠沒有
  寫入能力；寫入側才被允許 import 對應的寫入模組。已核准的 bridge
  即依此原則獨立存在（不與唯讀 API 同 process）。
- UI 層對唯讀 API 與寫入側的呼叫明確分離（不同 base URL），寫入呼叫
  一律 POST＋明確的使用者確認互動（不做「點一下就執行」的破壞性動作）。
- 寫入側同樣 bind `127.0.0.1`＋CORS 白名單；每次寫入動作寫一筆
  audit log（時間、動作、參數、結果）到 `logs/`。

### 5.3 各功能評估（gate 留待日後的三項）

**(1) Chat 派工（UI 直接對 CoS 交辦任務）——gate 留待日後**

- **寫入本質**：等價於新增一個「web」來源的 job 進 `hermes/jobs.db`
  （經 `enqueue()`），由既有 worker/headless CoS 流程消化——**不是**
  UI 直接呼叫 `claude`。這樣寫入面收斂為一個既有、已驗證的入口，
  且天然獲得 job 生命週期/重試/成本記錄。
- **邊界設計**：寫入 API 只提供 `POST /api/chat/enqueue`（payload：
  純文字 prompt）；回覆呈現走唯讀 API 的既有 job detail 端點輪詢。
  不提供任意指令執行、不提供檔案上傳。與 ARCHITECTURE.md 既有決策
  相容性佳（背景 job 只能寫 inbox 的既有規則自動適用）。
- **風險焦點**：本機瀏覽器任何頁面若繞過 CORS（例如非瀏覽器 client）
  即可派工——但這與本機任何人可直接跑 CLI 等價，威脅模型未實質擴大。
- **gate 判斷建議**：三項中風險最低、價值最高的一項；若日後只核准
  一項，建議是這項。

**(2) Job Retry(對 failed/dead_letter job 重新入列）——gate 留待日後**

- **寫入本質**：`jobs.db` 的狀態轉移寫入。
- **邊界設計**：只允許 `failed`／`dead_letter` → `queued` 的單向白名單
  轉移；不允許修改 payload、不允許刪除 job、不允許對 `completed`/
  `running` 操作；實作走獨立寫入模組（不把寫入函式加進 `data.py`）。
- **風險焦點**：與 worker 的原子 claim 邏輯互動（併發下的重複執行）；
  實作細案需比照 `hermes/db.py` 既有的原子性慣例。
- **gate 判斷建議**：可做，但價值取決於實際 retry 頻率——建議觀察
  P1/P2 上線後的實際需求再核准。

**(3) Session 管理（archive／刪除 Hermes session 等）——gate 留待日後**

- **寫入本質**：對 `%LOCALAPPDATA%\hermes\state.db` 的寫入——**這是
  Hermes 擁有的資料庫**，外部程式寫入可能與 Hermes 運行中的 WAL 寫入
  衝突，且 schema 歸 Hermes 版本管，升級即可能破壞外部寫入假設。
- **邊界設計（若做）**：**不直接寫 state.db**，只透過 Hermes 官方 CLI／
  API 執行管理動作（如同 bridge 對 dashboard 的做法：固定指令白名單）；
  若 Hermes 無對應官方指令，該動作就不做——不重演「官方指令看似無效
  就繞過用內部函式」的教訓四。
- **gate 判斷建議**：**三項中唯一建議暫緩**——觀測價值已由 P2 功能一
  （唯讀 session 列表）覆蓋大半，寫入風險最高、依賴外部專案 schema，
  建議等真實需求出現且確認官方指令存在後再議。

### 5.4 Bridge process 控制 — ✅ 已核准（2026-07-23，最小寫入例外）

- **拍板結果**：使用者推翻 v1 預設的「P0 唯讀化」方案，**現在即核准
  bridge 為最小寫入例外**，實作排入 P0（§4.1 第 7 項）。
- **使用者親定安全規格（一字不漏照錄，為硬性 DoD——對應 §4.1 P0 DoD
  第 5 條的逐條測試/檢核要求）**：
  - 僅允許固定白名單操作：啟動 Hermes Dashboard、查詢 health、重新
    載入與停止「由 AgentOS 啟動的」Hermes process。
  - 不得提供任意 shell command API。
  - 必備：PID/process ownership 驗證、重複啟動防護、localhost-only
    限制、audit log。
  - 其他設定寫入功能仍維持唯讀，後續再個別審核。
- **實作定位與補充設計（不放寬上述規格，只落地它）**：
  - bridge 為獨立小型 process（範本 `agentos-local.mjs` 的改寫），
    **不在**唯讀 API process 內——唯讀 API 的 import guard／405 攔截
    不因 bridge 存在而放寬（§3.2）。
  - 指令與參數全部寫死在 bridge 內（沿用範本既有「固定那一條
    `hermes dashboard` 指令」的做法），HTTP 介面不接受任何指令/參數
    字串輸入——「不得提供任意 shell command API」的技術落實方式。
  - stop／reload 僅作用於 bridge 自己 spawn 的 process（記錄並驗證
    PID/process ownership；對非 AgentOS 啟動的 Hermes process 一律
    拒絕操作）——範本原版 `--stop` 走 CLI 全域停止，**不符本規格**，
    P0 需改寫為 ownership-verified 的實作。
  - 重複啟動防護：已在線（health 可達或已持有存活 child process）時
    再收到啟動請求＝no-op 或明確拒絕，不產生第二個 process（範本已有
    `dashboardStart` promise 去重的雛形，需補測試鎖定）。
  - audit log：每次啟動/停止/重載操作寫一筆（時間、操作、PID、結果）
    到 `logs/`。
  - **本核准僅涵蓋上述四種白名單操作**——「其他設定寫入功能仍維持
    唯讀，後續再個別審核」（使用者原句）：任何設定變更、profile 切換、
    模型 pin 等寫入面不因本核准開啟，個別功能仍走 §5.3 式的獨立 gate。

---

## 6. 技術前置清單（P0 開工前/開工中必辦，彙整）

1. Node.js ≥ 22.13 安裝（本機目前為 Python venv 環境，Node 是新依賴）。
2. `npm install` 前的供應鏈檢視：lockfile 依賴清單審閱＋`npm audit`，
   結果留紀錄。
3. 剝離 `.openai/hosting.json`／`chatgpt-auth.ts`／Cloudflare Worker
   （D1/R2）／drizzle；框架降級依已拍板的選項 b（純 Vite + React SPA）
   執行。
4. 清除 `page.tsx` 全部 mock 假資料（P0 DoD 第 4 條）。
5. `hermes` 指令（`HERMES_BIN`）本機可用性驗證；`hermes dashboard`
   冷啟動耗時實測 vs bridge 90 秒 timeout（gateway 3.5 分鐘慢啟動教訓）。
6. Windows spawn 驗證：`node_modules/.bin/*` shim 在 Windows 需 `.cmd`／
   `shell: true` 的實測與修正。
7. 唯讀 API 框架選型（FastAPI vs stdlib）——engineering 依依賴面評估，
   不影響 §3 的技術強制設計。
8. Bridge 改寫規格對齊：範本 `--stop` 的全域停止語意改為
   ownership-verified（§5.4）；audit log 落點與格式定案。

---

## 7. 文件連動（狀態更新：隨 v2 定稿同批執行/待辦）

| # | 待辦 | 擁有者 | 狀態 |
|---|---|---|---|
| 1 | `docs/stage3-dashboard-observability-proposal.md` 加註：「§0.1 拍板已於 2026-07-23 被推翻（見 webui-migration-proposal.md）；§2–§4 功能與安全設計仍有效，為遷移時設計正本」 | planning（隨本次定稿執行） | ✅ 已執行（2026-07-23） |
| 2 | `docs/hermes-integration-roadmap.md` 立新 stage（Stage 5）指向本提案；Stage 3 節註記凍結與搬遷去向 | planning（隨本次定稿執行） | ✅ 已執行（2026-07-23） |
| 3 | `dashboard/README.md`：P1 起註記新 UI 並存狀態；P2 後標 deprecated 與並行觀察期 | engineering（隨對應 phase） | ⏳ 待對應 phase |
| 4 | 新增 `webui/README.md`（啟動方式、Node 版本、安全邊界、bridge 安全規格與 audit log 位置——比照 `dashboard/README.md` 安全邊界節的寫法） | engineering（P0） | ⏳ 待 P0 |
| 5 | 本次「推翻 §0.1」決策、bridge 最小寫入例外核准、範本來源（OpenAI starter 改造、其 ARCHITECTURE.md 為過期複本）記入 memory | CoS 經 `knowledge` | ⏳ 待辦 |

---

## 8. 風險總表

| 風險 | 涉及 phase | 影響 | 緩解 |
|---|---|---|---|
| npm 供應鏈引入大量新依賴 | P0 | 新攻擊面/維護面 | 已拍板選項 b（純 Vite+React）縮小依賴；P0 供應鏈檢視留紀錄；lockfile 鎖定 |
| 三鐵律在新架構重建時漏一條 | P1–P2 | 唯讀保證失效 | §3 逐條技術強制＋每條有對應測試（403/405/import guard/mode=ro）；各 phase DoD 含鐵律驗證項 |
| **Bridge 寫入例外被實作走樣或日後被擴大** | P0 起 | 白名單例外變成泛用操作口 | 使用者親定規格一字不漏為硬性 DoD（§4.1 第 7 項＋DoD 第 5 條逐條測試）；指令/參數寫死、無任意 shell API；PID ownership＋重複啟動防護＋audit log；過渡期 script 八項檢查獨立驗證；「其他設定寫入維持唯讀、後續個別審核」明文鎖住擴張路徑 |
| 過渡期安全檢查 script 給出假陰性（檢查不到位卻報 pass） | P0–P2 | 誤信安全狀態 | script 檢查項與 P0 DoD 第 5 條一一對應（八項明列）；P2 正式版本取代時要求「涵蓋面不縮水」的對應表（§4.3 DoD 第 5 項） |
| 憑證欄位經新增的 API/UI 兩層意外外洩 | P2 | 安全事故重演 | 白名單＋雙重防護原樣移植於 Python 端＋API 序列化前第三道掃描＋三層假密鑰斷言測試（§3.4） |
| mock 假資料殘留誤導觀測 | P0 | 「儀表板顯示假狀態」比沒有更糟 | P0 DoD 第 4 條硬性清除；未接線區塊不呈現 |
| `hermes dashboard` 慢啟動超過 bridge timeout | P0 | 內嵌功能誤判失敗 | P0 實測冷啟動耗時、調整 timeout（gateway 3.5 分鐘教訓） |
| Windows spawn `.bin` shim 失敗 | P0 | 一鍵啟動不可用 | P0 實測修正（`.cmd`/`shell:true`） |
| Stage 3 凍結期間憑證觀測缺口持續 | P1–P2 期間 | v2 功能二回應的真實安全事故缺口延後補上 | 已拍板過渡處置：P0 先交付最小安全檢查 script（唯讀＋報告）；P2 順序維持功能二最先；P2 後由正式版本取代 |
| 雙 runtime（Python＋Node）長期維護面 | 全部 | 單人專案維護成本上升 | 資料層單一真相留在 Python（已拍板選項 A）；UI 層縮到純 SPA（已拍板選項 b）；Streamlit 並行觀察期後退役，Python 側僅剩 data.py+api.py |
| `auth.json`／`jobs.json` schema 與 v2 理解有落差 | P2 | 白名單對不上、漂移判定失準 | 沿用 v2 既有緩解：engineering 以安全方式（只印 key 名稱）先驗證 schema |
| 未核准的 P3 功能被夾帶進 P0–P2 | 全部 | 唯讀邊界被稀釋 | §0.4 明確禁止（含 disabled 按鈕都不做）；P2 DoD 第 3 條驗收檢核；bridge 例外範圍以 §5.4 規格封死 |
| 範本 `ARCHITECTURE.md` 過期複本被誤當現況 | 全部 | 依過期假設實作 | §1.1 明確標註；文件連動待辦 5 記入 memory |

---

## 9. 拍板結果記錄（v1 §9 待拍板項 → 2026-07-23 使用者逐項拍板）

> v1 的本節是「使用者需回答的最小問題集」；使用者已於 2026-07-23 逐項
> 回答，本節改為決策記錄正本。**全部 phase（P0→P1→P2）已無未決前置，
> 可開工。**

1. **資料層架構**：✅ 已拍板——**選項 A**（Python 唯讀 API 包既有
   `data.py`；採 v1 推薦案）。設計見 §2.2。
2. **UI 框架剝離深度**：✅ 已拍板——**選項 b**（降級純 Vite + React
   SPA，剝掉 vinext/wrangler；採 v1 推薦案）。設計見 §2.3。
3. **Bridge spawn/stop 定位**：✅ 已拍板——**現在即核准為最小寫入例外**
   （**推翻** v1「P0 唯讀化」的預設方案）。使用者親定安全規格四條
   （固定白名單操作／不得任意 shell command API／PID ownership＋重複
   啟動防護＋localhost-only＋audit log 必備／其他設定寫入維持唯讀後續
   個別審核）一字不漏納入 §5.4 與 §4.1 P0 第 7 項，為硬性 DoD。
4. **Streamlit 退役時點**：✅ 已拍板——**並行觀察期**（採 v1 預設建議）：
   P2 驗收後標 deprecated，並行一個自然使用週期、期間零維護只讀，觀察
   期滿後再實際移除。見 §4.3 DoD 第 4 項。
5. **P2 交付前的憑證觀測缺口**：✅ 已拍板——**先做最小安全檢查 script**
   （唯讀檢查＋輸出報告，不自動修改系統；至少驗證八項：localhost-only、
   固定指令白名單、禁止任意 shell 參數、PID ownership、重複啟動防護、
   CORS、敏感資料暴露、audit log）。排入 **P0 第 8 項**（其驗證標的多為
   bridge 性質，需 bridge 入 repo 後才有標的）；**P2 完整安全檢查功能
   完成後由正式版本取代**（§4.3 DoD 第 5 項）。
6. **P3 各功能 gate**：✅ 已拍板——**三項全部留待日後**（Chat 派工／
   Job Retry／Session 管理均未核准），未核准前零程式碼、UI 不出現入口；
   評估與邊界設計保留於 §5.3，日後核准任一項時據此出實作細案。
