# hermes-agent 升級 v0.19.1 受控 merge 計畫（v1）

> 產出：engineering domain 隔離 worktree 調查（2026-08-03）。
> 本檔**不 commit**，留工作樹供審閱。live 切換須經使用者核准後由主 session 執行。

## TL;DR

- merge 目標 = 官方 tag `v2026.7.30`（release commit `cc4cab2f592e60a197e796506de9168f74baf3ea`，"chore: release v0.19.1 (2026.7.30)"）。上游 main tip 已超前 934 commits，依指示**不追 tip、只到 0.19.1**。
- 整合已在隔離 worktree 完成：branch `integration/v0.19.1-custom`，**整合 tip = `aa65ff2863786f58ac59c442d36d06f22bb2041b`**（短碼 `aa65ff286`）= merge commit `d834c5002` + 測試對齊 `444a3fab2` + Slack adapter bug 修正 `aa65ff286`（見 §3.8）。
- 衝突 6 檔，全部依「客製功能與上游改進都要活」解掉；上次刻意放棄的 `_standalone_send` 多 token fallback 取捨**這次沒有重演**（上游 0.19.1 對 `plugins/platforms/slack/` 零變更）。
- 沙箱驗證：venv + `pip install -e ".[messaging]"` + web `npm install/build` 全綠；前端全套 148 tests 全綠（含客製 gateway-multiplex 13 tests）；Python 三 chunk 共 **13,123 passed**，失敗全數歸類為環境性（Unix-only／跨測試干擾），詳見 §4。
- 有 2 個需使用者知悉的解衝突判斷（§3.2 gateway/run.py、§3.4 web_server.py），都屬「上游修正取代客製寫法、客製功能經其他路徑存活」——不算推翻既有拍板，但列出供確認。
- ⚠ 測試過程有一起**測試隔離洩漏事故**（上游測試 mock 不完整，殃及 live gateway 進程與 live home 少量檔案），詳見 §11——live gateway 目前推定停機，需使用者確認後重啟。

## 0. 調查方法與隔離聲明

- 主 repo（`C:\Users\razer\AppData\Local\hermes\hermes-agent`，live gateway 使用中）只做過一件寫入：`git fetch upstream`（僅寫 refs，不動工作樹、不動 branch）。
- 所有 merge／解衝突／安裝／測試都在隔離 worktree：
  - 路徑：`C:\Users\razer\AppData\Local\hermes\worktree-0191`
  - branch：`integration/v0.19.1-custom`（基於 live main tip `970118870`）
  - 沙箱 venv：worktree 內 `.venv`（與 live 的 `hermes-agent\venv` 完全分離）
  - `HERMES_HOME` 指向 session scratchpad 下的 `hermes-home-sandbox`，未讀寫 `~/.hermes` 正本。
- 未 push、未跑 `hermes update`、未動 live main。**例外誠實揭露**：上游測試自身的隔離缺陷造成 runtime 層洩漏（live gateway 進程受波及、live home 少量檔案被觸碰），非本調查主動操作，完整記錄與處置建議見 §11。

## 1. 座標（事實表）

| 項目 | 值 |
|------|-----|
| live main（客製 tip） | `970118870` = v0.19.0 base + 12 carried commits（Slack delivery hardening 四階段 + outbound-only + gateway multiplexer 系列） |
| merge base（v0.19.0 基準點） | `3910ab28c0892fcf846fc61318d2fd15689eddf1` |
| merge 目標（v0.19.1） | `cc4cab2f592e60a197e796506de9168f74baf3ea`（tag `v2026.7.30`） |
| 整合 tip | `d834c50023eed1bbc41c636ef76cb2929a4c5e0b`（`integration/v0.19.1-custom`） |
| 上游 tip 超前 0.19.1 | 934 commits（本次不納入） |
| 0.19.0→0.19.1 規模 | first-parent 1,413 commits；4,109 檔、+282,704/−406,918 行 |

## 2. 上游 0.19.0 → 0.19.1 變更摘要

