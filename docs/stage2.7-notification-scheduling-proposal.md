# Stage 2.7 — Notification & Scheduling（設計提案 v2）

日期：2026-07-18　狀態：**v2——九項開放問題已全數拍板（2026-07-18，皆採
建議值）；2.7a／2.7b／2.7c 全階段實作、部署與驗收皆已完成，Stage 2.7
全階段 ✅ 完成並關閉（2026-07-18）**
負責規劃：`planning` domain
負責領域（實作階段）：`engineering`（notifier 程式碼、schema、測試）；
`automation`（timer 頻率／觸發時機決策與 unit 檔——2.6 提案 §12 明文
「排程化與 Slack 投遞已拍板列 Stage 2.7——automation 到 2.7 才進場」，
本階段是 automation 首次實質進場）。

依賴文件（本次規劃已逐一查讀實際狀態，不猜測既有機制的行為）：
[stage2.6-domain-dispatch-proposal.md](stage2.6-domain-dispatch-proposal.md)
（特別是 §2 選項 (a) 拍板、§7 Slack 延至 2.7、§12 automation 進場時點、
§15.3 成本基準 $0.846、§15.6 遺留事項）、
[stage2.5-episode-triage-proposal.md](stage2.5-episode-triage-proposal.md)
（§20.3 遺留：needs_review 觸發率監測）、
[hermes-integration-roadmap.md](hermes-integration-roadmap.md)（優先順序節：
2.7 定位與 Stage 3 的關係、決策依據＝成本基準與 needs_review 觸發率）、
[deployment-sync-plan.md](deployment-sync-plan.md)（部署側＝WSL、
systemd user units、sync 慣例）、
`hermes/db.py`（jobs／dispatch 兩層表、`enqueue_once`、
`list_undelivered_completed`／`mark_delivered`、`mark_failed` 的
dead_letter 語意）、`hermes/worker.py`（常駐 daemon、source routing）、
`hermes/bridge_dispatch.py`（list/approve/reject/resume-approved/status＋
decision 計數）、`hermes/bridge_triage_enqueuer.py`（`--event-id`、
enqueue_once 三分支）、`hermes/systemd/`（既有 timer 樣式與時刻表）。

## 版本標記

- **v1** ＝本文件的第一個正式版本（2026-07-18 落地），Stage 2.7 的規劃
  初稿。2.6 提案 §7 已拍板「Slack 投遞 2.6 不做、列 Stage 2.7；頻道
  對應留待 2.7 規劃時由使用者指定」，§12 拍板「排程化與 Slack 投遞列
  2.7、automation 到 2.7 才進場」——本文件就是那個「另案規劃」。
- **v2** ＝本文件（同日修訂）：使用者對 §11 九項開放問題**全部採納
  建議值並拍板**，各設計節的「建議」措辭就地轉為「已拍板」的確定式，
  §11 原文保留為決策紀錄；另依使用者同意把「scanner systemd unit 註解
  過時」的順手更正明文列入 2.7b 範圍（§9），不另開待辦。完整差異見
  下方「版本差異（v1→v2）」簡表。

## 版本差異（v1→v2）

| 面向 | v1 | v2（本文件） |
|---|---|---|
| §11 九項開放問題 | 逐項附建議值，待使用者拍板 | **全數拍板（2026-07-18），皆採建議值**；逐項就地標註 ✅，原文保留為決策紀錄 |
| §2 通知機制 | 「建議選 (a) 獨立 notifier」「建議雙層冪等」 | **已拍板：獨立 notifier＋notification_log 新表為權威、send ledger 為第二層（§11 第 8 題）為定案設計** |
| §3 事件清單／summary／頻道 | 建議清單＋anomaly 評估項、summary 截斷「留待拍板」、頻道「本提案不預設拍板結果」 | **已拍板**：五種事件＋anomaly 全納入；summary 納入、截斷 200 字元＋標註；頻道＝單一 `#agentos`（C0BHE9NFW0P），驗收先用測試頻道 C0BHZC2EG84 |
| §4 排程參數／頻率／meta-monitoring | 建議 08:15／08:25 每日一次、`--max-new 5`、「建議 v1 不做 meta-monitoring」 | 全數轉為定案值（§11 第 4、5、6 題） |
| §6.1 cron prompt 無聲失敗歸屬 | 「建議列 Hermes 側待辦」 | **已拍板：列 Hermes 側待辦，不進 2.7 DoD（§11 第 7 題）** |
| §9 2.7b 範圍 | 未含 scanner unit 註解更正（調查發現另行回報） | **明文列入 2.7b 範圍**（使用者同意動 systemd 目錄時一併更正，不另開待辦） |
| §13 start blocker | 軟前置含「§11 開放問題拍板」 | 拍板已完成——軟前置只剩 allowlist 佈建（2.7c 部署動作）；硬 blocker 候選（WSL 側 `hermes send` 實測）不變 |
| 條件式完成標準 | 部分節帶「待拍板」「留待拍板」措辭 | 全文清除條件式措辭（沿用 2.5/2.6 規劃的教訓：不留條件式完成標準；「待拍板」字樣僅存在於 §11 原文決策紀錄與版本對照） |

---

## 0. 定位與範圍邊界（本提案最重要的一節，後續所有設計都從這裡導出）

**一句話定位**：Stage 2.5／2.6 建成了「episode → triage → 人工核准 →
domain 執行」的完整管線，但全程（除 scanner）人工觸發、人工檢視；
**Stage 2.7 = (A) 把管線生命週期中「需要人知道」的事件主動投遞到
Slack + (B) 把管線的 triage 段（scanner → importer → enqueuer）排程化，
讓「產生待核准候選」這件事無人值守地發生**。人不再需要主動輪詢 CLI 才
知道「有候選等你核准」——這是 2.6 §7 拍板時點名的報酬時點：「通知的
價值要到『執行是非同步發生、人不在場』時才出現——那是排程化之後的
需求」。(A) 與 (B) 因此是同一個 stage 的一體兩面，不拆成兩個 stage。

### 0.1 鐵律（一條，凌駕本提案所有其他設計）

**dispatch 的人工核准 gate 不可被排程化繞過。**排程化的終點是「候選
產生並通知」；`approve`／`reject`／`resume-approved` 以及任何會建立
`bridge_domain_dispatch` job 的路徑，**維持 100% 人工 CLI 觸發**。這是
2.6 §2 拍板的選項 (a) 的核心安全結構——「人」是未信任 episode 內容與
有工具執行環境之間的結構性 gate——2.7 不放寬、不開任何例外，包括
「低風險自動核准」（2.6 §2.3 已否決的 (c)）在本階段同樣不重議。
排程化讓 gate **之前**的一切自動化；gate 本身與 gate **之後**的觸發，
一律人工。

### 0.2 Stage 2.7 擁有的範圍

1. **通知（A）**：一個新的 notifier 元件，掃描 `jobs.db`（jobs 表＋
   dispatch_records）中值得通知的生命週期事件（§3 清單），經
   **hermes-agent 的 `hermes send` CLI** 投遞到 Slack（§5 介面邊界），
   冪等（同一事件恰好通知一次，§2.3）。
2. **排程化（B）**：importer 與 enqueuer 的自動觸發（scanner 已有
   08:05 timer，2.4b 起既有），使 to_inbox episode 無人值守地變成
   triage job；worker 常駐既有，triage job 因此自動執行——排程化的
   淨效果是「候選自動出現」。含成本上限防護（§4.4）。
3. **notifier 自身的排程**：通知也是無人值守發生的（否則又回到人工
   輪詢）。
4. **驗收**：低量真實驗收（頻道收到通知、重跑零重複、鐵律抽查——
   全程零未經核准的 dispatch）。

