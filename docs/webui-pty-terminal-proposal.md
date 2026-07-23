# Web UI — Claude Code CLI 真終端機（PTY 嵌入）設計提案（v2）

日期：2026-07-23（v2；v1 同日稍早）　狀態：**v2 已核准——2026-07-23 使用者拍板
（§10 五項均採建議預設，含 §3.2 殘餘風險知情確認）；排 P3，P2 完成後開工**
負責規劃：`planning` domain
負責領域（實作階段）：`engineering`（PTY server、前端 xterm.js 整合、
測試、audit log）；`automation` 角色為零（無排程決策）。

依賴文件：

- [webui-migration-proposal.md](webui-migration-proposal.md) v2（遷移案正本——
  本提案是其 §5「寫入型功能獨立評估」中 **Chat gate 的形態具體化**；§5.2
  隔離架構原則、§0.4 排除清單、P0 已核准的 bridge 最小寫入例外先例，本提案
  全部沿用）。
- `CLAUDE.md`（前台／背景 session 的記憶寫入規則——§6 的定位依據：是否
  headless 以實際呼叫方式（是否帶 `-p`）為準）。
- `memory/hermes-credential-handling-safety-lessons.md`（教訓一：無欄位過濾
  的讀取會把明文憑證帶進紀錄——§5 transcript 決策與 §3 殘餘風險的直接依據）。
- P1 已完成的現況（待 commit）：`dashboard/api.py`（stdlib、`127.0.0.1:8799`、
  GET-only、CORS/405/import guard）、`dashboard/redact.py`（第三道掃描共用
  正本）、`webui/src/views/` 五區塊對等。**本提案不動這些，也不污染其鐵律。**

---

## 版本標記

- **v2**（2026-07-23）＝使用者**全案核准**後的定稿版：§10 第 1–5 項全部採
  v1 建議預設（token＋Origin 雙層／不落 transcript／1 session＋30＋5 分鐘
  idle／60 秒 grace 無 reattach／v1 零參數），第 6 項 **§3.2 三項殘餘風險
  知情確認成立**——**P3 Chat gate（PTY 形態）正式解除**。開工時點：
  **P2 完成後**（排序拍板不變）。內文設計相對 v1 未變，§10 改為拍板結果
  記錄。
- **v1**（2026-07-23 稍早）＝第一個正式版本（草案）。來源：使用者啟動 P3
  Chat gate 並拍板形態——原話：「我要在總覽和jobs中間放一個ClaudeCode CLI
  的功能，讓我可以直接在右邊content區可以直接跟CoS互動」；在「聊天式
  headless」與「PTY 真終端機」兩形態之間**明確選了 PTY**（xterm.js 類體驗，
  等同在瀏覽器裡開一個完整互動 `claude` session，含 permission prompt）。
  排序拍板：**P2 先做，Chat 排 P3**；本提案於 P2 實作期間並行定案。

---

## 0. 定位與範圍邊界

**一句話定位**：在新 Web UI 新增一個「CoS 終端機」view（nav 位置：總覽與
Jobs 之間），透過一個**獨立的 PTY server process**（Node + node-pty +
WebSocket，bind `127.0.0.1`）spawn 一個**且僅一個、指令寫死的** `claude`
互動式 CLI process（cwd 寫死 repo 根），前端以 xterm.js 呈現完整終端機
體驗。**這是本系統至今風險最高的單一功能**——瀏覽器可觸達的完整終端機
＝潛在的任意指令執行面，本提案的設計重心是把這個面收斂到「等同使用者
本人在本機開一個 `claude` session」，並把收斂不掉的殘餘風險誠實列出
（§3），供使用者知情核准。

### 0.1 與遷移提案 v2 §5.3(1) 的關係（形態變更，明確交代）

- 遷移提案 §5.3(1) 原本評估的 Chat 派工形態是「headless enqueue」（UI →
  `POST /api/chat/enqueue` → job queue → headless CoS）——寫入面最小、
  但互動性也最低（輪詢 job detail，無即時對話、無 permission prompt）。
