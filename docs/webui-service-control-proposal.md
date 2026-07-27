# Web UI — 背景常駐狀態燈號＋服務控制鍵 設計提案（v1.1）

日期：2026-07-24（v1）／2026-07-27（v1.1）　狀態：**v1.1——唯讀部分已交付
（commit 3871129），寫入部分已於 2026-07-27 經使用者核准並實作**
負責規劃：`planning` domain
負責領域（實作階段，若核准）：`engineering`（唯讀探測函式＋API endpoint＋
UI 燈號、寫入側白名單操作＋audit＋測試）；`automation` 於本提案角色為零
（常駐機制本身是並行案，見下方依賴聲明）。

**依賴聲明（重要）**：`automation` 正在並行實作「WSL Ubuntu + hermes
`systemd --user` 服務開機常駐化」（Task Scheduler 喚醒＋linger）。本提案
以「常駐機制已存在」為前提設計——**燈號（唯讀部分）在該案完成前即可運作**
（顯示現況本來就是它的用途），但「常駐」二字的語意判準（哪些單元應該在
線＝綠燈的定義）與 **stop 鍵和自動重啟策略的互動**（§2.4）必須以該案
定案的單元清單與 `Restart=`／linger 行為為準——列為寫入部分的 start
blocker（§5 第 5 項）。
**（v1.1 更新）此 start blocker 已解除**——查證結論見版本標記與 §2.4。

依賴文件：

- [webui-migration-proposal.md](webui-migration-proposal.md) v2（已完工）——
  §3 三鐵律技術強制、§5.2 寫入側隔離架構、§5.4 bridge 最小寫入例外
  （本提案寫入部分的直接先例與對照組）。
- `dashboard/data.py`（`get_systemd_status()` 既有模式——`systemctl --user
  list-units` 呼叫、容錯慣例「環境不支援回傳空 dict 不噴錯」）、
  `dashboard/api.py`（唯讀 API 8799，GET-only）。
- `memory/hermes-gateway-init-slow.md`（gateway 啟動約 3.5 分鐘才寫狀態檔
  ——燈號「啟動中」中間態的直接依據）。
- `hermes/systemd/`（單元清單現況：常駐 service `hermes-worker`／
  `hermes-telegram`；timer 驅動 `hermes-rss`／`hermes-cron-daily-memory-check`
  ／`hermes-bridge-scanner`／`hermes-bridge`／`hermes-bridge-pipeline`／
  `hermes-bridge-notifier`）。

---

## 版本標記

- **v1**（2026-07-24）＝第一個正式版本（草案）。來源：使用者原話——
  「在webui的上方加一個是否背景常駐的文字和燈號，也許也加一個重啟和
  關閉鍵」。使用者已明確拍板**先規劃就好，不實作**。
- **v1.1**（2026-07-27）＝寫入部分拍板與實作紀錄。使用者核准四項決策：
  1. **寫入部分整體 gate：核准實作**（§5 項 6）。
  2. **宿主：併入既有 bridge 8787**（§2.3 選項 a）——以「白名單第二群組」
     擴充，兩群組白名單以獨立常數分列，測試斷言枚舉完整清單，防群組
     互相滲透。
  3. **單元白名單：僅 `hermes-worker.service` 與 `hermes-telegram.service`**，
     不含 timer（§5 項 2 採建議）。
  4. **關閉鍵層級：僅服務層級**，不做 `wsl --terminate`（§5 項 4 採建議）。

  另兩項現況：§5 項 3（輪詢頻率）已隨 commit 3871129 的唯讀實作定案
  （前端 30 秒＋API 端快取；§1 唯讀部分視為已由 3871129 交付，端點名為
  `/api/resident-status` 而非本提案草擬的 `/api/service-status`）；
  §2.4 start blocker 已解除——查證結論：喚醒機制是 Task Scheduler task
  `HermesWslKeepAlive`（`hermes/windows/hermes-wsl-keepalive.vbs`，
  `wsl -d Ubuntu --exec sleep infinity`，只保 distro 活著），**不會把被
  stop 的服務拉回來**；systemd `Restart=always` 對明確 `systemctl stop`
  不觸發。stop 的真實語意因此定案（見 §2.4），並已寫進 UI 按鈕旁說明。
  實作落點：bridge `webui/scripts/bridge.mjs`（第二群組常數＋route＋
  audit）、UI `webui/src/ServiceControl.tsx`、測試
  `webui/tests/service-control.test.mjs`、安全檢查
  `scripts/webui_security_check.py` 第 12 項。