官方定位：patch rollup（~1,000+ PRs / ~2,789 commits），完整 curated release notes 官方留到 v0.20.0 才出。實際盤點重點：

### 2.1 有沒有動到客製熱區

| 熱區 | 上游動了什麼 | 對客製的影響 |
|------|--------------|--------------|
| `plugins/platforms/slack/`（ledger/allowlist/outbound-only 的宿主） | **零變更** | 客製原樣續行。上次放棄的「upstream channel 級多 token fallback」取捨**不需重新拍板** |
| `hermes_cli/send_cmd.py` | 僅 `read_user_config_raw` 重構（env bridge 讀法） | auto-merge 乾淨；`--message-key`/`--force-resend` CLI 路徑完整 |
| `cron/scheduler.py` `_deliver_result` | 新增 **relay fail-closed** 閘門（relay 目標失敗不得走 standalone 重送） | 1 衝突，已解（§3.1） |
| gateway multiplexer lifecycle（`gateway/run.py`、`hermes_cli/gateway.py`） | credential 衝突路徑改為**不 disconnect**（保護 Photon sidecar）；新增 listener/sidecar port 衝突偵測；named-profile guard 上游自己也有一份 inline 版 | 2 衝突，已解（§3.2、§3.3） |
| `hermes_cli/web_server.py` | 大重構：profile/git/cron/sessions 等路由抽到 `web_routers/*` APIRouter 模組（`web_deps.late()` 晚綁定回 web_server）；messaging 端點改讀 **scoped profile 自己的** `gateway_state.json` | 2 衝突，已解（§3.4） |
| `gateway/run.py` 整體 | 大規模 refactor：TurnContext/TurnRunner seam、SessionState 合併 19 個 session dicts、shared markdown chunker 等（檔案 ~35k 行變動） | 客製 multiplexer 區塊（`_start_one_profile_adapters`、conflict recorder 等）auto-merge 存活，僅 1 處衝突 |
| 測試套件 | **大瘦身**：prune wave 1+2，46,820 → 19,757 test functions | 客製 12 個測試檔全數存活；`test_gateway_windows.py` 的 4 個上游 drain 測試被上游 prune（§3.5） |

### 2.2 依賴變更（影響 live 重裝的）

- `cryptography` 46.0.7 → **48.0.1**（CVE 修補）
- `starlette` 1.0.1 → **1.3.1**、`python-multipart` 0.0.27 → 0.0.32（web/mcp/computer-use extras）
- **`nemo-relay` 從 optional extra 轉為核心依賴**（平台 gated，win32/AMD64 在列，`>=0.6.0,<0.7`；舊 `nemo-relay` extra 清空）
- 新增 extras：`wake`（wake-word 套件組）、`otlp`（OpenTelemetry 匯出）、`vercel`
- `py-modules` 新增 `hermes_state_common` / `hermes_state_portability` / `hermes_state_schema` / `hermes_state_search`（hermes_state 拆分）——**editable 重裝必要**，舊 `.pth` 不含這些模組
- 新 pytest markers：`requires_wal`、`no_isolate`、`ssh`

### 2.3 Breaking / 行為變更（與本部署相關者）

- **config 自動遷移地板拉到 v12** + deprecated shim 退場（`refactor: config auto-migration support floor at v12`）。本機 config 一直跟著版本走，風險低，但 runbook 驗證項含 doctor。
- relay 失敗 fail-closed（不再 fallback standalone）——只影響 relay lane，本部署 Slack 走 standalone/adapter 路徑，不受影響。
- web dashboard 路由模組化：對 API 消費端（AgentOSUI Web UI 的唯讀 API）**路由表等價**（上游宣稱 route-table equality verified），行為不變。
- Windows native 修正一批（`fix(windows): native Windows correctness for CLI, gateway status, banner, ...`）——正向。

## 3. 衝突清單與逐檔解法（6 檔）

解法原則：**客製功能與上游改進都要活**。

### 3.1 `cron/scheduler.py`（standalone fallback 閘門）