- 使用者 2026-07-23 拍板**改採 PTY 形態**：要的是「直接跟 CoS 互動」的
  完整前台體驗，不是丟任務等結果。**本提案即 Chat gate 的實作形態正本**；
  原 enqueue 形態**不做**（若未來有「排程式丟任務」需求另案再議，不與
  本提案混同）。
- 遷移提案 §5.3(2)(3)（Job Retry／Session 管理）gate 狀態不變（留待日後），
  不受本提案影響。
- **（v2 補記）本提案已於同日稍後經使用者全案核准**（見版本標記與 §10），
  P3 gate 已解除；開工前置只剩「P2 完成」這一項排序條件。

### 0.2 本提案範圍

- 獨立 PTY server（新 process、新 port，與唯讀 API `8799`、bridge `8787`
  物理隔離）。
- 前端「CoS 終端機」view（xterm.js）＋連線授權機制。
- 生命週期管理（單一 session、idle timeout、斷線處置）＋ audit log。
- **不含**：任何對唯讀 API／bridge／`dashboard/` Python 側的修改。

### 0.3 明確不做（不論是否核准，一律不做）

- **任意 shell**：不提供任何「spawn 任意指令」「開 PowerShell/bash」的
  端點或參數——spawn 目標寫死為 `claude`（無使用者可控參數，§3.1），
  client 唯一能送的是 PTY stdin 位元組與 resize 訊息。
- **對外曝露**：bind 非 `127.0.0.1`、反向代理、tunnel 一律不做（沿用
  遷移案 §0.4）。
- **多 session／多分頁並行**（v1）：同時最多 1 個 claude process
  （§5.1，已拍板採此值）。
- **斷線 reattach／session 持久化**（v1）：不維護可重新接回的 server 端
  buffer——狀態面與風險面都會放大；claude 本身的 `--resume`／`--continue`
  已提供「接回上一段對話」的官方途徑，不在 PTY 層重造（§5.3）。
- **完整 transcript 落地**（已拍板：不落）：terminal 輸出是**未經**
  `dashboard/redact.py` 三道掃描的原始流，可能含明文憑證（教訓一），
  只記事件不記內容（§5.4）。
- **UI 傳遞任意 CLI 參數**：v1 連 `--resume` 等固定參數組都不從 UI 傳
  （§3.1；已拍板採零參數）。
- **提權**：不以系統管理員身分 spawn；PTY server 與 claude 皆跑在使用者
  本人權限。

### 0.4 與三鐵律／既有寫入例外的關係

- **唯讀側零污染**：PTY server 是獨立 Node process，與 `dashboard/api.py`
  （Python、GET-only、import guard）、bridge（8787）互不 import、互不
  同 port、互不同生命週期——唯讀 API 的全部鐵律測試不因本功能存在而改變
  一字。
- **這是第二個、也是目前最大的寫入例外**：第一個（bridge）的寫入面是
  「四種白名單 process 操作」；本功能的寫入面是「一個完整互動 claude
  session 能做的一切」。兩者都遵守遷移案 §5.2 隔離原則（獨立 process、
  localhost-only、audit log），但風險量級完全不同——這正是本提案需要
  獨立 gate、且 §3 必須誠實寫盡殘餘風險的原因（該 gate 已於 2026-07-23
  以知情確認方式解除，見 §10 第 6 項）。

---

## 1. 威脅模型（先想清楚誰能打到這個面）

