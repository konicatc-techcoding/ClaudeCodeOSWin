# WSL Keepalive 監控缺口 補強提案(v1.1)

日期:2026-07-27　狀態:**v1.1——第一階段已拍板並執行完畢(含實測);
第二階段拍板暫緩**

## 版本標記

- **v1**(2026-07-27)= 初版草案。
- **v1.1**(2026-07-27 同日)= 拍板+執行紀錄:
  1. **第一階段(A+B)核准**,Repetition 間隔 15 分鐘、RestartOnFailure
     Count=10/PT1M——已由主 session 親自執行完畢。
  2. **第二階段(C+D watchdog+toast)拍板「暫緩」**——殘餘風險(WSL 本身
     壞掉時靠 webui 紅燈被動發現)已知情接受;待議項 3(通知管道)隨之擱置。
  3. **副作用知情接受**:刻意 `wsl --shutdown` 會在 ≤15 分鐘內被復活;
     要保持關機須先 `schtasks /change /disable`(已補進
     hermes/systemd/README.md 維運要點)。
  4. **實作修正(推翻 §1.1 一項假設,實測為證)**:Repetition **不掛在
     LogonTrigger**——實測(12:13 terminate 後 14+ 分鐘零 tick)證明
     LogonTrigger 的 Repetition 要等下次登入才上膛,重註冊後的當前 session
     是空窗;改為**保留原 LogonTrigger + 新增 TimeTrigger(StartBoundary
     在過去)攜帶 Repetition PT15M**,註冊後立即生效。§1.1「不新增第二個
     trigger」一句作廢。另一實測發現:**RestartOnFailure 對 `schtasks /run`
     手動啟動的實例不生效**,只保觸發器啟動的執行——B 的價值僅限正常
     登入鏈,手動介入後的保險靠 TimeTrigger。
  5. **端到端實測通過(零人工介入)**:`wsl --terminate`(12:13)→
     12:30:00 TimeTrigger tick 自動拉回 task → distro 復活 → worker/
     telegram 12:30:07 由 linger 自動 active;下個 tick 12:45 正常排定。
     驗證紀錄正本在 hermes/systemd/README.md。
負責規劃:`planning` domain
負責領域(實作階段,若核准):Task Scheduler 變更=**主 session 親自執行**
(真實基礎設施操作);腳本類(若核准 C/D)=`engineering`。

**背景(一句話)**:`HermesWslKeepAlive`(LogonTrigger + RestartOnFailure
3 次×1 分鐘)於 2026-07-24 失效後永久放棄,WSL distro 靜默停擺三天——
webui 紅燈有正確顯示,但那是被動面,沒開 UI 就看不到。細節已記於
auto-memory(wsl-keepalive-monitoring-gap),此處不重複考古。

---

## 0. 定位與範圍邊界

**一句話定位**:讓 keepalive 這條「基礎設施層」鏈路在失效後能**自動拉回**
(第一層,價值最高),並評估失效持續時的**Windows 側通知**(第二層)——
兩層分開拍板,不綁成一個大方案。

### 0.1 明確不做

- **不自癒任何個別 service**:自癒對象只有 keepalive task/distro 層。
  被使用者明確 stop 的服務**絕不**由本案任何機制拉回——不破壞
  webui-service-control-proposal v1.1 已拍板的 stop 語意(「停止後不自動
  恢復,直到下次 Windows 登入/WSL distro 重啟時由 systemd 依 enable
  狀態重新拉起」)。註:本案自癒若重啟了 distro,enabled 服務被 linger
  拉起,屬於該語意**明文列出的例外情境**(distro 重啟),不是違反。
- **watchdog 不住在 WSL 裡**:distro 死掉時 WSL 側一切(systemd timer、
  notifier、telegram bot)跟著死——偵測與通知路徑一律在 Windows 側。
  `hermes-bridge-notifier` 只能負責「distro 在線時的服務層異常」,不作為
  distro 層防線。
- **偵測不喚醒 distro**:任何「查狀態」只能用 `wsl --list --running` /
  `--list --verbose` 這類不啟動 distro 的指令(先例:
  `dashboard/data_resident.py` 分層探測、凍結指令常數)。喚醒 distro 只
  能是「自癒動作」的明確意圖,不能是偵測的副作用。
- **不依賴 WSL 的通知管道**:Telegram bot 在 WSL 內,直接排除。
- **不改 keepalive 本體設計**:vbs wrapper、`sleep infinity`、
  IgnoreNew、PT0S 無限時限維持不動——只補「放棄後沒人管」的缺口。

