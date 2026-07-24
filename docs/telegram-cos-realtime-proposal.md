# Telegram → CoS 即時指令橋接 評估案（v1）

日期：2026-07-24　狀態：**v1 草案——純評估案，待使用者核准方向；
「核准後怎麼實作」也維持在設計層級，本案不含任何實作排程**
負責規劃：`planning` domain
負責領域（若日後成案）：`engineering`（adapter 路由與 job source、
handler、測試）；`automation`（若涉及排程週期調整才進場）；本評估案
本身零程式碼、零設定變更。

**依賴聲明**：「即時」的前提是 worker 與 telegram adapter **常駐在線**
——`automation` 正在並行實作「WSL + hermes systemd --user 開機常駐化」
（Task Scheduler 喚醒＋linger）。本案以「常駐機制已存在」為前提評估；
若常駐案未完成，本案任何選項的「即時」都只在服務手動啟動期間成立。
另外 WSL 睡眠/喚醒窗口天然存在（機器睡著就沒有即時），本案不假裝能
超越這個物理限制。

依賴文件與現況出處：

- `ARCHITECTURE.md` §1／§2（`hermes/adapter/invoke_cos.sh`＝headless
  `claude -p` 入口；job 生命週期；telegram 對話性來源的
  `thread_id → session_id` resume（<24h 熱）設計；jobs.db／worker）。
- [hermes-integration-roadmap.md](hermes-integration-roadmap.md)
  Stage 2.5–2.7 節（現行 Telegram 訊息的歸宿：episode → 排程 triage →
  人工核准 dispatch → 通知，**非即時**）。
- `hermes/adapters/telegram.py`（polling adapter、`allowed_chat_ids`
  白名單、`delivered_at` 回覆追蹤、Stage 4 新增的 `push_message()`
  主動推播）。
- `.claude/settings.json`＋CLAUDE.md（headless 模式的權限邊界：Bash
  最小白名單、`memory/inbox/` 只能新增——本案安全面的既有地基）。
- Stage 2.6d 成本基準（dispatch 單筆 $0.846；2.5d triage 每筆遠低於此）
  ——成本面的實測錨點。

---

## 版本標記

- **v1**（2026-07-24）＝第一個正式版本（評估草案）。使用者已明確拍板
  **先規劃就好，不實作**——本案交付架構選項比較＋明確建議＋待拍板項，
  不交付實作步驟細節。

---

## 0. 定位與範圍邊界

**一句話定位**：評估「使用者在 Telegram 打指令，CoS 即時接手執行並
回覆」的可行架構——比較三個選項（即時直通／縮短排程週期／混合），
從安全、成本、與既有 2.5–2.7 管線的關係三個面向給出明確建議。

### 0.1 現況（評估的起點，不猜測）

- Telegram 訊息目前的路徑：polling adapter 收訊 → 進入 Stage 2 episode
  bridge 體系 → **排程** triage（08:15 pipeline）→ `action_candidate`
  需**人工核准**才 dispatch → 通知（08:25 notifier）。從發訊到有動作，
  以「天」為單位，且中間有人工 gate——**這是刻意的設計**（人是未信任
  內容與有工具環境之間的結構性 gate，2.6 拍板），不是缺陷。
- 另一方面，v0.1 架構本來就設計過 telegram job 的直通路徑（telegram
  source job → worker → `invoke_cos.sh` headless CoS → 回覆，含
  thread resume），基礎設施（jobs.db、worker、adapter 收發、
  `delivered_at` 追蹤）都存在且驗證過——「即時」不是從零發明，是
  **選擇性地啟用一條已有地基的路**。
- 安全現況：`telegram.json` 的 `allowed_chat_ids` 白名單既存（dashboard
  只回報筆數不洩內容）；headless CoS 受 `-p` 模式權限邊界約束（Bash
  最小白名單、memory 只能寫 inbox、delegation policy 共用）。

### 0.2 本案明確不評估／不做

- **不取代 2.5–2.7 管線**：排程 triage／人工核准 dispatch 是已驗收的
  資產，任何選項都以「並存」為前提，不動既有管線一行。
- **不開放非白名單 chat**：`allowed_chat_ids` 是硬前提，本案不評估
  公開 bot。
- **不做「Telegram 遙控任意 shell」**：即時路徑的能力上限＝headless
  CoS 的既有權限邊界，本案不放寬 `-p` 模式的任何白名單。