---

## 0. 定位與範圍邊界

**一句話定位**：在 webui 頂部（header 區）新增「背景常駐狀態」文字＋
燈號（**純唯讀**，經唯讀 API 8799 曝露），並評估新增「重啟／關閉」服務
控制鍵（**寫入**，延續 bridge「最小寫入例外」模式但誠實處理 ownership
模型差異，獨立核准 gate）。

### 0.1 兩部分的 gate 分離（本提案的核心結構）

- **燈號（唯讀）**：不新增任何寫入面——探測是 `wsl` 唯讀查詢、曝露走
  既有唯讀 API（GET-only／import guard 不變）。**核准本提案即可實作**，
  不需要額外 gate。
- **重啟/關閉鍵（寫入）**：是繼 bridge、PTY 之後的**第三個寫入例外候選**，
  且對象性質與前兩者都不同（§2.1）——**獨立核准 gate**，且有一項 start
  blocker（依賴 automation 常駐案定案，§5 第 5 項）。核准本提案 ≠ 核准
  寫入部分。

### 0.2 明確不做（不論是否核准，一律不做）

- **任意單元名／任意參數**：單元清單與操作動詞全部寫死（§2.2），HTTP
  介面不接受任何單元名或參數字串——沿用 bridge「不得提供任意 shell
  command API」的既有規格精神。
- **`enable`／`disable`／`mask`**：改變「開機是否自動啟動」屬常駐機制
  設定，歸 automation 常駐案管，本功能不碰（避免兩案互相改對方的
  狀態）。
- **`daemon-reload`／修改 unit 檔／journalctl 之外的任何管理操作**：
  不做。
- **關閉整個 Ubuntu distro（`wsl --terminate`）**：v1 預設不做（見
  待拍板項 4——影響面超出 hermes 服務，包含使用者可能在 WSL 跑的
  其他東西，預設只到服務層級）。
- **對外曝露**：一律 localhost-only（沿用遷移案 §0.4）。
- **自動修復**：燈號紅了不自動重啟——顯示與操作分離，任何重啟都是
  使用者明確按鍵（比照排程健康表「只偵測不修復」的既有原則）。

---

## 1. 唯讀部分——背景常駐狀態燈號

> **（v1.1 標記）本節已由 commit 3871129 交付**：資料層
> `dashboard/data_resident.py`、endpoint `GET /api/resident-status`
> （非本提案草擬的 `/api/service-status`）、UI `webui/src/ResidentStatus.tsx`
> （sidebar 燈號＋tooltip 明細）。輪詢頻率照 §1.3 建議值定案
> （前端 30 秒）。以下保留原規劃文字作為設計依據。

### 1.1 資料來源設計

新增 `dashboard/data.py::get_background_service_status() -> dict`（唯讀，
比照既有容錯慣例），分**三層**探測，由粗到細：

1. **WSL distro 層**：`wsl --list --running`（或 `wsl -d Ubuntu -e true`
   的快速探測）——distro 沒在跑，後面都不用問，直接回報
   `{"distro_running": False}`。
2. **systemd 單元層**：`wsl -d Ubuntu systemctl --user list-units
   --type=service,timer --all --no-legend --plain`——**複用既有
   `get_systemd_status()` 的解析邏輯**（同一份 parser，不寫第二份），
   差別只在指令前面包 `wsl -d Ubuntu`。取各 `hermes-*` 單元的
   `active`／`activating`／`inactive`／`failed`。