- 衝突：HEAD = 客製 Slack idempotency key（`_standalone_send_kwargs["message_key"]`）；上游 = 新增 relay fail-closed 區塊。
- 解法：**兩者都留**。上游 relay 檢查先行（relay 失敗 → 記錯誤 → `continue`，不進 standalone），之後才是客製 Slack ledger key 計算。互不干擾：relay 目標根本不會走到 Slack standalone 送出。

### 3.2 `gateway/run.py`（multiplexer credential 衝突拒絕路徑）⚠ 需知悉

- 衝突：HEAD = 客製 `_record_profile_credential_conflict`（把拒絕寫進被拒 profile 的 `gateway_state.json` 供 dashboard 顯示）+ `_safe_adapter_disconnect`；上游 = **移除 disconnect**（never-connected adapter 沒有資源；disconnect 反而會動到共享 platform state，同 credential 的 Photon adapter 甚至會關掉主 profile 的 live sidecar）+ 新增 listener/sidecar port 衝突偵測區塊。
- 解法：保留客製 record（dashboard 呈現不變）+ **採上游「不 disconnect」語意**（這是上游發現的 bug 修正，取代客製寫法）+ 全收上游新 listener 衝突偵測（其 claim 簿記在後續 auto-merge 區已被引用，必留）。
- 給使用者：客製功能全存活；唯一被換掉的是「拒絕後 disconnect」這個動作，上游論證它有害。維持此解不需拍板，僅告知。

### 3.3 `hermes_cli/gateway.py`（named-profile-under-multiplexer guard）

- 衝突：HEAD = 客製委派 `gateway_ownership.multiplex_effective`（f8de8bee2 的 single source of truth）；上游 = 自己的 inline 版（env override → raw config read）。
- 解法：**保留客製委派**。語意等價（env 覆寫優先、config 兩鍵、fail-open→False），且客製版是 4 個 CLI/模組層 guard 共用的唯一實作，換 inline 會造成雙實作漂移——正是客製 commit 要消滅的東西。

### 3.4 `hermes_cli/web_server.py`（2 處 + 1 處結構抽取）⚠ 需知悉

- 衝突 A/B（`GET /api/messaging/platforms`、`POST .../test`）：HEAD = 讀全域 runtime 再套客製 `_overlay_profile_conflict_runtime`；上游 = scoped 時**直接讀該 profile 自己的** `gateway_state.json`（配合新 `resolve_gateway_liveness` + `profile_home` 參數的整套設計，#71211 系列）。
- 解法：**採上游 scoped 讀法**。客製 credential-conflict 記錄本來就寫在同一個檔案裡，上游讀法會原生帶出 conflict entry——客製功能經資料路徑存活，overlay shim 變冗餘（保留定義未刪，零風險）。
- 衝突 C（profiles 路由）：上游把 `/api/profiles` 等整組抽到 `hermes_cli/web_routers/profiles.py`，經 `late("_profile_to_dict")` 晚綁定回 web_server。客製 `_profile_to_dict`（含 `served_by_multiplexer` 欄位、`multiplex_configured` 未給時自動計算）仍在 web_server，router 呼叫時自動走客製版——**前端 multiplexer ownership 顯示不動任何程式碼即存活**（已由前端 13 個 gateway-multiplex 測試證實）。解法：採上游 router 抽取。
- 給使用者：overlay 從「主動合併」變「同檔原生呈現」，行為面等價（scoped 條目原本就以 profile 檔為權威）。維持此解不需拍板,僅告知。

### 3.5 `tests/hermes_cli/test_gateway_windows.py`

- 衝突：HEAD = 基準 drain 測試 ×4 + 客製 multiplexer guard 測試 ×5；上游 = 測試瘦身 wave 把 drain 測試 prune 掉（`_drain_gateway_pid` 實作仍在，只是上游不再測）。
- 解法：跟隨上游刪 4 個 drain 測試，**保留 5 個客製 multiplexer guard 測試**。合併後檔案 12 個測試（7 上游基準 + 5 客製）。

