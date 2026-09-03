# Web UI — 排版可讀性改版（typography）設計規格（v1）

規格撰於：2026-08-15　　落地於：2026-09-03
狀態：**已實作完成——commit `ae1eff1`（"feat(webui): 排版可讀性改版落地
（擱置 19 天的 typography patch + 三紅修復）"），156/156 測試全綠**
負責領域（實作階段）：`engineering`（`webui/src/globals.css` 排版覆蓋層、
`webui/src/App.tsx` 導覽與 topbar 結構、測試期望值同步）
性質：**純呈現層**。零資料流／API／狀態邏輯／安全語意變更。

依賴文件：

- [webui-migration-proposal.md](webui-migration-proposal.md)——Web UI 本體
  架構（純 Vite SPA、唯讀 API、寫入側隔離）。本規格不觸及其中任何一條鐵律。
- [webui-pty-terminal-proposal.md](webui-pty-terminal-proposal.md) §7/§8
  ——「ClaudeCode CLI 位於總覽與 Jobs 之間」這條導覽順序約定的出處，由
  `webui/tests/ui-static.test.mjs:80` 鎖定（見下方 §3.3）。
- [webui-service-control-proposal.md](webui-service-control-proposal.md)、
  [webui-update-button-proposal.md](webui-update-button-proposal.md)
  ——本次改版掃過的既有樣式區塊（`.resident-*`、`.update-*`、`.cred-*`）
  的規格出處；語意色與 cascade 優先權紀律皆源自此二份。

**素材出處說明**：本規格原以「給 Claude Code 的 prompt」形式存在於
`agentos-ui-patch/`（2026-08-15 產出的素材包，含預覽 HTML 與 CSS patch 草稿）。
程式碼落地後，素材包已移出 repo 至
`C:\Users\razer\dev\_archive\agentos-ui-patch-20260815\`，規格內容整理成本文件
留存。日後要動排版層，讀本文件即可，不需要回頭找素材包。

---

## 0. 為什麼要做

深色介面（`#0d0f14` 底）＋中英混排的組合，把三個問題同時放大：

1. `body` 的字型堆疊首位是 **Inter，而 Inter 完全不含中文字符**。每一段
   中英混排都在跨字型，中文掉到第 4、5 順位，x-height／字重／基線都對不上。
2. 全站大量 10–11px 字。**中文需要比拉丁字大 1–2px 才有同等辨識度**，
   11px 以下的中文實質不可讀。
3. 大量 `font-weight: 800`（`.notification-button em` 甚至 `900`）。中文字型
   少有 800 字重，瀏覽器合成假粗體（faux bold），在深色底小字上特別糊。

同時 `--text: #f3f4f8` 打在 `#0d0f14` 上約 17:1，過高的對比在深色介面會造成
halation（發光糊邊）；而 `--muted-2: #626a7a` 在面板底色上只有約 3.5:1
（不過 WCAG AA），卻專門配 10–11px 小字——是全站最難讀的組合。

---

## 1. 硬性約束（不可協商）

- **禁止 `!important`**。`globals.css` 內有明確的特異性紀律（檔內註解有寫）：
  要蓋過帶元素選擇器的規則（例 `.data-table th` 是 (0,1,1)）就用同等或更高
  特異性的選擇器。驗收條件：`grep -c '!important' webui/src/globals.css`
  的結果不得比改動前多。
- **`webui/tests/` 下的 cascade 解析測試不准為了矇混而改**。
  `stage3-render.test.mjs` 與 `update-precheck-render.test.mjs` 鎖著
  `.cred-pool-block` 與 `.update-facts` 的優先權。改完要跑測試；若期望值
  因樣式變更而需更新，**只更新期望值，斷言一條不刪、不放寬、目的完整保留**。
- **語意色一律不動**：`--green` / `--orange` / `--red` / `--blue` / `--violet`
  / `--claude-orange`、燈號色、`#fbbf24` 暖機黃、`#9ca3af` 離線灰。
- 不新增任何顏色到 `:root` 以外的新色彩系統。
- 新規則一律以**檔尾覆蓋層**形式新增，不改既有規則，方便日後整段回退。
  （此約束帶來一個非顯而易見的副作用，見 §3.1。）