- **不承諾睡眠喚醒**：機器睡著時沒有即時，喚醒節奏歸 automation
  常駐案。

---

## 1. 架構選項比較

### 選項 A — 即時直通（每則訊息立即喚醒 headless CoS）

allowlist chat 的**每一則**訊息：adapter 立即 enqueue（telegram source
job）→ worker 即時消化 → `invoke_cos.sh`（`claude -p`，thread 熱則
`--resume`）→ 回覆送回 Telegram。

- **優點**:互動體驗最好（類聊天）；完全走 v0.1 既有設計，機制新增最少。
- **安全面**：觸發面＝allowlist chat 的任何訊息——**遠端文字直接驅動
  有工具的 headless agent**，每則都是。Telegram 帳號被盜或手滑轉傳，
  就是一次全額觸發。prompt injection 面最大（所有閒聊、轉傳內容都進
  `claude -p`）。
- **成本面**：每則訊息一次 `claude -p`。實測錨點：dispatch 單筆
  $0.846（帶 Agent 工具的完整任務）；輕量對話會低一些，但以「則」計費
  的結構讓成本與聊天習慣直接掛鉤，**不可控**。
- **與既有管線關係**：同一則訊息既觸發即時 job 又進 episode bridge，
  會產生「即時已回、隔天 triage 又消化一次」的重複處理，需要額外去重
  設計——並存成本不低。

### 選項 B — 保持排程、縮短週期

pipeline timer 從每日一次改高頻（例如每 15–30 分鐘）。

- **優點**：零新機制、人工核准 gate 原樣保留。
- **缺點**：**本質上不是即時**（最好情況仍等一個週期＋人工核准）；
  高頻跑 triage 的成本隨頻率線性上升且多數是空轉；更根本的問題是
  **語意錯配**——triage 管線是為「對話沉澱成記憶/行動候選」設計的，
  不是為「指令-回覆」設計的，縮短週期改變不了它不會即時回覆 Telegram
  這件事。
- 結論：不能達成使用者要的體驗，僅列為對照。

### 選項 C — 混合：指令前綴觸發即時，其餘照舊（**建議方案**）

- **觸發規則**：僅 allowlist chat **且**訊息帶明確指令前綴（建議
  `/cos <內容>`，Telegram 原生指令語法，前綴可拍板）才走即時路徑；
  **其餘訊息完全照舊**走既有 episode → 排程 triage 管線，零改動。
- **即時路徑**：命中前綴 → enqueue 一個**新 source**（如
  `telegram_command`，與既有 `telegram`／`bridge_*` source 區隔，沿用
  2.5a 的 `enqueue_once`／`external_key` 冪等基礎）→ worker →
  `invoke_cos.sh`（headless，thread 熱則 resume）→ `send_message()`
  回覆（`delivered_at` 既有追蹤）。
- **為什麼是它**：
  1. **意圖明確性**＝安全的第一道柵欄：只有使用者明確打 `/cos` 的訊息
     才會驅動有工具的 agent——閒聊、轉傳、誤觸都不會；prompt injection
     面從「所有訊息」縮到「使用者親手下的指令」。
  2. **成本可控**：付費事件＝明確指令數，不＝聊天量；可再加護欄
     （§2.3）。
  3. **與既有管線零衝突**：非指令訊息的路徑一字不動；指令訊息用獨立
     source，triage 管線天然不會重複消化（source 隔離），不需要複雜
     去重。
  4. 人工核准 gate 的既有鐵律不受影響——`/cos` 是**使用者本人**下令，
     「人在迴路」發生在發訊當下，與 2.6「未信任 episode 內容需人工
     核准」保護的對象（自動沉澱的內容）不同層，不構成繞過。

## 2. 選項 C 的設計層要點（維持設計層級，不展開實作步驟）

### 2.1 安全邊界（誰能觸發、能觸發到什麼）

- **誰能觸發**：`allowed_chat_ids` 白名單（既有機制）∩ 帶 `/cos` 前綴
  ——雙條件。非白名單 chat 的 `/cos` 一律忽略＋（可選）記一筆。