### 0.3 Stage 2.7 明確不做的範圍（逐條附理由）

1. **不自動 approve、不自動 dispatch、不自動 requeue**——§0.1 鐵律。
   dead_letter 的唯一重跑路徑維持人工 `python3 hermes/db.py requeue`
   （2.5 §4.3／2.6 §8 既有設計）。
2. **不排程 `bridge_dispatch.py list`**——list 會寫入（冪等登記
   dispatch_records），且 `--actor` 語意是「操作者身份」；notifier 對
   候選的偵測改用唯讀掃描（§2.4），dispatch CLI 全套維持人工。理由：
   維持「dispatch_records 的每一列都對應一次人工操作序列」的稽核語意
   單純性；候選通知不需要先登記 record 也能做到。
3. **不深入 Hermes 內部**——Slack 投遞能力在 hermes-agent repo（不在
   本 repo），2.7 的介面就是 `hermes send` CLI（§5）；本 repo 不
   import hermes-agent 的模組、不碰它的 config／ledger 實作、不做任何
   Slack API 直呼。
4. **不做雙向互動**——通知是 outbound-only（hermes send 本身也是
   outbound-only）；「在 Slack 按鈕核准」之類的互動式核准明確不做
   （那會把核准 gate 搬進第三方介面，攻擊面與稽核鏈都要重新設計，
   量體也不支撐）。
5. **不排程 reconcile**——維持 2.4b 以來「reconcile 是回填/對帳工具、
   人工觸發」的既有決定（roadmap 明文，2.5 規劃時重新查證過仍成立）。
6. **不做 cron/platform 唯讀橋接**——2.6 §15.6 第 2 項的未排程構想，
   本階段不納入（理由見 §6.3）。
7. **不修改 worker**——延續 2.6 「零 worker 變更＝零回歸面」的慣例；
   通知不做成 worker 內的事後 hook（否決理由見 §2.2）。
8. **不做 dashboard／告警系統**——needs_review 觸發率監測維持 2.6b 的
   CLI 計數＋本階段的逐筆通知（§6.2），不建面板。觀測性面板屬
   Stage 3。
9. **不動 telegram 投遞路徑**——`list_undelivered_completed`／
   `mark_delivered` 既有機制與 telegram adapter 消費行為零改動。

---

## 1. 現況盤點（2026-07-18，對照實際程式碼與部署狀態，不猜測）

### 1.1 管線各段的觸發方式現況

| 段 | 元件 | 觸發方式 | 冪等保證 |
|---|---|---|---|
| episode 偵測 | `bridge_scanner.py scan` | **已排程**：`hermes-bridge-scanner.timer` 每日 08:05、`Persistent=true` | watermark 只前進；失敗不推進、下次補掃 |
| inbox 落地 | `bridge_importer.py import` | **人工 CLI**（2.4c 起維持） | episode-aware 查重、檔案已存在由 reconcile 回填 |
| triage enqueue | `bridge_triage_enqueuer.py enqueue` | **人工 CLI**（2.5b） | `enqueue_once` 三元組唯一索引；hash 漂移 fail-closed |
| triage 執行 | `worker.py`（常駐）→ `invoke_cos_triage.sh` | **自動**（job 一進 queue 就會被 claim） | job 狀態機；`max_attempts=1` |
| 候選登記／核准／派工 | `bridge_dispatch.py` | **人工 CLI**（2.6，鐵律） | 雙層冪等（UNIQUE record＋enqueue_once） |
| dispatch 執行 | worker 既有 else 分支 → `invoke_cos.sh` | **自動**（核准後） | 同上；`max_attempts=1` |

**關鍵觀察**：worker 是常駐 daemon——所以 (B) 只要把 importer＋enqueuer
排程化，「to_inbox → 候選出現」整段就無人值守打通了；**不需要**為
triage 執行本身新增任何排程。缺的只是中間兩個人工環節與「事後沒人
知道」的通知缺口。

### 1.2 既有 timer 時刻表（部署側 WSL，systemd user units）

08:00 `hermes-cron-daily-memory-check`（N-gate 整併）→ 08:05
`hermes-bridge-scanner`（episode 偵測）→ 08:10 `hermes-bridge`
（skill-sync）；另有 `hermes-rss.timer` 每 30 分鐘。既有慣例：避開
整點、相鄰 timer 隔 5 分鐘、oneshot＋不設 Restart、`Persistent=true`。

**WSL on-demand 的誠實前提**（scanner timer 註解明文）：目前 WSL 不是
always-on——多數情況「每日 08:05」實際上是下次手動喚醒 WSL 時的
catch-up 補跑。**2.7 的排程化繼承同一前提**：它交付的是「WSL 醒著或
被喚醒時，管線自動走完並通知」，不是 7×24 即時性。這不是本階段要
解決的問題（要解決得改變 WSL 運行模式，屬另案的部署決策）。

### 1.3 Slack 投遞能力現況（跨 repo，2026-07-17 已佈建並實測）

- 能力在 **hermes-agent repo**（`%LOCALAPPDATA%\hermes\hermes-agent`），
  不在本 repo。可用介面：
  `hermes send -t slack:<channel> --message-key <key>`。
- 已具備：SQLite send ledger（message-key 冪等——同 key 重送 no-op）、
  per-profile 頻道 allowlist **fail-closed**（不在名單內的頻道直接
  拒送）、outbound-only。
- 已知頻道：`#agentos`（`C0BHE9NFW0P`，default／codereviewer 的
  allowlist 已含）、`#ai-news`（`C0BHG2195BL`，07-18 已實測收到 cron
  投遞）、`#ai-chainresearch`、`#research`、`#codingreport`、
  `#intelligence`、測試頻道 `C0BHZC2EG84`。
- **未驗證事實（誠實標註，§13 start blocker）**：`hermes send` 至今的
  佈建與實測都在 **Windows 側**；notifier 照本提案定案跑在 WSL
  部署側（systemd timer），「WSL 內能否直接呼叫 `hermes send` 並成功
  投遞」尚未實測——這是本提案唯一的硬 start blocker 候選。

### 1.4 本 repo 既有可複用機制（2.7 不重造）

| 機制 | 出處 | 2.7 的複用方式 |
|---|---|---|
| `delivered_at`／`list_undelivered_completed`／`mark_delivered` | `hermes/db.py`（泛用於任何 source，目前只有 telegram 消費） | 2.6 §7 點名的「2.7 機制起點參考」——但只涵蓋 completed job，通知事件面更廣，v1 不直接沿用（§2.3 決策） |
| `scan_triage_results()` 唯讀掃描＋防禦性 parse | `bridge_dispatch.py`（`PRAGMA query_only=ON`） | notifier 的候選／needs_review／異常偵測直接複用同一函式（單一實作，不寫第二份 parse） |
| decision 累計計數 | `bridge_dispatch.py list`（2.6b，§5.4 觸發率監測） | 觸發率監測的既有落地；2.7 只補「出現即通知」（§6.2） |
| append-only 稽核／冪等表慣例 | `job_requeue_events`、`dispatch_events`、`_migrate_schema` | `notification_log` 表的設計樣板（§2.3） |
| oneshot service＋timer＋靜態測試 | `hermes/systemd/`＋`test_systemd_units.py` | 新 timer units 照既有樣式；靜態測試守住「排程一律無範圍參數」類的規則 |
| `sync_to_wsl.sh` 部署慣例 | `scripts/`＋deployment-sync-plan.md | 下發與部署驗證照舊 |

### 1.5 成本與量體基準（2.6d 建立，roadmap 明文為 2.7 決策依據）

