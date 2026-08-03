# Web UI — Hermes 更新（受控升級）功能 設計提案（v1）

日期：2026-07-24（**v1.1 更新：2026-07-25**）
狀態：**階段一＝已核准並實作完成（含 2026-07-25 切片 1 補完 live 版本字串）；
階段二＝已拍板延後（見 §9 項 1／項 7），其寫入部分仍是獨立最嚴核准 gate、
核准前零程式碼**
負責規劃：`planning` domain
負責領域（實作階段，若核准）：`engineering`（唯讀預檢探測＋API＋UI、
階段二白名單執行流程＋audit＋測試）；真實 gateway/服務操作依既有工作
慣例由主 session 徵求核准後親自執行（見 §0.3）。

**這顆按鈕的第一鐵律（設計前提，不可協商）**：**絕不呼叫 `hermes update`
或 bootstrap installer 的 update 路徑**（v1.1 用字更正：原寫「自動 update
路徑」——那條路徑本身並不是自動的，見 §1）。理由是 2026-07-24 的血淋淋
事故（§1）——兩者的 diverged fallback 都是 `reset --hard origin/main`，
會把本機客製硬化整批毀掉。本功能包裝的是**已驗證過的受控升級流程**
（§2），不是發明新流程、更不是給那兩條毀滅路徑套個 UI。

依賴文件與事故出處：

- `memory/hermes-agent-repo-work.md`——受控升級慣例（2026-07-17 確立）、
  WSL 側 1b fast-forward 同步先例（2026-07-18）、**2026-07-24 updater
  事故與完整受控修復紀錄**（本提案的直接設計依據）、工作慣例
  （每 phase 核准、真實基礎設施操作由主 session 親自執行）。
- `memory/hermes-gateway-init-slow.md`——gateway 重啟後狀態檔約 3.5 分鐘
  才寫，升級後驗證的「啟動中」窗口不可誤判。
- [webui-service-control-proposal.md](webui-service-control-proposal.md)
  ——WSL 側 `wsl -d Ubuntu systemctl --user` 探測/控制模式、燈號慢啟動
  中間態設計，本提案 WSL target 的服務重啟直接複用其規格。
- [webui-migration-proposal.md](webui-migration-proposal.md) §5.2/§5.4
  ——最小寫入例外的隔離架構與 audit 慣例（本提案是繼 bridge、PTY、
  service-control 之後**風險最高**的寫入功能，gate 最嚴）。

---

## 版本標記

- **v1**（2026-07-24）＝第一個正式版本（草案）。來源：2026-07-24 updater
  事故修復完成後，使用者要一顆「Hermes 更新」按鈕，並明確拍板**先規劃、
  零程式碼**；且明確想用**WSL 側 fast-forward 同步**（現落後、要追到
  Windows 的 `970118870`）當按鈕的首次測試案例——因為它是無衝突、可安全
  全自動的場景。
- **v1.1**（2026-07-25）＝**事實校正版**（切片 0）＋階段一補完（切片 1）。
  來源：engineering 的唯讀事實盤點，查出四件與本提案原文不符的實況：
  1. **觸發源不是「自動」，是 GUI 一鍵**（§1 v1.1 更正框）——這是最重要的一項，
     它改變了防重演的設計目標。
  2. **中和不是結構性保證**，有明確失效條件（§6.1，原提案完全沒寫）。
  3. **首測案例已隨 2026-07-25 re-graft 消失**（§9 項 1）——兩側已 `0 0`。
  4. **停機面是 4 個不是 3 個**，且 masked 的不是 `hermes-bridge.timer`（§4.1）。

  同時把 §3.1／§3.2 的模組名、端點名、未落地項與實作對齊，並補上
  `webui/README.md` 的安全邊界節（§6 項 4 早該做而欠著的）。
  使用者依此拍板：**採切片 0＋切片 1，階段二延後**。

---

## 0. 定位與範圍邊界

**一句話定位**：在 webui 提供「Hermes 更新」功能，分兩階段——階段一是
**唯讀「升級預檢」**（顯示兩側版本/落後/能否 ff/有無 diverge/rescue ref，
零風險，可先做）；階段二是**寫入「受控執行」，僅在 ff-only 判定通過時
提供執行鈕**，包裝已驗證的受控流程，偵測到會 diverge/需 merge 時**拒絕
自動執行、退回人工受控流程，絕不 fallback reset**。

### 0.1 能自動 vs 不能自動的誠實界線（本提案的核心張力）

- **一顆按鈕無法自動解 merge 衝突**。這是硬事實，不是能力不足——
  2026-07-24 的 Windows 側 remerge 有 **6 檔衝突**需人判斷（客製功能
  與上游改進都要活）；把「解衝突」交給任何自動 fallback，結果就是那天
  的災難（`reset --hard` 把衝突「解決」成純上游）。
- **界線劃在 fast-forward**：
  - **ff-only 場景＝可安全全自動**：本地 tip 是遠端的祖先，無分歧、無
    衝突，`git merge --ff-only` 要麼乾淨前進、要麼直接失敗（不會製造
    分歧）。WSL 側現況正是此例（`c12c64f9e` → Windows `970118870`，
    後者是前者的後代，見事故紀錄）——**這才是按鈕能全自動執行的案例，
    也是使用者指定的首測**。
  - **會 diverge/需 merge ＝按鈕拒絕自動執行**：偵測到本地有遠端沒有的
    commit（分歧）→ 按鈕**不提供自動執行**，只顯示「需人工受控 merge」
    ＋指向受控流程（§2 的完整步驟），由 engineering＋主 session 依既有
    慣例手動走。按鈕在這種情況**唯一正確的行為是拒絕並說明**，不是
    「盡力而為」。

### 0.2 分階段（延續本專案唯讀先行慣例）

- **階段一（唯讀升級預檢）**：零風險、可先實作、隨本提案核准即做。
- **階段二（寫入受控執行）**：僅 ff-only 通過才給執行鈕；獨立且**最嚴**
  的寫入 gate（升級是本系統最高風險寫入）。

### 0.3 與既有工作慣例的關係（不繞過人）

