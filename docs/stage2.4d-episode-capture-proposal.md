# Stage 2.4d — Desktop 長壽 session 的 Episode Capture（設計提案）

日期：2026-07-11　狀態：**提案・待使用者核准（proposal only，未實作、未改任何既有檔案）**
負責領域：`engineering`
修訂：2026-07-11 第二版——方向已獲使用者核准（暫不開工），依指示補齊三個
blocker：(1) cursor DB 重建的誠實修正＋recovery 流程（§3.2）；(2) schema v2
新增 `first_message_id`／`last_message_id`／`source_content_hash` 完整性欄位
（§1.2、§4.5）；(3) `bridge_cursors` 複合主鍵＋profile fail-closed 邊界（§1.2、§6.1）。

> **本文件是設計提案，不是決策記錄。** 核准前不得動 schema、程式碼、config、
> systemd units 或部署側資料。§0.1 所列項目已由使用者拍板；其餘設計待
> 本文件整體核准後才進入實作。

---

## 0. 問題與核心語義（使用者已拍板，本提案嚴格遵循）

實際使用模式：主要用 Hermes Desktop，session 是**可長期復用的上下文容器**
（過幾天拿出來繼續用，避免重頭講）。實測：64 個 session 只有 20 個有
`ended_at`（desktop 0/2、tui 8/46）——現行 bridge 以「`ended_at` 已設＝完結」
為匯入判準，**結構性漏掉大部分 session**，而且永遠不會補上（這些 session
本來就不會被「結束」）。

拍板的核心語義（本提案的公理，不再重議）：

1. **Hermes session ＝ 長壽上下文容器；AgentOS 匯入單位 ＝ immutable
   episode／capture checkpoint。** 同一 session 可有多個 episode。
2. 每次擷取只取 **per-session cursor（`last_captured_message_id`）之後的新訊息**。
3. **event_id 必須包含穩定的 episode boundary**（不可用匯入時間等不穩定值）。
4. `ended_at`、inactivity threshold、manual checkpoint **都只是 episode 的
   trigger**——trigger 與擷取語義解耦。
5. **pre-cutover 歷史不得自動湧入**：44 個 `ended_at=None` 的既有 session
   不得因新判準批次進入管線。
6. importer 排程與 headless CoS **暫不啟用**（維持 2.4c 既有邊界）。
7. **明確否決**：單純把 inactivity 當 completed（會把復用中的 session 誤判
   完結並整包匯入）。

解耦的價值（明文寫出）：inactivity 觸發 episode 後 session 又活過來——
episode 已 immutable、內容不變；新訊息屬於**下一個** episode。session 的
「復活」在這個模型裡不是異常狀態，而是常態；任何 trigger 都只回答
「現在要不要切一刀」，不回答「session 是否完結」。

### 0.1 已定案（2026-07-11 使用者拍板，從開放問題移入）

1. **`episode_cutover` ＝ 2.4d 部署啟用的精確 UTC 時刻**——部署當下
   （翻 `episodes.enabled: true` 之前）記錄的實際時間戳，**不是日期概念值**
   （不取 00:00、不取部署「日」）。
2. **`inactivity_hours` ＝ 72**。
3. **接受方案 A**：legacy 列與 episode 列同表共存。
4. **ended 後復活不加特例**：統一規則（eligible 只看訊息、trigger 只看時機）。
5. **manual checkpoint 不觸發 importer**：scan／import 職責分離不破例。

---

## 1. Schema 影響（提案七項之一）

### 1.1 兩案評估

**方案 A（主推）：episode ＝ `bridge_sessions` 的一列；event_id 帶 boundary；
另設 per-session cursor table。**

- `bridge_sessions` 的一列從「一個 session 的處理狀態」重新定義為
  「**一個匯入單位（episode 或 legacy session-level 記錄）**的處理狀態」。
  episode 列的 `event_id` ＝ `hermes:<sid>:<first>..<last>`（見第 2 節），
  沿用既有 `UNIQUE(event_id)` 做 episode 層級去重。
- 新增小表 `bridge_cursors`：per-session 游標（`last_captured_message_id`）
  與 episode 序號計數，**純簿記、不是狀態機**。
- `bridge_sessions` 加 2 欄：`episode_seq`（int、optional；NULL＝legacy
  session-level 記錄）、`capture_trigger`（enum：`ended | inactivity |
  manual | legacy`；optional，NULL 視同 legacy）。

**方案 B：獨立 `bridge_episodes` table；`bridge_sessions` 降為 session
游標/彙總表。**

- episodes 表帶完整 17 欄語義＋boundary＋trigger；bridge_sessions 改為
  `session_id UNIQUE` 的游標與彙總（last_episode_seq、彙總狀態等）。

**取捨對照**：

| 面向 | A（episode＝列） | B（獨立 episodes 表） |
|---|---|---|
| repository 層改動 | **近零**：`upsert_session_state`／`touch_last_seen`／`mark_failed`／`list_by_import_status` 全部以 event_id 為 key，episode 列直接復用 | 全套 API 重寫或複製一份（第二份 upsert／enum 驗證／條件必填） |
| `UNIQUE(event_id)` 去重骨幹 | 原樣沿用（Stage 2 DoD 2「恰好一次」的既有保證） | 要在新表重建，並處理兩表間 event_id 命名空間 |
| reconcile／importer | 逐列處理的既有流程天然對應逐 episode 處理 | 佇列來源、回填目標都要改表 |
| 3 筆既有部署記錄 | **原樣保留**（legacy 列，event_id 不變） | 必須搬遷或雙表並存，migration 面積大 |
| 語義潔癖 | 一表混 legacy 列與 episode 列（以 `episode_seq IS NULL` 區分） | 表職責最乾淨 |
| session 彙總查詢 | `GROUP BY session_id`（列數量級：低頻 episode × 少量 session，無效能疑慮） | 直接讀彙總表 |

