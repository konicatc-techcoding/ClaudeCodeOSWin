# Memory Lifecycle 補強提案(v1.1)

日期:2026-07-30　狀態:**v1.1——四項已拍板,第一批實作中;A1 查證中**

## 版本標記

- **v1**(2026-07-30)= 初版草案。
- **v1.1**(同日拍板):
  1. **汰選檔位=(b) 全自動歸檔**(使用者選了比建議檔位 (a) 更自動的一檔:
     不逐項詢問,直接歸檔 `memory/.archive/`,review 報告列清單供事後知悉;
     可搬回,git 歷史為最終保險)。豁免清單核准(拍板決策/安全事故教訓/
     `feedback_*`)。冷啟動保護維持。
  2. **A1 核准方向、先跑兩個前置查證**(§2.1);`inactivity_hours` 維持 72。
  3. **升格採 C-a**(MEMORY.md 索引分層)。
  4. **B1 核准**(recall log,接受方向性下限限制);每 episode 前置摘要步
     **暫緩**(拍板項 5 採建議)。
  - 第一批落地分工:`scripts/log_recall.py`+SKILL.md retention 節=
    engineering;delegation_policy.md 步驟 1.5、consolidation_policy.yaml
    `retention:` 區塊、cron_jobs.yaml daily-memory-check prompt 第 4 步=
    互動式 CoS session(均已同日完成)。
負責規劃:`planning` domain
負責領域(實作階段,若核准):腳本/bridge 擴充=`engineering`;
consolidate-memory 相關 skill 文件擴充=互動式 session 決策+`engineering` 實作
(memory-taxonomy §3 的既有升級程序);政策文件(CLAUDE.md/delegation_policy/
consolidation_policy.yaml)變更=互動式 CoS session。

**背景(一句話)**:使用者 2026-07-30 拍板兩個方向——(1) Hermes session
(含 named profile 如 gptcoding 的設計討論)**不經逐次詢問**自動蒸餾進記憶;
(2) 記錄每條記憶被 recall 的次數與時間,定期檢視:頻繁 recall 的升格長期
記憶、久未 recall 的刪掉。本提案把兩者落在既有機制上,不重造管線。

---

## 0. 定位與範圍邊界

**一句話定位**:補齊記憶生命週期的「進」(session 自動蒸餾)與「出」
(recall 統計驅動的升格/汰選)——進的部分是**擴充既有 bridge 管線的覆蓋面**,
出的部分是**給既有 recall-first 程序加統計埋點、給 consolidation 加定期檢視**。
全部疊在既有機制上,零新排程、零新狀態機。

### 0.1 明確不做

- **不動 auto-memory**:Claude Code 自身的 `~/.claude` 記憶體系是另一套,
  本案的統計/汰選/蒸餾全部只針對本 repo 的 `memory/`。
- **不改寫入權限模型**:自動蒸餾產出一律落 `memory/inbox/`,經
  consolidate-memory 進正本——headless 不碰正本這條鐵律原樣沿用
  (ARCHITECTURE.md §4、memory-taxonomy §2)。
- **不建第二套擷取管線**:cursor/去重/敏感 fail-closed/cutover 底線只有
  bridge 一份實作(2.4d 的既定立場)——named profile 覆蓋走 bridge 擴充,
  不另寫獨立蒸餾器。
- **recall 統計不寫記憶正本檔**:frontmatter 計數直接否決——每次 recall
  都得改正本,headless 入口根本無權寫,寫入模型當場破功(見 §2.2)。
- **不做全自動刪除(v1 底線)**:破壞性動作至少保留「歸檔可回復」或
  「人工確認」其中一層——與使用者「刪掉」原話的差異誠實列為待拍板項 1。
- **不做 session 結束的即時偵測**:每日批次(既有 08:00/08:05 排程)已滿足
  「不用問我」;即時性用調 `inactivity_hours` 這根便宜槓桿處理(§1.1)。

---

## 1. 現況盤點(設計基礎——「自動蒸餾」大半已存在)

default profile 的自動鏈今天就是「不問你」的:

```
Hermes session(default profile)
  → 08:05 HermesBridgeDaily:scanner 切 episode(ended/archived/72h inactivity/manual)
  → importer 落 inbox(原文 episode;too_short/敏感 fail-closed 在此把關,
    07-23 實績:1471 筆 cron 雜訊 session 被 too_short 排除,0 誤入)
  → 08:00 daily-memory-check:N-gate 達標即分派 knowledge 跑 consolidate-memory
  → memory/*.md 正本(蒸餾=consolidation 這一步,全程無人工詢問)
```

真正的缺口只有三個:

| # | 缺口 | 對應本案 |
|---|---|---|
| G1 | **named profile 全漏**:episode capture 是 default-only fail-closed(2.4d §6.1);gptcoding 等 lane profile 的 session 在 `profiles/<name>/state.db`,無任何擷取(STATUS 07-29 也列了同根源的觀測缺口) | §2.1 方案 A |
| G2 | **recall 零統計**:recall-first 是 prompt 層程序(delegation_policy 步驟 1.5),沒有任何落地紀錄 | §2.2 方案 B |
| G3 | **記憶只進不出**:無升格/汰選機制,正本與索引只會長大 | §2.3 方案 C |

### 1.1 附帶的便宜槓桿:蒸餾延遲

`inactivity_hours: 72` 表示設計討論最壞 3 天後才切 episode 進管線。若嫌慢,
調 `hermes/config/bridge.yaml` 一行(如 72→24)即可,無程式碼變更;切早的
代價只是同主題分成多個 episode,consolidation 會再整併(bridge.yaml 已明文
此語義)。附在待拍板項 2 一起答。

---

## 2. 方案對照

### 2.1 A:自動蒸餾的 named profile 覆蓋(G1)

| 方案 | 內容 | 主要優點 | 主要弱點 |
|---|---|---|---|
| A0. 維持現狀(基準) | 只蒸餾 default profile | 0 成本 | gptcoding 等設計討論永遠進不了記憶——正是使用者點名的場景 |
| **A1. 擴充既有 bridge 到多 profile** | scanner/importer 逐 profile 掃描(profile 清單進 bridge.yaml);啟用 2.4d §6.1 **已預留**的 event_id namespace `hermes/<profile>:<sid>:<first>..<last>`;`bridge_cursors` 主鍵本來就是 `(source_profile, session_id)`,結構天然支援 | 去重/敏感 fail-closed/cutover 底線/too_short 門檻**全部繼承**;2.4d 明文「屆時只需啟用 namespace+放行檢查,不需 migration」 | 實作面積在 bridge 三件組+adapter;每 profile 的 per-profile cutover 要定(防歷史湧入,同 §5.1 哲學) |
| A2. 獨立 per-profile 蒸餾器 | 新 script 直讀 profile state.db 產摘要落 inbox | 看似快 | 重造第二套 cursor/去重/敏感偵測——違反「回填規則只有一份實作」的既有立場,**不推薦** |

A1 的兩個**前置查證項**(先查證再開工,查證=`engineering` 或主 session 讀設定即可):

1. 部署側 scanner 實際讀哪(幾)顆 state.db——WSL `~/.hermes/state.db` 與
   Windows 主 state.db/`profiles/<name>/state.db` 的覆蓋關係要先釐清
   (07-29 拍板 lane 憑證單一存放 Windows 側,lane session 應落 Windows 側
   profile db;WSL 讀 Windows db 需經 `/mnt/c` + 既有快照複製慣例)。
2. named profile 的 session 量與雜訊形態——lane coding session 可能「很長
   但全是 tool 輸出」,too_short 擋不住;確認既有 usefulness 排除訊號
   (`command_trial_and_error` 等)在 consolidation 端是否足夠。

**蒸餾執行者與位置(A 的子問題)**:維持「inbox 收原文 episode、蒸餾發生在
consolidation」(現狀),**不新增**「每 episode 先呼叫模型產摘要再落 inbox」
的前置步驟。理由(成本誠實):新增摘要步=每 episode 多一次模型呼叫,且摘要
品質直接決定記憶品質、錯了無法從 inbox 還原;現狀的模型成本只有 N-gate 達標
時的 consolidation pass(每次全文重讀,但頻率被 N-gate 壓住)。若 A1 上線後
episode 原文量大到 consolidation 吃不下,再回來議摘要步——列待拍板項 5。

### 2.2 B:recall 統計(G2)

| 方案 | 內容 | 評估 |
|---|---|---|
| **B1. append-only log(推薦)** | `logs/recall_log.jsonl`,recall 後 append 一行;欄位:`ts`、`entry`(interactive/headless)、`result`(hit_skill/hit_memory/miss)、`hit_ids`(命中的 skill 名/memory 檔名)、`task_hint`(一句話任務分類) | 不碰記憶正本、兩個入口都可寫(logs/ 本來就是 runtime 元件的 Episodic 層);JSONL 逐行 append 天然可合併 |
| B2. 記憶檔 frontmatter 計數 | 每次 recall 改該 memory 檔的 metadata | **否決**:headless 無權寫正本;每次 recall 都產生 git diff 雜訊;違反 0.1 邊界 |

B1 的落地機制與誠實限制:

- **埋點**:delegation_policy.md 步驟 1.5(recall 那一行本來就是強制輸出)
  補一句「講出 recall 結果後,執行 `scripts/log_recall.py` append 一行」;
  helper script 由 `engineering` 實作(含格式驗證與測試),CoS 只負責呼叫。