- triage 單筆 ~$0.06–0.12；dispatch 單筆 $0.846（headless CoS＋domain
  subagent 全鏈）。
- 現況 decision 分布：memory_only=5、action_candidate=2、
  needs_review=0、異常=0。
- 量體極小、episode 產生速率低（個位數／週的量級）——這支持 (B) 採
  **每日一次**的保守頻率與低成本上限（§4.4），也支持通知走「批次
  掃描」而非即時 hook。

---

## 2. 核心決策一：通知的觸發機制（A）（✅ 已拍板 2026-07-18：選項 (a) 獨立 notifier）

### 2.1 三個候選選項

| | (a) 獨立 notifier 掃描器（**已拍板採用**） | (b) worker／CLI 事後 hook | (c) 只沿用 `delivered_at` 投遞模式 |
|---|---|---|---|
| 形狀 | 新增 `hermes/bridge_notifier.py`：oneshot 唯讀掃描 jobs.db → 比對 `notification_log` → 逐筆呼叫 `hermes send` → 記錄已通知 | worker `process_job` 完成／死信時、或 dispatch CLI 操作後，行內直接送 Slack | 擴充 `list_undelivered_completed` 消費端：completed 且未 delivered 的 job 投 Slack 後 `mark_delivered` |
| 涵蓋面 | 任何 DB 可見事件（候選、needs_review、completed、dead_letter…） | 只有被 hook 的程式路徑；reaper 死信、人工 DB 操作等旁路漏接 | **只有 completed job**——待核准候選、needs_review、dead_letter 都不是 completed job，結構性涵蓋不到 |
| 對既有元件的侵入 | 零（worker 零改動、CLI 零改動） | 改 worker（違反零回歸慣例）＋通知失敗會污染 job 執行路徑 | 改動小，但 `delivered_at` 語意會被兩種消費者（telegram／Slack）競爭 |
| Slack 不可用時 | notifier 失敗，管線完全不受影響；下次重掃補送 | job 執行與通知耦合——Slack 掛了要決定 job 算不算成功 | 投遞層重試，尚可 |
| 冪等 | notification_log＋message-key 雙層（§2.3） | 需另建；hook 重入語意複雜 | delivered_at 單層 |

### 2.2 決定：選 (a)（✅ 使用者已拍板 2026-07-18；以下為決策理由紀錄）

1. **涵蓋面是硬需求**：§3 的事件清單裡最有價值的兩種——「新候選待
   核准」與「dead_letter」——都不是 completed job，(c) 結構性做不到；
   (b) 漏接 reaper 路徑（`reap_stale_jobs` 直接把 running 改
   dead_letter，不經 `process_job` 的失敗分支）。
2. **通知必須是 best-effort 旁路，不能反向影響管線**：(b) 把第三方
   服務可用性（Slack／gateway）耦合進 job 執行路徑，違反「管線正確性
   不依賴通知」的基本立場；(a) 天然解耦。
3. **零 worker 變更**：2.6 用測試斷言守住的「worker.py 零改動」慣例
   在 (a) 下繼續成立。
4. **與量體相稱**：事件量極低（§1.5），批次掃描的延遲（最壞一個
   排程週期）完全可接受；即時性不是本階段需求（§1.2 WSL on-demand
   前提下也做不到真即時）。

**被否決的替代方案**：(b)、(c) 理由如上表。(c) 的 `delivered_at` 不是
完全棄用——未來若要做「dispatch job 結果全文投遞」（本階段只通知
「完成了」不投全文，§3.3、§11 第 9 題已拍板），屆時再評估以
`delivered_at` 模式承載，本階段不動它。

### 2.3 通知冪等：`notification_log` 表＋message-key 雙層（✅ 已拍板，§11 第 8 題）

沿用 2.6 §5.3 的雙層冪等精神：

- **第一層（本 repo，權威）**：`jobs.db` 新表 `notification_log`，
  `_migrate_schema` 冪等新增：

```sql
CREATE TABLE IF NOT EXISTS notification_log (
    message_key  TEXT NOT NULL UNIQUE,  -- 冪等錨點（§2.4 決定性設計）
    event_type   TEXT NOT NULL,         -- §3 事件類型 enum
    subject_id   TEXT NOT NULL,         -- job_id / triage_job_id / event_id
    channel      TEXT NOT NULL,         -- 實際投遞頻道
    sent_at      TEXT NOT NULL,
    send_result  TEXT                   -- hermes send 的回覆摘要（稽核用）
);
```

  只 INSERT，無 UPDATE/DELETE（append-only 慣例）。**送成功才寫入**
  ——send 失敗不落表，下次重掃自然重試；「送成功但寫入前 crash」的
  縫隙由第二層兜住。
- **第二層（hermes-agent，既有）**：`hermes send --message-key` 的
  SQLite ledger——同 key 重送 no-op。即使第一層縫隙發生，Slack 端也
  恰好一則。
- **被否決的替代方案**：只靠 hermes send ledger、本 repo 無狀態。
  否決理由：(i) 冪等真相落在另一個 repo 的內部實作，本 repo 無法
  查詢「哪些已通知」來做觀測與測試斷言；(ii) 無狀態重掃意味每次都對
  全部歷史事件呼叫一次 send CLI（靠遠端 no-op），事件累積後是無謂的
  子程序開銷；(iii) 本 repo 既有慣例就是自己的表自己稽核。

### 2.4 message-key 設計（決定性、可重建）

`agentos27:<event_type>:<subject_id>`，全小寫、無時間戳——同一事件
不論 notifier 重跑幾次，key 恆同。各事件的 subject_id 取「該事件的
自然主鍵」：

| event_type | subject_id | 唯一性依據 |
|---|---|---|
| `candidate_pending` | triage job_id | 一筆 triage 至多成為候選一次（superseded 換基準時是新 job_id → 新通知，正確：那是新的待核准事實） |
| `needs_review` | triage job_id | 同上 |
| `triage_dead_letter` | job_id | job 唯一 |
| `dispatch_completed` | dispatch job_id | 同上 |
| `dispatch_dead_letter` | dispatch job_id | 同上 |
| `anomaly` | triage job_id | 同上 |

注意：dead_letter 經人工 requeue 後再次死信，job_id 不變 → 不會二次
通知。v1 接受（requeue 本來就是人工在場的操作，人已知情）；若未來
要每次死信都通知，key 可加 `:<attempts>`，列為演化不預做。

### 2.5 通知偵測來源：唯讀掃描，複用 `scan_triage_results()`

notifier 對「候選／needs_review／異常」的判定**直接呼叫**
`bridge_dispatch.scan_triage_results()`（唯讀、防禦性 parse、
superseded 判定都是現成的單一實作）；對 dispatch job 狀態則唯讀查
`jobs` 表（`source='bridge_domain_dispatch'` 且 status ∈
{completed, dead_letter}）＋ triage dead_letter 同法。notifier 全程
對 jobs 表唯讀（`PRAGMA query_only=ON` 慣例），唯一寫入是
`notification_log`。**「候選待核准」的通知不依賴 dispatch_records
是否已登記**——人工跑 list 與否不影響通知（§0.3 第 2 條的配套）。

---

## 3. 通知事件清單與內容格式（A）（✅ 已拍板 2026-07-18，§11 第 1、2、3、9 題）

### 3.1 通知的事件（✅ 已拍板，§11 第 2 題：照此清單辦理）