memory 既定慣例：真實基礎設施操作（重啟 live gateway、動生產服務）由
主 session 徵求核准後親自執行，不放給 subagent 自動化。本按鈕**不推翻
這條**——階段二即使 ff-only 全自動，也定位為「把主 session 會手動下的
那串確切指令，在明確確認後由受控流程代跑」，且執行前顯示完整指令序列
供確認、每步 audit、可中斷、失敗即停。對 **Windows live gateway**（使用者
的生產服務）尤其保守（見 §4 多目標的差異處理）。

---

## 1. 事故背景（2026-07-24，本提案存在的理由，如實記錄）

- 10:49，**bootstrap installer 的 `mode=Update`** 發現歷史分歧後，把 Windows 側
  main 從客製 merge `c12c64f9e` **硬 `reset --hard` 到純上游**並升
  0.18.2→0.19.0（`df1464ef9`）。

  > **⚠ v1.1 更正（2026-07-25）——本項原文寫「自動 `mode=Update`（非手動觸發）」，
  > 那是錯的。** 觸發源已於 2026-07-24 查明、2026-07-25 獨立複查吻合：
  > **不是排程、不是開機項、不是背景自動路徑，而是 Hermes Desktop（Electron）
  > in-app updater 的 Install 鈕——GUI 一鍵。** 證據鏈：
  >
  > 1. `%LOCALAPPDATA%\hermes\logs\bootstrap-installer.log:2691` —
  >    `2026-07-24T02:49:27Z INFO hermes_bootstrap_lib: Hermes installer starting mode=Update force_setup=false`（UTC 02:49 ＝ 本地 10:49）；
  >    同檔 `:2726` `Fast-forward not possible (history diverged), resetting to match remote...`。
  > 2. `%LOCALAPPDATA%\hermes\logs\desktop.log:4493-4495` —
  >    `[updates] restart: Updating Hermes …` → `[updates] launched updater:
  >    …\hermes-setup.exe --update --branch main; exiting desktop to release venv shim`。
  >    **是桌面 app spawn 了那支 installer。**
  > 3. 那三行字串的唯一產生點是
  >    `hermes-agent/apps/desktop/electron/main.ts:2584` 的 `applyUpdates()`
  >    （`:2641` restart 文案、`:2704` launched updater）；`applyUpdates()`
  >    在 main process 只被 `:10201-10202` 的 `ipcMain.handle('hermes:updates:apply')`
  >    呼叫，renderer 端呼叫者僅三個、**全是使用者手勢**：
  >    `updates-overlay.tsx:61`＋`handleInstall`（Install 鈕）、
  >    `store/updates.ts:249 startActiveUpdate()`←`settings/about-settings.tsx:147` 的
  >    `<Button onClick={…}>`、`command-palette/index.tsx:523`。
  > 4. **背景輪詢不會自動 apply**：`store/updates.ts:648-684 startUpdatePoller()`
  >    只跑 `checkUpdates()`／`checkBackendUpdates()`（30 分鐘 interval＋視窗 focus），
  >    最多彈一個 toast，**從不呼叫 apply**。
  >
  > **這個更正改變了防重演的設計目標**：原本以為要「找出並停掉一個排程」，
  > 實際上要做的是——**承認那顆鈕永遠都在（只要 Desktop 還裝著，它就一鍵可達），
  > 把防線做成「即使被按下去也不會毀」**。詳見 §6。
- 損害：Windows live 行為失去全部客製硬化——**Slack outbound allowlist
  變死設定、send ledger／multiplexer ownership 不生效**；所幸客製 branch
  refs／config 完好、無 stash 損失，WSL 側未受影響（＝異地備份）。
- 修復（路線 B，當日完成，受控流程實測有效）：rescue ref →隔離 worktree
  完成 merge（客製 tip `03bb983e3`×上游 `3910ab28c`，6 檔衝突、40 檔
  auto-merge、沙箱 574 tests 全綠）→ 主 session live 切換（停 gateway →
  `git reset --hard 970118870` → `pip install -e ".[messaging]"` → 受控
  重啟 → live 驗證全過含 allowlist 負面 fail-closed）。整合 tip
  `970118870`；rescue refs `rescue/pre-remerge-20260724`＝`df1464ef9`、
  `rescue/pre-updater-merge-20260724`＝`c12c64f9e`。
- **根因＝內建 updater 與 bootstrap installer 的 update 流程都會毀客製歷史**
  （diverged fallback＝`reset --hard origin/main`；`hermes update`
  在 `hermes_cli/main.py:11086` 同樣）。**破壞是 update flow 的固有設計，
  repo 帶客製就必炸**——`non_interactive_local_changes: stash` 對「已提交的
  diverged 歷史」無效。**這是本按鈕第一鐵律的由來，也是 §6 防重演的對象。**

---

## 2. 按鈕包裝的受控升級流程（非發明，是 2026-07-24 剛驗證過的那套）

以下是**人工受控流程的完整步驟**；階段二只在 **ff-only 子集**上自動化其中
安全的部分，需 merge 的部分維持人工。

1. 停 gateway（受控，不是 kill）。
2. 建 **rescue ref / 安全 tag**（升級前 live tip，rollback 錨）。
3. 從 `origin/main` 開 integration branch。
4. merge 客製 tip →**（有客製時）解衝突**——客製功能與上游改進都要活。
5. 沙箱測試全綠（`HERMES_HOME` 沙箱化，零新增失敗 vs 環境性 baseline）。
6. `git reset --hard <integration tip>`（切 live main）。
7. `pip install -e ".[messaging]"`（重建依賴）。
8. 受控 gateway 重啟（狀態檔約 **3.5 分鐘**，不可誤判為失敗）。
9. live 驗證：`gateway doctor`、**Slack allowlist 負面測試 fail-closed**、
   ledger 冪等、版本字串含 local commit。
10. 防重演確認。

**ff-only 場景的簡化**（階段二自動化的正是這個子集）：步驟 4「解衝突」
不存在（ff 無衝突）、步驟 6 的 `reset --hard` 換成 `git merge --ff-only`
（語意安全、失敗不製造分歧）；其餘步驟（tag、依賴重建、受控重啟、驗證）
照跑。

