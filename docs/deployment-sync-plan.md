# Deployment Sync Plan — Windows repo ↔ WSL 部署複本

**狀態**：v0.1（Stage 1.4 交付）。同步腳本已交付並通過 dry-run 驗證；**首次真正同步與任何自動排程都尚未執行/啟用**，需使用者核准。

| 端點 | 路徑 | 角色 |
|---|---|---|
| Windows repo | `C:\Users\razer\dev\ClaudeCodeOSWin`（WSL 視角 `/mnt/c/Users/razer/dev/ClaudeCodeOSWin`） | 開發正本——互動式 session、所有近期變更都在這裡 |
| WSL 部署複本 | `/home/razer/dev/ClaudeCodeOSWin`（ext4，刻意不放 /mnt/c 以求效能與 SQLite 可靠性） | 部署側——五個 systemd user 服務從這裡跑 |

不在本方案範圍：`~/.hermes` ↔ `C:\Users\razer\AppData\Local\hermes`（state.db 共用，Stage 0 已用 symlink 解決，本方案完全不碰）。

---

## 1. 現況調查（2026-07-09）

### 1.1 git 狀態

- **兩側都不是 git repo**（皆無 `.git`；Windows 側有 `.gitignore`，是為未來版控準備的，但從未 `git init`）。
- 因此「git-based 同步」目前沒有既有基礎，採用前要先做版控導入決策（見 §3）。

### 1.2 差異程度

- WSL 側是 **2026-07-07 部署時的快照 + runtime 產物**，內容嚴格落後 Windows 側。
- 對全部差異檔做過衝突掃描（內容不同 **且** WSL mtime 較新者）：**零衝突**。所有內容差異都是 Windows 側較新（diff 顯示為 Windows 側純新增段落），WSL 側沒有任何需要保留的正本/程式碼修改。
- WSL 側 `memory/inbox/` 頂層**沒有**未回流的新檔案；`.processed/` 兩側完全一致（同兩個檔案）。原任務書擔心的「WSL 側有大量未同步 inbox」不成立。
- 關鍵落後範例：WSL 側 `hermes/config/cron_jobs.yaml` 仍是舊版一行 prompt，**沒有 N-gate 邏輯**；缺 `docs/`、`registry/{consolidation_policy,capability_lanes,bridge_state_schema}.yaml`、`hermes/session_adapter/`、`.agents/`、`.codex/`、`AGENTS.md`、多個 scripts。
- 兩側文字檔皆為 **LF** 行尾（含 systemd units、.sh、.py、.yaml），無 CRLF 污染問題。

### 1.3 WSL 側特有物 / 分側維護檔案（同步時必須保護）

| 路徑 | 性質 |
|---|---|
| `hermes/config/.env` | WSL-only 機密（mode 600），Windows 側沒有此檔 |
| `hermes/config/telegram.json` | **部署側本地維護的密鑰**（bot token + 白名單）。依 2026-07-09 bot 邊界決策（見 §2.1）不同步。目前兩側內容恰好 byte-identical（部署時複製的起點）；今後部署側直接改自己的檔，Windows 側副本視為 unmanaged reference |
| `hermes/state/*`（`rss_seen.json`、`telegram_offset.json`、`hermes_skills_snapshot.json`） | 活的 runtime 狀態，adapters 持續讀寫；Windows 側同名檔是過期舊 copy |
| `hermes/jobs.db` | Hermes job queue（SQLite），worker 持續寫入 |
| `logs/` | 服務日誌 |
| `.venv/` | Linux 原生 venv（Windows 側是 Windows 原生 venv，互不相容） |
| `.claude/settings.local.json` | 機器本地權限設定 |
| `.claude/settings.json`、`.claude/launch.json` | **平台適配版**：引用 `.venv/bin/...`；Windows 版引用 `.venv/Scripts/...`。內容合法地分歧 |

### 1.4 重要環境事實

- **live systemd units 是 symlink 指進 repo**：`~/.config/systemd/user/hermes-*.{service,timer}` → `/home/razer/dev/ClaudeCodeOSWin/hermes/systemd/*`。同步 `hermes/systemd/` 等於直接改 live unit 定義（daemon-reload / 下次啟動後生效）。目前兩側 unit 內容完全一致，同步是 no-op，但這條路徑永遠要有意識地對待。
- **喚醒 WSL 會觸發 persistent timer catch-up**：本次調查喚醒 Ubuntu 時確認 timers `LAST` 顯示當日 14:50 已補跑過一輪（更早的喚醒觸發的）；`hermes-rss.timer` 在喚醒後約 5 分鐘再度排定。每次為了同步喚醒 WSL 都可能消耗 headless 呼叫，這是同步作業的固有副作用。
- 預設 WSL distro 是 `docker-desktop`，操作一律要帶 `-d Ubuntu`。