| 事件 | 為什麼值得通知 | 頻道（✅ 已拍板，§11 第 1 題） |
|---|---|---|
| `candidate_pending`：新 `action_candidate`（基準筆）出現且尚無 approved/rejected 決策 | 這是整個 (A) 的核心報酬——「有候選等你核准」，不通知就回到人工輪詢 | pipeline 事件頻道＝`#agentos` |
| `needs_review`：新 needs_review 出現 | 2.5 §20.3 遺留的觸發率監測的「事件面」；現況 0 筆，出現即值得看 | 同上 |
| `dispatch_completed`：dispatch job 完成 | 核准是人做的，人在等結果；含成本回報 | 同上 |
| `dispatch_dead_letter`：dispatch job 死信 | 需要人工介入（讀 log 評估副作用→requeue）；不通知就是無聲失敗 | 同上 |
| `triage_dead_letter`：triage job 死信 | 同上（排程化後 triage 無人在場，死信必須浮上來） | 同上 |

### 3.2 明確不通知（噪音過濾）＋anomaly（✅ 已拍板納入）

- **`memory_only`**：不通知。它是預設常態（現況 5/7），沒有後續動作，
  通知純噪音。
- **triage job completed（非候選）**：不通知，同上理由；分布統計看
  CLI 計數即可。
- **superseded／existing no-op／已 approve、reject 的人工操作本身**：
  不通知——人工 CLI 操作時人就在場。
- **`anomaly`（defensive parse 異常／stale record）**：
  **✅ 已拍板（§11 第 2 題）：v1 納入通知**，以單獨 event_type 標示。
  理由：fail-visible 紅旗理論上不該存在（現況 0），正是「沒人看 CLI
  就永遠沒人知道」的類型。

### 3.3 通知內容格式（防洩漏設計；summary 與 completed 內容 ✅ 已拍板，§11 第 3、9 題）

- **絕不包含 episode 全文或 artifact 內容**——與 2.6 §4.2「episode
  全文不入 prompt」同精神：未信任內容不進入第三方通道。Slack 訊息裡
  只放結構化欄位：event_type、event_id、job_id、decision、
  suggested_owner、成本（若有）、下一步指令提示（例如
  `python3 hermes/bridge_dispatch.py list --actor ...`）。
- **`summary` 的處置（✅ 已拍板）**：**納入，截斷至 200 字元**，並在
  訊息中標註「模型摘要，未經人審」。決策理由紀錄：summary 是模型從
  未信任內容產出的文字，但已是 2.5 zero-tools 輸出層的產物、繁體
  中文、且是人判斷「要不要現在去核准」的最有用資訊；被否決的替代
  方案（完全不含 summary，只給 id）更保守但通知可用性大減。
- **`dispatch_completed` 的內容（✅ 已拍板，§11 第 9 題）**：只通知
  「完成＋成本＋job_id」，**不投 `jobs.result` 全文**（result 是 CoS
  整合輸出，可能夾帶任務過程中讀到的內容；人工用 `status` 子指令看
  全文）。全文投遞列為未來演化（屆時評估 `delivered_at` 模式承載）。
- 訊息組裝是程式碼樣板（deterministic、快照測試把關），與 dispatch
  prompt 樣板同慣例。

### 3.4 頻道對應（✅ 已拍板 2026-07-18，§11 第 1 題——2.6 §7 留給使用者的值就此定案）

**全部 pipeline 事件進單一頻道 `#agentos`（`C0BHE9NFW0P`）；2.7c
驗收先用測試頻道 `C0BHZC2EG84`，通過後切正式頻道。**決策理由紀錄：
(i) 事件量極低，分頻道是過度設計；(ii) `#agentos` 已在 default
profile allowlist 內，零佈建成本；(iii) 依 domain 分頻道
（engineering→`#codingreport` 等）的前提是 dispatch 量體大到需要分流
——現況 2 筆。分頻道列為量體成長後的演化，本階段不設計。

---

## 4. 核心決策二：triage 段排程化設計（B）（✅ 參數與頻率已拍板 2026-07-18，§11 第 4、5、6 題）

### 4.1 排程的單位與順序：單一 oneshot「pipeline」service 串行跑 importer → enqueuer

三個候選：

| | (a) 單一新 oneshot unit 串行 importer→enqueuer（**已拍板採用**） | (b) importer、enqueuer 各自獨立 timer | (c) systemd 依賴鏈（scanner 的 OnSuccess=／Wants 串後續） |
|---|---|---|---|
| 形狀 | `hermes-bridge-pipeline.service`（oneshot：先 `bridge_importer.py import`，成功才 `bridge_triage_enqueuer.py enqueue`）＋ timer 每日 08:15 | 08:15 importer timer、08:25 enqueuer timer | 改 scanner unit 加 OnSuccess= 觸發 importer，再鏈 enqueuer |
| 順序保證 | 同一 process 內串行，硬保證 | 只靠時刻錯開；WSL catch-up 補跑順序**不保證**（Persistent 補跑無先後承諾） | systemd 層保證，但要改既有 scanner unit |
| 失敗語意 | importer 失敗 → enqueuer 不跑、unit failed 可觀測（單一失敗點） | 兩個獨立失敗點；enqueuer 可能在 importer 失敗當日照跑（無害但混亂） | 鏈中斷語意要逐 unit 推敲 |
| 對既有 unit 的侵入 | 零（scanner unit 不動） | 零 | **要改 2.4b 已上線的 scanner unit**——違反最小侵入 |

**決定：(a)（✅ 已拍板，§11 第 5 題含時刻）。**scanner（08:05）與
pipeline（08:15）之間不做硬依賴——理由：兩者本就冪等且解耦
（importer 吃的是 bridge_state 的 discovered 列，當日 scanner 若失敗，
importer 跑了也只是 no-op，隔天補上）；10 分鐘間隔沿用既有 5 分鐘
間隔慣例並留餘裕。catch-up 情境下兩個 timer 補跑順序即使顛倒，效果
只是「當日新 episode 延到下一輪才 enqueue」——冪等機制保證不漏不重
（§4.3）。

執行參數：importer 帶 `--limit 10`（單輪「實質結果」上限——落地／
needs_review／failed 等狀態轉換才計數；**2026-08-03 語義修正**：
too_short／duplicate 等 skip 類零成本判定不佔上限、每輪全量出清。
舊語義直接截斷佇列、把判定次數也掐在 10，佇列以「每日新進雜訊−10」
的速度發散（實測積壓 6283 筆）；limit 的本意是 inbox 洪水 fail-safe，
拍板值 10 不變）；enqueuer 帶 `--max-new 5`（§4.4，已拍板）。
排程一律不帶範圍／dry-run 參數，比照「排程一律無參數 scan」的既有
規則，並由 `test_systemd_units.py` 靜態測試守住。

### 4.2 notifier 的排程（✅ 已拍板：每日 08:25）

`hermes-bridge-notifier.service`＋timer，**每日 08:25**（pipeline 之後
10 分鐘：當日新候選在同一個早上批次內完成「產生→通知」）。notifier
也可隨時人工觸發（oneshot CLI 本體，比照全家族慣例）。

**為什麼不更高頻**（例如每 30 分鐘，比照 rss；決策理由紀錄）：WSL
on-demand 前提下高頻 timer 只會在喚醒時補跑一次，實際頻率沒有差異；
always-on 化之後若需要，調 OnCalendar 即可，不是設計變更。dispatch
job 完成通知因此最壞延遲一天——v1 接受（人工核准當下人本來就在場，
可自己跑 `status`；通知的主要價值在候選與死信）。

### 4.3 冪等論證（排程化不需要新機制，逐段對照）

排程化＝把既有人工 CLI 交給 timer 重複執行；每一段的重跑安全都已有
既有保證，2.7 **零新增冪等機制**：