3. **gateway 就緒層（中間態的關鍵）**：service `active` ≠ 真的可用——
   gateway 啟動後約 **3.5 分鐘**才寫狀態檔（既有教訓）。故對「剛轉
   active 的服務」以狀態檔存在性／mtime（或既有的 health 探測方式，
   engineering 依實況選定）補一層判斷，避免「service 綠了但實際還在
   暖機」被誤報為全常駐。

**技術細節**：`wsl.exe` 呼叫加 timeout（建議 10 秒，distro 冷啟動時
`wsl -d` 本身可能要拉起 distro——探測指令**必須**選不會觸發 distro
啟動的形式，例如先用 `wsl --list --running` 判斷，distro 沒跑就不再
往下呼叫，**探測不能有「把系統叫醒」的副作用**）；失敗一律回傳明確的
「無法查詢」結構，不噴例外（既有慣例）。

### 1.2 燈號狀態機（不是只有紅綠）

| 燈號 | 條件 | 文字建議 |
|---|---|---|
| 綠 | distro 運行中＋常駐單元全部 `active`＋gateway 就緒層通過 | 「背景常駐中」 |
| 黃 | distro 運行中＋任一單元 `activating`，或 `active` 但 gateway 暖機中（3.5 分鐘窗口） | 「啟動中／暖機中」 |
| 橙 | distro 運行中＋部分單元 `inactive`/`failed`（列出是哪幾個） | 「部分服務未運作」 |
| 紅 | distro 未運行（＝完全沒有背景常駐） | 「背景服務未運作」 |
| 灰 | 探測失敗（`wsl` 指令錯誤／timeout） | 「無法查詢」 |

「常駐單元」的判準清單**不硬編在前端**：由 `data.py` 內一份明確常數
（或讀 `hermes/systemd/` 檔案清單）定義，並待 automation 常駐案定案後
對齊（該案說了算哪些單元「應該」在線）。

### 1.3 曝露與 UI

- 唯讀 API 新增 `GET /api/service-status`（沿用 8799、GET-only、CORS、
  序列化前掃描——本 endpoint 無憑證資料，但防線統一過）。
- UI：webui header 區（全 view 共用頂部）顯示「燈號＋狀態文字」；點擊
  展開單元明細（哪個單元什麼狀態——資料已在回應裡，不另發請求）。
- 輪詢：前端定時輪詢（頻率待拍板項 3，建議 30 秒）＋ API 端短快取
  （沿用既有 5 秒快取慣例）——`wsl` 呼叫比本機檔案讀取重，輪詢頻率
  是成本／即時性的取捨，故列拍板。
- 探測失敗／未核准寫入部分時：只顯示燈號，不顯示任何操作鍵（不做
  disabled 假按鈕——mock 清零原則）。

---

## 2. 寫入部分——重啟／關閉鍵（獨立核准 gate）

> **（v1.1 標記）本節已於 2026-07-27 經使用者核准並實作**：宿主採 §2.3
> 選項 (a)（併入 bridge 8787，白名單第二群組），單元白名單僅兩個常駐
> service（不含 timer），關閉鍵僅服務層級。實作與測試落點見版本標記。

### 2.1 與 bridge 既有例外的誠實對照（為什麼不能直接說「照 bridge 做」）

| 面向 | bridge（已核准） | 本功能（候選） |
|---|---|---|
| 對象 | **自己 spawn 的子 process**（Hermes dashboard） | **WSL 側具名 systemd 單元**（非本 process 的子程序） |
| ownership 驗證 | PID/process ownership（只能停自己啟動的） | **PID ownership 模型不適用**——單元由 systemd 管理，非 bridge 子程序 |
| 替代的邊界機制 | — | **固定具名單元白名單**（寫死清單）＋固定操作動詞（§2.2）——「能控制什麼」由白名單窮舉，不由 ownership 推導 |
| 停止語意 | kill 自己的 child | `systemctl --user stop`——**可能被單元的 `Restart=` 策略或依賴關係抵銷**（§2.4） |