### 0.2 與「只偵測不修復」原則的誠實對照

排程健康表的「只偵測不修復」是**服務層**原則(紅燈不代打人工判斷)。
keepalive 是**基礎設施層**:它的存在目的本來就是「自動維持 distro 在線」,
RestartOnFailure 本身就是既有的自癒機制——本案只是把「自癒會在 3 分鐘
後永久放棄」修好,不是引入新的修復哲學。因此結論是:**基礎設施層自癒
不違反該原則,但邊界必須釘死在 §0.1 第一條**(絕不下探到服務層)。

---

## 1. 方案對照

| 方案 | 內容 | 新增程式碼 | 復原延遲 | 主要弱點 |
|---|---|---|---|---|
| E. 維持現狀(基準) | webui 紅燈被動偵測 | 0 | 無自癒(靠人開 UI 發現) | 本次事故的現況:三天沒人知 |
| **A. Task 加定期觸發** | LogonTrigger 加 `Repetition`(建議 PT15M);活著時 IgnoreNew=no-op,死了下個 tick 自動拉回 | 0(純 task 設定) | ≤15 分鐘 | WSL 本身壞掉時每 15 分鐘靜默重試,永遠不吵人 |
| B. 調大 RestartOnFailure | 例如 Count=10/Interval=PT1M | 0 | 1–10 分鐘(僅短暫故障) | 治標:持續失敗(如 `wsl --shutdown` 後環境未恢復)仍會用盡放棄——只能當 A 的輔助 |
| C. Windows 側 watchdog 腳本 | 獨立排程腳本:`wsl --list --running`+task 狀態檢查,異常→`schtasks /run`+記錄 | 一支腳本+一個新 task | 依排程間隔 | 多一個要維護的排程,且 watchdog 自身也可能靜默失效;純自癒能力上對 A 邊際價值≈0 |
| D. 通知層 | 自癒持續失敗(連續 N 次)時通知使用者 | 依管道(見 §1.2) | — | 需要一個偵測載體——實務上 D≈C 的簡化版,兩者合併評估 |

### 1.1 方案 A 技術細節(推薦主案)

- **改法**:`schtasks /change` **做不到加 trigger**——正確路徑是
  `schtasks /query /tn HermesWslKeepAlive /xml` 匯出備份 → 在
  `<LogonTrigger>` 內加 `<Repetition><Interval>PT15M</Interval>
  <StopAtDurationEnd>false</StopAtDurationEnd></Repetition>`(不設
  Duration=登入期間無限重複)→ `schtasks /create /f /xml` 重註冊 →
  再次 `/query /xml` 逐項比對(PT0S、IgnoreNew、Principal 不得跑掉)。
- **行為**:task Running(wscript 活著)時,Repetition tick 被
  IgnoreNew 吞掉,零成本 no-op;task 已放棄(Ready+Last Result≠0)時,
  下個 tick 重新啟動整條鏈(wsl.exe 啟動=喚醒 distro=自癒意圖,合規)。
- **間隔取捨**:15 分鐘=最壞停擺窗口 15 分鐘,對「三天沒人知」是三個
  數量級的改善;更短(5 分鐘)沒有技術障礙,只是失敗場景下重試更頻繁。
- **與 LogonTrigger 並存**:就是同一個 trigger 加 Repetition,不新增第二
  個 trigger,語意最單純。
- **副作用要知情**:使用者若刻意 `wsl --shutdown` 想讓 distro 休息,
  ≤15 分鐘內會被復活——現行 RestartOnFailure 本來就會在 3 分鐘內復活
  (行為既存,A 只是把它變成永不放棄)。「要 distro 保持關機須先停用
  task」在 hermes/systemd/README.md 維運要點已是既有慣例,實作時補一句
  進該 README 即可。列為待拍板項 4。

### 1.2 方案 D 通知管道評估(若做)

| 管道 | 依賴 WSL? | 成本 | 評估 |
|---|---|---|---|
| Windows toast(PowerShell WinRT,零外部套件) | 否 | 低 | **建議**。限制:只在使用者登入且在機器前才看得到——但整條 keepalive 鏈本來就是 login-scoped(LogonType=Interactive),限制對齊、不新增假設 |
| 寫檔補充 webui 燈號(last-incident 檔給 tooltip) | 否 | 低 | 仍是被動面,只能當 toast 的補充,不能當主通知 |
| Slack | **待查證** | 中 | Slack 送信有 ledger/allowlist 慣例,且已知送信路徑疑在 WSL 側(hermes CLI)——若查證屬實直接排除;v1 不假設可用 |
| Telegram | 是 | — | 排除(硬約束) |