### 3.6 `website/docs/reference/cli-commands.md`

- 表格衝突：保留客製 `doctor` / `cleanup` 兩列 + 採上游帶 relay 連結的 `enroll` 列。

### 3.7 上次取捨的狀態確認

07-24 放棄的「standalone `_standalone_send` 上游 channel 級多 token fallback」：上游 0.19.1 對 `plugins/platforms/slack/` 目錄**完全沒動**（`git diff 3910ab28c cc4cab2f5 -- plugins/platforms/slack/` 為空），結構未變 → 依任務指示維持同一取捨，且這次根本沒有衝突發生。**不需重新拍板。**

### 3.8 merge 後跟進修正（2 commits，測試驅動）

沙箱測試揪出兩件事，已修並 commit 到整合 branch：

1. **`444a3fab2` test(merge)**：客製測試 `test_multiplex_credential_conflict_status.py` 兩處仍斷言舊「拒絕後 disconnect」語意，對齊 §3.2 採納的上游 no-disconnect 語意（同 970118870「reconcile 2 custom tests to upstream evolution」模式）。客製 conflict 記錄功能本身測試全過。
2. **`aa65ff286` fix(slack)** ⚠ 需知悉：上游 0.19.1 重寫的 `TestStandaloneSendMedia` 測試照出一個 **live main 現存的潛在 bug**——07-24 客製 retry-loop 重寫把 `resolve_proxy_url` 加進 `_standalone_send` 的函式內 import，名稱遮蔽（shadowing）導致**較早執行的 media 上傳路徑**（`files_upload_v2` 分支）一觸即 `UnboundLocalError`。即 live 上「Slack standalone 送出帶附件」目前是壞的（純文字路徑不受影響，這也是平常沒炸的原因）。修正 = 函式內只 import `proxy_kwargs_for_aiohttp`（回到上游 base 模式）。修後 Slack 全測試集 350 passed。

## 4. 沙箱驗證結果

環境：worktree 內獨立 `.venv`（Python 3.11.0）、`pip install -e ".[messaging]"` 成功、另補 `pytest==9.0.2`/`pytest-asyncio==1.3.0`/`mcp==1.26.0`（後者比 07-24 baseline 多裝，消除「缺 pytest-asyncio」與 mcp 收集錯誤兩類環境性噪音）。`HERMES_HOME` = scratchpad 沙箱。`PYTHONIOENCODING=utf-8`（迴避 cp950 baseline）。

- 版本字串：`hermes --version` → `Hermes Agent v0.19.1 (2026.7.30) · local d834c500` ✅
- 前端：`npm install` + `npm run build`（tsc + vite）✅；全套 vitest **21 檔 / 148 tests 全數通過**，含客製 `gateway-multiplex.test.ts` 13 tests ✅
- 客製功能靜態確認：`tools/slack_send_ledger.py`、`hermes_cli/gateway_ownership.py`、`_synthesize_slack_send_pconfig`（tools+cron）、`compute_effective_enabled`（gateway/config.py）、`--message-key` CLI 路徑全部在位 ✅
- Python 熱區套件（`tests/gateway` + `tests/hermes_cli` + `tests/tools` + `tests/cron`）：

分三個 chunk 跑（單進程整跑，附 `--timeout=180` 防卡死）：

| Chunk | passed | failed | skipped | 失敗歸類 |
|-------|-------:|-------:|--------:|----------|
| `tests/tools` + `tests/cron` | 5,171 | 164 | 134 | 全環境性：Unix 權限（0600/0700/chown）、`/dev/*` 裝置路徑、`AF_UNIX`、WinError 1314（symlink 需特權）、lazy-install 停用（daytona/fal）、MSYS rg 路徑轉換 |
| `tests/gateway` | 4,506 | 52 | 29 | 4 個 merge 相關（已修，§3.8：2 個測試對齊 + 2 個揪出 Slack adapter bug）；其餘環境性：feishu「Could not determine home directory」×23、WinError 10106（scrubbed-env 子進程 winsock init）、systemd（Unix-only） |
| `tests/hermes_cli` | 3,446 | 192 | 48 | 主要為**跨測試干擾**（上游 CI 用 per-file subprocess isolation，本地單進程整跑沒有）：干擾大宗檔案獨立重跑全綠——客製 4 檔 web_server 測試 65/65、`test_webhook_cli`+`test_model_validation` 41/41；其餘環境性（`os.chown`、temp 環境無 npm/uv、Nous account 未登入、update-flow 測試隔離洩漏見 §11） |
| **合計** | **13,123** | 408 | 211 | **修完 §3.8 後，無任何可歸因於 merge 的殘餘失敗** |