結論：本功能**延續** bridge 模式的四個要素（固定白名單操作、無任意
shell、localhost-only、audit log），但 ownership 一項以「具名白名單
窮舉」**替代**而非沿用——這個差異必須讓使用者知情後才核准。

### 2.2 操作規格（若核准，此為硬性邊界）

- **單元白名單寫死**：具名清單（建議僅常駐 service：
  `hermes-worker.service`／`hermes-telegram.service`；是否含 timer 見
  待拍板項 2）。HTTP 介面以**枚舉索引或白名單內字串嚴格比對**選擇單元，
  任何不在清單內的值一律 400＋audit。
- **操作動詞白名單**：僅 `start`／`stop`／`restart` 三種（枚舉）。
- **指令固定模板**：`wsl -d Ubuntu systemctl --user <op> <unit>`——
  `<op>`／`<unit>` 只能來自上述兩個白名單，模板其餘部分寫死，不接受
  任何其他參數。
- **audit log**：每次操作一筆（時間、單元、動詞、結果/exit code）——
  沿用 bridge 的 audit 慣例與落點。
- **UI**：明確按鈕＋二次確認（stop/restart 是破壞性動作，比照遷移案
  §5.2「不做點一下就執行」）；操作後燈號進入黃（等待狀態收斂），不
  樂觀更新。
- **localhost-only＋CORS 白名單＋（若獨立 server）比照 PTY 的授權層級
  評估**：service 控制的危害面低於 PTY（只能對兩三個具名單元做三種
  動作），與 bridge 同級的 Origin 檢查即可，不需要 token——但若拍板
  併入 bridge，自然沿用其既有檢查。

### 2.3 宿主 process 選項（待拍板項 1）

- **選項 (a)（推薦）：併入既有 bridge 8787，以「白名單第二群組」擴充**。
  理由：bridge 本來就是「process/服務控制型寫入例外」的宿主，Origin
  檢查、audit、重複操作防護的基礎設施已實作並通過安全檢查 script 驗證
  ——同性質操作集中一處，audit 一份，不新增第四個 port。**代價與條件**：
  8787 目前核准的白名單只有四種 hermes dashboard 操作，擴充＝**必須
  重新走核准**（正是本 gate 的作用）；實作上兩個群組的白名單以獨立
  常數分列＋測試斷言枚舉完整清單，防止群組間互相滲透。
- **選項 (b)：獨立小 server**（如 8788）。隔離最乾淨、單獨核准單獨
  下線，但複製一份 Origin/audit 基礎設施、多一個常駐 process，維護面
  與埠清單再膨脹。
- 推薦 (a)：在單人本機系統上，「同性質寫入集中管理＋清單枚舉測試」比
  「process 隔離」更能對抗實際風險（白名單靜默膨脹），且擴充本來就要
  重新核准，gate 沒有被繞過。

### 2.4 stop 鍵與自動化機制的互動（設計課題，start blocker——**v1.1 已解除**）

- **`Restart=` 抵銷問題**：常駐 service 若設 `Restart=always`（或
  automation 常駐案為了「crash 自動重啟」而設定），使用者按 stop 後
  systemd 語意上 stop 是明確意圖不會自動重啟、但**Task Scheduler 喚醒
  邏輯或 linger 搭配的任何「確保在線」機制可能把它拉回來**——「關閉」
  鍵的真實語意（停多久？停到下次開機？停到下次喚醒排程跑？）**必須等
  automation 常駐案的機制定案後才能誠實定義**。本提案不猜：列為寫入
  部分的 start blocker，實作前與該案對齊並把定案語意寫進 UI 文字
  （按鈕旁明確說明「停止後將於（何時）自動恢復」或「不會自動恢復」）。