**主推方案 A**。理由：整個 Stage 2 管線的冪等骨幹就是
`UNIQUE(event_id)`＋`open(mode="x")`，A 讓 episode 直接繼承這條骨幹，
改動集中在「event_id 怎麼組、boundary 怎麼算」，而不是把 repository／
reconcile／importer 三層全部重寫；B 的潔癖收益不值那個 migration 與
第二份實作的風險（「回填規則只該有一份實作」是 2.4c 已明文的立場，
B 會誘發同類複製）。

### 1.2 欄位層面定義（方案 A）

**`bridge_sessions` 新增欄（17 → 22 欄）**：

| 欄位 | 型別 | 必填 | 語義 |
|---|---|---|---|
| `episode_seq` | int | optional | 該 session 的第幾個 episode（1 起算）。NULL＝pre-2.4d 的 legacy session-level 記錄。只供人讀與排序；**identity 仍以 event_id 的 boundary 為準** |
| `capture_trigger` | enum | optional | `ended | inactivity | manual | legacy`。記「這刀是誰切的」，純追蹤；NULL 視同 legacy（migration 會回填 `legacy`） |
| `first_message_id` | int | optional（episode 列必填） | boundary 顯式欄位：episode 首則訊息的 Hermes rowid。legacy 列 NULL |
| `last_message_id` | int | optional（episode 列必填） | boundary 顯式欄位：episode 末則訊息的 Hermes rowid。legacy 列 NULL |
| `source_content_hash` | string | optional（episode 列必填） | episode 內容雜湊（§4.5）：scanner 切刀時由**同一 snapshot** 計算；importer 匯入時重算比對，不一致 → needs_review |

boundary 承載的立場（**修訂**先前「只由 event_id 承載、不設顯式欄」的版本）：
`event_id`（＝`event_id_range`＝`hermes:<sid>:<first>..<last>`）仍是**唯一性
載體**（UNIQUE 去重 key）；`first_message_id`／`last_message_id` 是**顯式
查詢與 recovery 欄位**——cursor 重建（§3.2）要對 session 取
`max(last_message_id)`，靠 SQL 直接查比解析字串可靠。兩者不是兩份真相：
repository 的 `create_episode()` 由同一組 boundary 值同時產生 event_id 與
顯式欄位，**測試強制兩者一致**（矩陣 #24：任一 episode 列的 event_id 解析
結果必須等於顯式欄位，不一致即 schema 對齊測試失敗）。

**新表 `bridge_cursors`（per-session 游標，純簿記）**：

| 欄位 | 型別 | 必填 | 語義 |
|---|---|---|---|
| `source_profile` | string (PK 之一) | required | 來源 Hermes profile。**複合主鍵 `(source_profile, session_id)`**——不同 profile 的同名 session 絕不共用 cursor（§6.1） |
| `session_id` | string (PK 之一) | required | Hermes session id |
| `last_captured_message_id` | int | required | **cursor**：此 rowid（含）以前的訊息已被某個 episode 涵蓋或被判定永不自動擷取。只前進不後退 |
| `last_episode_seq` | int | required | 已切出的最大 episode 序號（0＝尚無 episode） |
| `updated_at` | string | required | UTC ISO 8601 |

cursor 前進與 episode 列建立在**同一個 SQLite transaction** 內完成
（repository 層新 API，例如 `create_episode(...)`）：要嘛「episode 列
＋cursor 前進」都成立，要嘛都不成立——保證每則訊息至多屬於一個
episode、cursor 永不回退。`create_episode()` 撞到既有 event_id
（`UNIQUE` conflict，同 boundary 已存在）時視為冪等 no-op：不動既有列、
只把 cursor 推進到該 boundary 的 last（重掃／recovery 後重切同一刀的
安全路徑）。

**可拋棄語義的誠實界定（修訂）**：`bridge_cursors` 與 watermark 同屬部署側
可拋棄狀態，但「刪掉重建後直接重掃」**並非無害**——重建後 cursor 消失，
若逕自從 episode_cutover 重切，切出的 boundary 可能與歷史 episode
**不同**（例如歷史已匯入 `100..120`，重建後累積新訊息切出 `100..130`），
而 `UNIQUE(event_id)` **只擋完全相同的 boundary，擋不住不同 boundary 的
重疊內容**；episode 檔名查重精確到 boundary，同樣擋不住。因此 db 重建後
的**必要前置**是 §3.2 的 recovery 流程（reconcile 從 inbox 目錄真相重建
cursor），且 scanner 對「無 cursor 但已存在該 session episode 檔」的情況
fail-closed 拒切（§3.2）。「可拋棄」的正確含義是：**db 消失不損失任何
可重建資訊**（cursor 可由落地檔案重建、判定可由重跑重derive），不是
「消失後不需要 recovery 步驟」。

### 1.3 registry schema 版本策略：v1 → **v2**

`claudecodeos.bridge_state.v1` 當初 in-place 修訂的理由是「definition-only、
無 runtime 寫入者與存量資料，升 v2 會暗示一個不存在的 migration」
（memory-bridge-state.md §6.2）。**這個前提現在不成立**：部署側已有
3 筆存量資料與活的寫入者，且本次真的有 migration（加欄＋新表）。
依同一邏輯反向適用：**升 `claudecodeos.bridge_state.v2`**，yaml 內容：

- `bridge_sessions` 22 欄（17 欄原樣＋5 新欄：`episode_seq`、
  `capture_trigger`、`first_message_id`、`last_message_id`、
  `source_content_hash`；enum 值照舊由 yaml 供驗證）。條件必填規則
  （比照 `error_reason`／`imported_inbox_path` 慣例，由 repository 驗證）：
  episode 列（event_id 含 `..`）五個新欄全必填；legacy 列全 NULL；
- 新增 `bridge_cursors` 區塊（上表 5 欄，複合主鍵
  `(source_profile, session_id)`）；
- 修訂 `event_id` description：「session 層級 `hermes:<sid>`（legacy）或
  episode 層級 `hermes:<sid>:<first>..<last>`」，並記載保留的未來
  profile namespace（§6.1，本階段不啟用）；