---

## 3. 階段一——唯讀「升級預檢」

### 3.1 資料來源設計（純唯讀，無任何寫入/fetch 副作用的邊界）

> **實況對齊（v1.1，2026-07-25）**：實作**沒有**放進 `dashboard/data.py`，
> 而是獨立成新模組 **`dashboard/data_update.py::get_update_precheck()`**
> （:554，45 秒 TTL 快取＋全域容錯）。理由是這塊有自己的白名單/守門機制，
> 獨立模組才能被靜態測試與安全檢查逐條鎖定。**以下條列的欄位語意不變。**

對**每個 target**（§4）回報：

- **當前版本**：live 版本字串（含 upstream＋local commit，如
  `v0.19.0 upstream 3910ab28 + local 97011887`）、當前 HEAD sha。
  → **✅ 已落地（2026-07-25 切片 1）**。實測輸出與上面這個範例逐字相同：
  `v0.19.0 upstream 3910ab28 + local 97011887 (+12 carried commits)`。
  **零副作用的取得方式**（實作 `data_update.py::_live_version()`）：
  版本號讀 **HEAD 的 `pyproject.toml` blob**（`git show HEAD:pyproject.toml`，
  凍結字面、零參數化）、`upstream <sha>` 取 `git merge-base <upstream>/main HEAD`
  （`merge-base` 本來就在白名單）、`local <sha>` 與 carried 數沿用既有查詢。
  **刻意不跑 `hermes --version` 或任何 hermes CLI**——那會 spawn process 並可能
  讓 banner 寫 `~/.hermes/.update_check`，即「看狀態」變「動狀態」；
  也不讀 venv 的 `dist-info`（WSL 端會被迫多開一個非 git 的呼叫，破壞
  「subprocess 只有一個位點且只跑 git」這條可靜態驗證的不變式）。
- **落後/領先**：對 `origin/main`（**是否要主動 `git fetch` 是待拍板項 2**
  ——fetch 有網路副作用但不改工作區；預設建議「預檢只讀本地已知
  refs，明確標示『遠端資訊可能過期，按〔重新整理遠端〕才 fetch』」，把
  副作用收在使用者明確動作後）計算 ahead/behind 數。
  （v1.1 更正：原文誤寫「待拍板項 5」，正確是 **§9 待拍板項 2**。）
  → **落地狀況**：ahead/behind ✅ 已落地；**〔重新整理遠端〕fetch 按鈕
  ❌ 尚未實作，且刻意保留**——§9 待拍板項 2 未拍板前不引入任何網路副作用。
  目前 UI 上的「重新整理」鈕只是**重跑唯讀預檢**，不 fetch。
- **ff 判定**：本地 tip 是否為目標 tip 的祖先（能否 ff）——**這是階段二
  是否給執行鈕的唯一開關**。
- **diverge 判定**：本地有無目標沒有的 commit（客製分歧）＋若有，列出
  哪些（sha＋summary，不展開 diff）。
- **rescue ref 現況**：現存 `rescue/*` tag 清單與指向（讓使用者一眼看到
  「上次升級的 rollback 錨還在不在」）。
- **服務狀態**：該 target 的 gateway/服務是否在線（複用 service-control
  提案的燈號探測，避免在服務運行中誤動）。

**唯讀鐵律**：本函式**只跑讀取類 git 指令**（`rev-parse`／`merge-base
--is-ancestor`／`rev-list --count`／`for-each-ref`／`log`）＋唯讀狀態
探測；**絕不**跑 `fetch`（除非使用者按明確按鈕）、`merge`、`reset`、
`checkout`、`pull`、`pip`。以枚舉允許的 git 子指令白名單實作，非白名單
子指令在程式層就拒絕。

### 3.2 曝露與 UI

- 走既有唯讀 API 8799（GET-only）；新增 `GET /api/update-precheck`
  （`dashboard/api.py:198-202`）。
  > **實況對齊（v1.1）**：本節原文寫的端點名是 `GET /api/hermes-update-status`，
  > **實作採用的是 `GET /api/update-precheck`**。日後階段二若要新增端點，
  > 請以實作名為準，不要照本提案的舊名寫。
- UI：獨立「Hermes 更新」view（或設定區塊），把兩側 target 並列成一張
  「一眼看懂」表：版本、落後幾個、能否 ff（綠可/橙需 merge/灰未知）、
  有無 diverge、rescue ref、服務狀態。
  → ✅ 已落地：`webui/src/views/UpdatePrecheck.tsx`，掛載於
  `webui/src/App.tsx`（nav `id: "update"`）。
- **明確不在階段一出現任何執行鈕**（唯讀先行）。
  → ✅ 已落地並被三重鎖定：`webui/tests/update-precheck-render.test.mjs`
  的「零執行入口」渲染斷言、同檔的原始碼靜態斷言（view 內唯一 `onClick`
  只能是 `reload`、不得自訂 `<button>`）、`scripts/webui_security_check.py`
  第 11 項。

**階段一落地總表（v1.1，2026-07-25 盤點）**

| §3 設計項 | 狀態 |
|---|---|
| 資料層（雙基準比較、五態燈、整體燈＋`overall_driver`） | ✅ `dashboard/data_update.py` |
| 唯讀強制（兩層白名單、單一 subprocess 位點、remote 名 regex、WSL 不喚醒） | ✅ 同上＋測試＋安全檢查第 11 項 |
| `GET /api/update-precheck` | ✅ `dashboard/api.py:198-202`（端點名與原文不同，見上） |
| Web UI view（兩端並列、零執行鈕） | ✅ `webui/src/views/UpdatePrecheck.tsx` |
| ff 判定／diverge 清單／rescue ref／服務狀態 | ✅ |
| **live 版本字串** | ✅ **2026-07-25 切片 1 補上**（原 commit `b81f54f` 未含） |
| **〔重新整理遠端〕fetch 按鈕** | ❌ 刻意未做（§9 待拍板項 2 未拍板） |
| `webui/README.md` 安全邊界節（§6 項 4 的承諾） | ✅ **2026-07-25 切片 0 補上**（原 commit 未含） |
| §10.1 `follow` role（WSL 跟隨 Windows 計入燈號） | ❌ 刻意未做，僅 docstring 記載限制（需獨立核准） |