| # | 威脅來源 | 攻擊路徑 | 對策（§2） |
|---|---|---|---|
| T1 | **同一瀏覽器裡的惡意/被入侵網頁** | 任何網頁都能對 `127.0.0.1` 發請求；WebSocket **不受 CORS preflight 保護**（cross-site WebSocket hijacking）——只靠 bind 127.0.0.1 完全不夠 | WS upgrade 時 server 端強制 **Origin 白名單**（`^http://(localhost|127\.0\.0\.1):\d+$`，非白名單直接拒絕 upgrade）＋ **per-boot 隨機 token**（惡意網頁不知道 token，連不上） |
| T2 | **本機其他 process**（同使用者權限） | 直接連 `127.0.0.1:<port>`，可偽造任意 Origin header | token 是主要防線；但誠實承認：同使用者權限的本機 process 理論上讀得到 token 的存放位置——此時它「本來就能」直接跑 `claude`，威脅模型未實質擴大（§3.2 第 3 點） |
| T3 | **使用者本人誤操作** | 在終端機裡做出破壞性動作 | 這正是 Claude Code permission 系統的守備範圍（前台互動 session 逐項詢問）；PTY 層不重造第二套 permission |
| T4 | **session 內容注入**（貼進終端的文字含指令誘導） | 與任何本機 `claude` 使用完全相同 | 不新增於本功能的既有風險；沿用系統既有防線（delegation policy、permission prompt），提案不宣稱有額外緩解 |
| T5 | **DNS rebinding** | 惡意網域解析到 127.0.0.1 繞過同源 | Origin 白名單同時擋掉（rebinding 後 Origin 仍是惡意網域）；token 為第二層 |

**結論**：連線授權採**雙層**——(1) WS upgrade 的 Origin 白名單（server 端
強制），(2) per-boot 隨機 token。兩層缺一不可：只有 Origin 檢查擋不住
本機 process（可偽造 header），只有 token 則暴露面依賴 token 保管。

---

## 2. 架構設計

```
npm run local（launcher，既有腳本擴充）
  ├─ 產生 per-boot 隨機 token（crypto.randomBytes ≥ 32 bytes hex）
  ├─ 啟動 PTY server（Node，bind 127.0.0.1:8801，token 經環境變數傳入）
  ├─ 啟動 Vite dev server（token 經 Vite env 注入前端，不落磁碟、不進 git）
  └─ （既有）bridge 8787／使用者自行啟動唯讀 API 8799

瀏覽器「CoS 終端機」view
  └─ WebSocket ws://127.0.0.1:8801/?token=<token>
       ├─ server 驗證：Origin 白名單 → token 比對（constant-time）→
       │   session 數上限檢查 → 全過才 upgrade，否則拒絕＋audit 記一筆
       ├─ client→server 訊息：僅兩種——stdin 位元組、resize {cols, rows}
       ├─ server→client 訊息：PTY 輸出位元組
       └─ upgrade 成功後 spawn（僅此一途，無其他 spawn 入口）：
            node-pty.spawn(<claude 絕對路徑>, [], { cwd: <repo 根，寫死>,
                                                     env: 繼承使用者環境 })
```

設計要點：

1. **獨立 process、獨立 port**：建議 `127.0.0.1:8801`（與 8787/8799 區隔；
   實際值 engineering 定案即可，唯 bind 位址寫死 `127.0.0.1`、無參數化
   入口——比照唯讀 API 的既有原則「沒有改的入口」）。
2. **token 生命週期**：每次 launcher 啟動重新產生（per-boot）；不寫入任何
   持久檔案（只存在 launcher → 兩個子 process 的環境變數／記憶體與前端
   bundle 執行期）；比對用 constant-time compare。（已拍板採此機制，
   §10 第 1 項。）
3. **驗證失敗行為**：Origin 不符／token 錯誤／已達 session 上限 → 拒絕
   upgrade（HTTP 4xx）＋ audit log 記一筆（時間、來源資訊、拒絕原因），
   不洩漏「差在哪」的細節給 client。
4. **訊息面最小化**：WS 協定只有 stdin／resize／輸出三種訊息——沒有
   「執行指令」「開新 process」「改設定」類型的訊息，未來加任何新訊息
   類型都需回到本提案增補（防止協定面靜默膨脹）。