- 檔頭記錄 v1→v2 的 migration 語義（第 3 節）。

`hermes/config/bridge.yaml` 新增（政策層，版控＋同步下發，沿用 fail-loud 慣例）：

```yaml
episodes:
  enabled: false            # 2.4d-4 部署驗證通過後才翻 true（rollout 開關）
  episode_cutover: "<2.4d 部署啟用當下記錄的精確 UTC 時刻>"  # 已拍板（§0.1）：
                            # 翻 enabled 前取當下時間戳寫入，不是日期概念值
  inactivity_hours: 72      # 已拍板（§0.1）
```

`episodes.enabled=false` 或整個區塊缺失時，scanner 維持 2.4c 行為
（只掃 ended_at）；`enabled: true` 但 `episode_cutover` 缺失 → fail loud
（比照 cutover 慣例，絕不默認全掃）。

---

## 2. 既有 event_id 相容方案（提案七項之二）

三個層級共存，命名空間可機械區分，`UNIQUE(event_id)` 不變：

| 層級 | 格式 | 出處與用途 |
|---|---|---|
| 訊息 | `hermes:<sid>:<rowid>` | adapter `claudecodecos.event.v1` 既有慣例，不動 |
| episode | `hermes:<sid>:<first>..<last>` | **新**：bridge_sessions episode 列的去重 key。與既有 `event_id_range` 格式完全相同——boundary 是穩定值（cursor 只前進，first/last 在 create_episode 當下固定、之後 immutable），符合「event_id 必含穩定 episode boundary」的拍板 |
| session（legacy） | `hermes:<sid>` | 既有 3 筆部署記錄與 pre-2.4d 慣例，原樣保留，不再新增 |

區分規則：含 `..` ＝ episode；含 `:` 但無 `..` 且冒號後是整數 ＝ 訊息；
其餘 ＝ session 層級。（Hermes sid 格式 `20260630_183709_063b4e40` 不含
冒號，無歧義。）

**profile namespace 預留（本階段不啟用，§6.1）**：現行三種格式全部隱含
`source_profile=default`。未來支援多 profile 時的擴充格式**現在就定案**
（避免屆時再一次 event_id migration）：`hermes/<profile>:<sid>:<first>..<last>`
（session／訊息層級同理加 `hermes/<profile>:` 前綴）。`/` 不出現在既有
格式的 source 段（恆為裸 `hermes`），機械可區分。本階段任何元件讀到
`hermes/` 開頭的 event_id 或非 default 的 source_profile 一律 fail-closed
拒絕處理（§6.1），**不得默默視為 default**。

**inbox 檔名（deterministic，從 boundary 衍生、不含匯入時間）**：

```
hermes_session_<sid>_ep<first>-<last>.md
```

與 adapter 既有 idempotency 精神一致：同一 episode 不管何時重跑都對到
同一檔名，`open(mode="x")` 天然擋重複落地。**注意既有實作的一個陷阱**
（設計必須明文處理）：adapter `_find_existing_import()` 用子字串
`hermes_session_<sid>` 掃已匯入檔——episode 檔名包含這個子字串，若不改，
**第一個 episode 落地後會把同 session 的所有後續 episode 全部誤判為
already-imported**。因此 2.4d-3 必須把「已匯入掃描」改成 episode-aware：

- episode 匯入的查重 needle ＝ `hermes_session_<sid>_ep<first>-<last>`
  （精確到 boundary）；frontmatter 對照改比 `event_id_range` 全值；
- legacy 檔 `hermes_session_<sid>.md`（無 `_ep`）只擋 legacy session-level
  重匯，**不擋** episode；
- scanner reconcile 的 `_FILENAME_RE` 加 ep 捕獲組：
  `hermes_session_(?P<sid>...)(?:_ep(?P<first>\d+)-(?P<last>\d+))?\.md`——
  有 ep 段 → 回填到 episode event_id；無 ep 段 → 回填到 legacy
  `hermes:<sid>`（既有行為不變）。

**frontmatter（`claudecodeos.inbox.v1`，additive、不升版）**：frontmatter
定位本來就是「建議、非必要條件」（memory-taxonomy §5），加欄不破壞任何
既有讀者。episode 檔新增：

```yaml
session_id: <sid>                 # 既有欄，不變——同 sid 多檔從此合法
event_id_range: "hermes:<sid>:<first>..<last>"   # 既有欄；episode 檔＝episode event_id 本身
episode: 3                        # 新欄：episode_seq
capture_trigger: inactivity       # 新欄：ended | inactivity | manual
```

reconcile 對帳優先序：frontmatter `event_id_range`（可直接還原 episode
event_id）→ 檔名 ep 捕獲組 → 無 ep 段退回 legacy session 層級。三處
（DB event_id、inbox 檔名、frontmatter event_id_range）由同一個 boundary
衍生，任一處都能還原另外兩處——這是「全部一致」的機械保證。

---

## 3. Migration 與 Recovery（提案七項之三＋cursor 重建）

### 3.1 Migration：部署側既有 3 筆的處置

部署側 `bridge_state.db` 現有 3 筆 session 層級記錄（`hermes:<sid>` 格式；
imported／skipped／needs_review 各一）。

**建議：視為 legacy session-level 記錄，原樣保留；不轉為 episode 0。**
理由：

1. 它們的 event_id 與 inbox 檔名（`hermes_session_<sid>.md`，imported 那筆
   已在 `.processed/`）是一組既成事實且互相一致；改寫 event_id 或補
   episode 序號會讓 DB 與 `.processed/` 實體檔名對不上，違反「目錄位置是
   唯一真相」的既有立場，reconcile 反而要加特例。
2. 「episode 0」是假 boundary——這些記錄當初是整包 session 匯入/判定，
   沒有 first..last 可言；捏造 boundary 違反「event_id 必含**穩定**
   boundary」的公理。
3. legacy 列與 episode 列在方案 A 下天然共存（`episode_seq IS NULL`），
   保留成本為零。