---

## 2. 推薦組合與理由

**第一階段(建議立即核准):A 為主 + B 為輔,零新程式碼。**

- A 把「永久放棄」修成「永不放棄、最壞 15 分鐘拉回」,直接消滅本次
  事故的形態;B 調成 Count=10/Interval=PT1M,讓短暫故障在分鐘級恢復,
  A 只當超時 backstop。兩者都是同一次 XML 重註冊順手完成。
- C 在「自癒」這件事上對 A 沒有邊際價值(A 已覆蓋),不推薦為自癒方案。

**第二階段(獨立拍板,可暫緩):C+D 合併為一支最小 watchdog——**
「偵測+通知」而非自癒(自癒已由 A 負責)。定期(如每小時)用
`wsl --list --running` 查 distro、`schtasks /query` 查 task Last Result,
發現「distro 不在線且距上次檢查仍未恢復」即發 toast+寫 last-incident 檔。
誠實評估:若接受「A 自癒後殘餘風險=WSL 本身壞掉時靠 webui 紅燈被動
發現」,第二階段可以不做——這是成本/覆蓋的取捨,列待拍板項 3。

---

## 3. 風險表

| 風險 | 影響 | 緩解 |
|---|---|---|
| XML 重註冊時掉設定(PT0S/IgnoreNew/Principal) | keepalive 被 72h 上限殺掉等回歸 | 先 `/query /xml` 備份;重建後逐項比對;主 session 親自執行並實測(`wsl --terminate` → 等 Repetition tick 拉回) |
| 刻意關機被 15 分鐘內復活 | 使用者意圖被機器覆蓋 | §1.1 知情副作用;README 補「保持關機須先停用 task」;拍板項 4 |
| WSL 本身損壞時 A 無限靜默重試 | 換一種形式的「沒人知」 | 第二階段 C+D 通知;若暫緩,誠實接受殘餘風險=webui 紅燈被動面(拍板項 3) |
| 自癒觸發 distro 重啟拉回 enabled 服務,被誤解為破壞 stop 語意 | 語意混亂 | §0.1:distro 重啟拉回 enabled 單元是 v1.1 已拍板 stop 語意的明文情境;本案不新增任何服務層操作 |
| (若做 C)watchdog 自身靜默失效 | 防線疊防線的無限遞迴 | 接受單層(watchdog 只有一個,失效時仍有 A 自癒+webui 紅燈);其 task 進 schtasks 便可被日常 `schtasks /query` 巡檢覆蓋 |
| Repetition tick 與手動 `schtasks /run` 併發 | 重複實例 | 既有 IgnoreNew 已擋,無需新機制 |

---

## 4. 實作切分

- **第一階段(A+B)**:純 Task Scheduler XML 變更——**主 session 親自
  執行**(真實基礎設施操作,不分派):備份 XML → 修改 → `/create /f`
  重註冊 → 比對驗證 → `wsl --terminate` 實測自癒 → README 維運要點
  補述。無任何程式碼變更。
- **第二階段(C+D,若核准)**:watchdog 腳本+toast 通知=`engineering`
  (含「偵測不喚醒 distro」的行為測試,比照 data_resident.py 先例);
  新 task 註冊仍由主 session 執行。Slack 管道查證(憑證在哪側、送信
  路徑)=先於實作的獨立查證項。

---

## 5. 待拍板項(最小問題集)

1. **核准第一階段(A+B)?** 含間隔選擇:Repetition 15 分鐘(建議)
   或更短;RestartOnFailure 調 Count=10/Interval=PT1M(建議)或其他值。
2. **第二階段(C+D watchdog+toast)現在做、暫緩、還是明確不做?**
   暫緩/不做=接受「WSL 本身壞掉時靠 webui 紅燈被動發現」的殘餘風險。
3. **若做 D:通知管道採 Windows toast?**(Slack 列待查證,查證結果
   若在 WSL 側則排除;不查證亦可,直接採 toast。)
4. **「刻意 `wsl --shutdown` 會在 ≤15 分鐘被復活」可接受?**(要保持
   關機須先停用 task——既有慣例的延伸,但從 3 分鐘窗口變成永久有效,
   需明確知情同意。)
