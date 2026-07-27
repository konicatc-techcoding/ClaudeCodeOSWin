# AgentOS Web UI(P0+P1+P2+P3)

以 AgentOSUI 範本為雛形的新 Web UI,依 `docs/webui-migration-proposal.md`(v2)
交付:純 Vite + React SPA(已剝離範本的 Next/vinext/wrangler/Cloudflare
Worker/D1/R2/drizzle/OpenAI 託管假設),加上 Local Bridge 最小寫入例外。

**P0 範圍**:「Hermes Dashboard」view(經 Bridge 啟動並以 iframe 內嵌)。
範本原有的 Chat/Monitor view 全部是硬編假資料,依 P0 DoD 第 4 條已整塊
移除——未接線的區塊不呈現。

**P1 範圍(既有功能對等)**:新增五個資料 view——總覽/Jobs/成本/Memory/
Logs,與既有 Streamlit dashboard(`dashboard/app.py`)功能對等,全部經
**唯讀 API**(`dashboard/api.py`,`http://127.0.0.1:8799`)取數。UI 層零
檔案/資料庫直接存取(§3.3 獨立資料層)——取數只有 `src/api.ts` 的 fetch
一條路,畫面上零硬編資料。既有 Streamlit dashboard 維持可用、零改動,
與新 UI 並存(退役時點依提案 §4.3 DoD 第 4 項,在 P2 後才進入觀察期)。

## 啟動方式

需求:

- Node.js `>=22.13.0`(實測環境 v22.23.1)
- `hermes` 指令可在 PATH 直接執行(實測 hermes v0.18.2)
- Python venv(repo 根 `.venv`,唯讀 API 使用;零額外套件——stdlib only)

```bash
cd webui
npm install     # 第一次
npm run local   # 一鍵啟動 Local Bridge(127.0.0.1:8787)+ PTY server(127.0.0.1:8801)
                # + UI(http://127.0.0.1:5173);PTY per-boot token 亦在此產生

# 另開一個終端啟動唯讀 API(五個資料 view 的資料來源;不啟動時 UI 會
# 顯示連線錯誤與這行指令,不顯示假資料):
cd .. && .venv/Scripts/python.exe dashboard/api.py   # http://127.0.0.1:8799
```

其他指令:`npm run dev`(只起 UI)、`npm run build`、`npm test`、
`npm run typecheck`。

## localhost-only(鐵律)

- Vite dev/preview server host 寫死 `127.0.0.1`(`vite.config.ts`,無參數化入口)。
- Bridge bind 寫死 `127.0.0.1`(`scripts/bridge.mjs` 的 `BRIDGE_HOST` 常數)。
- Bridge CORS 只允許 `^http://(localhost|127.0.0.1):<port>$` 的 origin,其餘 403。
- 唯讀 API bind 寫死 `127.0.0.1`(`dashboard/api.py` 的 `API_HOST` 常數,
  `create_server()` 無 host 參數、CLI 無 host 選項);CORS 同上白名單,
  非白名單 origin 403;HTTP 層全域攔非 GET → 405(`dashboard/test_api.py`
  逐條測試)。

## 唯讀 API(P1,`dashboard/api.py`)

三鐵律技術強制見 `dashboard/api.py` docstring 與提案 §3;endpoint 一對一
對應 `dashboard/data.py` 既有函式(全部 GET、回 JSON):

| Endpoint | data.py 函式 |
|---|---|
| `/api/health` | `jobs_db_exists()`(+ ok/readonly 標記) |
| `/api/status-counts` | `get_status_counts()` |
| `/api/jobs?limit&status&source` | `get_recent_jobs()` |
| `/api/jobs/<id>` | `get_job()` |
| `/api/cost-summary` | `get_cost_summary()` |
| `/api/systemd-status` | `get_systemd_status()` |
| `/api/memory/inbox-counts` | `get_memory_inbox_counts()` |
| `/api/memory/files` | `get_memory_files()` |
| `/api/domains` | `get_domain_status()` |
| `/api/adapter-config` | `get_adapter_config_status()` |
| `/api/logs/<name>?lines` | `tail_log()`(檔名嚴格白名單 regex,擋 traversal) |