**migration 步驟（2.4d-4 部署時執行，新增 `bridge_state.py migrate` CLI，冪等）**：

1. `ALTER TABLE bridge_sessions ADD COLUMN episode_seq / capture_trigger`
   （先查 `PRAGMA table_info`，已存在則跳過——冪等）。
2. `CREATE TABLE IF NOT EXISTS bridge_cursors`。
3. 既有列回填 `capture_trigger='legacy'`（`episode_seq` 維持 NULL）。
4. **cursor 種子（belt-and-suspenders，建議做）**：對唯一一筆 imported
   legacy 記錄，若 `event_id_range` 存在，取其 `<last>` 寫入
   `bridge_cursors.last_captured_message_id`（`last_episode_seq=0`）——
   保證已匯入過的內容即使落在 episode_cutover 之後也絕不二次擷取。
   skipped／needs_review 兩筆**不種 cursor**：它們的既有內容全在
   episode_cutover 之前，第 5 節的 cutover 底線已足以擋住自動擷取。
5. registry marker 檢查：migrate 後 db 內容對得上 v2 yaml（測試把關）。

**needs_review 的健康 session 復活後**：legacy needs_review 列原樣留著
（它記的是「pre-episode 整包判定」這個歷史事實）；session 之後有新訊息
且觸發切刀時，產生 **episode 1 的新列**（新 event_id），判定完全獨立
（第 4 節）。兩筆記錄靠 `session_id` 關聯，查 session 全史＝
`WHERE session_id=? ORDER BY episode_seq NULLS FIRST`。legacy 列的
needs_review 仍留給互動式 session 人工處置（維持 2.4c 語義），episode
管線不消費、也不清除它。

### 3.2 Recovery：bridge_state.db 重建後的 cursor 重建（blocker 修訂）

**問題的誠實陳述**：db 重建後 cursor 消失，若直接重掃並從 episode_cutover
重切，boundary 會隨「重建之後又累積了多少新訊息」而變——歷史已匯入
`100..120` 的 session，重建後可能切出 `100..130`。`UNIQUE(event_id)` 只擋
**完全相同**的 boundary；episode 檔名查重也精確到 boundary——兩者都
**擋不住不同 boundary 的重疊內容重複落地**。所以 recovery 不是可選的
最佳化，是重建後的**必要步驟**。

**Recovery 流程（整合進 reconcile——目錄真相回填本來就只有這一份實作）**：

reconcile 掃 `memory/inbox/` 本層＋`.processed/`＋`.failed/` 時（既有職責），
對每個帶 boundary 的 episode 檔（deterministic 檔名 `_ep<first>-<last>` 或
frontmatter `event_id_range`，兩者都攜帶 boundary）額外做：

1. 回填 episode 列（既有 episode-aware 回填，§2）——含顯式欄位
   `first_message_id`／`last_message_id`（從 boundary 直接寫入；
   `source_content_hash` 無法自檔案還原，回填 NULL 並在 decision_reason
   註明，不影響去重）；
2. **重建 cursor**：對每個 `(source_profile, session_id)`，取其所有已落地
   episode 檔的 `max(last_message_id)`，upsert 進 `bridge_cursors`
   （**只前進不後退**：現值更大時 no-op——recovery 對健康 db 重跑無害，
   天然冪等）；`last_episode_seq` 取已落地檔 frontmatter `episode` 的最大值
   （缺 frontmatter 時取檔案數保守估計，只影響序號可讀性、不影響 boundary
   正確性）。

**scanner 側的 fail-closed 防護（防「忘了跑 recovery 就掃」）**：episode
偵測遇到「該 session 無 cursor、但 inbox 三層存在該 sid 的 `_ep` 檔」時，
**拒切**該 session 並回報「cursor 缺失但已有 episode 落地檔——請先跑
reconcile」（記錄並跳過，不是錯誤退出；其他 session 照常處理）。偵測用的
「存在性探測」復用 importer 的同一 needle helper（單處實作），不複製
reconcile 的回填邏輯。

**cursor 重建後切刀位置的穩定性推演（精確結論，逐 case）**：

| Case | 重建後 cursor | 下一刀 boundary | 與歷史的關係 |
|---|---|---|---|
| session 的**最後**一個 episode 有落地檔（to_inbox／.processed／.failed） | ＝歷史 cursor（該檔 last） | `[last+1..max]` | **穩定**：與未重建時完全相同 |
| 最後一個（或多個連續尾端）episode 是**無檔判定**（needs_review／skipped／export-failed——這些依 fail-closed 從不落地） | ＝最後**落地** episode 的 last，**低於**歷史 cursor | 一刀吸收「無檔區段＋新訊息」，boundary **比歷史寬** | **不穩定但受控**（見下） |
| session 從無任何落地檔（全部 episode 都無檔判定，或從未切過） | 無 cursor → episode_cutover 底線 | `[cutover 後首則..max]` | 同上，boundary 可能與歷史任何一刀都不同 |

「不穩定但受控」的精確含義——後果分析：

- **不會重複落地**：歷史落地內容已被重建 cursor 蓋住（第 1 種 case 的
  保證）；無檔區段本來就沒落地過，被新刀吸收後**第一次**有機會落地，
  inbox 無重複。
- **無檔判定被重判一次**：判定是 deterministic 的（敏感 pattern、結構性
  排除都是純函式）——敏感區段被吸進新刀後，新刀整刀再次 needs_review
  （fail-closed 傳染整個 episode，無害且保守）；skipped 區段可能因與新
  訊息合併而翻成 pass（too_short 門檻被合併內容超過）——這是**擴大保留**
  而非資料損失，可接受。
