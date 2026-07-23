# AgentOS Web UI(P0)

以 AgentOSUI 範本為雛形的新 Web UI,依 `docs/webui-migration-proposal.md`(v2)
P0 交付:純 Vite + React SPA(已剝離範本的 Next/vinext/wrangler/Cloudflare
Worker/D1/R2/drizzle/OpenAI 託管假設),加上 Local Bridge 最小寫入例外。

**P0 範圍**:只有「Hermes Dashboard」一個 view(經 Bridge 啟動並以 iframe
內嵌)。範本原有的 Chat/Monitor view 全部是硬編假資料,依 P0 DoD 第 4 條
已整塊移除——未接線的區塊不呈現,P1 接上唯讀 API 後才逐步加回。

## 啟動方式

需求:

- Node.js `>=22.13.0`(實測環境 v22.23.1)
- `hermes` 指令可在 PATH 直接執行(實測 hermes v0.18.2)

```bash
cd webui
npm install     # 第一次
npm run local   # 一鍵啟動 Local Bridge(127.0.0.1:8787)+ UI(http://127.0.0.1:5173)
```

其他指令:`npm run dev`(只起 UI)、`npm run build`、`npm test`、
`npm run typecheck`。

## localhost-only(鐵律)

- Vite dev/preview server host 寫死 `127.0.0.1`(`vite.config.ts`,無參數化入口)。
- Bridge bind 寫死 `127.0.0.1`(`scripts/bridge.mjs` 的 `BRIDGE_HOST` 常數)。
- Bridge CORS 只允許 `^http://(localhost|127.0.0.1):<port>$` 的 origin,其餘 403。

## Local Bridge 安全規格(2026-07-23 使用者核准的最小寫入例外)

實作:`scripts/bridge.mjs`(核心,可測試)+ `scripts/agentos-local.mjs`
(launcher,零參數呼叫=全部走凍結常數)。規格正本:提案 §5.4,逐條落實:

1. **僅四種白名單操作端點**,無其他操作入口:
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