5. **與 P0 供應鏈慣例一致**：新增依賴逐一列於 §4.2，`npm install` 前
   lockfile 檢視＋`npm audit` 留紀錄（沿用遷移案 P0 DoD 第 7 條慣例）。

---

## 3. spawn 邊界與殘餘風險（本提案最重要的誠實段落）

### 3.1 PTY 層能保證的（寫死範圍）

- spawn 目標**寫死**為 `claude` CLI（server 啟動時解析一次絕對路徑並
  鎖定；Windows 下明確處理 `claude.cmd`／`claude.exe` shim，P3 開工時
  實測）。
- **零使用者可控參數**：v1 spawn 引數陣列為空（前台互動模式，不帶
  `-p`）；client 沒有任何管道影響 spawn 的指令、參數、cwd、env。
  （已拍板採零參數，§10 第 5 項。）
- cwd **寫死** repo 根（`CLAUDE.md` 自動載入，session 即 CoS）。
- claude process 結束（使用者打 `exit`／`/quit`／crash）→ PTY session
  即終止並通知前端——**不會**掉回任何 shell。這是「不提供任意 shell」
  的關鍵一環：PTY 的生命週期嚴格等於 claude process 的生命週期。

### 3.2 PTY 層**不能**保證的（殘餘風險——使用者已於 2026-07-23 逐項知情確認）

1. **「終端機能做的事」的實際邊界不在 PTY 層，在 Claude Code 的
   permission 系統**。一個前台互動 `claude` session 本來就能（經使用者
   在 session 內核准）執行 Bash 指令、讀寫檔案、上網——PTY 層寫死
   「只能 spawn claude」只是把入口收斂成一個，**不是**把能力收斂。
   等價陳述：**任何能連上這個 WebSocket 的人＝坐在這台機器前的操作者
   本人**。這是本功能的本質，不是可以修掉的缺陷。
2. **憑證外洩通道**：終端機輸出是原始流，`dashboard/redact.py` 的三道
   掃描**完全不適用**——在 session 裡讀 `auth.json` 之類的操作會把明文
   憑證直接印在畫面上（教訓一在此通道原樣適用，且本功能無技術手段攔截，
   只能靠 session 內的操作紀律與 P2 憑證唯讀檢視頁提供的安全替代管道）。
   這也是 §5.4 不落 transcript 的直接理由。
3. **token 的保護範圍**：token 擋得住「不知道 token 的網頁」（T1/T5），
   擋不住「同使用者權限、能讀 process 環境或注入前端的本機惡意程式」
   （T2）——但後者本來就能直接執行 `claude` 或任何指令，本功能沒有給它
   新能力。威脅模型的誠實結論：**本功能把「瀏覽器」加入了可觸達終端機
   的介面清單，防線確保這個新介面不比既有介面（本機 shell）更弱，但
   無法比它更強**。
4. **headless 白名單不適用**：`.claude/settings.json` 針對 headless 的
   最小 Bash 白名單約束的是 `-p` 模式；本 session 是前台互動模式，走的
   是 permission prompt 逐項詢問——保護模型不同（人在迴路），不是沒有
   保護，但提案明確指出這個差異，避免誤以為 headless 白名單在此生效。

---

## 4. Windows PTY 技術現實與供應鏈增量

### 4.1 技術現實

- **node-pty / ConPTY**：Windows 10 1809+ 提供 ConPTY，本機（19045）
  支援。node-pty 是**原生模組**：安裝時優先用 prebuilt binary，無對應
  prebuilt 時 fallback 到 node-gyp 現地編譯（需 VS Build Tools＋Python）
  ——P3 開工第一步就實測「本機 Node 版本有無 prebuilt」，若被迫走
  node-gyp，建置需求與產物要寫進 `webui/README.md`。