merge 相關性判定邏輯：本次 merge 的人為決策只落在 6 個衝突檔＋客製檔案；非客製、非衝突區的檔案內容 = 上游原樣，該區失敗只可能是「上游程式碼在 Windows 沙箱」的環境問題。針對客製／衝突區的定向驗證全綠：

- 12 個客製測試檔（sandbox2 乾淨 home、`PYTHONUTF8=1`）：**343 tests 全過**（含 §3.8 兩修正後 8/8、350 Slack 全集）
- 前端全套 vitest：**148/148**（含客製 gateway-multiplex 13）
- 模組 smoke import：cron/gateway/hermes_cli/tools 熱區 10 模組 + `web_server`（FastAPI app 建構、`/api/profiles` 路由掛載、`_profile_to_dict` multiplex 參數）全部通過

已知環境性收集排除：`tests/hermes_cli/test_gateway.py`（import `pty`，Unix-only）、`tests/tools/test_file_read_guards.py`（`/dev/zero` 裝置讀取在 Windows 永久輪詢）、`tests/hermes_cli/test_dashboard_unified_launch.py`（mock 不完整、spawn 真 dashboard 死等——見 §11）。cp950 類失敗以 `PYTHONUTF8=1` 消除（07-24 baseline 同類）。

對照 07-24 baseline（574 tests 全綠參考量級 + 缺 pytest-asyncio/Unix-only/cp950 三類環境性）：本次規模大得多（上游測試套件全面重寫後仍收 13k+ green），環境性失敗類別一致且全數可解釋，**零新增 merge 失敗**。

## 5. Live 切換 runbook（主 session 經使用者核准後執行）

live 事實：gateway 由 Scheduled Task `\Hermes_Gateway` 啟動；live venv = `C:\Users\razer\AppData\Local\hermes\hermes-agent\venv`（editable `hermes_agent-0.19.0`，內含 messaging+web+mcp 套件組）；另有 `\HermesBridgeDaily`、`\HermesWslKeepAlive` 兩個排程不受影響。

```powershell
# 於 C:\Users\razer\AppData\Local\hermes\hermes-agent

# S1 安全 tag（rescue 錨，命名沿用 rescue/pre-remerge-20260724 慣例）
git tag rescue/pre-0191-20260803 970118870
git tag -l "rescue*"   # 驗證

# S2 停 gateway（受控停機窗口開始；注意 gateway 啟動後約 3.5 分鐘才寫狀態檔）
venv\Scripts\hermes.exe gateway stop
# 確認無殘留 gateway 進程後再繼續

# S3 切換 live main 到整合 tip
git status               # 必須乾淨
git reset --hard aa65ff2863786f58ac59c442d36d06f22bb2041b
git log --oneline -3     # 應見 aa65ff286 / 444a3fab2 / d834c5002

# S4 重建 editable 安裝（dist-info 應變 0.19.1；py-modules 新增 hermes_state_* 拆分，必做）
venv\Scripts\python.exe -m pip install -e ".[messaging]"
# live venv 亦承載 dashboard 與 mcp，starlette/mcp pin 有變，同步補齊：
venv\Scripts\python.exe -m pip install "starlette==1.3.1" "python-multipart==0.0.32" "mcp==1.26.0"
dir venv\Lib\site-packages | findstr hermes_agent   # 應見 0.19.1

# S5 前端重建（web/ 有上游+客製變更）
cd web && npm install && npm run build && cd ..

# S6 受控重啟
venv\Scripts\hermes.exe gateway start
# 或 schtasks /Run /TN "Hermes_Gateway"
```