---

## 4. 多目標（target 至少兩端，處理方式不同）

| target | ff 判定對象 | 「切 live」方式 | 服務重啟 | live 驗證重點 |
|---|---|---|---|---|
| **Windows gateway** | 本機 hermes-agent vs 上游/整合 tip | 幾乎總是**需 merge**（Windows 側帶客製 tip）→ 按鈕多半**拒絕自動、退回人工** | gateway restart（生產服務，最保守） | doctor＋allowlist 負面 fail-closed＋ledger＋版本字串 |
| **WSL 服務** | WSL main vs **`origin/main`**（`origin` 即本機 Windows 路徑 remote，免網路免憑證；官方在 `upstream`） | **ff-only**（2026-07-25 re-graft 後兩側樹相同，未來同步**永遠可 ff**） | **不適用**——WSL **無 live gateway**（`ps` 無 gateway process），Telegram bot／worker 與 hermes-agent 零耦合**不需停**；需暫停的是 **4 個觸發源**（3 個 systemd --user timer ＋ Windows 排程 `\HermesBridgeDaily`——**見 §4.1 的 v1.1 更正，原文寫「3 個」不完整**） | 同上，WSL 側（重點仍是 allowlist 負面 fail-closed） |

**WSL 側現況（2026-07-25 re-graft 已執行完成，見
[wsl-regraft-plan.md](wsl-regraft-plan.md) §10）**：

- WSL `main` 已對齊 Windows 整合 tip `970118870`，`git diff --stat origin/main`
  零輸出 → **兩側樹逐 byte 相同**；因此**未來同步永遠是 ff**，正是本按鈕階段二
  設計的目標場景。
- remote 結構**已改**（原本 `origin` = 官方、`windows-side` = 本機路徑）：
  現為 **`origin` = `/mnt/c/Users/razer/AppData/Local/hermes/hermes-agent`**、
  **`upstream` = 官方 NousResearch**。語意是「WSL 跟隨 Windows 整合 tip」，
  順帶讓 `hermes update` 的 diverged fallback `reset --hard origin/main` 退化成
  **no-op**（防重演，§6）。
  ⚠️ 換名時 `branch.main.remote` 會被 rename 一併帶走，必須手動修回 `origin`
  才有這個 fail-safe——踩坑細節見 wsl-regraft-plan §10.2 偏差 1。
- **「服務重啟」欄對 WSL 不適用**：WSL 內沒有任何 hermes gateway process，
  live gateway 只在 Windows 側；停機需求僅止於 timer 面。2026-07-25 實測
  **Telegram bot 停機 0 分鐘**。這也修正了 §9 待拍板項 6 的前提（見該項註記）。

#### 4.1 停機面其實是 4 個，不是 3 個（v1.1 更正，2026-07-25 實測）

上表「只需暫停 3 個 systemd --user timer」**不完整**，且既有理解中
「`hermes-bridge` 的 timer 是 masked」**是錯的**。實測（唯讀）：

```
$ wsl -d Ubuntu --exec bash -lc 'systemctl --user list-timers --all --no-pager'
NEXT                          LEFT   LAST                          PASSED     UNIT                                  ACTIVATES
Sat 2026-07-25 16:19:48 CST   14min  Sat 2026-07-25 15:49:48 CST   15min ago  hermes-rss.timer                      hermes-rss.service
Sun 2026-07-26 08:00:00 CST   15h    Sat 2026-07-25 08:00:02 CST   8h ago     hermes-cron-daily-memory-check.timer  hermes-cron-daily-memory-check.service
Sun 2026-07-26 08:10:00 CST   16h    Sat 2026-07-25 08:10:02 CST   7h ago     hermes-bridge.timer                   hermes-bridge.service

$ wsl -d Ubuntu --exec bash -lc 'systemctl --user list-unit-files --no-pager | grep -i hermes'
hermes-bridge-notifier.timer      masked   enabled     ← masked 的是這三個
hermes-bridge-pipeline.timer      masked   enabled
hermes-bridge-scanner.timer       masked   enabled
hermes-bridge.timer               enabled  enabled     ← 不是 masked，active waiting
hermes-cron-daily-memory-check.timer  enabled  enabled
hermes-rss.timer                  enabled  enabled
hermes-telegram.service           enabled  enabled     ← active running
hermes-worker.service             enabled  enabled     ← active running
```

**正確的停機面 ＝ 4 個觸發源**：

| # | 觸發源 | 說明 |
|---|---|---|
| 1 | `hermes-rss.timer` | 每 30 分鐘 |
| 2 | `hermes-cron-daily-memory-check.timer` | 每日 08:00 |
| 3 | `hermes-bridge.timer` | 每日 08:10（**enabled，非 masked**） |
| 4 | **Windows 排程 `\HermesBridgeDaily`** | 每日 **08:05**，`wsl.exe -d Ubuntu -- bash -lc "systemctl --user start hermes-bridge-{scanner,pipeline,notifier}.service"` |

第 4 項是關鍵：`hermes-bridge-{scanner,pipeline,notifier}.timer` 之所以被 mask，
正是因為改由 **Windows 側排程**以 service 形式直接拉起（避免雙重觸發）。
**它會繞過 WSL timer**——升級期間只暫停 WSL timer 並不能阻止它在 08:05 把
bridge service 拉起來。階段二的停機步驟必須把這個 Windows 排程一併納入
（或把升級時窗排在 08:00–08:15 之外）。

⚠️ 本專案安全鐵律：**排程只查不動**。此處僅記錄事實，實際是否暫停、如何暫停，
屬階段二實作時由主 session 受控執行的決定。

**關鍵不對稱**：Windows 側因帶客製，**結構上就不是 ff 場景**——按鈕對
Windows 的常態行為是「預檢顯示需 merge → 不給自動執行鈕 → 指向人工受控
流程」；WSL 側同步才是按鈕自動執行的正常用例。提案不假裝按鈕能自動升
Windows，那正是事故的根源。