1. scanner：watermark 只前進、失敗不推進（2.4a/2.4b 既有）。
2. importer：episode-aware 查重、檔案已存在→reconcile 回填路徑
   （2.4c/2.4d 既有）；重跑零重複落地（2.4d 驗收實測）。
3. enqueuer：`enqueue_once` 三元組唯一索引——created／existing no-op／
   conflict fail-closed（2.5a/2.5b 既有）；**conflict（hash 漂移）在
   排程模式下的浮現方式**＝exit 3＋stderr → unit failed 可觀測，
   加上 notifier 的 anomaly 通知（§3.2）。
4. triage 執行：job 狀態機＋`max_attempts=1`（2.5c 既有）。
5. 通知：`notification_log` UNIQUE＋send ledger 雙層（§2.3 新增，但
   樣板是既有慣例）。

### 4.4 成本上限防護（✅ 已拍板，§11 第 4 題：`--max-new 5`）

排程化前的成本結構是「每筆呼叫都是人工有意識的決定」（2.6 §10）；
排程化後 triage 呼叫改為自動發生，需要上限意識：

- **天然上限**：episode 產生速率（個位數／週）×triage 單筆
  ~$0.06–0.12——正常情境每日成本接近零。
- **防護（已拍板）**：enqueuer 新增 `--max-new N` 旗標（單次執行新建
  job 數上限，超過即停止並以非零 exit＋訊息浮現，剩餘候選下輪處理或
  人工介入），排程帶 `--max-new 5`。防的是異常情境（例如 scanner／
  importer 行為異常導致候選爆量、或大量歷史 episode 突然湧入），
  單日 triage 成本硬上限 ≈ 5×$0.12＝$0.60。
- **明確不做**：金額制預算追蹤（讀 cost_usd 加總對照預算）——量體不
  支撐這個複雜度，筆數上限已足夠；dispatch 段無自動成本（鐵律：
  無自動 dispatch＝無自動 $0.846）。

### 4.5 失敗可見性（✅ 已拍板，§11 第 6 題：v1 不做 meta-monitoring）

- 各 oneshot unit 失敗 → systemd failed 狀態可觀測
  （`systemctl --user list-units --failed`）＋ stderr 落
  `logs/hermes/*.log`——既有慣例。
- **notifier 能通知「DB 可見」的失敗**（dead_letter、anomaly），但
  **不能通知它自己或其他 unit 沒被執行**——「timer 沒跑」在 jobs.db
  裡沒有足跡，且 notifier 自己掛了誰通知 notifier 是遞迴問題。
- **已拍板（v1）**：接受此限制，不做 meta-monitoring（watchdog、
  heartbeat 到 Slack 等）。決策理由紀錄：WSL on-demand 前提下「今天
  沒跑」是常態不是異常，heartbeat 會天天誤報；使用者每次喚醒 WSL
  檢查 `list-timers`／`--failed` 是現行工作模式。若未來 WSL
  always-on，再評估 heartbeat（列演化，不預做）。

---

## 5. Slack 投遞介面（跨 repo 邊界的明文化）

- **唯一介面**：子程序呼叫
  `hermes send -t slack:<channel> --message-key <key> ...`。notifier
  把它當黑盒：exit code＋stdout 判定成敗；成功才寫 notification_log。
- **allowlist fail-closed 是特性不是障礙**：拍板頻道（測試頻道
  `C0BHZC2EG84`→正式 `#agentos`）在驗收前需人工加入對應 profile 的
  allowlist（部署側 config 操作，比照 07-17 `#ai-news` 的既有流程：
  備份 config → 加名單 → 受控 restart）。notifier 不嘗試繞過、不
  自行改 config。
- **Hermes／Slack 不可用時的行為**：send 失敗 → 該筆不落
  notification_log → notifier 非零 exit（unit failed 可觀測）→ 下次
  排程重掃自動補送（message-key 保證補送不重複）。**管線本身
  （scanner／importer／enqueuer／worker）完全不受影響**——通知是
  best-effort 旁路（§2.2 決策的直接推論）。
- **gateway 啟動慢的既知事實**（memory 註記：啟動後約 3.5 分鐘才寫
  狀態檔）：notifier 對 send 失敗一律「留待下輪」，不做行內重試
  等待——簡單且與批次語意一致。

---

## 6. 小項納入評估（三項，處置皆定案）

### 6.1 cron prompt 無聲失敗改進（2.6 §15.6 第 4 項）——✅ 已拍板（§11 第 7 題）：列 Hermes 側待辦，不進 2.7 DoD

- 事實：問題與修法都在 **hermes-agent 側**（cron prompt 內容——搜尋
  工具故障時投「簡短故障通知」而非 `[SILENT]`）；本 repo 零程式碼
  涉及。
- 決策理由紀錄：2.7 的 DoD 應該全部落在本 repo 可驗證的產出物上；把
  另一 repo 的 prompt 修改綁進本 stage 的完成定義，會讓 stage 關閉
  依賴外部 repo 的節奏。它可以在 2.7 期間順手完成（成本極低），但屬
  hermes-agent 的受控變更慣例，不是本提案的交付物。

### 6.2 needs_review 觸發率監測（2.5 §20.3 遺留第 1 項之三）——既有計數＋逐筆通知，關閉

- 現況：2.6b 已落地 CLI decision 累計計數（`list` 順帶輸出），2.6d
  建立第一筆基準（needs_review=0）。
- 定案：既有計數＋本階段的 `needs_review` 逐筆通知（§3.1）已足夠。
  計數回答「趨勢」（人工跑 list 時看），通知回答「出現了」（無人
  值守時浮現）——兩者互補後，這條遺留可視為關閉。不建 dashboard、
  不設閾值告警（量體不支撐；觀測性面板屬 Stage 3）。

### 6.3 cron/platform 唯讀橋接（2.6 §15.6 第 2 項）——維持未排程構想，不納入 2.7

理由：(i) 需求證據只有一次（2.6d 那筆 automation 任務靠間接證據回答
平台狀態），且該筆的原始問題已完整結案；(ii) 它是新的跨 repo 唯讀
介面設計（Hermes cron／gateway 即時狀態的讀取契約），與 2.7 的通知／
排程主線無關，塞進來會稀釋範圍；(iii) Hermes 內部狀態介面不穩定，
貿然建橋接是維護負債。維持「未排程想法」原標記，待第二次真實需求
出現再議。

---

## 7. 總體資料流（(A)＋(B) 合併、九項拍板後的形狀）

```
[timer 08:05] scanner ──> bridge_state: discovered        （既有，不動）
[timer 08:15] hermes-bridge-pipeline（新 oneshot，串行）
      ├─ bridge_importer.py import --limit 10 ──> memory/inbox/ + to_inbox
      └─ bridge_triage_enqueuer.py enqueue --max-new 5 ──> enqueue_once
                       │
      worker（常駐，既有）──> invoke_cos_triage.sh（zero-tools）
                       │
      jobs.result: decision ∈ {memory_only, action_candidate, needs_review}
                       │
[timer 08:25] hermes-bridge-notifier（新 oneshot，jobs 表唯讀）
      ├─ scan_triage_results()（複用）→ candidate_pending / needs_review / anomaly
      ├─ jobs 表：triage/dispatch 的 dead_letter、dispatch completed
      ├─ 比對 notification_log（UNIQUE message_key）
      └─ hermes send -t slack:<頻道> --message-key agentos27:...（冪等第二層）
                       │
      Slack #agentos（C0BHE9NFW0P；驗收期＝C0BHZC2EG84）
                       │
      █ 人工核准 gate（鐵律，不排程）█
      bridge_dispatch.py list / approve / reject / resume-approved（全人工）
                       │
      dispatch job → worker → invoke_cos.sh → domain subagent（2.6 既有閉環）
                       │
      下一輪 notifier：dispatch_completed / dispatch_dead_letter 通知
```