- **DB 列的 boundary 與歷史不同**：舊列已隨 db 消失（整庫重建情境），
  不產生同庫並存的重疊列。**部分重建**情境（bridge_sessions 倖存、只有
  bridge_cursors 消失）下，recovery 第 2 步從檔案重建 cursor 後：若無新
  訊息，重切的刀與倖存列 boundary 相同 → `create_episode` 的 UNIQUE
  conflict 冪等路徑（§1.2）接住、只推 cursor；若有新訊息且尾端存在無檔
  判定列，會產生一筆與倖存 needs_review 列 boundary 重疊的新列——
  **誠實承認：這是設計接受的殘留情況**，兩列都是 needs_review／待人工，
  內容不落地、人工檢視時一併處置；要根絕它得讓 recovery 也能從「無檔
  判定」重建 cursor，但那些判定沒有檔案真相可依（唯一來源就是 db 自己），
  邏輯上不可能，fail-closed 的重判是正確的取捨。

**結論**：切刀位置穩定 ⇔ 「session 尾端最後一次切刀有落地檔」。已落地
內容在 recovery 後**保證**不重複落地；無檔判定（needs_review／skipped／
failed-無檔）在重建後會以可能更寬的 boundary 重判一次，結果 deterministic
且 fail-closed，不造成 inbox 重複、可能多出待人工的重疊 DB 列（僅部分
重建情境）。文件其他各節不得再出現「event_id 去重保證重建後重疊無害」
的說法（第一版此類敘述已全數修訂）。

---

## 4. 狀態機（提案七項之四）

### 4.1 episode 層級狀態＝既有六值 enum，語義原樣、範圍縮小

episode 列的 `import_status` 沿用 `discovered / skipped / to_inbox /
imported / failed / needs_review`，狀態機圖（memory-bridge-state.md §3）
與 importer 轉換表（§3.1）**逐條照舊**，唯一差別是判定對象從「整個
session」縮小為「該 episode 的訊息範圍（boundary 內）」。retry 語義照舊：
failed episode 自動重試、`increment_retry_count`、達 `max_import_retries`
轉 needs_review——全部以 episode 列為單位。

### 4.2 session 層級不設狀態機

`bridge_cursors` 只有游標與計數，**沒有** import_status——session
「處理到哪」的答案就是它的 episode 列集合。避免第二個狀態機與
episode 狀態打架（例如「session 整體算 imported 嗎」這種無法定義的
問題根本不讓它出現）。legacy 列的狀態是歷史快照，不參與新語義。

### 4.3 復活 session 與敏感判定：**每 episode 獨立判定**

- 上一 episode 因敏感命中而 needs_review，**不 block** 下一 episode：
  新 episode 內容乾淨即可獨立放行落地。理由：敏感偵測 fail-closed 的
  保護對象是「即將落地的內容」，episode N 的內容不落地已達成保護；
  把污點傳染給 episode N+1 沒有安全收益，只會讓長壽 session 一次敏感
  永久失聯。
- 偵測範圍＝該 episode 將落地的完整 render 的超集（比照 2.4c
  `_full_session_text` 的「掃超集才安全」原則）：boundary 內全部 event
  原始 content＋tool_calls＋**session 標題與 metadata**（每個 episode 的
  render 都會帶 session 標頭，所以標題也要每次掃——標題敏感則每個
  episode 都 needs_review，fail-closed 可接受）。
- **敏感 episode 的 cursor 語義（設計上必須明文）**：episode 列在
  create_episode 時已建立、cursor 已前進——needs_review 只是該列的
  狀態，**boundary 不回收、cursor 不回退**。該段內容的人工補匯走互動式
  路徑（原文永在 Hermes state.db），與 2.4c 對 needs_review 的立場一致。

### 4.4 同一 episode 的 immutability 保證

三層機制疊加：

1. boundary 在 create_episode 的 transaction 內固定，event_id 由 boundary
   衍生——同一 episode 的 identity 恆定；
2. inbox 檔 `open(mode="x")` 永不覆寫——內容落地一次之後不變；
3. scanner 對既有列只 `touch_last_seen`（既有硬條件）——episode 列的
   判定欄位不被重掃洗掉。

Hermes 側後續變化（訊息被 compact、active 翻轉）不追溯已落地的 episode
——inbox 檔是 capture checkpoint 的快照，本來就不承諾與來源同步。
「切刀時看到的內容」與「匯入時讀到的內容」之間的漂移由 §4.5 的
完整性檢查偵測。

### 4.5 內容完整性檢查：`source_content_hash`（blocker 修訂）

scanner 與 importer 各自開 snapshot、時間點不同——boundary（rowid 範圍）
固定不代表**內容**不變（Hermes compaction／active 翻轉／內容改寫都可能
發生在兩次讀取之間）。設計：

- **scanner 切刀時**：`create_episode()` 由**同一個 scan snapshot** 對
  boundary 內容計算雜湊存入 `source_content_hash`。定義：對 eligible
  events（`active=1`、rowid ∈ [first..last]、依 rowid 升冪）的
  normalized 欄位（rowid、role、type、content、tool_calls 原始值、
  timestamp）做固定鍵序 JSON 序列化後 SHA-256——確定性、與 render
  格式解耦（render 改版不會假性 mismatch）。
- **importer 匯入時**：對自己的 snapshot 以同一純函式**重算**，先於
  敏感偵測比對（流程順序：export → **hash 驗證** → 敏感偵測 → 4.2 排除
  → 落地；完整性是其他判定的前提——內容都不對了，判定與落地都不該做）。
- **不一致 → `needs_review`**：decision_reason 記
  `integrity:content_hash_mismatch`＋可能原因（state.db 內容被 compaction
  ／修改、或兩次讀取窗口不一致），**不記任何內容**（沿用只記標籤的硬
  約束）；不落地、cursor 不回退（boundary immutable 語義不變）、不進
  自動重試（內容漂移不是暫時性錯誤，重試只會反覆 mismatch——與 failed
  的 retry 路徑明確區分）。人工處置走互動式路徑。
- **狀態機影響**：§3.1 轉換表新增一列——「hash 不一致（完整性檢查）→
  `needs_review`」；其餘狀態與轉換不變。reconcile 回填的 episode 列
  hash 為 NULL（無法自檔案還原，§3.2），importer 對 hash 為 NULL 的列
  跳過比對（回填列本來就已落地或已判定，不再進匯入流程）。