---

## 5. 階段二——寫入「受控執行」（獨立、最嚴 gate）

### 5.1 觸發條件（層層閘門，任一不過就不給執行）

1. 該 target 預檢判定 **ff-only ＝ true**（有 diverge 直接不提供執行鈕）。
2. 使用者明確選定 target＋明確按「執行升級」。
3. 執行前 UI **顯示將跑的確切指令序列**供使用者逐條確認（tag→ff-merge→
   依賴重建→服務重啟→驗證的實際指令），確認後才動。

### 5.2 執行流程（固定模板，白名單，無任意指令）

固定序列，每步 audit、可中斷、**失敗即停不 fallback**：

1. **自動建 rescue ref**（`rescue/pre-update-<target>-<timestamp>`＝當前
   live tip）——先建錨再動任何東西。
2. 停該 target 服務（受控）。
3. `git merge --ff-only <target-tip>`——**唯一允許的前進指令**；一旦
   非 ff（理論上預檢已擋，這裡是二次防線）**立即失敗停止**，絕不改用
   `reset`/`pull`。
4. `pip install -e ".[messaging]"`（重建依賴）。
5. 受控服務重啟（等狀態檔，3.5 分鐘窗口內顯示「升級中/暖機」不誤判）。
6. live 驗證（doctor／allowlist 負面 fail-closed／ledger／版本字串）——
   任一不過 → 標記失敗並**明示 rollback 指令**（`git reset --hard
   <rescue ref>`＋重裝＋重啟），但 **rollback 也不自動跑**（回滾是破壞性
   操作，需使用者明確確認，比照「執行前顯示指令序列」原則）。

### 5.3 安全模式（延續 bridge/service-control，且更嚴）

- **固定操作白名單**：只有上述固定序列的具名步驟；git 子指令限
  `tag`/`merge --ff-only`/`rev-parse` 等枚舉集，**無 `reset`（除
  rollback 且需二次確認）、無 `pull`/`fetch --autostash`、無任意參數**。
- **無任意指令 API**：target 與動作皆枚舉，HTTP 不接受任何指令字串。
- **localhost-only＋audit**：每步一筆 audit（時間、target、步驟、指令、
  結果/exit）到 `logs/`。
- **target ownership**：對 WSL 走具名單元（service-control 白名單）；對
  Windows gateway 走既有 gateway restart 路徑，不自造停法。
- **宿主 process**：建議與 service-control 寫入部分同群組（若那案採
  「併入 bridge 8787」），但升級操作因風險最高，**其枚舉白名單獨立
  分組、獨立測試斷言**，且執行鈕的「顯示指令序列＋逐條確認」是本功能
  專屬的額外閘門（service-control 的 start/stop 不需要，升級需要）。

---

## 6. 防重演（納入本提案；前置調查項已於 2026-07-25 關閉）

事故根源是 **Hermes Desktop in-app updater 的 Install 鈕**（GUI 一鍵，
非本提案這顆按鈕，也非任何自動排程——見 §1 v1.1 更正框）。**那顆鈕移除不了、
也不打算移除**，所以本按鈕做得再安全都不夠：真正該做的是讓「那顆鈕被按下去」
這件事本身無害。故：

1. **前置調查項（原列為階段二的 start blocker 之一）→ ✅ 已完成**
   （查明於 2026-07-24；**2026-07-25 由 engineering 獨立唯讀複查，結論吻合**）。

   **結論（肯定面）**：觸發源＝**Hermes Desktop（Electron）in-app updater 的
   Install 鈕**，證據鏈見 §1 的 v1.1 更正框。

   **結論（否定面——查過哪些地方，以支持「不存在自動觸發源」）**：

   | 查過的地方 | 結果 |
   |---|---|
   | 全機 Windows Scheduled Tasks（`Get-ScheduledTask`，名稱/路徑比對 hermes\|update\|bootstrap\|nous） | Hermes 相關僅 3 個，**無一跑 update**：`\HermesBridgeDaily`（`wsl -d Ubuntu -- bash -lc "systemctl --user start hermes-bridge-{scanner,pipeline,notifier}.service"`，每日 08:05）、`\HermesWslKeepAlive`（keepalive vbs，登入）、`\Hermes_Gateway`（登入＋30 秒延遲；其 `Hermes_Gateway.vbs` 全文只 `pythonw.exe -m hermes_cli.main gateway run`） |
   | `HKCU:`／`HKLM:` 的 `…\CurrentVersion\Run` 與 `RunOnce` | 無任何 hermes 項（`RunOnce` 兩者皆空） |
   | 使用者 Startup 資料夾／All Users Startup 資料夾 | 只有 `Ollama.lnk`／`Volt Driver Control Panel Autostart.lnk`，無 hermes |
   | `%LOCALAPPDATA%\hermes\config.yaml` | update 相關鍵只有 `updates.pre_update_backup: true`（:169-170），**無任何 auto-update 開關** |
   | Hermes 內建 cron `%LOCALAPPDATA%\hermes\cron\jobs.json` | 5 個 job（garmin×2、aichain、github-models、alpha），**無 update job** |
   | Desktop 背景輪詢 `store/updates.ts:648-684` | 只 check＋toast，**從不 apply** |
   | 聊天 `/update` slash command（`gateway/slash_commands.py:4912`、`gateway/run.py:16730`） | 需使用者在 Slack/Telegram 主動下指令；`.update_pending.json` 目前不存在 |
   | 本專案 repo（`hermes/`、`scripts/` 的 ps1/vbs/cmd/sh/py/yaml） | 無任何 `hermes update`／`hermes-setup`／`--update` 呼叫（唯一命中是 `scripts/webui_security_check.py` 的**禁字清單**） |

   **唯一無法坐實的殘留（誠實記錄）**：**「那顆鈕是誰、在什麼情境下按的」在唯讀
   調查下不可得**——桌面 app 只記結果（`desktop.log`）不記操作者/手勢來源。
   但這**不影響防線設計**：不論誰按，防線的要求都是「按下去也不會毀」。