---

## 8. 失敗與 recovery 情境

- **send 成功但 notification_log 寫入前 crash**：下輪重送同
  message-key → hermes send ledger no-op → 本輪補寫 log。Slack 端
  恰好一則（§2.3 雙層的存在理由）。
- **hermes send CLI 不存在／不可執行**：notifier fail loud（非零
  exit、unit failed），零通知送出、零 log 寫入——不靜默降級成
  「假裝通知過」。
- **pipeline unit 中 importer 失敗**：enqueuer 不執行；unit failed
  可觀測；下輪從冪等狀態續跑。conflict（hash 漂移）→ enqueuer
  exit 3 → unit failed＋逐筆訊息在 log，人工調查（2.5b 既有語意，
  排程化不改變）。
- **triage/dispatch dead_letter**：notifier 通知（§3.1）；重跑維持
  唯一人工路徑 `db.py requeue`（附 per-job log 檢視 runbook，2.6 §8
  既有）。
- **排程與人工操作並行**：人工跑 importer/enqueuer/list 與 timer
  撞上——所有寫入路徑都有 UNIQUE/狀態機保護（§4.3），最壞情況是
  其中一方拿到 existing no-op 或明確報錯，零重複、零靜默。

---

## 9. 子階段拆分（比照 2.5/2.6 的模式；每個子階段開工前經使用者核准）

### 2.7a — notifier 核心＋notification_log（不排程、不真送）✅ 完成（commit `86287f7`）

- **範圍**：`jobs.db` migration（`notification_log`，冪等）；
  `hermes/bridge_notifier.py`：事件偵測（複用 scan_triage_results＋
  jobs 表唯讀查詢）、message-key 組裝、訊息樣板（§3.3 拍板格式：
  summary 截斷 200 字元＋標註）、`hermes send` 子程序呼叫封裝、
  `--dry-run`（零寫入零外呼，列出將通知清單）、`--send-cli` 可注入
  （測試 mock 用）；enqueuer `--max-new` 旗標。
- **DoD**：migration 冪等測試；事件偵測對三種 decision＋dead_letter＋
  anomaly 的分類測試（沙箱 jobs.db fixture）；message-key 決定性
  測試；訊息樣板快照測試（**斷言 episode/artifact 內容不出現**、
  summary 截斷）；冪等測試（mock send：重跑零重送；send 失敗不落
  log、下輪補送；「送成功未落 log」以 fault-injection 驗證第二層
  語意——mock ledger no-op）；`--max-new` 截斷測試；`--dry-run`
  零寫入；既有測試套件零回歸。
- **測試策略**：mock `hermes send`（fixture 腳本回固定 exit/輸出），
  不打真 Slack、不呼叫任何模型。
- **不做**：不裝 timer、不真送 Slack、不碰 worker、不碰
  bridge_state.db。
- **完工事實（2026-07-18）**：commit `86287f7`，六種事件類型（§3.1
  五種＋§3.2 anomaly）判定、mock send、42 個沙箱測試全綠；enqueuer
  `--max-new` 旗標隨本子階段一併完成。詳見 §15.1。

### 2.7b — 排程化 units（pipeline＋notifier timers）✅ 完成（commit `23d9f6a`）

- **範圍**：`hermes-bridge-pipeline.service/.timer`（08:15，串行
  importer→enqueuer，帶 `--limit 10`／`--max-new 5`）、
  `hermes-bridge-notifier.service/.timer`（08:25）；install/uninstall
  腳本項目；`test_systemd_units.py` 擴充（守住無範圍參數、oneshot、
  無 Restart、Persistent=true 慣例）；systemd README 更新；**順手項
  （✅ 使用者已同意）：更正 `hermes-bridge-scanner.service/.timer`
  內過時的「現況（2026-07-12）仍是 legacy ended_at scanner、episode
  capture 尚未啟用」註解**——2.4d 已於 2026-07-12 部署 episode
  capture（`episodes.enabled=true`），註解與現實不符；註解性修正、
  零行為變更，隨本子階段動 systemd 目錄時一併完成，不另開待辦。
- **DoD**：靜態測試綠；兩側 unit 檔 LF；scanner unit 註解更正完成
  （ExecStart 等行為行零改動，diff 僅註解）；**尚不 enable**——
  enable 是 2.7c 部署動作（比照 2.4b「五項完成標準全過後才 enable
  timer」的慣例）。
- **不做**：不改既有 scanner/memory-check/bridge units 的**行為**
  （scanner 僅註解更正）；不排程 reconcile、不排程 dispatch CLI。
- **完工事實（2026-07-18）**：commit `23d9f6a`，兩組 systemd unit 寫好
  （依 DoD 規劃暫未 enable，交由 2.7c 部署動作 enable）、scanner 過時
  註解修正完成、19 個測試全綠。詳見 §15.1。

### 2.7c — 部署＋真實驗收（低量、人工全程在場）✅ 完成（見 §15）

- **範圍**：start blocker 解除確認（§13：WSL 側 `hermes send` 實測）
  →頻道 allowlist 佈建（拍板流程：先測試頻道 `C0BHZC2EG84`，驗收
  通過後切 `#agentos`）→ `sync_to_wsl.sh --apply` 下發→手動觸發各
  unit 一輪（enable 前）→enable timers→至少一個自然（或 catch-up）
  排程週期的真實驗證。
- **DoD**：真實通知送達拍板頻道且重跑 notifier 零重複；至少一筆真實
  「排程產生的候選 → Slack 通知 → 人工 approve → dispatch →
  dispatch_completed 通知」全鏈（若當期無自然新 episode，以手動
  checkpoint 製造一筆，沿用 2.5d 全新鮮鏈路慣例）；**鐵律抽查：全程
  零未經人工核准的 dispatch job**（以 jobs 表＋dispatch_events 稽核
  斷言）；成本記錄回報（含排程化後首週 triage 自動成本）；timer
  失敗可見性至少人工演練一次（故意讓 send 失敗，確認 unit failed＋
  下輪補送）。
- **不做**：不擴大量體；驗收期間不調高頻率。
- **完工事實（2026-07-18）**：start blocker 解除過程遠比預期複雜（WSL
  側 Hermes-agent 落後 Windows main 1223 commit，非單純未實測——完整
  故事見 §15.2）；`sync_to_wsl.sh --apply` 部署、真實投遞、冪等重跑、
  鐵律稽核全數通過；兩組 timer 已 enable。詳見 §15.3。

---

## 10. 風險