## 5. Cutover／防湧入（提案七項之五）

### 5.1 初次 capture 的 cursor 起點

某 session 在 `bridge_cursors` 無記錄時（絕大多數既有 session），第一次
切 episode 的 eligible 訊息＝ **`timestamp >= episodes.episode_cutover`
的訊息**；有 cursor 時＝ `rowid > last_captured_message_id` 的訊息。
統一規則：

```
eligible = messages WHERE active=1
           AND (cursor 存在 ? rowid > cursor : timestamp >= episode_cutover)
boundary = [min(eligible.rowid) .. max(eligible.rowid)]
eligible 為空 → 不切 episode（episode 永不為空）
```

**pre-cutover 訊息永遠不自動擷取**——這是絕對底線，與掃描層 cutover
同一哲學：即使 bridge_cursors／bridge_state.db 整個刪掉重建，自動管線
也絕不越過 episode_cutover 往前擷取（注意：cutover 底線只保證「不往前」；
重建後「不重複」的保證來自 §3.2 recovery，兩者缺一不可）。人工 reconcile
／`adapter.py to-inbox` 補匯（互動式路徑）不受此限，維持既有例外。

**episode_cutover ＝ 2.4d 部署啟用的精確 UTC 時刻（已拍板，§0.1）**：
2.4d-4 翻 `episodes.enabled: true` 之前，取當下實際時間戳寫入 config——
是**時刻**不是日期概念值（不取當日 00:00）。效果：啟用前的一切訊息
（含掃描層 cutover 07-10 以來的積累）都不自動擷取，首日行為是「從啟用
那一刻起的新訊息才進管線」，零一次性批量、完全可預期。「掃描層歷史
底線」（07-10）與「episode 擷取底線」語義自此分開，不互相綁架。

### 5.2 44 個歷史 session 在新判準下的行為推演

| 情境 | 行為 |
|---|---|
| `ended_at=None`、episode_cutover 後**無**新訊息（多數） | eligible 為空 → 永遠不產生 episode、不進管線。**44 筆不會批次湧入** |
| `ended_at=None`、cutover 後**有**新訊息（復用中） | 等 trigger（inactivity 門檻到、或 manual checkpoint）→ 切出**只含新訊息**的 episode 1 |
| `ended_at` 已設且在掃描範圍（既有路徑曾經處理過的 20 筆） | legacy 列已存在者照舊（touch-only）；episode 判準下若 cutover 後還有新訊息（罕見：ended 後復活）才會多切 episode |
| 全新 session（cutover 後才開始） | 全部訊息 >= cutover，行為即「正常 episode 生命週期」 |

### 5.3 watermark 與 inactivity 的結構性衝突（設計發現，必須明文）

既有 scan watermark 過濾的是 `ended_at >= since`。episode 偵測**不能**
沿用「活動時間 >= watermark 才檢查」的過濾：inactivity trigger 恰恰在
「最近**沒有**活動」時才成立——上次 scan 時活動未達門檻的 session，
這次 scan 時其活動時間已 < watermark，會被過濾掉而永遠切不了刀。

**建議**：episode 偵測的候選過濾只用 **episode_cutover 政策底線**
（session 的最後活動時間 >= episode_cutover 即列入檢查），逐 session 以
cursor 比對決定有無新訊息——64 個量級的 session 數，每日一次全查
無效能問題；正確性交給 cursor＋event_id 去重，不靠 watermark。既有
watermark 保留原職（ended_at 掃描路徑的進度），**不延伸進 episode 語義**。
adapter 需加一個輕量查詢（如 `list_session_activity()`：per-session
max(rowid)、max(timestamp)，read-only、snapshot 模式照舊），避免為了
偵測而 export 全部訊息。

---

## 6. Trigger 三型與參數（提案七項之四／額外設計點）

| trigger | 條件（皆須 eligible 非空） | 語義 |
|---|---|---|
| `ended` | `ended_at` 已設（且 >= episode_cutover） | session 被明確結束——立即切刀，不等 inactivity |
| `inactivity` | `ended_at` NULL 且 `now - last_message_ts >= inactivity_hours` | 「暫時告一段落」的 checkpoint。session 之後復活＝正常，新訊息屬下一 episode |
| `manual` | 人工執行 checkpoint 指令 | 立即切刀，無視門檻（例如「這段討論很重要，現在就擷取」） |

- 參數位置：`hermes/config/bridge.yaml` 的 `episodes.inactivity_hours`
  （**已拍板 72**，§0.1——比「過幾天拿出來繼續用」的節奏保守；切早了也
  無資料損失，只是同一主題分成兩個 episode，consolidation 會再整併）。
- **與 session 復用的相容性（明文）**：inactivity 切刀後 session 又活過來
  ——已切的 episode immutable、內容與判定不變；新訊息累積、等下一次
  trigger 成為 episode N+1。**這正是 trigger 與擷取語義解耦的價值**：
  否決案（inactivity＝completed）會把復用中的 session 誤判完結並整包
  匯入，本設計裡 inactivity 只是「切一刀」的時機，session 永遠不被
  宣告死亡。
- **manual checkpoint 介面雛形**（CLI 子指令，不實作）：

  ```
  python3 hermes/bridge_scanner.py checkpoint <session_id> [--dry-run] \
      [--bridge-db PATH] [--state-db PATH] [--config PATH]
  ```

  行為＝對單一 session 走同一條 create_episode 路徑（trigger=manual）；
  eligible 為空時明確回報「無新訊息、不切」（exit 0，非錯誤）；
  --dry-run 零寫入。只建 discovered episode 列，匯入仍由 importer 執行
  ——scan 與 import 的職責分離不因 manual 而破例。