---

## 2. 內容分類表（每類的方向與策略）

| # | 內容 | 方向 | 策略 | 理由 |
|---|---|---|---|---|
| 1 | 程式碼 / 設定 / registry / docs / agents 定義 / skills / `hermes/systemd/`（含 `hermes/config/cron_jobs.yaml`、`rss_feeds.yaml`——這兩個是「有哪些排程/來源」的宣告，不是密鑰，**可同步**；`telegram.json` **不在此列**，見第 3 類） | Windows → WSL 單向 | rsync `--delete`（排除清單外全量鏡像） | Windows 是開發正本；WSL 側零本地修改（已驗證）。`--delete` 讓改名/刪除也能傳播，避免部署側殘留舊檔 |
| 2a | `memory/inbox/` 頂層（新件） | **雙向合併（只新增）** | Phase 2：WSL → Windows `--ignore-existing`，絕不覆蓋、絕不刪除 | inbox 規約是「背景只能新增、不改既有檔」，且檔名帶時間戳唯一，新增檔案雙向合併天然安全 |
| 2b | `memory/inbox/` 頂層（已歸檔件的清理） | WSL 側移除 | Phase 1：檔名存在於 Windows `.processed/` 或 `.failed/` 者，從 WSL inbox 頂層刪除。**必須排在合併之前**（2026-07-29 修正順序）：合併的 `--ignore-existing` 只比對 Windows inbox 頂層、看不到歸檔目錄，先合併會把已消化的檔案重新塞回 Windows 頂層造成重複 consolidation | 關閉生命週期：WSL 產生 → 合併到 Windows → consolidation 移入 `.processed/` → 下次同步時從 WSL inbox 移除，否則會被永遠當成「待處理」重複合併 |
| 2c | `memory/inbox/.processed/`、`.failed/` | Windows → WSL 單向 | 併入 Phase 3 正向同步 | 歸檔正本在 Windows（consolidation 在 Windows 側跑）；下發讓兩側 inbox 視圖一致，N-gate 只數頂層所以不受影響。體積小，同步成本可忽略 |
| 2d | `memory/*.md` 正本 | Windows → WSL 單向 | 併入 Phase 3；Phase 0 衝突掃描保護 | 規約：正本只有互動式 session / consolidation pass（都在 Windows 側）能編輯。若掃描發現 WSL 側正本較新且不同（=有人違規改了部署側），**中止並人工裁決**，不自動蓋掉 |
| 3 | `.venv/` | 不同步 | 排除 | 平台原生（Windows `Scripts\` vs Linux `bin/`），互不相容，各自重建 |
| 3 | `hermes/jobs.db`（含 `-wal`/`-shm`） | 不同步 | 排除 | WSL worker 持續寫入的 SQLite；同步中複製必損壞，且 Windows 側根本不該有它的副本 |
| 3 | `hermes/state/` | 不同步 | 排除（整個目錄） | 活 runtime 狀態、WSL 權威。若被 Windows 側過期副本蓋掉，RSS 會重複處理、Telegram 會重放舊訊息 |
| 3 | `logs/` | 不同步 | 排除 | WSL 本地 runtime 產物，Windows 側無對應 |
| 3 | `hermes/config/.env` | 不同步 | 排除 | WSL-only 機密；rsync 排除同時保護它不被 `--delete` 刪掉 |
| 3 | `hermes/config/telegram.json` | 不同步 | 排除（`--delete` 亦不影響：rsync 排除項預設受刪除保護） | **本機部署密鑰／分側維護**（與 `.env`、`settings.local.json` 同類）。bot 邊界決策見 §2.1 |
| 3 | `__pycache__/`、`*.pyc`、`.DS_Store`、`.git/` | 不同步 | 排除 | 產物/未來版控目錄 |
| 4 | `.claude/settings.local.json` | 不同步 | 排除 | 定義上就是機器本地 |
| 4 | `.claude/settings.json`、`.claude/launch.json` | 不同步（分側維護） | 排除 | 兩側差異只在 venv 路徑（`bin/` vs `Scripts/`），是必要的平台適配。若未來把兩份權限條目合併成一份可攜版，可移出排除清單 |
| 4 | `.claude/agents/`、`.claude/skills/`、`.claude/scheduled_tasks.lock` | Windows → WSL 單向 | 併入 Phase 3 | agent/skill 定義決定 headless CoS 行為，必須跟上開發正本 |

### 2.1 Telegram bot 邊界（2026-07-09 設計決策）

- **ClaudeCodeOS 使用自己的 Telegram bot**，作為 CoS / AgentOS 的**控制入口**——即 `hermes/adapters/telegram.py` 輪詢的那個 bot，token 與白名單在 `hermes/config/telegram.json`。
- **Hermes 各 profile 維持自己的 Telegram bots**，是 profile 的**對話入口**（使用者可能直接對 profile bot 下命令）。這些 bots **不由本 repo 管理**——ClaudeCodeOS 不管理、不同步、不覆寫。
- Hermes profile sessions 之後只透過 `hermes/session_adapter/`（read-only）與 Stage 2 bridge 被讀取，本 repo 不寫入。
- 落實到同步機制：**`cron_jobs.yaml`、`rss_feeds.yaml` 可同步（非密鑰）；`telegram.json` 不同步**——ClaudeCodeOS Telegram adapter 的 token 在部署側本地維護，不經 repo 同步下發。詳見 `hermes/README.md`「Bot 邊界」。

---

## 3. 方案比較與選型

| 方案 | 評估 | 結論 |
|---|---|---|
| **git-based**（Windows commit → WSL pull，file:// remote 或 bare repo 中介） | 天然衝突偵測與歷史。但：(1) 兩側目前都不是 git repo，導入 = 先做版控範圍決策（memory/ 要不要進版控、歷史起點）——超出本 stage、該由使用者拍板；(2) 對「同一 tracked 檔案兩側要不同內容」（`.claude/settings.json`/`launch.json`）沒有乾淨解法，要 skip-worktree 或拆檔；(3) inbox 回流仍需額外腳本，git 不會消除本方案最難的部分 | **不採用（v0.1），列為 v0.2 升級路徑**：先 `git init` Windows 側取得歷史與變更追蹤，同步機制可先維持 rsync 不變 |
| **rsync 單向 + inbox 反向合併（混合式）** | 不改變任何現有結構、立即可用、冪等、`--dry-run` 完整預演；排除清單顯式列舉所有分側/排除項；衝突偵測用前置掃描補足（比較內容 + mtime） | **採用**。見 §4 |
| **WSL 直接掛 /mnt/c 工作（消滅複本）** | 9p/DrvFs 效能差（正是當初搬去 ext4 的原因）；SQLite 在 9p 上鎖定語義不可靠（jobs.db 會出事）；DrvFs 權限是假的 777，systemd/credential 檔權限失真 | **否決** |
| robocopy（Windows 側發起） | 只能到 /mnt/c 邊界，寫不進 WSL ext4；同能力下不如 WSL 內 rsync | 否決 |

**選型：混合式 rsync**——正向鏡像（Windows → WSL、排除清單、`--delete`）+ inbox 反向合併（只新增）+ 歸檔清理 + 前置衝突掃描。實作為 `scripts/sync_to_wsl.sh`，在 WSL 內執行（經 `/mnt/c` 讀 Windows 側）。

## 4. 同步腳本

`scripts/sync_to_wsl.sh`（bash，LF，於 WSL Ubuntu 內執行）：

```
# 預演（不改任何檔案）
wsl.exe -d Ubuntu -e bash /mnt/c/Users/razer/dev/ClaudeCodeOSWin/scripts/sync_to_wsl.sh --dry-run
# 真正執行（需先停 worker/telegram，或帶 --allow-running）
wsl.exe -d Ubuntu -e bash /mnt/c/Users/razer/dev/ClaudeCodeOSWin/scripts/sync_to_wsl.sh --apply
```

- **Phase 0 前置檢查**：路徑/rsync 存在、`hermes-worker`/`hermes-telegram` 服務狀態（apply 時運行中即中止，除非 `--allow-running`）、衝突掃描（WSL 側較新且內容不同 → 列出並中止 apply，除非 `--force`）。
- **備份**（僅 apply，可 `--no-backup` 跳過）：`~/backups/ClaudeCodeOSWin-wsl-pre-sync-<ts>.tar.gz`（排除 `.venv`）。
- **Phase 1** inbox 歸檔清理（先清後併，見 §2 類別 2b 的順序理由；dry-run 下 Phase 2 會以 `--exclude` 排除待清檔，確保預演輸出等於 apply 實際行為）；**Phase 2** inbox 反向合併；**Phase 3** 正向鏡像——策略如 §2。
- 冪等：重複執行收斂到同一狀態。注意 `--apply` 一定從 Windows 發起或在 WSL 內跑 `/mnt/c` 側的腳本副本——腳本本身也是被同步的對象。

## 5. 同步時機（本階段只建議、不實作排程）

- **v0.1：手動觸發**。時機 =「Windows 側改了會影響部署行為的檔案之後」（`hermes/`、`registry/`、`.claude/agents|skills`、`CLAUDE.md`、`memory/` 正本），以及「懷疑 WSL 側 inbox 有新件待回流」時。
- 未來若要排程（**需使用者核准後另案實作**）：建議做成 Windows 側 scheduled task 或 WSL systemd timer 每日一次 `--dry-run` + 通知，apply 仍保持人工；因為 apply 涉及停服務與衝突裁決，不適合全自動。

## 6. 首次同步 runbook（待使用者核准後執行）

1. `wsl.exe -d Ubuntu -e bash /mnt/c/.../scripts/sync_to_wsl.sh --dry-run`，人工過目清單（重點：不得出現 `.venv`、`jobs.db`、`hermes/state`、`.env`、`telegram.json`、`settings*.json`、`launch.json`）。
2. `wsl.exe -d Ubuntu -e bash -lc "systemctl --user stop hermes-worker hermes-telegram"`（timers 可留著；它們觸發的 oneshot 服務跑完即退，撞上同步窗口的機率低，介意可一併 stop）。
3. `... sync_to_wsl.sh --apply`（自動先做 tar 備份）。
4. `systemctl --user daemon-reload`（unit 檔即使內容沒變，養成習慣——它們是 symlink 進 repo 的）。
5. `systemctl --user start hermes-worker hermes-telegram`，`systemctl --user list-units 'hermes*'` 確認 active。
6. 抽查：WSL 側 `hermes/config/cron_jobs.yaml` 應已含 N-gate prompt；`docs/`、`registry/consolidation_policy.yaml` 應已存在；`hermes/config/telegram.json` 與 `.env` 應維持部署側原樣未動。

## 7. 風險與緩解

| 風險 | 緩解 |
|---|---|
| 同步中服務正在讀寫檔案（worker 換程式碼到一半） | apply 預設要求 worker/telegram 已停；jobs.db/state 完全排除，不存在 SQLite 半寫問題 |
| 同步當下 WSL headless job 正好寫入新 inbox 檔 | Phase 3 對 inbox 頂層完全不動（不含在 `--delete` 範圍），新檔安全，下次 Phase 2 回流 |
| 部分同步的半完成狀態（中途斷電/中斷） | rsync 以檔案為單位原子替換；重跑一次即收斂（冪等）。服務在 apply 期間是停的，不會讀到混合狀態 |
| `--delete` 誤刪 WSL 側需要的檔案 | 所有 WSL-only 合法檔案都在排除清單（rsync 排除項預設受 `--delete` 保護）；apply 前有 tar 備份 |
| WSL 側被違規手改、被同步蓋掉 | Phase 0 衝突掃描（內容+mtime）攔截並中止，人工裁決 |
| Telegram bot 設定被誤同步/誤刪（bot 邊界破壞） | `telegram.json` 在排除清單 + 衝突掃描排除；`--delete` 不影響排除項。Hermes profile bots 根本不在本 repo 內，無同步路徑可觸及 |
| 喚醒 WSL 觸發 timer catch-up、消耗 headless 呼叫 | 已知固有副作用；同步視窗選在不介意補跑的時段，或先 `systemctl --user stop` 相關 timer 再同步（另案） |
| 同步改動 `hermes/systemd/` = 改 live unit（symlink） | runbook 固定含 daemon-reload；unit 變更需特別留意（目前兩側一致） |

## 8. Rollback

- **WSL 側**：解開 apply 前自動產生的 tarball——`tar -C ~/dev -xzf ~/backups/ClaudeCodeOSWin-wsl-pre-sync-<ts>.tar.gz`（`.venv` 不在備份內也不被同步觸碰，無需還原），然後 daemon-reload + 重啟服務。
- **Windows 側**：同步對 Windows 側唯一的寫入是 Phase 2 的 inbox 新增檔（只增不改），rollback = 刪掉那幾個新檔即可；其餘 Windows 內容完全不被觸碰。