| 風險 | 影響 | 緩解 |
|---|---|---|
| **通知風暴／重複通知** | Slack 頻道被洗版、狼來了效應 | 事件白名單（memory_only 等常態不通知，§3.2）；雙層冪等（notification_log UNIQUE＋send ledger）；量體現況極低；`--max-new` 間接限制單日新事件數 |
| **排程與人工操作並行的競態** | 重複 enqueue／重複登記／狀態機混亂 | 全部寫入路徑既有 UNIQUE＋狀態機保護（§4.3 逐段論證）；dispatch CLI 不排程，人工段無自動並行方 |
| **排程化後的自動成本** | triage 呼叫無人意識地累積 | `--max-new 5` 筆數硬上限（單日 ≈ $0.60 上限，已拍板）；dispatch 段零自動成本（鐵律）；2.7c 記錄首週實際成本回報 |
| **Slack／gateway 不可用** | 通知延遲或缺席 | best-effort 旁路設計：管線零受影響；失敗不落 log→下輪補送；fail loud 可觀測（§5、§8） |
| **未信任內容經 summary 進入 Slack** | 混淆性文字出現在通知（洩漏面／社交工程面） | episode/artifact 原文絕不入通知；summary 截斷＋標註「模型摘要未經人審」（§3.3，已拍板）；Slack 是唯讀通知通道、無互動核准（§0.3 第 4 條） |
| **排程化侵蝕核准 gate 的感知**（人習慣了自動化，對 approve 鬆懈） | 核准淪為 rubber-stamp | 鐵律明文（§0.1）；approve 介面既有的人審任務描述確認步驟不變；候選通知只給資訊不給一鍵核准 |
| **WSL on-demand 讓「每日排程」名不符實** | 期望與現實落差（以為即時，實際是喚醒補跑） | §1.2 誠實前提明文；Persistent=true catch-up 保證不漏；不承諾即時性 |
| **跨 repo 介面漂移**（hermes send 旗標／行為改版） | notifier 送信失敗 | 介面收斂在單一封裝函式；fail loud；hermes-agent 受控升級慣例（memory 既有）下人工驗證 |
| **notifier 對 jobs.db 的讀取假設過強** | 非預期 result 形狀導致 crash | 複用既有防禦性 parse 單一實作（§2.5）；異常走 anomaly 通知/呈現，不 crash |

---

## 11. 開放問題（✅ 九項已於 2026-07-18 全數拍板——**皆採建議值**；以下原文保留為決策紀錄，不刪）

1. **頻道對應**（2.6 §7 明文留給使用者）：pipeline 事件進哪個頻道？
   ——建議：**單一頻道 `#agentos`（C0BHE9NFW0P）**；2.7c 驗收先用
   測試頻道 `C0BHZC2EG84`，通過後切正式。分頻道列為量體成長後演化。
   **✅ 已拍板（2026-07-18）：採建議值（§3.4）。**
2. **通知事件清單**：§3.1 五種＋anomaly 評估項是否照案？memory_only
   確定不通知？——建議：照 §3.1／§3.2 辦理，anomaly 納入。
   **✅ 已拍板（2026-07-18）：採建議值（§3.1／§3.2）。**
3. **summary 是否進通知**：——建議：納入、截斷 200 字元、標註
   「模型摘要未經人審」；episode 原文無論如何不進。
   **✅ 已拍板（2026-07-18）：採建議值（§3.3）。**
4. **enqueuer 單次新建上限 `--max-new`**：——建議：5（單日 triage
   自動成本上限 ≈ $0.60）。
   **✅ 已拍板（2026-07-18）：採建議值（§4.4）。**
5. **排程時刻與頻率**：pipeline 08:15、notifier 08:25、皆每日一次？
   ——建議：是（WSL on-demand 下高頻無實益；always-on 化後再調）。
   **✅ 已拍板（2026-07-18）：採建議值（§4.1／§4.2）。**
6. **timer 失敗的 meta-monitoring**：是否做 heartbeat／watchdog 通知？
   ——建議：v1 不做（on-demand 下必然天天誤報；接受 systemd 既有
   可觀測性），列演化。
   **✅ 已拍板（2026-07-18）：採建議值（§4.5）。**
7. **cron prompt 無聲失敗改進歸屬**：納入 2.7 DoD 還是列 Hermes 側
   待辦？——建議：列 Hermes 側待辦，不進本 stage DoD（§6.1）。
   **✅ 已拍板（2026-07-18）：採建議值（§6.1）。**
8. **notification_log 位置**：jobs.db 新表（建議）還是只靠 hermes
   send ledger？——建議：jobs.db 新表為權威＋ledger 為第二層
   （§2.3，否決理由已列）。
   **✅ 已拍板（2026-07-18）：採建議值（§2.3）。**
9. **dispatch_completed 通知是否附結果摘要**：——建議：v1 只通知
   「完成＋成本＋job_id」，不投 `jobs.result` 全文（result 是 CoS
   整合輸出，可能夾帶任務過程中讀到的內容；人工用 `status` 子指令
   看全文）。列為未來演化（屆時可評估 `delivered_at` 模式承載）。
   **✅ 已拍板（2026-07-18）：採建議值（§3.3）。**

---

## 12. engineering／automation 分工

沿用 2.5 §15 確立的分工原則：產出物是程式碼/schema → engineering；
產出物是排程頻率/觸發時機決策 → automation。

- **2.7a**：engineering（notifier、migration、旗標、測試）。
- **2.7b**：automation 主導 timer 時刻／頻率／unit 語意決策（§4 的
  拍板值即其輸入），engineering 落 unit 檔與靜態測試（含 scanner
  unit 註解更正順手項）——這是 automation 自 roadmap Stage 2 以來
  首次實質進場。
- **2.7c**：部署與驗收（engineering 執行、使用者逐步核准），allowlist
  佈建屬 Hermes 側人工操作。

---

## 13. Start blocker 評估（2026-07-18 拍板後更新）

**一項硬 blocker 候選（需在 2.7c 前解除，不擋 2.7a/2.7b 開工）**：

1. **WSL 部署側能否呼叫 `hermes send` 並成功投遞**——Slack 佈建與
   07-17/07-18 的實測都在 Windows 側；notifier 的定案形態是 WSL
   systemd timer。需實測一次（對測試頻道送一則帶 message-key 的
   訊息）確認：WSL 內 `hermes` CLI 是否可用／send ledger 路徑是否
   正確／是否需經 Windows 側轉呼（例如 `hermes.exe` 或
   powershell.exe 橋接）。**若 WSL 側不可行**，備案：notifier 主體
   仍在本 repo，排程改 Windows 側 Scheduled Task 觸發（Windows 側
   有 repo 正本與 Python venv；jobs.db 在 WSL ext4 側是主要難點，
   屆時需重新評估——正因如此這是 blocker 而非細節）。此驗證是
   一次性、低成本（一則測試訊息），建議儘早執行。

**軟前置（不擋開工）**：

2. ~~§11 開放問題拍板~~——**✅ 已完成（2026-07-18，九項全數拍板、
   皆採建議值）**。
3. 目標頻道 allowlist 佈建（2.7c 部署動作，人工、分鐘級；拍板後
   目標明確：測試頻道 `C0BHZC2EG84` → 正式 `#agentos`）。

其餘機制（enqueue_once、worker 常駐、systemd 慣例、sync 慣例、
scan_triage_results 複用）全部是已上線、已實測的既有元件——與 2.6
相同，2.7 主要是組合它們，唯一的新外部依賴就是上述跨 repo 的
send CLI 呼叫路徑。**目前唯一待辦是使用者核准 2.7a 開工**（依既有
節奏：每個子階段開工前各自核准；硬 blocker 第 1 項在 2.7c 前解除
即可）。

**✅ 解除紀錄（2026-07-18）**：實測發現這項 blocker 遠比原評估嚴重
（並非單純「沒實測過」，而是 WSL 側 Hermes-agent 套件版本與 Windows
main 分岔 1223 個 commit），完整處置故事（釐清、安全查證、拍板選項
1b、執行步驟、實測結果）見 §15.2。已於 2.7c 部署前解除，不再是
未解決的 blocker。

---

## 14. 完成定義總表（全階段）

Stage 2.7 整體視為完成，當且僅當：

1. 2.7a–2.7c 各子階段 DoD（§9）逐項達成，每個子階段開工前經使用者
   核准（維持既有節奏）。
2. 至少一筆真實事件走完「排程產生候選 → Slack 通知 → 人工核准 →
   dispatch → 完成通知」全鏈；通知冪等經實測（重跑零重複）。
3. **鐵律零違反**：全程沒有任何一筆 `bridge_domain_dispatch` job 在
   缺少人工 approve 的情況下被建立（以稽核表斷言）。