- **與純 Vite SPA 的整合**：SPA 是靜態前端，PTY 必須是獨立 Node process
  （§2）——這與已拍板的架構（UI 不長資料層/能力層）一致，PTY server 是
  第三個明確列管的本機服務（8787 bridge／8799 唯讀 API／8801 PTY）。
- **claude CLI 的 Windows 形態**：spawn 前解析 `claude` 實際路徑
  （`.cmd` shim 或 exe），沿用 P0 已建立的「Windows spawn shim 實測」
  慣例。
- **終端相容性**：claude CLI 的 TUI（顏色、游標控制、中文寬字元）在
  xterm.js + ConPTY 下的實際呈現需實測；resize 事件必須正確轉發
  （node-pty `resize()`），否則 TUI 排版會壞。

### 4.2 供應鏈增量（逐一列出）

| 依賴 | 端 | 用途 | 評估 |
|---|---|---|---|
| `@xterm/xterm` | 前端 | 終端機渲染 | 純前端、廣泛使用（VS Code 同源）；無 native code |
| `@xterm/addon-fit` | 前端 | 終端尺寸自適應 content 區 | 同上，體積小 |
| `node-pty` | PTY server | ConPTY 偽終端 | **原生模組——本次供應鏈增量的風險重心**：prebuilt binary 信任面＋版本升級需重驗；鎖定版本、audit 留紀錄 |
| `ws` | PTY server | WebSocket server | 純 JS、依賴極少、廣泛使用 |

除上述四項不新增其他依賴；PTY server 本體用 Node 內建模組
（`crypto`／`http`）實作 token 與 upgrade 驗證。

---

## 5. 生命週期與審計

### 5.1 session 上限 — ✅ 已拍板

- **同時最多 1 個** claude process：第二個連線在 upgrade 階段被拒
  （明確錯誤訊息＋audit）。理由：單人系統；多 session 併發會放大
  memory 正本並發寫入面（§6）與資源面，v1 不需要。

### 5.2 idle timeout — ✅ 已拍板

- **30 分鐘無 stdin 輸入**（只算使用者輸入，不算輸出——長任務執行中
  輸出不斷但無輸入，不應誤殺）→ 先送提示到終端、再等 **5 分鐘**無
  回應才終止。

### 5.3 斷線／頁面關閉／重整處置 — ✅ 已拍板

- WebSocket 斷線（關頁、重整、網路）→ **60 秒 grace period** 後對
  claude process 送終止訊號（先溫和後強制）；grace 內同 token 重連可
  接回（單純的「重整不斷線」容錯，不是跨啟動的 reattach）。
- 超過 grace 未重連 → 終止並記 audit。**不做**長期 server 端 buffer／
  跨啟動 reattach（§0.3）；使用者要接回對話用 claude 官方 `--resume`
  （在新 session 內自行操作）。
- PTY server 本身收到 SIGINT/SIGTERM（launcher 關閉）→ 先終止 child
  再退出，不留孤兒 process（沿用範本 launcher 已有的 shutdown 模式）。

### 5.4 audit log（記什麼、不記什麼）— ✅ 已拍板：不落全文、只記事件

- **一定記**（`logs/` 下獨立檔案，每事件一行）：server 啟動/關閉、
  連線嘗試（成功/拒絕＋原因）、claude spawn（PID）、終止（原因：exit/
  idle/斷線 grace 到期/上限拒絕、exit code）、resize 不記。
- **不記**：stdin/stdout 內容（完整 transcript）。理由：終端流可能
  含明文憑證（§3.2 第 2 點、教訓一），落地即把「畫面上閃過」升級成
  「磁碟上長存」；且 claude 本身已有自己的 session 紀錄機制，PTY 層
  重複落一份只增風險不增資訊。

---

## 6. 與 CoS 架構的關係（定位聲明）