- **兩側合流**:前台寫 Windows 側、headless 寫 WSL 部署複本側——JSONL 併集
  即全量,掛進 `scripts/sync_to_wsl.sh` 既有的 reverse-merge(inbox 已有
  同型先例),或退而求其次統計時兩側各讀一次。實作時二選一,不是拍板題。
- **誠實限制**:埋點在 prompt 層,遵從是 best-effort——統計是**方向性下限**
  不是精確值。這對用途足夠:升格看「高頻」(下限高就是真的高),汰選門檻
  則必須保守設計(低 recall 只進候選清單,不直接動作,見 §2.3)。

### 2.3 C:定期檢視——升格與汰選(G3)

**排程載體**:掛在既有 `daily-memory-check`(08:00 WSL timer)——cron prompt
擴充一段「距上次 retention review 超過 `review_interval_days` 即在整併之外
加做一次檢視」;參數進 `consolidation_policy.yaml` 新 `retention:` 區塊
(建議 `review_interval_days: 30`、`stale_threshold_days: 90`、升格門檻沿用
既有 `skill_promotion.min_recall_reuse: 3` 的精神)。**不新增排程**——
比照本案 0 新 timer 的立場;檢視的執行者=`knowledge`(讀 recall log 統計
+正本),與 consolidation 同一分派路徑。

**升格語意**(目前記憶無長短期分層,三個選項):

| 選項 | 內容 | 評估 |
|---|---|---|
| **C-a. MEMORY.md 索引分層(推薦)** | 索引加「高頻」區段(或條目標記),高 recall 條目排前面/標出來;正本檔不動、不搬家 | 零新機制——recall-first 本來就先讀 MEMORY.md,索引排序=實質的「長期記憶優先權」;taxonomy §7 已要求索引 recall-友善,這是同方向延伸 |
| C-b. frontmatter `tier` 欄位 | 正本檔加 metadata | 只有 consolidation pass 能寫(合規),但讀側(recall)看的是索引不是逐檔 frontmatter,標了沒人消費 |
| C-c. 升入 CLAUDE.md 常駐 context | 最高頻的直接進系統 prompt | context 預算昂貴且 CLAUDE.md 是 Procedural 層——**只保留給「實質上是行為規則」的條目**,走既有 Semantic→Procedural 升級程序(taxonomy §3),不因 recall 高就自動進 |
| (既有)skill 升格 | recall 複用 ≥3 次的解法列 SKILL.md 候補 | taxonomy §7.1 已定案,MVP 缺自動計數——**B1 的 recall log 正好補上這個計數來源**,免費收益 |

**汰選安全層**(與使用者「刪掉」原話的誠實對照):

- 使用者原話是「一段時間沒被 recall 的刪掉」。建議的安全形式:
  `stale_threshold_days` 內零 recall 的條目→ retention review 產出
  **候選清單**→ 使用者確認後**降級歸檔**到 `memory/.archive/`(索引移除、
  正本搬離,recall 自然不再命中;要回復就搬回來)。真刪除不做——但誠實
  承認:`memory/` 在 git 版控內,即使真刪也可從歷史回復,所以「歸檔 vs
  刪除」的實質差異是**可發現性**(歸檔目錄一眼可見,git 考古要挖),
  不是可回復性。三個檔位(全自動歸檔/清單確認後歸檔/照原話直接刪)
  列待拍板項 1。
- **豁免清單**(永不進汰選候選,`retention.exempt` 進 yaml):拍板決策
  (substantive_decision 型內容)、安全/事故教訓、`feedback_*` 全型別
  (行為糾正低頻但關鍵)。`reference_*`/一般 `project_*` 可汰選——
  過期的外部參照與已完結專案脈絡正是該出去的東西。
- **統計偏差防護**:recall log 是下限值(§2.2),所以「零 recall」可能是
  「沒被記到」——候選清單必附「最後 recall 時間+log 覆蓋起算日」,
  且 log 累積不足 `stale_threshold_days` 天前不啟動汰選(冷啟動保護)。

---

## 3. 推薦組合與理由

**第一批(建議核准,低風險、零新排程、全部疊既有機制)**:
B1(recall log+步驟 1.5 埋點)+ C(daily-memory-check 掛 retention review、
C-a 索引分層升格、歸檔制汰選+豁免清單)。理由:G2/G3 互為依賴——沒有
統計就沒有可信的升格/汰選依據,先讓數據開始累積;全部改動是「一支小 script
+三份政策文件擴充」,不碰 bridge、不碰排程。