4. 既有系統零回歸：worker 零改動、telegram/rss/cron/scanner 路徑
   行為不變（scanner unit 僅 2.7b 註解更正）、`delivered_at` 語意
   不變、headless memory 邊界不變。
5. 排程化後首個觀察窗（建議一週）的自動 triage 成本與通知量回報
   使用者，作為「是否調頻率／是否進 Stage 3」的決策依據。

**✅ 全部達成（2026-07-18）**：完工紀錄正本見下方 §15。

---

## 15. 完工紀錄（Stage 2.7 全階段關閉，2026-07-18）

### 15.1 實作與部署事實

- **2.7a**（commit `86287f7`）：notifier 核心——`notification_log` 表
  （§2.3 設計落地）、六種事件類型判定（§3.1 五種＋§3.2 `anomaly`）、
  `hermes send` 子程序封裝（mock send 可注入）、message-key 組裝
  （§2.4）、訊息樣板（summary 截斷＋標註）、`--dry-run`；附帶
  enqueuer `--max-new` 旗標（§4.4）。42 個沙箱測試全綠。
- **2.7b**（commit `23d9f6a`）：`hermes-bridge-pipeline.service/.timer`
  （08:15）與 `hermes-bridge-notifier.service/.timer`（08:25）兩組
  systemd unit 寫好但**尚未 enable**（比照既有慣例，enable 是 2.7c
  部署動作）；`hermes-bridge-scanner.service` 過時註解修正（§9 2.7b
  範圍明文的順手項，零行為變更）；19 個測試全綠（含
  `test_systemd_units.py` 擴充）。

### 15.2 §13 唯一 start blocker 的完整故事

原提案 §13 列的唯一硬 blocker——「WSL 部署側能否呼叫 `hermes send`
並成功投遞」——原以為只是「沒實測過」的既知缺口，2.7c 實測後發現
遠比預期嚴重，記錄如下（含後續處置，供未來查閱）：

1. **問題比預期嚴重**：WSL 的 `~/.hermes/hermes-agent` 是一份**完全
   獨立的 git checkout**（shallow clone），落後 Windows 側 main 分支
   **1223 個 commit**——沒有 Slack delivery hardening、`hermes send`
   不支援 `--message-key`、也沒有 `SLACK_BOT_TOKEN`／頻道 allowlist
   設定。這不是「忘記測」，是兩邊套件版本從未同步過。
2. **釐清與 Stage 0 決策的界線**：這與「`state.db` 由 Windows 側
   symlink 共用」（Stage 0 拍板）是**兩件不同的事**——那條線仍然
   成立、沒被破壞。這次卡住的是 **Hermes-agent 程式碼本身**的版本
   在兩側完全獨立、從未同步（`sync_to_wsl.sh` 只同步本 repo，不涉及
   Hermes-agent 套件）。
3. **使用者拍板選項「1b」**：不比照完整的「1a」（建立長期自動同步
   機制），改採一次性把 WSL 側 hermes-agent 用 git fast-forward
   升級到與 Windows main 同一 commit，不建立持續同步流程。
4. **安全查證（動手前）**：
   - 先查 `hermes send` 路徑（`send_cmd.py`／`send_message_tool.py`）
     原始碼，確認完全不觸碰 `state.db`（docstring 明講刻意不載入
     完整 gateway 模組）——排除「升級可能對正在使用中的共用
     `state.db` 做 schema migration」的風險。
   - 再確認 WSL 本地那顆 commit（`05cbddc0`，一個 revert commit）是
     Windows main 的祖先——fast-forward 無損、不會產生分叉。
5. **執行步驟**：`git fetch --unshallow` 補齊 shallow clone 歷史 →
   用本機路徑把 Windows checkout 加為 git remote（同機、免網路）→
   `git merge --ff-only`（`05cbddc0` → `c12c64f9e9`）→
   `pip install -e ".[messaging]"` 重建 venv → 複製
   `SLACK_BOT_TOKEN`（未印明文）＋鏡像 Windows default profile 的
   七頻道 allowlist 到 WSL 側 `.env`／`config.yaml`（備份
   `.bak.20260718`）→ 打安全 tag `pre-1b-upgrade-20260718`（rollback
   錨點）。
6. **三項實測全過**：負面（非清單頻道 fail-closed 擋下）、正面
   （測試頻道 `C0BHZC2EG84` 真實送達）、去重（同 message-key 第二次
   no-op）。
7. **附帶討論、不納入 2.7 範圍的問題（架構備忘）**：使用者曾問
   「dispatch 能否用 Windows profile 的輕量／免費模型」——查證後
   確認 dispatch 執行路徑（`invoke_cos.sh` → `claude -p`）與 Hermes
   profile 完全無關、也與 `scripts/route_model.py`（OpenRouter 直呼）
   無關，三者是**三條獨立路徑**。這是獨立的未來架構決策，不屬於
   2.7，本次一併記入遺留事項（§15.4）供後續規劃參考。
8. **新遺留**：兩側 Hermes-agent 現在版本一致，但**沒有建立自動同步
   機制**——1b 是一次性 fast-forward，不是持續流程；之後 Windows 側
   再升級 Hermes-agent，需要人工對 WSL 側重跑同一套 fast-forward
   流程，否則會再度漂移（見 §15.4）。

### 15.3 2.7c 部署與驗收紀錄

- `sync_to_wsl.sh --apply` 部署 2.7a／2.7b（備份
  `pre-sync-20260718T124319`）；部署側測試套件全綠。
- notifier dry-run 正確列出 1 筆待通知事件（2.6d 的 dispatch job
  `06128712`，遲來但正確的通知）；真實投遞到測試頻道 `C0BHZC2EG84`
  成功；重跑驗證冪等（0 送出／1 略過）。
- **鐵律稽核**：notifier 執行前後 `dispatch_records` 表筆數維持
  2 筆不變、`jobs` 表無新增——全程零未經人工核准的 dispatch。
- 兩組 timer（`hermes-bridge-pipeline.timer` 08:15、
  `hermes-bridge-notifier.timer` 08:25）已 enable，下次觸發
  2026-07-19 08:15／08:25——**待確認**：明日首次自動觸發是否正常
  運作（pipeline 是否正確處理當日新 episode、notifier 是否正確
  推播到 `#agentos` 正式頻道），見 §15.4 遺留事項。

### 15.4 遺留事項

1. **明日首次自動觸發待確認**（2026-07-19 08:15／08:25）：兩組 timer
   剛 enable，尚未經過一次自然排程週期的真實驗證，需在觸發後人工
   檢視 pipeline 是否正確處理當日新 episode、notifier 是否正確推播
   到 `#agentos` 正式頻道。
2. **dispatch＋輕量模型的架構構想**（未排程）：dispatch 執行路徑
   （`invoke_cos.sh` → `claude -p`）目前與 Hermes profile、
   `scripts/route_model.py` 三者完全獨立；若未來要讓 dispatch 可選用
   Hermes profile 裡的輕量／免費模型，需要新的整合路徑（改
   `invoke_cos.sh` 或新增 adapter），且需先解決 **profile 資料跨機
   同步**問題（目前只有 `state.db` 有 symlink，Hermes profile 的
   `.env`／`config.yaml` 沒有同步機制）。列為未來架構決策，不屬於
   2.7 範圍。
3. **WSL／Windows 側 Hermes-agent 版本同步無自動化**：本次 1b 是
   一次性 fast-forward，不是持續流程；Windows 側日後再升級
   Hermes-agent，需人工對 WSL 側重跑同一套流程（§15.2 步驟 5），
   否則會再度漂移。