後續在同一支 GET-only server 上追加的唯讀 endpoint(資料層各自獨立模組):

| Endpoint | 資料層 | view |
|---|---|---|
| `/api/resident-status` | `dashboard/data_resident.py::get_resident_status()` | 背景常駐燈號 |
| `/api/update-precheck` | `dashboard/data_update.py::get_update_precheck()` | Hermes 更新(唯讀升級預檢;安全邊界見下方專節) |

每個回應在序列化前統一過 `dashboard/redact.py` 的遞迴憑證掃描(§3.4
第三道防線,P1 先立防線給 P2 用)。測試:
`.venv/Scripts/python.exe dashboard/test_api.py`(CORS/403、405、
import guard AST 白名單、bind 檢核、bot_token 不外洩、endpoint 行為)。

## P2:Stage 3 三項觀測功能(2026-07-23,設計正本 stage3 提案 v2 §2–§4)

資料層在 **`dashboard/data_stage3.py`**(新模組;`data.py`/`app.py` 零改動,
路徑 A 複用 `data.get_systemd_status()`),endpoint:

| Endpoint | data_stage3.py 函式 | view |
|---|---|---|
| `/api/capability-lanes` | `get_capability_lane_status()` | 憑證/Lane 狀態 |
| `/api/credential-status` | `get_hermes_credential_status()` | 憑證/Lane 狀態 |
| `/api/schedule-table` | `get_cron_schedule_table()` | 總覽(排程表區塊) |
| `/api/hermes/sessions?source&limit` | `get_hermes_sessions()` | Hermes Sessions |

**安全邊界(P2 新增的外部讀取路徑與唯讀邊界)**:

- 新增唯讀讀取路徑:`%LOCALAPPDATA%\hermes\state.db`(經
  `HermesSessionAdapter(snapshot=True)`,mode=ro + `PRAGMA query_only`)、
  各 profile `auth.json`(**白名單欄位抽取**,見下)、
  `%LOCALAPPDATA%\hermes\cron\jobs.json` 與各 profile cron store
  (純檔案讀取,不 import 任何 cron 寫入函式)、全域與各 profile 的
  `config.yaml`(**只抽 `model.default`/`model.provider` 兩個非敏感
  設定值**——供漂移比對與 lane 表「實際生效模型」欄;其他區塊一律
  不外流)。
- **憑證頁絕不顯示憑證值**:entry 只抽
  `id/priority/last_status/last_refresh/source/label` 六個白名單欄位
  (組新 dict,原始 dict 不出函式作用域);頂層 `providers` 只取名稱清單;
  `suppressed_sources` 不讀取。三道防線:資料層白名單+輸出前掃描
  (`redact.py` 共用正本)→ API 序列化前掃描 → UI 欄位白名單
  (禁止泛型 JSON dump)+不可移除警語。