**daily 08:05 scanner 排程的影響**：scan 職責擴充為「既有 ended_at 掃描
（照舊）＋ episode 偵測（`episodes.enabled: true` 時）」，同一次 oneshot
執行、無參數呼叫不變，systemd unit 檔**不需要改**（`test_systemd_units.py`
的「排程一律無參數 scan」守則繼續成立）。episode 偵測只產生 discovered
episode 列——**不落地、不匯入**，與現行「偵測→discovered 全自動；
discovered→inbox 人工執行 importer」的邊界一致。

**N-gate／consolidate-memory 下游**：inbox frontmatter 帶 `episode` 與
`capture_trigger` 欄（第 2 節），供 N-gate 的 orientation read 與
consolidation 把同 session 多 episode 關聯起來；但 consolidate-memory
**不需要理解 episode 概念**——每個 inbox 檔仍是獨立的待整併單位，
frontmatter 是 additive 輔助資訊（memory-taxonomy §5 的既有定位）。
唯一下游語義變化：「同 session_id 多個 inbox 檔」從異常變成合法，
reconcile 與 N-gate 計數天然以檔案為單位，無需改動。

### 6.1 Profile 邊界：本階段 default-only，fail-closed（blocker 修訂）

**本階段不做多 profile 支援**，但邊界必須 fail-closed 而非未定義：

- `bridge_cursors` 主鍵是 `(source_profile, session_id)`（§1.2）——即使
  未來兩個 profile 出現同名 session，cursor 也結構性不可能混用。
- **scanner**：episode 偵測只在 `--source-profile default`（含預設值）下
  執行；帶其他 profile 呼叫且 `episodes.enabled: true` 時 **fail loud
  （exit 1）**，明示「episode capture 本階段僅支援 default profile」——
  因為現行 event_id namespace 無 profile 段，非 default 的 episode 會
  寫出與 default 無法區分的 event_id（namespace 污染），寧可拒跑。
- **importer**：佇列中遇到 `source_profile != 'default'` 的 episode 列
  （理論上不該存在，防禦性處理）→ 回報動作
  `unsupported_profile_fail_closed`、**記錄並跳過**：不改狀態、不動
  cursor、不落地——絕不默默用 default 的 cursor 或混寫 namespace。
- **reconcile**：cursor 重建（§3.2）寫入的 key 帶 source_profile；對非
  default 來源的檔案（未來才可能出現）同樣記錄並跳過。
- **未來擴充路徑已預留**（§2）：`hermes/<profile>:<sid>:<first>..<last>`
  ——屆時只需啟用新 namespace＋放行檢查，不需要 migration 既有
  default 資料（裸 `hermes:` 恆等於 default，向後相容）。

| # | 測試 | 驗證點 |
|---|---|---|
| 1 | 多 episode 生成 | 同一 session 連續兩輪「新訊息→trigger」產生 ep1、ep2；boundary 相接不重疊不跳漏；episode_seq 遞增 |
| 2 | cursor 前進不回退 | create_episode 後 cursor＝last；重跑 scan 不動 cursor；人工對舊區間操作不能把 cursor 拉回 |
| 3 | cursor 與 episode 列的原子性 | create_episode 中途失敗（注入例外）→ 無 episode 列且 cursor 未動 |
| 4 | 復活 session | inactivity 切 ep1 後注入新訊息 → ep1 列與 inbox 檔完全不變；新訊息全部且只屬 ep2 |
| 5 | 敏感／乾淨 episode 混合 | ep1 敏感 → needs_review、不落地、只記類別標籤；ep2 乾淨 → 獨立放行落地 to_inbox；ep1 判定不變 |
| 6 | 敏感 episode 的 cursor 不回收 | ep1 needs_review 後 cursor 仍在 ep1.last；ep2 不含 ep1 訊息 |
| 7 | pre-cutover 不湧入 | 造 44-session 情境（ended_at=None、訊息全在 episode_cutover 前）→ scan 零 episode；cutover 前後訊息混合的 session → episode 只含 cutover 後訊息 |
| 8 | cursor 遺失的 fail-closed 防護 | 刪 bridge_cursors 後直接 scan：對「已有 `_ep` 落地檔」的 session **拒切並回報請先 reconcile**（不切出重疊 boundary）；對從無落地檔的 session 依 episode_cutover 正常首切 |
| 9 | inbox 檔名 deterministic | 同一 episode 任何時間重跑 → 同一檔名；檔名可由 boundary 純函式重算；不含匯入時間 |
| 10 | 重跑冪等（scanner 層） | 同窗口重掃：既有 episode 列只 touch_last_seen，狀態／decision_reason 不變；不重複 create_episode |
| 11 | 重跑冪等（importer 層） | 已 to_inbox 的 episode 重進佇列 → InboxAlreadyImportedError 路徑、DB 不動；episode needle 精確到 boundary |
| 12 | legacy 檔不誤擋 episode | inbox 有 `hermes_session_<sid>.md`（legacy）時，`<sid>_ep..` 的新 episode 仍可落地；反向亦然 |
| 13 | migration 冪等 | 對 2.4c 版 db（17 欄＋3 筆模擬記錄）跑 migrate 兩次：5 個新欄補齊、legacy 回填 `capture_trigger='legacy'`（boundary／hash 欄維持 NULL）、imported 筆 cursor 種子正確、第二次 no-op |
| 14 | migration 後舊 API 不回歸 | 既有 bridge_state 全套測試對 migrate 後 db 全綠（legacy 列語義不變） |
| 15 | trigger：ended | ended_at 設定即切（不等 inactivity）；ended 但無新訊息 → 不切空 episode |
| 16 | trigger：inactivity | 未達 inactivity_hours 不切；達門檻切；ended_at NULL 前提 |
| 17 | trigger：manual | checkpoint 立即切、無視門檻；無新訊息回報不切（exit 0）；--dry-run 零寫入 |
| 18 | reconcile episode-aware | .processed/ 的 episode 檔 → 回填對應 episode 列 imported；frontmatter event_id_range 優先、檔名 ep 捕獲組次之、無 ep 段回填 legacy |
| 19 | config gate | `episodes.enabled=false`／區塊缺失 → scanner 行為與 2.4c 位元級一致；enabled 但 episode_cutover 缺失 → fail loud exit 1 |
| 20 | 靜態守則不回歸 | 排程一律無參數 scan（systemd 測試）；scanner／importer 不 import sqlite3 直連 Hermes；read-only／snapshot 慣例 |