**第二批(獨立拍板,實作面積較大)**:A1(bridge 多 profile 擴充)。它是
「自動蒸餾」原始需求裡唯一真正缺的一塊,但牽動 bridge 三件組+namespace
啟用,且有兩個前置查證項(§2.1)——建議核准方向、先跑查證、查證結果回報
後再開工。搭配的便宜槓桿:`inactivity_hours` 是否調短(§1.1)一併答。

**明確不推薦**:A2(第二套蒸餾器)、B2(frontmatter 計數)、
「每 episode 前置摘要步」(暫緩,待拍板項 5)。

---

## 4. 風險表

| 風險 | 影響 | 緩解 |
|---|---|---|
| recall log 漏記(prompt 層 best-effort) | 統計偏低→誤把常用記憶當 stale | 統計定位=下限值;汰選只出候選清單+人工確認;冷啟動保護(log 覆蓋不足不啟動汰選);豁免清單兜底 |
| named profile 雜訊灌進 inbox(長 coding session 全是 tool 輸出) | consolidation 吃垃圾、模型成本上升 | 既有 too_short/exclusion_signals 先擋;A1 前置查證項 2 先看真實形態;importer `--limit 10` 既有節流;不夠再議摘要步(拍板項 5) |
| 歸檔誤傷仍在用的記憶 | recall miss、脈絡遺失 | 人工確認清單(建議檔位);歸檔可搬回;git 歷史為最終保險 |
| 多 profile namespace 啟用踩壞 default 資料 | event_id 污染、去重失效 | 2.4d §6.1 已預留擴充路徑+fail-closed 測試(矩陣 #27);裸 `hermes:` 恆等 default 向後相容,不需 migration |
| 敏感內容經 named profile 路徑進 inbox | 金鑰入 git | headless fail-closed(reject_import)與偵測 pattern 全部繼承 bridge 既有實作——A1 不新增任何繞過 |
| retention review 加重 daily-memory-check 負擔 | 08:00 job 變慢/變貴 | 每 30 天才真的執行一次;平日只比對日期(orientation read);頻率參數在 yaml 可調 |
| 兩側 recall log 合流遺漏 | headless 的 recall 沒進統計 | JSONL append-only 併集無衝突;掛 sync reverse-merge(有 inbox 先例);統計腳本兩側各讀亦可 |

---

## 5. 實作切分

- **第一批**:
  - `scripts/log_recall.py`+測試=`engineering`;
  - delegation_policy.md 步驟 1.5 補 log 呼叫、`consolidation_policy.yaml`
    加 `retention:` 區塊、`cron_jobs.yaml` daily-memory-check prompt 擴充
    =互動式 CoS session(政策文件);
  - retention review 的執行指引(候選清單格式、豁免判定、歸檔步驟)寫進
    consolidate-memory SKILL.md 或姊妹 skill=互動式 session 決策內容、
    `engineering` 落文件;
  - `memory/.archive/` 目錄慣例+MEMORY.md 索引分層格式=首次 retention
    review 時由 `knowledge` 依 skill 建立。
- **第二批(A1,若核准)**:
  - 前置查證兩項(§2.1)=先做,結果回報後定 per-profile cutover;
  - scanner/importer/adapter 多 profile 支援+namespace 放行+測試
    =`engineering`(依 2.4d §6.1 預留路徑);
  - bridge.yaml 加 profile 清單與 per-profile cutover=部署時比照 2.4d-4
    runbook(migration 窗口 timer disabled 的前置條件在現行排程模型下
    常態成立);
  - `inactivity_hours` 調整(若拍板)=config 一行。

---

## 6. 待拍板項(最小問題集)

1. **汰選動作選哪一檔?** (a) 候選清單→人工確認→歸檔 `memory/.archive/`
   (建議);(b) 全自動歸檔(不問,但可搬回);(c) 照原話直接刪(git 仍可
   考古)。**同時請核准豁免清單**:拍板決策/安全教訓/`feedback_*` 永不
   進候選。
2. **A1(named profile 自動蒸餾)核准方向、先跑兩個前置查證?**
   附帶:`inactivity_hours` 72 維持或調短(如 24)以縮短蒸餾延遲?
3. **升格語意採 C-a(MEMORY.md 索引分層)?** CLAUDE.md 常駐(C-c)僅保留
   給走既有 Semantic→Procedural 程序的行為規則,不因高頻自動進。
4. **recall 統計的誠實限制可接受?** prompt 層埋點=統計是方向性下限;
   接受即核准 B1(`logs/recall_log.jsonl`+`scripts/log_recall.py`+
   步驟 1.5 補一句)。
5. **「每 episode 前置摘要步」暫緩?** 暫緩=蒸餾點維持在 consolidation
   (現狀,零新增模型呼叫);待 A1 上線後若 consolidation 消化不了再議。