- **模型漂移旗標只偵測、只標示**(aligned/DRIFTED/n/a+花費方向):
  絕無任何 pin/對齊/修復的寫入入口(花費保護 #44585 不被繞過)。
- **session 列表不含訊息內容**:回傳結構遞迴不含 `content`,也不含
  metadata 的 `session_key`/`chat_id`;不做「點進去看對話全文」。

三層假密鑰斷言測試(fixture 一律 `FAKE_`/`TEST_` 前綴+tempfile 隔離):

```bash
.venv/Scripts/python.exe dashboard/test_data_stage3.py  # 資料層(白名單/掃描/退化/漂移三情境)
.venv/Scripts/python.exe dashboard/test_api.py          # API 回應全文層
npm test                                                # UI 渲染層(tests/stage3-render.test.mjs,rolldown+react-dom/server)
```

## Local Bridge 安全規格(2026-07-23 使用者核准的最小寫入例外)

實作:`scripts/bridge.mjs`(核心,可測試)+ `scripts/agentos-local.mjs`
(launcher,零參數呼叫=全部走凍結常數)。規格正本:提案 §5.4,逐條落實:

1. **僅四種白名單操作端點**(第一群組;2026-07-27 起另有服務控制第二
   群組,見下方專節),無其他操作入口:
   - `GET  /health` — 查詢 bridge 與 dashboard 狀態
   - `POST /api/hermes/dashboard` — 啟動 Hermes Dashboard
   - `POST /api/hermes/dashboard/reload` — 重新載入(重啟自有 process)
   - `POST /api/hermes/dashboard/stop` — 停止「由本 bridge spawn 的」process
2. **無任意 shell command API**:spawn 的指令與參數是凍結常數
   `hermes dashboard --host 127.0.0.1 --port 9119 --no-open`;HTTP 介面
   不讀取 request body、不解析 query 參數,任何指令/參數字串都進不來。
3. **PID/process ownership**:stop/reload 只作用於 bridge 自己 spawn 的
   child PID(Windows 以 `taskkill /PID <自有pid> /T /F` 樹殺);對非本
   bridge 啟動的 Hermes process 一律 409 拒絕。範本原版的
   `hermes dashboard --stop` 是 CLI **全域**停止語意(會殺掉所有 hermes
   web server),不符規格,已棄用。
4. **重複啟動防護**:dashboard 已在線(不論誰啟動)再收到啟動請求=
   no-op(`reused: true`),不產生第二個 process;併發啟動請求以
   promise 去重(有測試鎖定)。
5. **audit log**:每次 start/stop/reload 操作(含拒絕與 no-op)寫一筆
   「時間 | 操作 | PID | 結果」到 **`<repo>/logs/webui_bridge_audit.log`**;
   `GET /health` 屬查詢,不寫 audit。

其他設定寫入功能維持唯讀,後續個別審核(使用者原句;本 bridge 例外
不構成任何擴張依據)。

測試:`npm test`(`tests/bridge.test.mjs` 以 `FAKE_` 前綴 fixture 逐條
覆蓋上述規格;`tests/ui-static.test.mjs` 鎖定 mock 清零與託管殘留清零)。
過渡期八項安全檢查:`python scripts/webui_security_check.py`(repo 根,
純唯讀、輸出報告)。

## 服務控制鍵——bridge 白名單第二群組(2026-07-27 v1.1 核准)

設計正本 [docs/webui-service-control-proposal.md](../docs/webui-service-control-proposal.md)
v1.1 §2。這是 bridge 8787 上的**第二個白名單操作群組**:對 WSL 側兩個
hermes 常駐 systemd 單元做 start/stop/restart。與第一群組(Hermes
dashboard 操作)以**獨立常數分列**,測試斷言兩群組各自的完整枚舉,防
互相滲透。

- **與 bridge PID-ownership 模式的差異(誠實對照,提案 §2.1)**:第一
  群組的邊界是「只能停自己 spawn 的 child process」(PID ownership);
  本群組的對象是 systemd 管理的具名單元,**PID ownership 模型不適用**
  ——邊界改由「具名白名單窮舉」替代:能控制什麼由枚舉窮舉,不由
  ownership 推導。
- **單元枚舉寫死**(`SERVICE_UNIT_WHITELIST`):僅 `hermes-worker.service`
  /`hermes-telegram.service`,不含 timer。**動詞枚舉寫死**
  (`SERVICE_OP_WHITELIST`):僅 `start`/`stop`/`restart`。
- **指令固定模板**:`wsl -d Ubuntu systemctl --user <op> <unit>`
  (`SERVICE_COMMAND` 凍結常數)。route 表=兩枚舉的笛卡兒積(6 條
  `POST /api/service/<unit>/<op>`),lookup 全字串嚴格比對——op/unit
  永遠取自表內凍結值,不從 URL 解析、不讀 body、不解析 query。白名單外
  一律 **400 + audit**。
- **明確不做**(提案 §0.2):`enable`/`disable`/`mask`、`daemon-reload`、
  unit 檔、`wsl --terminate`——動詞枚舉封閉,技術上不存在入口。
- **stop 的真實語意**(v1.1 §2.4 定案,UI 按鈕旁明示):停止後不自動
  恢復,直到下次 Windows 登入/WSL distro 重啟時由 systemd(依 enable
  狀態)重新拉起(`HermesWslKeepAlive` 只保 distro 活著,不會拉回被
  stop 的服務)。
- **audit log**:沿用同一份 `logs/webui_bridge_audit.log`,每次操作
  (含拒絕)一筆——時間、動詞(`service:<op>`)、單元、結果/exit code。
- **重複操作防護**:同一單元已有操作進行中 → 409 + audit。
- **UI**(`src/ServiceControl.tsx`,sidebar 燈號下方):枚舉與 bridge
  一致(測試斷言);全部動作**二次確認**;操作後**不樂觀更新**(顯示
  黃色收斂等待,狀態以燈號 30 秒輪詢收斂為準);bridge 未運行時按鈕為
  明確停用狀態+原因說明(不做假按鈕)。唯讀燈號(`ResidentStatus.tsx`)
  維持零操作入口——讀寫分離,顯示歸燈號、操作歸本元件。

測試:`tests/service-control.test.mjs`(FAKE wsl fixture,**絕不對真實
systemd 單元執行任何操作**;枚舉完整性/400/409/exit code/audit/UI 邊界
逐條鎖定);`scripts/webui_security_check.py` 第 12 項靜態鎖定。

## P3:ClaudeCode CLI(PTY 真終端機,2026-07-24)

設計正本與核准紀錄:`docs/webui-pty-terminal-proposal.md`(v2,含 §3.2
殘餘風險知情確認)。**這是整個 Web UI 中唯一的寫入型 view**——xterm.js
連上獨立 PTY server,spawn 一個完整的前台 `claude` session。

### 能力聲明(提案 §3.2 殘餘風險,使用者已知情確認)

- 本 view 開的是**前台互動 session**(不帶 `-p`),與本機終端機**同權**:
  可經使用者在 session 內核准執行指令、讀寫檔案、**編輯 memory 正本**。
  「只能 spawn claude」收斂的是入口,不是能力——能力邊界在 Claude Code
  的 permission 系統(人在迴路),不在 PTY 層。
- 終端輸出是**未經** `dashboard/redact.py` 掃描的原始流:在 session 裡讀
  憑證檔會把明文直接印在畫面上,PTY 層無技術手段攔截(教訓一適用)。
  安全替代管道:憑證/Lane 狀態頁(P2)。
- token 擋得住不知道 token 的網頁(cross-site WS hijacking/DNS rebinding),
  擋不住同使用者權限的本機惡意程式——後者本來就能直接跑 `claude`,威脅
  模型未擴大。

### 架構與安全機制

- **獨立 process、獨立 port**:`scripts/pty-server.mjs`,bind 寫死
  `127.0.0.1:8801`(常數,無參數化入口),與 bridge(8787)/唯讀 API
  (8799)零共用程式碼路徑;pty-server 的 import 僅 node 內建+`ws`+
  `node-pty`(不碰 bridge/唯讀資料層,有測試+安全檢查鎖定)。
- **雙層連線授權**(WS upgrade 時逐層驗證,缺一不可):
  1. Origin 白名單:僅 `http://127.0.0.1:5173` 與 `http://localhost:5173`
     (凍結常數、精確全字串比對;缺 Origin 一律拒絕)。
  2. per-boot token:launcher 每次啟動 `randomBytes(32).toString("hex")`,
     經環境變數注入 PTY server(`AGENTOS_PTY_TOKEN`)與 Vite
     (`VITE_AGENTOS_PTY_TOKEN`),**不落磁碟**;比對走 sha256 等長化+
     `timingSafeEqual`(constant-time)。拒絕回應不洩漏差在哪,audit 記
     精確原因。缺 token 時 PTY server 拒絕啟動(無「無授權模式」)。
  - 配套:Vite dev/preview `cors: false`(SPA 同源不需要 CORS;避免其他
    origin 讀取含 token 的轉譯模組——收緊,不影響既有判準)。
- **spawn 邊界**:目標=啟動時解析一次並鎖定的 `claude` 絕對路徑(PATH+
  PATHEXT 解析,實測本機為 `C:\Users\razer\.local\bin\claude.exe`,非
  .cmd shim);引數=凍結空陣列(v1 零參數);cwd=repo 根(寫死)。
  client 唯二能送的訊息是 `stdin` 與 `resize`——未知訊息類型→audit+
  斷線。claude process 結束=session 終止,**不掉回任何 shell**。
- **生命週期**(提案 §5 拍板值):同時最多 1 個 session(第二個連線
  409);idle 30 分鐘無 stdin → 終端提示,再 5 分鐘無 stdin **且輸出
  靜默** → 終止(只計輸入;長任務輸出中不誤殺——終止前檢查近期輸出,
  輸出未靜默則延後);WS 斷線 60 秒 grace 內同 token 可重連接回(無
  server 端 buffer,斷線期間輸出丟棄;不做跨啟動 reattach,續對話用
  claude 官方 `--resume` 在新 session 內自行操作);launcher 關閉以
  taskkill 樹殺 PTY server,ConPTY 底下的 claude 一併結束,不留孤兒。
- **audit log**:`<repo>/logs/webui_pty_audit.log`,每事件一行(時間|
  事件|PID|結果):server-start/stop、connect-reject(含原因)、spawn、
  disconnect/reconnect/grace-expired、idle-warning/idle-timeout、
  protocol-violation、exit、terminate。**絕不記 stdin/stdout 內容**
  (不落 transcript,教訓一;有假密鑰斷言測試+靜態檢查「唯一寫入點
  在 audit()」)。
- **UI**:nav 在「總覽」與「Jobs」之間,label「ClaudeCode CLI」;頁面
  頂部警語不可移除(無條件渲染,有靜態測試);PTY server 未啟動/缺
  token 時顯示明確狀態與啟動指引,不做假介面;進 view 只做 GET /health
  探測,**不自動 spawn**——「啟動 session」按鈕才是那個明確的使用者動作。

### Windows/ConPTY 實測(2026-07-24,Node v22.23.1)

- `node-pty@1.1.0` 安裝走 **prebuilt binary**(`prebuilds/win32-x64`,
  含 conpty.node+pty.node),**未觸發 node-gyp 現地編譯**——本機毋須
  VS Build Tools。升級 node-pty 或 Node 大版本後需重驗 prebuilt 是否
  仍命中(`install` script fallback 是 `node-gyp rebuild`)。
- ConPTY 可用(Windows 10 19045):`pty.spawn` 真實 claude 與 node
  fixture 皆正常;輸出含 ANSI/VT 序列,resize 經 `pty.resize()` 轉發。
- node-pty 的 ConPTY handle 會讓 node process 事件迴圈不退出:測試
  runner 因此加 `--test-force-exit`(`npm test`;斷言失敗仍正確回傳
  非零 exit code,已驗證);對已結束 process 呼叫 `kill()` 會噴
  AttachConsole 噪音,server 端已先以 `process.kill(pid,0)` 探活避開。
- **TUI 渲染**:協定層實測通過(真實 claude 的 ANSI 流經 WS 到達
  client;E2E 見下)。xterm.js 瀏覽器端的視覺呈現(顏色/中文寬字元/
  resize 重排)屬人工目視項,待使用者實際開頁確認;若有排版問題依
  提案「不可用時誠實回報再議」處理。

### E2E 紀錄(2026-07-24,真實環境)

`node tests/fixtures/e2e-pty-client.mjs`(需 `npm run local` 在跑;
一次性驗證 client,不屬於 `npm test` 套件):Vite 200 → token 經 Vite
注入前端模組(與瀏覽器同途徑取得)→ 非白名單 Origin 403 / 錯誤 token
403 → 正確雙證連上、spawn 真實 claude(TUI 輸出+ANSI 到達)→ `/exit`
→ 收到 exit 訊息、health 回報 session 清空。另實測:斷線 60 秒 grace
到期自動終止真實 claude(audit 三行:disconnect/grace-expired/terminate);
launcher 樹殺後 claude child 同步結束、8801/8787/5173 全部釋放,零殘留。
E2E 過程未在 claude session 內執行任何實質指令。

## Hermes 更新頁——唯讀升級預檢的安全邊界(階段一,2026-07-24;版本欄 2026-07-25 補)

設計正本 [docs/webui-update-button-proposal.md](../docs/webui-update-button-proposal.md)
§3/§6/§8。**本節是該提案 §6 第 4 項明文要求載明的安全邊界。**

- **第一鐵律(不可協商)**:**本頁絕不呼叫 `hermes update`
  (`hermes_cli/main.py:11086`)、bootstrap installer 的任何 update 模式
  (`hermes-setup.exe --update`)、或 Hermes Desktop Install 鈕所走的那條路徑
  (`apps/desktop/electron/main.ts:2584 applyUpdates()`)。**
  理由是 2026-07-24 事故:那三條路徑的 diverged fallback 都是
  `reset --hard origin/main`,會把本機客製整批毀掉。**本頁只「看」,不「動」。**
- **零執行鈕**:UI 沒有任何執行/升級/同步入口(階段二寫入未核准)。唯一互動是
  共用的「重新整理」讀取鈕,它**只重跑唯讀預檢,不 fetch、不寫入**。
  三重鎖定:`views/UpdatePrecheck.tsx` 內不得自訂 `<button>`、view 內唯一
  `onClick` 只能是 `reload`(渲染測試 + 原始碼靜態斷言 +
  `scripts/webui_security_check.py` 第 11 項)。
- **git 子指令白名單(兩層強制)**:資料層 `dashboard/data_update.py` 只跑
  `rev-parse` / `rev-list` / `merge-base` / `for-each-ref` / `describe` /
  `log` / `status --porcelain` / `remote` / `remote get-url` /
  `show HEAD:pyproject.toml`。**絕不**跑 `fetch` / `pull` / `merge` /
  `reset` / `checkout` / `clone` / `push` / `commit` / `rebase` / `stash`。
  強制方式:(1) 無參數查詢必須是 `FROZEN_GIT_TEMPLATES` 成員;
  (2) 帶 remote 名的查詢必須用 `REF_TEMPLATE_BUILDERS` 建構器,且 remote 名
  須過 `REMOTE_NAME_RE`(擋 `-` 開頭旗標、`/` 路徑、空白 → 無法注入)。
  非白名單一律 `ValueError`。
- **單一 spawn 位點**:全模組 `subprocess.run` **恰為一處**(`_exec`),
  且只會是 `git`(Windows)或 `wsl -d <distro> --exec git`(WSL)——
  模組認得的可執行檔常數只有 `GIT_BIN` / `WSL_BIN` 兩個。
- **遠端資訊不 fetch**:所有 ahead/behind/ff 一律相對**本地已有的**
  `<remote>/main` 計算,UI 明白標示「遠端資訊可能過期——未執行 fetch」。
- **WSL 不喚醒**:探測前先用 `data_resident._distro_state()`(只跑
  `wsl --list --verbose`)守門;distro 非 Running 時**完全不下任何 `wsl -d`
  指令**,直接顯示灰燈。「觀測」不得變成「改變系統狀態」。
- **live 版本字串同樣零副作用**(2026-07-25 補):
  `v0.19.0 upstream 3910ab28 + local 97011887 (+12 carried commits)`——
  版本號讀 **HEAD 的 `pyproject.toml` blob**(`git show HEAD:pyproject.toml`,
  凍結字面、零參數化),`upstream <sha>` 取 `merge-base`。
  **刻意不跑 `hermes --version` 或任何 hermes CLI**:那會 spawn process、
  載入整包 hermes,且其 banner 會寫 `~/.hermes/.update_check` ——
  「看狀態」就變成「動狀態」了。
- **偵測型防線(這頁存在的另一半價值)**:預檢會在「客製 commit 相對官方上游
  歸零」(疑似被 reset 成純上游)、「rescue ref 全部遺失」、「工作樹髒」時亮**紅**;
  在「本機領先私有備份 N 個 commit(尚未推送)」時亮**橙**。後者正是防重演機制
  的失效條件警告——`reset --hard origin/main` 只在 `main == origin/main` 時
  才是 no-op(見提案 §6.1)。

## 實測數據(2026-07-23,Windows 10,hermes v0.18.2)

- **`hermes dashboard` 冷啟動**:spawn 到 HTTP 可回應 **34.7 秒**
  (34741 ms);經 bridge 的第二次啟動 11.7 秒。範本原 timeout 90 秒
  本次足夠,但考量 hermes web UI 需重新 build 的情境與 gateway
  「啟動後約 3.5 分鐘才寫狀態檔」的既有教訓,bridge `START_TIMEOUT_MS`
  放寬為 **240 秒**。
- **`hermes dashboard --status` 會回報 stale PID**(實測列出 3 個已不存在
  的 process)——不可作為存活依據,bridge 一律以自有 child process 狀態
  + HTTP 探測判定。
- **Windows spawn 行為**:
  - `spawn("hermes", ...)` 不帶 shell 可直接解析 PATH 上的 `hermes.exe`
    (CreateProcess 行為),毋須 `shell: true`。
  - `spawn("node_modules/.bin/vite")`(無副檔名)→ `ENOENT`;
    `spawn("node_modules/.bin/vite.cmd")`(不帶 shell)→ 同步 throw
    `EINVAL`(Node ≥20.12.2 對 CVE-2024-27980 的 hardening)。
  - 修正:launcher 以 `node node_modules/vite/bin/vite.js` 啟動 dev
    server——不經 shell、指令仍為寫死常數,與「無任意 shell」規格相容。

## 供應鏈

依賴面(相對範本大幅縮減):runtime 僅 `react`/`react-dom`,dev 僅
`vite`/`@vitejs/plugin-react`/`typescript`/型別包。範本的
next/vinext/wrangler/drizzle/tailwind/eslint-config-next 全數移除
(樣式為手寫 CSS,未使用 Tailwind utility,`@import "tailwindcss"`
一併移除)。`npm audit`(2026-07-23):vite 8.0.13 有 1 個 high
(GHSA-fx2h-pf6j-xcff `server.fs.deny` Windows bypass、GHSA-v6wh-96g9-6wx3
launch-editor NTLMv2)→ 已升級 `vite@8.1.5`,audit **0 vulnerabilities**。

P3 增量(2026-07-24,提案 §4.2 核准的四項,鎖定精確版本、除此不新增):

| 依賴 | 版本 | 端 | 理由 |
|---|---|---|---|
| `@xterm/xterm` | 6.0.0 | 前端 | 終端渲染;純前端、VS Code 同源,無 native code |
| `@xterm/addon-fit` | 0.11.0 | 前端 | 終端尺寸自適應 content 區,體積小 |
| `node-pty` | 1.1.0 | PTY server | ConPTY 偽終端;**原生模組=供應鏈風險重心**,本機命中官方 prebuilt(win32-x64),升級需重驗 |
| `ws` | 8.21.1 | PTY server | WebSocket server;純 JS、零 runtime 依賴 |

`npm audit`(2026-07-24,四項安裝後):**0 vulnerabilities**。
token/upgrade 驗證用 Node 內建 `crypto`/`http` 實作,無額外依賴。