---

## 8. 分階段 rollout（提案七項之七）

| 階段 | 內容 | 邊界 | 驗證點 |
|---|---|---|---|
| **2.4d-1** schema＋repository | registry v2 yaml；bridge_state.py：加欄 DDL、bridge_cursors、`create_episode`（原子）、episode event_id helpers、`migrate` CLI | 只動 schema 與 repository；scanner／importer 行為零變化 | schema-程式對齊測試、矩陣 #2/#3/#13/#14 全綠；既有 10+25 測試零回歸 |
| **2.4d-2** scanner episode 偵測 | adapter `list_session_activity()`；scan 加 episode 偵測（config gate 後面）；`checkpoint` 子指令；bridge.yaml 加 episodes 區塊（enabled: false） | 只產 discovered episode 列，不落地；enabled=false 下位元級舊行為；systemd unit 不動 | 矩陣 #1/#7/#10/#15–17/#19/#20；dry-run 輸出逐筆可審 |
| **2.4d-3** importer episode 化 | adapter range export（boundary 內訊息）＋episode 檔名／frontmatter；importer 佇列改逐 episode；`_find_existing_import` episode-aware；reconcile ep 捕獲組 | importer 仍純手動執行；敏感 fail-closed／落地順序（先檔案後狀態）逐條沿用 | 矩陣 #4–6/#8/#9/#11/#12/#18；2.4c 的 25 tests 對 legacy 路徑零回歸 |
| **2.4d-4** 部署 migration＋驗證 | sync 下發 → `migrate`（對既有 3 筆）→ dry-run scan 逐筆檢視（預期零湧入）→ 翻 `episodes.enabled: true` → 一次真實 scan → 人工跑一次 importer → 冪等重跑 → fingerprint 檢查（Hermes state.db／jobs.db／inbox 禁區零寫入） | 比照 2.4b 部署劇本：**全部驗證過了才翻 enabled** | 3 筆 legacy 原樣＋cursor 種子正確；首日零 episode（episode_cutover=部署日）或僅預期中的新活動 |

**全程維持「不啟用 importer 排程、不啟用 headless CoS」的方式**：

- 不新增任何 systemd unit／timer（importer 沒有 unit 檔可 enable）；
  `test_systemd_units.py` 繼續靜態把關「只有 scanner timer、一律無參數 scan」；
- importer 只能人工 CLI 執行（現狀）；episode 偵測只到 discovered 為止，
  discovered 堆著不會自己前進——這就是邊界本身；
- 本提案不含任何 enqueue／invoke_cos 呼叫點；roadmap 上「enqueue 給
  headless CoS」維持後續階段，屆時另案設計。

---

## 9. 最大設計風險與開放問題

**風險（依嚴重度排序）**：

1. **already-imported 查重的範圍重定義**（第 2 節陷阱）：`hermes_session_<sid>`
   子字串比對散在 adapter 與 scanner 兩處，episode 化後任何一處漏改都會
   造成「ep1 落地後整個 session 失聯」或反向的重複落地——矩陣 #11/#12
   是為此而設，實作時建議把「查重 needle 產生」收斂成單一 helper。
2. **watermark／inactivity 結構性衝突**（§5.3）：若沿用 watermark 過濾
   episode 候選，inactivity trigger 會系統性漏切且極難察覺（沒有錯誤、
   只是永遠不發生）。設計上已用「cutover 底線＋逐 session cursor 比對」
   繞開，但這改變了 scan 的成本模型（每日全查活躍 session），session
   數量級大幅成長時要重新評估。
3. **cursor 原子性依賴單一 transaction**：create_episode 的「列＋cursor
   同動」若被未來改動拆開（例如 cursor 移到別的儲存），「每則訊息至多
   屬一個 episode」的不變量即失效——建議在 repository docstring 與測試
   （矩陣 #3）雙重釘死。
4. **rowid 與 active flag 的來源假設**：boundary 基於 messages.rowid 單調
   遞增與 `active=1` 過濾。若 Hermes 未來 compact 機制把舊訊息 active
   翻轉或重寫 rowid，已切 episode 不受影響（immutable），但 cursor 之前
   「後來才變 active」的訊息會永久漏擷——接受此限制並明文記錄
   （原文永在 state.db，可人工補匯），不設計自動回補。

**開放問題（待使用者拍板）**：

1. `episode_cutover` 取 **2.4d 部署日**（本提案建議，最保守）還是沿用
   07-10 掃描 cutover（部署首刀會包含 07-10 起的積累訊息）？
2. `inactivity_hours` 預設 72 是否符合實際節奏？（切早無資料損失，
   只影響 episode 粒度。）
3. 方案 A 的 legacy 列與 episode 列同表共存，是否接受？（若使用者強烈
   偏好表職責潔癖，方案 B 成本已列於 §1.1。）
4. `ended` trigger 是否需要對 `ended_at < episode_cutover` 但 cutover 後
   有新訊息的邊角（ended 後復活的舊 session）特別處理？本提案立場：
   統一規則已涵蓋（eligible 只看訊息，trigger 只看時機），不加特例。
5. manual checkpoint 是否需要順手觸發一次 importer（一條龍）？本提案
   立場：不要——scan／import 職責分離是既有邊界，checkpoint 只切刀。

---

## 10. 本提案未動的東西（明文重申）

- 未修改任何既有檔案（schema yaml、bridge_state.py、bridge_scanner.py、
  bridge_importer.py、adapter.py、bridge.yaml、systemd units、任何文件）。
- 未新增／啟用任何排程；未接 headless CoS；未 commit。
- 部署側 bridge_state.db 的 3 筆記錄原樣未碰。