- **能觸發到什麼**：headless CoS 的既有權限邊界，本案不放寬——`-p`
  模式 Bash 最小白名單、`memory/` 正本不可寫（inbox-only）、delegation
  policy 共用。**誠實標註**：這仍是「遠端文字 → 有工具的 agent」通道，
  白名單內帳號被盜＝可以下指令；緩解是 Telegram 帳號本身的 2FA（使用者
  side）＋成本護欄（§2.3）＋audit（jobs.db 天然記錄每筆指令與結果）。
- **能力分級（設計層選項，列待拍板）**：可考慮試營運期先限制即時路徑
  為「查詢/回報類」（比照 2.5c 的 no-tools 技術強制），穩定後再開全
  能力 headless——分級的技術手段（旗標組合）已在 2.5c 實測過，不是
  新發明。

### 2.2 與既有機制的接點（全部是既有件，無新輪子）

- 收發：`hermes/adapters/telegram.py`（polling＋`send_message`）。
- 佇列與冪等：`hermes/db.py`＋2.5a `enqueue_once`（新 source＋
  `external_key`＝message id）。
- 執行：`hermes/worker.py` source routing（2.5c 先例）→
  `invoke_cos.sh`。
- 對話連續性：`thread_id → session_id` resume（<24h 熱）——
  ARCHITECTURE.md 既有設計。
- 觀測：jobs.db → 新 webui Jobs view 天然可見（含成本欄）。

### 2.3 成本護欄（設計層，值列待拍板）

- rate limit：每 chat 每分鐘 N 則（超出回覆「稍後再試」不 enqueue）。
- 每日指令數／成本上限：達上限後當日改回覆提示不執行（fail-closed
  精神，與 #44585 花費保護同方向）。
- 觀測：成本本來就進 jobs.db，webui 成本頁可見。

## 3. 風險總表

| 風險 | 影響 | 緩解／殘餘 |
|---|---|---|
| 遠端文字驅動有工具 agent（本案本質） | Telegram 帳號被盜＝可下指令 | allowlist＋前綴雙條件；headless 權限邊界不放寬；能力分級選項；audit；**殘餘**：帳號安全依賴 Telegram 側，需知情 |
| 指令內容 prompt injection | 誘導 headless CoS 越權 | headless 白名單＋inbox-only 是技術強制；delegation policy；能力分級可再縮攻擊面；殘餘與 2.6 dispatch 同級、已有處理先例 |
| 成本失控 | 以則計費隨使用量無上限 | §2.3 rate limit＋每日上限（fail-closed）；成本進 jobs.db 可觀測 |
| 與 triage 管線重複處理 | 同訊息被消化兩次 | 選項 C 以 source 隔離天然避免（指令訊息不進 episode 路徑或標記排除——設計細節留實作提案） |
| 「即時」預期落空（機器睡眠/服務未起） | 使用者以為壞了 | 依賴聲明明示；可設計「服務不在線時 Telegram 得不到回覆」的預期管理（甚至由常駐案的喚醒機制間接改善）；webui 常駐燈號（另案）提供對照 |
| worker 併發＝1 的排隊延遲 | 指令遇到長任務要等 | 誠實標註：MAX_CONCURRENT=1 是既有成本控制；是否為即時路徑調整併發屬實作提案的課題，本案不預設 |

## 4. 建議

**採選項 C（混合）**：`/cos` 前綴＋chat allowlist 雙條件觸發即時
headless 路徑（新 source、`enqueue_once` 冪等、thread resume、
`send_message` 回覆），其餘訊息維持既有排程 triage 管線零改動、
兩路並存。若核准方向，下一步是由 `planning` 出實作提案（含 source
命名、去重細節、能力分級與護欄值的最終設計），再依慣例核准後才動工
——本案依拍板維持在評估層級，不含實作排程。

## 5. 待拍板項清單（使用者需回答的最小問題集）

1. **方向**：採建議選項 C（混合），或 A（全訊息即時）／B（縮短排程，
   不建議）？
2. **觸發語法**：`/cos <內容>` 前綴？（或其他關鍵字——需是不會誤觸的
   明確形式）
3. **能力分級**：試營運期即時路徑先走 no-tools 查詢/回報模式（建議），
   或一步到位全能力 headless？
4. **成本護欄值**：每 chat 每分鐘上限、每日指令/成本上限——採建議
   結構、數值由使用者定（或授權實作提案給預設值）。
5. **順序確認**：本案成案的實作排程，是否等 automation 常駐案完成後
   再議（「即時」的前提）？