2. **停用/中和** → **採「中和」，未「停用」**（兩者差別務必分清）：
   - **未停用**：Hermes Desktop 仍安裝著（`desktop-build-stamp.json` builtAt
     2026-07-24T02:57Z、`hermes-setup.exe` 仍在 `%LOCALAPPDATA%\hermes\`），
     **那顆 Install 鈕仍然存在、仍然一鍵可達**。刻意不移除 Desktop（那會犧牲
     日常可用性），而是讓它按下去也無害。
   - **已中和（2026-07-24 firebreak，2026-07-25 複查有效）**：
     `origin` 改指使用者私有備份 repo、官方轉為 `upstream`，使
     `reset --hard origin/main` 退化成 **no-op**；`updates.pre_update_backup: true`。
     實測：Windows `main == origin/main`（`rev-list --count --left-right
     origin/main...HEAD` ＝ `0 0`）、`branch.main.remote` ＝ `origin`；
     WSL 同構（`origin` ＝ `/mnt/c/.../hermes/hermes-agent`、亦 `0 0`、工作樹乾淨）。
     rescue refs 本地＋異地（`origin/rescue/pre-updater-merge-20260724`）皆在。
3. **偵測型防線（本按鈕順帶提供）→ ✅ 已落地上線**（階段一，commit `b81f54f`）：
   預檢頁顯示 rescue ref 現況與「live tip 是否＝已知客製整合 tip」，一旦被外力
   reset 成純上游，預檢立刻紅燈可見——把「隔天才發現」變「一眼看到」。
   實作位置：`dashboard/data_update.py:_classify_target()` 的客製遺失偵測
   （以 **upstream 組**衡量 `ahead == 0` → 紅）與 rescue ref 遺失 → 紅。
4. **文件**：`hermes-agent-repo-work` memory 已記事故與慣例；
   `webui/README.md` 已補「Hermes 更新頁——唯讀升級預檢的安全邊界」節
   （2026-07-25 切片 0），載明「本頁絕不呼叫 `hermes update`／bootstrap
   installer 的任何 update 模式／Desktop Install 鈕所走的那條路徑」，
   並完整列出 git 子指令白名單、單一 spawn 位點、WSL 不喚醒、零 fetch、
   版本欄零副作用與偵測型防線。

### 6.1 殘留風險：中和**不是結構性保證**（v1.1 新增，原提案完全沒寫）

`reset --hard origin/main` 是 no-op 的**前提是 `main == origin/main`**。因此：

> **一旦本機有客製 commit 尚未 push 到私有備份（`main` 領先 `origin/main`），
> 那顆 Install 鈕再被按下去，就會把那些未推送的 commit 吃掉。**

也就是說，**防重演的有效性繫於「每次客製後都要 push」這條人為紀律**，
不是結構性保證。這是目前這套 firebreak 的真實邊界，必須寫下來而不是假裝沒有。

**已有的緩解（不需額外開發，階段一就在跑）**：預檢的 **backup 組**在
`ahead > 0` 時會亮**橙**並明寫「本機領先備份 N 個 commit（尚未推送——防重演
基準未涵蓋這些客製）」——見 `dashboard/data_update.py:_classify_comparison()`
的 backup 分支。**這就是上面第 3 項偵測型防線對本殘留風險的具體落點**：
橙燈出現 ＝ 「現在按那顆鈕會掉東西」的提前警告。

**未來若要把它升級成結構性保證**（超出本提案範圍，需獨立評估）：可考慮
push hook／定期自動 push 客製 tip 到私有備份，或在 Desktop 端封掉該入口。

---

## 7. 風險總表

| 風險 | 影響 | 緩解 |
|---|---|---|
| 按鈕淪為 `hermes update` 的 UI 皮 | 重演 2026-07-24 硬 reset 毀客製 | 第一鐵律：絕不呼叫 update/bootstrap 自動路徑；只跑 §2 受控序列；`merge --ff-only` 是唯一前進指令、非 ff 即停 |
| 對 diverge 場景「盡力自動」 | 自動解衝突＝把衝突解成純上游 | 預檢 ff 判定是執行鈕唯一開關；diverge → 拒絕自動、退人工；§5.2 二次防線再擋一次 |
| 預檢誤跑 fetch/寫入 | 「看狀態」變「動狀態」 | §3.1 唯讀鐵律：git 子指令白名單、fetch 收在明確按鈕後；行為測試驗證無寫入副作用 |
| Windows live gateway 被自動流程動到 | 生產服務中斷 | Windows 常態＝需 merge＝按鈕拒絕自動；即使未來某次 Windows 可 ff，仍走「顯示指令序列＋逐條確認＋主 session 慣例」最保守路徑 |
| 升級後 3.5 分鐘暖機被判失敗 | 誤觸 rollback、越弄越糟 | 沿用 gateway 慢啟動教訓：驗證前給足暖機窗口、UI 顯「升級中」；rollback 一律需二次確認不自動 |
| rollback 也自動跑 | 破壞性連鎖 | rollback 明示指令但**不自動執行**，需使用者確認（與執行鈕同閘門） |
| update 觸發源未除（＝Desktop Install 鈕，**已查明、未停用、採中和**） | 按鈕再安全也會被外力 reset | §6 前置調查**已完成**＋firebreak 中和（`reset --hard origin/main` 成 no-op）＋預檢偵測型防線**已上線** |
| **未推送的客製 commit 被那顆鈕吃掉**（中和的失效條件，v1.1 新增） | 未 push 的客製整批消失——中和只在 `main == origin/main` 時成立 | §6.1：預檢 backup 組於 `ahead > 0` 亮橙並明寫「尚未推送——防重演基準未涵蓋」；紀律面則是「每次客製後就 push 到私有備份」 |
| 升級白名單靜默膨脹 | 最高風險寫入面擴大 | 枚舉白名單獨立分組＋測試斷言完整清單；任何擴充回本提案重新核准 |

---

## 8. 明確不做清單（按鈕一律不做）

- **不呼叫 `hermes update`**（`hermes_cli/main.py:11086`）或 bootstrap
  installer 的任何 update 模式（`hermes-setup.exe --update`）——第一鐵律，
  無例外。**也不呼叫 Hermes Desktop 的 Install 鈕所走的那條路徑**
  （`apps/desktop/electron/main.ts:2584 applyUpdates()`）——那正是 §1 事故的
  實際入口。
- **不做 `git reset --hard origin/main`／`pull`／`fetch --autostash`／
  任何會丟棄本地 commit 的操作**作為前進手段（rollback 用的 `reset
  --hard <rescue ref>` 是唯一 reset，且需二次確認、指向 rescue 不指向
  origin）。
- **不對 diverge/需 merge 的 target 提供自動執行**——只顯示狀態＋指向
  人工受控流程，不「盡力而為」。
- **不自動解 merge 衝突**（按鈕做不到，也不假裝做得到）。
- **不自動跑 rollback**（破壞性，需確認）。
- **不在階段一出現任何寫入鈕**（唯讀先行）。
- **不接受任意 git 指令/參數/target 名**（全枚舉白名單）。
- **不繞過主 session 對生產 gateway 的既有操作慣例**。
- **不對外曝露**（localhost-only）。

---

## 9. 待拍板項清單（使用者需回答的最小問題集）

1. **首發範圍**：採建議「先做階段一（唯讀預檢，含兩側 target 並列表）
   ＋階段二僅 WSL ff-only 執行（使用者指定首測）」，Windows 側階段二
   維持「預檢顯示需 merge、不給自動鈕」？
   → **✅ 已拍板（2026-07-25）：階段一做（已完成＋切片 1 補完）；
   階段二延後**。
   > **⚠ 硬事實：原定的「首測案例」已經不存在了。** 本提案的版本標記與本項
   > 都以「WSL 落後、ff 追到 Windows `970118870`」為使用者指定的首測場景；
   > 但 **2026-07-25 re-graft 之後兩側已逐 byte 相同**——實測
   > `wsl … git rev-list --count --left-right origin/main...HEAD` ＝ `0 0`、
   > 工作樹乾淨，**沒有任何東西可以 fast-forward**。
   >
   > 要重新產生首測案例，得**先讓 Windows 側受控 merge 官方一次**
   > （目前對 `upstream/main` 落後 295 / 領先 12），WSL 才會出現可 ff 的落差。
   > 而那是**獨立的高風險作業**（§2 的完整人工受控流程、需解衝突），
   > **不是這顆按鈕的附屬品**，不應該為了「讓按鈕有東西可測」而去觸發它。
   >
   > 使用者據此拍板：**階段二延後**，現在只做切片 0（文件校正）＋
   > 切片 1（階段一補完 live 版本字串）——因為在沒有可端到端驗證的案例下
   > 先做階段二骨架是空轉。
2. **預檢的遠端資訊**：採建議「預檢只讀本地已知 refs、標示可能過期、
   〔重新整理遠端〕按鈕才 fetch」，或允許預檢自動 fetch（有網路副作用
   但不改工作區）？
3. **階段二宿主**：與 service-control 寫入部分同群組（併入 bridge 8787、
   升級操作獨立分組獨立測試，建議），或獨立 server？
   > **⚠ v1.1 加註：這個推薦選項掛在一個尚未存在的東西上。**
   > 1. bridge 8787 **確實已存在且成熟**（`webui/scripts/bridge.mjs`，
   >    凍結 `FIXED_COMMAND`:28-31、PID ownership:111-117/166-171、
   >    audit:58-67 落 `logs/webui_bridge_audit.log`、CORS:41/203/214、
   >    bind 寫死 127.0.0.1:27），基礎設施可直接沿用。
   > 2. **但它目前的白名單只有四種 `hermes dashboard` 操作**，沒有任何
   >    systemd／git／升級操作。
   > 3. 而 [webui-service-control-proposal.md](webui-service-control-proposal.md)
   >    的狀態仍是「**v1 草案——待使用者核准（寫入部分為獨立核准 gate，
   >    核准前零程式碼）**」：**唯讀燈號已實作**（`dashboard/data_resident.py`
   >    ＋`GET /api/resident-status`），**寫入部分零程式碼**。
   >    ⇒「與 service-control 寫入部分同群組」目前是**串聯依賴**：要嘛先核准
   >    該案寫入部分，要嘛階段二改走獨立宿主，要嘛階段二自己當 8787
   >    白名單第二群組的**第一個**使用者（等於順帶替 service-control 開路）。
   > 4. **白名單範圍直接衝突，必須合併拍板**：service-control 提案
   >    §2.4／§5 待拍板項 2 建議「單元白名單**不含 timer**」
   >    （理由：timer 的停止語意與 `Persistent=true` 補跑糾纏）；
   >    但階段二需要的正是「暫停 timer → 升級 → 恢復 timer」（§4.1，4 個觸發源）。
   >    兩案的白名單範圍不能各自決定。
4. **rescue ref 保留策略**：採建議「每次升級自動建、保留最近 N 個、
   預檢頁顯示」，N＝？
5. **（start blocker，順序確認）**階段二實作前完成 §6 前置調查（installer
   update 觸發源）並停用之——確認此依賴順序（防重演先於給按鈕）。
   → **✅ 本 blocker 已解除（2026-07-25）**。解除依據三項：
   1. **調查完成**：觸發源＝Hermes Desktop in-app updater 的 Install 鈕
      （GUI 一鍵），**非排程/開機項/背景自動路徑**——證據鏈與否定範圍見
      §1 v1.1 更正框與 §6 第 1 項。
   2. **中和已落地並複查有效**：`origin` 指私有備份、`main == origin/main`
      （實測 `0 0`）⇒ `reset --hard origin/main` 為 no-op；
      `updates.pre_update_backup: true`；rescue refs 本地＋異地皆在。
   3. **偵測型防線已上線**：階段一預檢會對「客製遺失」「rescue 遺失」
      「本機領先備份（未推送）」亮燈（§6 第 3 項、§6.1）。

   **⚠ 但要誠實說清楚「停用」與「中和」不是同一件事**：
   本項原文要求的是「**停用**觸發源」，實際採取的是「**中和**」——
   **Hermes Desktop 與那顆 Install 鈕都還在、仍然一鍵可達，並未停用**
   （刻意不移除，那會犧牲日常可用性）。因此**殘留風險依然存在**：
   一旦客製 commit 未 push 到私有備份，那顆鈕按下去仍會吃掉它們（§6.1）。
   **拍板時請以「中和 + 偵測」而非「已停用」理解此 blocker 的解除。**
6. **WSL 服務重啟窗口**：WSL telegram bot 在線，ff-only 升級要停機——
   採建議「執行前明示將停機、由使用者選時機觸發」？
   → **前提已被 2026-07-25 re-graft 推翻（本項可簡化）**：實測證實
   Telegram bot／worker 跑在 ClaudeCodeOSWin 自己的 `.venv`，不 import
   hermes-agent、不呼叫 `hermes` CLI，**升級不需要停它**（實際停機 0 分鐘）。
   WSL 端真正需要的只是「暫停觸發源 → 升級 → 恢復」。
   （v1.1 再更正：觸發源是 **4 個**不是 3 個，見 §4.1。）
7. **階段二整體 gate**：階段一隨本提案核准即做；階段二（WSL ff-only
   執行）是否現在核准，或先用階段一預檢一段時間、觸發源停用後再開？
   → **✅ 已拍板（2026-07-25）：階段二延後**，先用階段一預檢一段時間。
   主要理由不是安全顧慮（blocker 已解除，見項 5），而是**沒有可端到端驗證
   的案例**（見項 1 的硬事實）。本次只執行：
   - **切片 0**：文件校正（§1／§3／§4.1／§6／§9 各項＋`webui/README.md`
     安全邊界節）——即本 v1.1。
   - **切片 1**：階段一補完 live 版本字串（§3.1 第一項）。

---

## 10. 後續改進待辦（2026-07-25 re-graft 後新增）

### 10.1 燈號盲點：WSL 最該被監控的那一條，目前不計入整體燈

**現況（已知限制，非 bug）**：2026-07-25 re-graft 把 WSL 的 `origin` 指向
**本機 Windows 路徑**（`/mnt/c/.../hermes/hermes-agent`）。而
`dashboard/data_update.py::_role_for_url()` **依 URL 判角色**，本機路徑不含
`nousresearch/hermes-agent` 也不含 `hermes-agent-private`，因此被判為
**`peer`**，其 `counts_toward_overall` 為 `false`。

後果：**WSL target 的整體燈完全由 `upstream` 組（官方 NousResearch）驅動**，
而「**WSL 有沒有跟上 Windows 整合 tip**」這條**不計入燈號**——偏偏那才是 WSL
最該被監控的一條（WSL 的正確語意就是「跟隨 Windows」，對官方落後多少反而
是預期中的常態，兩側都落後同樣數量）。

> **✅ 已落地（2026-08-03，只做偵測層/唯讀）**：新增 `follow` role，判準是
> `data_update.py::_is_windows_repo_url()`——**路徑正規化後與凍結的
> `WINDOWS_REPO_PATH` 逐字相等**（純字串比對，零 I/O、零新增指令面），
> 而非「只要是本機路徑」。誤判邊界：別名路徑（自訂 automount 根、
> symlink/junction、8.3 短檔名、UNC）認不出 → 退回 `peer`＝修正前行為
> （偽陰性，安全側）。燈號語意與 `upstream` 組相反：落後＝藍（該同步了）／
> 領先或分歧＝橙（異常）。同嚴重度時 `DRIVER_PRIORITY` 讓 `follow` 優先被
> 標為 `overall_driver`。**本次不含任何執行層**：無寫入路徑、無執行鈕、
> 無 bridge 白名單項目；階段二仍延後。詳見 `data_update.py` 模組 docstring。
>
> **追加拍板（2026-08-03，選項 b）**：follow 存在的 target 上，`upstream`
> 組**降為資訊性**（`counts_toward_overall=false`，照常顯示、UI 標
> 「僅供參考」），整體燈由 `follow` 組**獨力驅動**——WSL 卡片即 follow
> 三態：已跟上＝綠／落後＝藍（該同步了）／領先或分歧＝橙（異常）。
> 理由：WSL 的 `upstream` 組因設計使然恆為橙（帶客製 diverged 是常態），
> 若計入會讓整體燈「橙飽和」，follow 的訊號永遠顯不出顏色差異。實作為
> `_apply_follow_demotion()`——規則跟著 remote 拓撲走（「有 follow 組」⇒
> 該端是跟隨者），**不硬編 target id**；拓撲若變（WSL 改指雲端）upstream
> 自動回復計入。`backup` 不受降級影響（防重演基準任何 target 都計入）。
> 無 follow 的 target（Windows）行為完全不變。

**待辦（需獨立評估與核准，不在本次改動範圍）**：考慮新增一種 role
（暫名 `follow` / `peer-authoritative`），讓「**本機路徑 remote 且指向
Windows hermes-agent repo**」能被辨識並**計入 WSL target 的整體燈**：

- 判定不能只靠「是本機路徑」——要能確認該路徑就是 Windows 側 hermes-agent
  repo（否則任何本機 remote 都會被誤升級成權威基準）。
- 語意應是「落後 = 該同步了（可 ff，藍）／領先或分歧 = 異常（橙，因為 WSL
  理論上不該有 Windows 沒有的東西）」，與 `upstream` 組的語意不同。
- 需同步更新 `data_update.py` 的 `ROLE_LABEL`、`_build_comparison()` 的
  `counts_toward_overall`、`_classify_target()` 的燈號合成，以及該模組
  docstring 的角色表與燈號表。
- ~~**在此待辦落地前，判定邏輯維持不動**——只在 docstring 記載此限制，避免
  在沒有完整評估的情況下改動燈號語意。~~（已由上方 2026-08-03 落地取代；
  docstring 的「已知限制」段已同步改寫成 `follow` role 的判準與誤判邊界。）
