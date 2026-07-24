# Web UI — Hermes 更新（受控升級）功能 設計提案（v1）

日期：2026-07-24　狀態：**v1 草案——待使用者核准（階段二寫入部分為獨立
最嚴核准 gate；核准前零程式碼）**
負責規劃：`planning` domain
負責領域（實作階段，若核准）：`engineering`（唯讀預檢探測＋API＋UI、
階段二白名單執行流程＋audit＋測試）；真實 gateway/服務操作依既有工作
慣例由主 session 徵求核准後親自執行（見 §0.3）。

**這顆按鈕的第一鐵律（設計前提，不可協商）**：**絕不呼叫 `hermes update`
或 bootstrap installer 的自動 update 路徑**。理由是 2026-07-24 的血淋淋
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

- 10:49，**bootstrap installer 的自動 `mode=Update`**（非手動觸發）發現
  歷史分歧後，把 Windows 側 main 從客製 merge `c12c64f9e` **硬
  `reset --hard` 到純上游**並升 0.18.2→0.19.0（`df1464ef9`）。
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
- **根因＝內建 updater 與 bootstrap installer 的自動 update 都會毀客製
  歷史**（diverged fallback＝`reset --hard origin/main`；`hermes update`
  在 `hermes_cli/main.py:11086` 同樣）。**這是本按鈕第一鐵律的由來，也是
  §5 防重演的對象。**

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

新增 `dashboard/data.py::get_hermes_update_status() -> dict`，對**每個
target**（§4）回報：

- **當前版本**：live 版本字串（含 upstream＋local commit，如
  `v0.19.0 upstream 3910ab28 + local 97011887`）、當前 HEAD sha。
- **落後/領先**：對 `origin/main`（**是否要主動 `git fetch` 是待拍板項 5**
  ——fetch 有網路副作用但不改工作區；預設建議「預檢只讀本地已知
  refs，明確標示『遠端資訊可能過期，按〔重新整理遠端〕才 fetch』」，把
  副作用收在使用者明確動作後）計算 ahead/behind 數。
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

- 走既有唯讀 API 8799（GET-only）；新增 `GET /api/hermes-update-status`。
- UI：獨立「Hermes 更新」view（或設定區塊），把兩側 target 並列成一張
  「一眼看懂」表：版本、落後幾個、能否 ff（綠可/橙需 merge/灰未知）、
  有無 diverge、rescue ref、服務狀態。
- **明確不在階段一出現任何執行鈕**（唯讀先行）。

---

## 4. 多目標（target 至少兩端，處理方式不同）

| target | ff 判定對象 | 「切 live」方式 | 服務重啟 | live 驗證重點 |
|---|---|---|---|---|
| **Windows gateway** | 本機 hermes-agent vs 上游/整合 tip | 幾乎總是**需 merge**（Windows 側帶客製 tip）→ 按鈕多半**拒絕自動、退回人工** | gateway restart（生產服務，最保守） | doctor＋allowlist 負面 fail-closed＋ledger＋版本字串 |
| **WSL 服務** | WSL main vs Windows main（本機路徑 remote，免網路） | **ff-only**（WSL 落後、是 Windows 的祖先）→ **按鈕可安全全自動**（首測） | `wsl -d Ubuntu systemctl --user restart <hermes 單元>`（複用 service-control 規格） | 同上，WSL 側 |

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

## 6. 防重演（納入本提案，含前置調查項）

事故根源是 installer **自動** update 觸發，不是這顆按鈕——所以本按鈕
做得再安全，只要那條自動路徑還在，就可能再被它硬 reset 一次。故：

1. **前置調查項（列為階段二的 start blocker 之一）**：查明 2026-07-24
   10:49 bootstrap installer `mode=Update` 的**自動觸發源**（Scheduled
   Task？某個 wrapper 在啟動時自動跑 installer？）——memory 明列「觸發源
   待查」。查明前不宜宣稱已防重演。
2. **停用/中和自動 update 觸發源**：觸發源查明後評估停用（例如移除/停用
   對應 Scheduled Task，或在 installer 呼叫點加 guard）——具體手段待調查
   結果，屬 engineering＋主 session 受控執行。
3. **偵測型防線（本按鈕可順帶提供）**：預檢頁顯示 rescue ref 現況與
   「live tip 是否＝已知客製整合 tip」，一旦被外力 reset 成純上游，預檢
   會立刻紅燈可見——把「隔天才發現」變「一眼看到」（呼應排程漂移旗標的
   同款價值）。
4. **文件**：`hermes-agent-repo-work` memory 已記事故與慣例；本功能上線
   後於 `webui/README.md` 安全邊界節載明「本按鈕絕不呼叫 hermes
   update/bootstrap 自動路徑」。

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
| 自動 update 觸發源未除 | 按鈕再安全也會被外力 reset | §6 前置調查（start blocker）＋停用觸發源＋預檢偵測型防線 |
| 升級白名單靜默膨脹 | 最高風險寫入面擴大 | 枚舉白名單獨立分組＋測試斷言完整清單；任何擴充回本提案重新核准 |

---

## 8. 明確不做清單（按鈕一律不做）

- **不呼叫 `hermes update`**（`hermes_cli/main.py:11086`）或 bootstrap
  installer 的任何自動 update 模式——第一鐵律，無例外。
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
2. **預檢的遠端資訊**：採建議「預檢只讀本地已知 refs、標示可能過期、
   〔重新整理遠端〕按鈕才 fetch」，或允許預檢自動 fetch（有網路副作用
   但不改工作區）？
3. **階段二宿主**：與 service-control 寫入部分同群組（併入 bridge 8787、
   升級操作獨立分組獨立測試，建議），或獨立 server？
4. **rescue ref 保留策略**：採建議「每次升級自動建、保留最近 N 個、
   預檢頁顯示」，N＝？
5. **（start blocker，順序確認）**階段二實作前完成 §6 前置調查（installer
   自動 update 觸發源）並停用之——確認此依賴順序（防重演先於給按鈕）。
6. **WSL 服務重啟窗口**：WSL telegram bot 在線，ff-only 升級要停機——
   採建議「執行前明示將停機、由使用者選時機觸發」？
7. **階段二整體 gate**：階段一隨本提案核准即做；階段二（WSL ff-only
   執行）是否現在核准，或先用階段一預檢一段時間、觸發源停用後再開？