### 驗證清單（S7）

1. `venv\Scripts\hermes.exe --version` → `v0.19.1 (2026.7.30) · local aa65ff28…`
2. `venv\Scripts\hermes.exe gateway doctor --json` → 無異常 artifact（唯讀）
3. **allowlist 負面 fail-closed**：對**不在** per-profile outbound allowlist 的 Slack 頻道送測試訊息 → 必須被拒（fail-closed），不得送出
4. **`--message-key` 冪等**：同 key 對 allowlist 內頻道送兩次 → 第二次 dedup 跳過，Slack 只出現一則
5. gateway 狀態檔出現、多 profile 由 multiplexer 服務（dashboard Profiles 頁 `served_by_multiplexer` 標示正常；耐心等 ~3.5 分鐘初始化）
6. cron 排程送達一輪正常（觀察 `_deliver_result` 無 relay 誤閘）

## 6. Rollback（明示指令，不自動執行）

```powershell
venv\Scripts\hermes.exe gateway stop
git reset --hard rescue/pre-0191-20260803          # 回 970118870
venv\Scripts\python.exe -m pip install -e ".[messaging]"   # 回 0.19.0 dist-info
cd web && npm install && npm run build && cd ..
venv\Scripts\hermes.exe gateway start
venv\Scripts\hermes.exe --version                  # 應回 v0.19.0
```

rescue tag 不刪，保留為歷史錨點（同 `rescue/pre-remerge-20260724`）。

## 7. 私有備份 push（切換驗證通過後）

```powershell
git push origin main                                # 新 main（= aa65ff286）
git push origin integration/v0.19.1-custom          # 整合 branch 本身
git push origin rescue/pre-0191-20260803            # rescue 錨
```

（`origin` = konicatc-techcoding/hermes-agent-private；**不**push upstream。）

## 8. WSL 側 ff-only 後續

WSL 部署複本 `origin` 指向 Windows 本機 repo（07-25 re-graft 後兩側 tip 一致 = `970118870`）。Windows main 前進到 `aa65ff286` 後：

```bash
# WSL 內；先停 hermes 相關 systemd timer/service（沿用 wsl-regraft-plan Phase 2 清單）
git fetch origin
git merge --ff-only origin/main        # 970118870 → aa65ff286 必為 ff
.venv/bin/python -m pip install -e ".[messaging]"
# 重啟服務,驗證 hermes --version = v0.19.1
```

注意 WSL 側「未 push 即失效」弱點（memory:hermes-agent-repo-work）：Windows 側完成 §7 push 後再做 WSL 對齊，順序不可反。

## 9. 需使用者拍板／知悉事項

| # | 事項 | 性質 |
|---|------|------|
| 1 | §3.2：credential 衝突拒絕路徑改採上游「不 disconnect」語意（客製 record 保留） | 知悉即可（上游 bug 修正，客製功能無損） |
| 2 | §3.4：dashboard conflict 呈現由 overlay shim 改為上游 scoped 原生讀法 | 知悉即可（行為等價，資料同源） |
| 3 | §3.5：4 個上游 drain 測試隨上游 prune 刪除 | 知悉即可（實作仍在，客製測試全留） |
| 4 | 上次 `_standalone_send` 取捨：上游結構未變，維持原取捨 | 無需動作 |
| 5 | `nemo-relay` 轉核心依賴（win32 會裝 `>=0.6.0,<0.7`） | 知悉（S4 重裝時自動帶入） |
| 6 | worktree 與 `integration/v0.19.1-custom` 留存不清理，live 切換直接引用 | 依任務指示 |
| 7 | §3.8-2：Slack standalone media 路徑 shadowing bug 修正（live main 現存 bug，本 branch 已修） | 知悉；若近期 live 上「Slack 送附件失敗」有災情，此為根因 |
| 8 | §11：測試洩漏事故——live gateway 推定停機、3 個洩漏測試進程待清、live home 少量檔案被洩漏 dashboard 觸碰 | **需使用者處置**（重啟 live gateway、殺洩漏進程） |