- 瀏覽器內 PTY 開的是**前台互動式 session**（不帶 `-p`）——依 CLAUDE.md
  既有規則（是否 headless 以實際呼叫方式為準），它與 Claude Code
  Desktop/CLI 的前台 session **完全同權**：走 Delegation Policy、
  **可以編輯 `memory/*.md` 正本**、不受 headless 的 inbox-only 限制。
- 影響與註明：這是刻意的定位（使用者要的就是「直接跟 CoS 互動」的完整
  前台體驗），不是漏洞；但要明確意識到——**這個 view 是整個 Web UI 中
  唯一能改動長期記憶正本的入口**，與其餘唯讀 view 的性質根本不同，UI
  上應有視覺區隔（§7）。
- 與既有兩入口（Desktop 前台／Hermes headless）的關係：本質上是前台
  入口的第三種呈現（同一台機器、同一個 repo、同一套 CLAUDE.md），不
  新增第三套決策邏輯；session 歷史依既有規則屬本機、不進 Shared Context。

---

## 7. UI 整合

- **nav 位置**：插在「總覽」與「Jobs」之間（使用者原話指定）。
- **label 建議**：「CoS 終端機」（hint：`Claude Code CLI`）——明確傳達
  「這是完整終端機」而非聊天視窗，避免低估其能力面。
- **視覺區隔**：view 頂部固定一行不可移除的說明：「這是完整的前台
  Claude Code session——與本機終端機同權，可經你核准執行指令與修改
  檔案」；與唯讀 view 在視覺上明確區分（例如深色終端底＋警示色標記）。
- **PTY 服務未啟動／連不上時**：顯示明確的「服務未啟動」狀態＋啟動
  指令說明——**不做假介面、不做 disabled 的假終端**（沿用 P0 mock
  清零原則）。
- **claude process 未 spawn 時**：進入 view 不自動 spawn；顯示明確的
  「啟動 session」按鈕，使用者主動點擊才建立連線＋spawn（寫入例外
  一律要明確的使用者動作，比照遷移案 §5.2「不做點一下就執行」精神——
  這裡「點擊啟動」本身就是那個明確動作）。

---

## 8. Definition of Done（P3 驗收判準）

1. PTY server 獨立 process、bind 寫死 `127.0.0.1`、與 8787/8799 零共用
   程式碼路徑；唯讀 API 既有全部鐵律測試零改動、零回歸。
2. **連線授權雙層皆有測試**：(a) 非白名單 Origin 的 upgrade 被拒；
   (b) 錯誤/缺失 token 被拒（含 constant-time 比對實作檢核）；(c) 超過
   session 上限被拒——三種拒絕各留 audit 記錄。
3. **spawn 邊界測試**：client 訊息面僅 stdin/resize（協定層拒絕未知
   訊息類型並記 audit）；spawn 引數/cwd 寫死的 code review 檢核項；
   claude process 結束後 session 終止、不掉入 shell 的實測。
4. 生命週期實測：單一 session 上限、idle timeout（含「長任務輸出中不
   誤殺」情境）、斷線 60 秒 grace 重連/終止、launcher 關閉不留孤兒。
5. audit log 涵蓋 §5.4 全部事件；**log 內不含任何 stdin/stdout 內容**
   （以含假密鑰字串的 session 實測斷言 log 無該字串——沿用三層假密鑰
   測試的既有模式）。
6. Windows 實測：node-pty prebuilt/建置路徑、claude shim 解析、TUI
   渲染（顏色/中文寬字元/resize）三項留紀錄於 `webui/README.md`。
7. 供應鏈：四項新依賴鎖版、`npm audit` 無未處置 high/critical、檢視
   紀錄存在。
8. UI：nav 位置正確、§7 警語存在且不可移除、服務未啟動時無假介面。
9. `webui/README.md` 安全邊界節更新：PTY 功能的能力聲明（§3.2 殘餘
   風險原文收錄或連結本提案）、port、token 機制、audit log 位置。

---

## 9. 風險總表