---

## 2. 原始規格

### 2.1 `webui/src/globals.css`——九個區塊的覆蓋層

在檔案最末端新增一個有註解分區的覆蓋層。

**1. 字型堆疊——中文優先。**

```
font-family: "Noto Sans TC", "Segoe UI Variable Text", "Segoe UI",
  -apple-system, BlinkMacSystemFont, "PingFang TC", "Microsoft JhengHei", sans-serif;
font-size: 14px;
line-height: 1.6;
-webkit-font-smoothing: antialiased;
```

等寬字（`.code-block`、`.update-facts b`、`.update-basis-ref`、
`.update-rescue ul`、`.update-diverge ul`、`.cred-model-facts dd`）保持
monospace，但在 stack 末端補 `"Noto Sans TC"`，避免中英混排的欄位掉回
預設字型。

**2. 內文色與灰階。**
`--text` 由 `#f3f4f8` 改 `#e2e5ec`（解 halation）。
四層灰收斂成三層：`--muted` → `#9aa1b1`、`--muted-2` → `#8b93a3`。
檔內硬編的 `#d8dbe3` / `#b8bec9` 兩層灰一併對齊到 `#dfe3ea` / `#a7aebc`。

**3. 標題行高。** 全域 1.6；`h1/h2/h3`、`.page-identity h1`、`.panel-head h2`、
`.section-heading h2`、`.metric > b`、`.update-card-title b` 收到 1.3。

**4. 字級階梯——最小 12px。**
規則：**最小 12px**；內文與表格 14px；`.panel-head h2` 17px；
`.metric > b` 24px；`.section-heading h2` 19px。
純拉丁的 kicker（`.preview-title small`）可留 11px。
逐條掃過 `globals.css` 裡所有 `font-size: 10px` 與 `11px` 的宣告
（`.nav-item small` 11、`.user-card small` 10、`.metric > small` 11、
`.data-table th` 10、`.message small` 10、`.composer > p` 10…），一律提到
12px 以上。

**5. 字重收斂成 400 / 500 / 700。**
標籤 / pill / 表頭 / 按鈕 / 導覽項 → `500`；品牌字、標題、數字 → `700`。
不得殘留 `font-weight: 800|900`。

**6. 表頭。** `.data-table th` 由 `10px / 800 / uppercase / letter-spacing: .08em`
改為 `12px / 500 / letter-spacing: .04em / text-transform: none /
color: var(--muted) / padding: 10px`。
同理拿掉套在中文標題上的 uppercase 與寬字距：`.cred-pool-block h3`、
`.service-control-title`、`.update-facts span`、`.update-rescue > span`、
`.cred-model-axis h3`——`text-transform: uppercase` 對中文無效，
`letter-spacing` 只會把中文字拆散。

**7. 數字欄。** `.data-table td`、`.metric > b`、`.metric-card b`、
`.update-basis-counts b`、`.update-facts b` 加
`font-variant-numeric: tabular-nums`，讓成本與時間可直向比對。

**8. 導覽分組 + topbar**（搭配 §2.2 的 `App.tsx`）。新增 `.nav-group`、
`.nav-text`、`.nav-icon-write`、`.page-kicker`、`.page-caption` 五個 class：

- `.main-nav { gap: 0 }`；`.nav-group { display: grid; gap: 4px }`；
  `.nav-group + .nav-group { margin-top: 18px }`
- `.nav-item` 由 `min-height: 54px` 降到 `44px`，grid 改 `28px 1fr 12px`
  （副標只在 active 顯示，不需要固定高）
- `.nav-icon-write { color: var(--claude-orange); background: rgba(217,119,87,.14) }`
- `.topbar { height: auto; min-height: 76px; padding: 16px 22px }`
- `.page-kicker` 取消 `writing-mode: vertical-rl` 與 `transform: rotate(180deg)`，
  改平排 `12px / 500 / letter-spacing: .14em / #8079d4`
- `.page-caption { margin: 0 0 15px; max-width: 82ch; color: var(--muted);
  font-size: 14px; line-height: 1.65; text-wrap: pretty }`