## 10. 風險表

| 風險 | 緩解 |
|------|------|
| live venv 殘留舊 pin（starlette 1.0.1 等）與新版程式碼不合 | S4 明列補裝三個 pin；rollback 走 S6 |
| gateway 啟動慢被誤判失敗 | 已知 ~3.5 分鐘初始化（memory:hermes-gateway-init-slow），驗證清單明示等待 |
| config 遷移地板 v12 | doctor + 首輪 cron 觀察；異常即 rollback |
| WSL 側先動造成兩側漂移 | §8 順序鎖定：Windows push 完才動 WSL |
| 洩漏測試進程佔住 port／干擾切換 | §11 清單先清完再跑 §5 runbook |

## 11. 測試隔離洩漏事故記錄（2026-08-04 沙箱測試期間）

上游測試套件有數個測試 mock 不完整，在本機（非 CI）執行時洩漏了真實副作用。**worktree 與 live repo 的 git 狀態皆未受損**（已驗證：live main 乾淨停在 `970118870`；worktree branch/commits 完整），但 runtime 層有三件事需使用者知悉／處置：

1. **洩漏 dashboard 曾以 live home 執行約 1 小時**：`test_dashboard_unified_launch.py` 的測試 spawn 了真實 `dashboard -p default --port 9119` 子進程——`-p default` 讓它繞過 `HERMES_HOME` 沙箱覆寫、解析到 live home（`C:\Users\razer\AppData\Local\hermes`）。它觸碰了：`web-ui-build-stamp.json`（00:04）、`state.db`（00:05，SQLite 開啟/掃描）、`skills/`、`state/`、`cron/`（mtime 更新）。`config.yaml`／`.env`／launcher scripts 未動（mtime 為舊）。該進程已由本 session 終止。
2. **「Restarting 2 unmapped Windows gateway process(es)」**（01:28-29，`tests/hermes_cli` chunk 的 update-flow 測試收尾洩漏）：真實的 Windows post-update 重啟邏輯被執行，殺掉並重啟了 2 個 `gateway run` 進程。live gateway pid 檔記錄的 **pid 16224 現已不存在**（注意：live `gateway_state.json` 的 updated_at 早在 08-03 08:04 就停更，無法斷定 16224 是被此事故殺掉、還是更早已停）。同批「Refreshed Windows gateway launcher scripts」寫的是沙箱路徑，live 的 `gateway-service\Hermes_Gateway.cmd/.vbs`（mtime Jul 7）與 Scheduled Task 指向皆未變。
3. **3 個洩漏的測試 gateway 進程尚在**（本 session 嘗試清理被權限機制擋下）：PID **13560**（worktree venv）、**25156**、**25836**（venv launcher 顯示為 base python）。它們跑的是 worktree 程式碼＋測試環境 home，未寫 live state（live `gateway_state.json` 停更時間佐證）。

**建議處置順序（使用者核准後由主 session 執行）**：
```powershell
taskkill /PID 13560 /F /T
taskkill /PID 25156 /F /T
taskkill /PID 25836 /F /T
# 確認無殘留 gateway run 進程後，重啟 live gateway：
schtasks /Run /TN "Hermes_Gateway"
# ~3.5 分鐘後驗證 gateway_state.json updated_at 恢復更新
```
（若要先觀察再重啟亦可——live gateway 本就可能自 08-03 08:04 起已停，重啟前後可對照 cron/Slack 通知是否恢復。）

**防重演**：live 切換後若要在本機再跑上游測試，這三個檔案先 `--ignore`：`test_dashboard_unified_launch.py`、`test_cmd_update.py` 系（update-flow 洩漏）、`test_gateway.py`（pty）；或只跑客製測試集。