| 風險 | 影響 | 緩解／殘餘 |
|---|---|---|
| 瀏覽器面觸達完整終端機（本功能本質） | 任意指令執行面（經 permission） | 雙層連線授權（Origin＋token）把觸達面收斂到「知道 token 的本機前端」；**殘餘**：能力邊界在 Claude Code permission 系統，PTY 層不可再縮（§3.2 第 1 點，使用者已知情確認） |
| Cross-site WebSocket hijacking / DNS rebinding | 惡意網頁操作終端 | server 端 Origin 白名單＋token（§1 T1/T5）；有測試（DoD 2） |
| 明文憑證流經終端輸出 | 教訓一重演；transcript 落地會放大 | 不落 transcript、只記事件（§5.4，已拍板）；P2 憑證唯讀檢視頁是安全替代管道；**殘餘**：畫面即時顯示無法攔截，靠操作紀律（使用者已知情確認） |
| node-pty 原生模組供應鏈/建置 | prebuilt 信任面；升級重驗成本；建置失敗擋開工 | 鎖版＋audit 紀錄；P3 開工第一步實測 prebuilt；建置需求文件化（DoD 6/7） |
| token 洩漏給本機其他 process | 該 process 可開終端 | 誠實承認不可防（T2）；等價於它本來就能跑 `claude`，威脅模型未擴大（§3.2 第 3 點，使用者已知情確認） |
| memory 正本寫入入口進入瀏覽器 | 誤操作污染長期記憶 | 前台 permission 人在迴路（T3）；UI 警語與視覺區隔（§7）；單一 session 上限降低並發寫入面 |
| idle/斷線處置誤殺長任務 | 執行中任務被中斷 | idle 只計 stdin、先提示後終止；斷線 60 秒 grace（§5.2/5.3）；有實測（DoD 4） |
| 協定面日後靜默膨脹（加新訊息類型） | 邊界稀釋 | §2 第 4 點明文：任何新訊息類型需回本提案增補，並列 code review 檢核 |
| TUI 相容性問題（ConPTY×xterm.js×claude） | 體驗劣化/不可用 | P3 開工實測（DoD 6）；不可用時誠實回報再議，不硬上 |

---

## 10. 拍板結果記錄（v1 §10 待拍板項 → 2026-07-23 使用者全案核准）

> v1 的本節是「使用者需回答的最小問題集」；使用者已於 2026-07-23
> **全案核准**——第 1–5 項均採建議預設、第 6 項知情確認成立。
> **P3 Chat gate（PTY 形態）正式解除**；開工時點為 P2 完成後。

1. **連線授權機制**：✅ 已拍板——採建議「launcher per-boot 隨機 token
   （環境變數注入前端與 server、不落磁碟）＋ WS upgrade Origin 白名單」
   雙層。設計見 §1／§2。
2. **transcript 落地**：✅ 已拍板——採建議「不落全文，audit 只記事件」。
   設計見 §5.4。
3. **session 上限與 idle timeout**：✅ 已拍板——採建議「同時 1 個；
   30 分鐘無 stdin 輸入提示＋5 分鐘後終止」。設計見 §5.1／§5.2。
4. **斷線處置**：✅ 已拍板——採建議「60 秒 grace 可重連，逾時終止，
   不做跨啟動 reattach（接回對話走 claude 官方 `--resume`，使用者自行
   操作）」。設計見 §5.3。
5. **spawn 參數形態**：✅ 已拍板——採建議「v1 零參數（純前台互動）」。
   設計見 §3.1。
6. **殘餘風險知情確認**（本提案的核心 gate 條件）：✅ **成立**——使用者
   已明確確認理解並接受 §3.2 三項殘餘風險：(1) 能力邊界在 Claude Code
   permission 系統，任何能連上此 WS 者等同本機操作者；(2) 終端流不經
   redact 三道掃描，憑證明文可能出現在畫面上；(3) token 擋不住同權限
   本機 process（威脅模型未擴大）。