**9. 表格橫向捲動。** `.data-table td` 原本全欄 `white-space: nowrap`，
長 job id 加兩個時間戳一定橫捲。改成 `white-space: normal`，
只有 `td:first-child` 保持 nowrap。

另：`@media (max-width: 760px)` 裡隱藏 `.nav-item > span:nth-child(2)` 的規則
要跟著新的 `.nav-text` 結構調整，窄視窗仍然只留圖示。

### 2.2 `webui/src/App.tsx`——導覽與 topbar

**導覽：十項平鋪改三組。** `NAV_ITEMS` 改成 `NAV_GROUPS`：

- **觀測**（唯讀）：總覽、Jobs、成本、Memory、Logs、Hermes Sessions
- **操作**（有寫入或外部控制）：ClaudeCode CLI、Hermes Dashboard
- **治理**：憑證 / Lane 狀態、Hermes 更新

每組上面一行 `.nav-label` 組名。`NAV_GROUPS` 的型別要保留 `ViewId` 的
exhaustive 檢查。

**副標只在 active 項顯示。** 原本十行副標常駐，且 760px 以下整個
`display: none`——這本來就證明它不是必要資訊。`{active && <small>{item.sub}</small>}`。

**寫入型 view 標記。** `NavItem` 加 `write?: true`，`terminal` 與 `hermes`
帶上；圖示 class 變成 `nav-icon nav-icon-write`。ClaudeCode CLI 是唯一的
PTY 寫入型 view，導覽上要看得出來。

**topbar。** kicker 原用 `writing-mode: vertical-rl` 直排——佔 84px 固定高、
可讀性差、內容只是重複右邊的標題。改成與 `<h1>` 同列的平排小字
（`className="page-kicker"`），移除包住標題的 `<div>` 與它的左側分隔線。

**長說明移出 header。** `PAGE_META[...].desc` 從 `.topbar` 移到
`.main-content` 的第一行 `<p className="page-caption">`。Hermes 更新那筆 desc
近 80 字，塞在固定 84px 高的 header 裡一定爆版。**`PAGE_META` 的內容一字不改，
只換渲染位置。**

### 2.3 驗收條件

- build 與 `webui/tests/` 全綠。
- `grep -c '!important' webui/src/globals.css` 不得比改動前多。
- 所有 `font-size` 不小於 12px（純拉丁 kicker 除外）、沒有殘留
  `font-weight: 800|900`、語意色 hex 一個沒少。
- 十個 view 逐一開過，尤其 Hermes 更新頁（最長的 desc）與憑證頁
  （固定欄寬表格）。

---

## 3. 落地時發現、原規格沒寫到的三個坑

**這一節是本文件最有價值的部分。任何人日後再動排版層都會踩到這三項。**

### 3.1 媒體查詢排序——「貼在檔尾」與「響應式覆寫」直接衝突

**現象。** §1 要求新規則一律貼在檔尾（方便回退）。但 `globals.css` 既有的
`@media (max-width: 1100px)` 與 `@media (max-width: 760px)` 位在
**第 182–196 行**，後面還接著約 115 行的基礎規則。新排版層貼在檔尾，
排序上就晚於那兩個媒體查詢區塊——**同特異性後寫者勝**，窄視窗的覆寫被整個
蓋掉。實際失效的有四條：

- `.nav-item { grid-template-columns: 1fr; padding: 9px }`
  （78px 側欄的單欄只留圖示佈局）
- `.topbar { min-height: 86px; padding: 14px 18px }`（窄視窗 topbar 高度／內距）
- `.page-identity h1 { font-size: 22px }`
- `.main-content.hermes-content { height: calc(100vh - 86px) }`

**解法。** 在檔尾再開一個 `@media (max-width: 760px)` 區塊，以**同特異性**
把那幾條復位。媒體查詢排在最後本來就是該有的順序，因此不需要 `!important`
（守住 §1 的特異性紀律）。

另需額外加一條**原規格沒有**的覆寫：窄視窗原本靠
`.page-identity p { display: none }` 藏掉頁面說明；說明搬到內容區的
`.page-caption` 之後不再被那條命中（Hermes 更新頁近 80 字會整段跑出來），
所以要在復位區塊裡補 `.page-caption { display: none }`，比照原行為。