- **（v1.1 定案）blocker 解除的查證結論與 stop 真實語意**：喚醒機制是
  Task Scheduler task `HermesWslKeepAlive`
  （`hermes/windows/hermes-wsl-keepalive.vbs`，內容為
  `wsl -d Ubuntu --exec sleep infinity`）——它只保 distro 活著，**不會把
  被 stop 的服務拉回來**；systemd 的 `Restart=always` 對明確的
  `systemctl stop` 不觸發（crash 才觸發）。因此 stop 的真實語意定案為：
  **停止後不自動恢復，直到下次 Windows 登入／WSL distro 重啟時由
  systemd（依 enable 狀態）重新拉起**。這句話已寫進 UI 按鈕旁的說明
  文字（`webui/src/ServiceControl.tsx` 的 `STOP_SEMANTICS_TEXT`，由
  測試與安全檢查第 12 項鎖定不可移除）。
- **timer 不建議納入 stop 對象**（待拍板項 2 的建議理由）：timer 的
  「停止」和排程語意糾纏（Persistent=true 的補跑等），誤操作面大於
  價值；排程健康觀測已有 P2 排程健康表。

---

## 3. 風險總表

| 風險 | 影響 | 緩解 |
|---|---|---|
| 探測指令意外喚醒 WSL distro | 「觀測」變成「改變系統狀態」 | §1.1 分層探測：distro 未跑就止步，不呼叫任何會拉起 distro 的指令；此行為列入測試 |
| gateway 暖機期被誤判為故障 | 使用者誤按重啟、越弄越糟 | 燈號有黃色中間態（3.5 分鐘教訓直接內建）；UI 文字明示「暖機中」 |
| 寫入白名單靜默膨脹 | 例外變泛用操作台 | 白名單常數分群＋測試斷言枚舉完整清單；任何擴充回本提案增補並重新核准 |
| stop 被自動重啟機制抵銷、語意混亂 | 使用者以為關了其實沒關 | §2.4 start blocker：等 automation 案定案後定義真實語意並寫進 UI |
| `wsl` 呼叫慢／timeout 拖累 API 回應 | 燈號卡頓或整個 API 變慢 | 探測走獨立 endpoint＋timeout＋快取；不與其他 endpoint 共用同步呼叫路徑 |
| 與 automation 常駐案互相踩腳 | 兩案改同一批單元的狀態 | 本功能不碰 enable/disable/unit 檔（§0.2）；操作僅 runtime start/stop/restart |
| 第三個寫入例外的核准疲勞 | 邊界審查流於形式 | gate 分離明確（§0.1）；對照表誠實列出與 bridge 的差異（§2.1），不包裝成「跟以前一樣」 |

---

## 4. 實作切分建議（若核准）

- **第一批（唯讀）**：`get_background_service_status()`＋API endpoint＋
  header 燈號＋測試（含「不喚醒 distro」行為測試、五態狀態機測試、
  mock `wsl` 輸出）。無 start blocker，隨核准即可做。
- **第二批（寫入，獨立 gate＋blocker 解除後）**：白名單操作＋audit＋
  UI 按鈕＋測試（白名單枚舉、非白名單 400、audit 斷言）；比照 P0 慣例
  把新增操作納入安全檢查 script 的檢查項。

---

## 5. 待拍板項清單（使用者需回答的最小問題集）——**v1.1 全數已決**

1. **寫入鍵宿主**【已決 2026-07-27】：採推薦「併入 bridge 8787、白名單
   第二群組、重新核准」（選項 a）。兩群組白名單以獨立常數分列，測試
   斷言枚舉完整清單。
2. **單元白名單範圍**【已決 2026-07-27】：採建議——僅常駐 service
   （`hermes-worker.service`／`hermes-telegram.service`），不含 timer。
3. **燈號輪詢頻率**【已決,隨 commit 3871129 唯讀實作定案】：前端 30 秒
   ＋API 端短快取（§1 已交付）。
4. **關閉鍵層級**【已決 2026-07-27】：採建議——僅服務層級,不做
   `wsl --terminate`。
5. **（start blocker）**【已解除,查證結論見 §2.4/版本標記】：
   `HermesWslKeepAlive` 只保 distro 活著,不會拉回被 stop 的服務;
   `Restart=always` 對明確 stop 不觸發——stop 語意已誠實定義並寫進 UI。
6. **寫入部分整體 gate**【已決 2026-07-27】：核准實作。