**為什麼不能改用「把既有媒體查詢搬到檔尾」來解決。**
這是更「乾淨」的直覺解，但**不可採**：既有媒體查詢在第 182–196 行，
其後還有約 115 行基礎規則。這 115 行裡有多少條與媒體查詢內的宣告構成
「同特異性、靠順序決勝」的隱性關係，無法靜態窮舉——把媒體查詢往後搬，
等於一次性翻轉所有這些關係的勝負，會擾動一大批與本次改版無關的樣式。
**在檔尾補一個復位區塊只影響列名的四（五）條規則，擾動面是可枚舉的；
搬動既有規則的擾動面不可枚舉。** 這是刻意選擇範圍小、可驗證的做法，
不是偷懶。

### 3.2 `.main-content.hermes-content` 與 topbar 高度耦合

`globals.css:56` 寫死 `height: calc(100vh - 84px)`，那個 84px 對應的是
**舊的 `.topbar { height: 84px }`**。§2.1 第 8 塊把 topbar 改成
`min-height: 76px` 之後，Hermes iframe 容器的扣除量沒跟著改，畫面底部就多出
**8px 空隙**。

修法是在覆蓋層補 `.main-content.hermes-content { height: calc(100vh - 76px) }`，
窄視窗維持 86px（由 §3.1 的復位區塊處理）。

**通則：凡是動 `.topbar` 高度，都必須連帶檢查 `.main-content.hermes-content`
的 `calc()` 扣除量**（目前三處：`globals.css:56` 的舊值、檔尾覆蓋層的 76px、
以及兩個 760px 媒體查詢裡的 86px）。這是全檔唯一一處把 topbar 高度硬編進
另一個元件的地方。

### 3.3 導覽順序是拍板過的約定，不是可自由重排的樣式

原規格 §2.2 把 `terminal`（ClaudeCode CLI）移到〔操作〕組首位，並自認是
「刻意的取捨」。**這是錯的。**「ClaudeCode CLI 位於總覽與 Jobs 之間」是
PTY 提案裡拍板過的約定，並由 **`webui/tests/ui-static.test.mjs:80`** 以
索引比較鎖定（`overviewIdx < terminalIdx < jobsIdx`）。

落地時的處置：**terminal 移回〔觀測〕組第二位**——三組分組結構與這條約定
可以並存，分組本身不需要犧牲順序。測試因此自然轉綠，`ui-static.test.mjs`
一行未動。`NAV_GROUPS` 上方的註解也同步改寫，把原規格「要回復原順序就把
terminal 移回去」那段刪掉，改成明確記載這條約定與測試位置。

**不要再把它移走。** 若真要改順序，那是需要重新拍板的產品決策，
不是排版調整。

---

## 4. 仍未解的兩項（明確未處理，不是遺漏）

1. **`"Noto Sans TC"` 沒有自帶 webfont。**
   字型堆疊首位依賴本機已安裝的字型。本機有
   `C:\Windows\Fonts\NotoSansTC-VF.ttf`，所以看不出問題；**換機或他人環境
   會掉到 fallback**（`"Segoe UI Variable Text"` 之後），§2.1 第 1 塊想解的
   跨字型問題會部分回歸。
   要不要自帶字型是**獨立議題**——涉及資源體積（Noto Sans TC 可變字重
   全字集是數 MB 等級），需要先決定子集化策略與載入方式，不在本次範圍。

2. **`.page-identity > div` 的兩條規則已成死規則。**
   `globals.css:50`（`padding-left: 18px; border-left: 1px solid var(--line)`）
   與媒體查詢裡的第 190 行（`padding: 0; border: 0`）。§2.2 已移除包住標題的
   那個 `<div>`，十個 view 全部改完後這兩條再也不會命中任何節點。
   目前保留不刪，**可於下次回寫排版層時一併清掉**——單獨為此動檔案不划算，
   而且會踩到 §3.1 講的「搬動既有規則擾動 cascade」的同一類風險。
