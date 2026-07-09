# Stage 0 — Hermes Shared Storage Bootstrap（Windows ↔ Ubuntu/WSL2）

日期：2026-07-07　狀態：**完成**（Phase B maintenance window 已於同日執行，全部 DoD 通過；共用模式為「互斥使用」，見下）

## 目標

Windows Hermes（`C:\Users\razer\AppData\Local\hermes`）為唯一 Source of Truth；
Ubuntu (WSL2) Hermes（`/home/razer/.hermes`）共用其 `state.db`、`sessions/`、`skills/`、`memories/`，
但保留 Ubuntu 專屬的 `config.yaml`、`.env`、`hermes-agent/`。不建立第二份 state.db、不改 schema、不合併 session。

## 採用的方法：檔案系統 symlink（`/home/razer/.hermes/* → /mnt/c/...`）

```
/home/razer/.hermes/state.db  -> /mnt/c/Users/razer/AppData/Local/hermes/state.db
/home/razer/.hermes/sessions  -> /mnt/c/Users/razer/AppData/Local/hermes/sessions
/home/razer/.hermes/skills    -> /mnt/c/Users/razer/AppData/Local/hermes/skills
/home/razer/.hermes/memories  -> /mnt/c/Users/razer/AppData/Local/hermes/memories
```

原有的 Ubuntu 目錄保留為 `sessions.pre-stage0`、`skills.pre-stage0`、`memories.pre-stage0`（rollback 用）。
`state.db` 在 Ubuntu 端原本不存在，symlink 是新增的。
SQLite 會解析 symlink，`-wal`/`-shm` sidecar 建立在 Windows 真實路徑旁——與 Windows Hermes 共用同一組 WAL 檔，**不會**產生第二份 db。

### 為什麼不是官方設定

實地查過 Hermes v0.18.0 原始碼（`hermes_constants.py`、`hermes_state.py`、`hermes_cli/`）、`hermes --help`、`config.yaml` 支援欄位、doctor 輸出：

- 唯一官方路徑覆寫是 **`HERMES_HOME` 環境變數**（`hermes_constants.get_hermes_home()`），但它搬移**整個** home——包含 `config.yaml`、`.env`、`auth.json`、`hermes-agent/`（含 Linux venv/node 二進位）。設成 `/mnt/c/...` 會讓 Ubuntu 直接使用 Windows 的 config/venv，違反「保留 Ubuntu 專屬 config」且根本跑不起來（平台二進位不相容）。
- **沒有**任何 granular 設定（config key、CLI flag、env var）可單獨指定 state.db / sessions / skills / memories 路徑。
- profiles 機制（`~/.hermes/profiles/`）是多實例隔離，不是共用機制。

因此依「官方支援 > symlink > bind mount」順位，落到 symlink。bind mount（`mount --bind /mnt/c/... ~/.hermes/...`）效果與 symlink 相同但需要 root、且 WSL 重開機不保留（要進 `/etc/fstab`），沒有額外好處，故不採用。

## 關鍵實測：SQLite WAL 跨 9p/drvfs 邊界的行為

全部在 scratch DB（`backup_stage0/waltest.db`，WAL mode，NTFS 上）實測，未動正本：

| # | 情境 | 結果 |
|---|------|------|
| 1 | WSL 唯讀開啟（無 Windows 連線） | **OK**（journal_mode=wal, 正常查詢） |
| 2 | WSL 讀寫 insert（無 Windows 連線） | **OK**（integrity ok） |
| 3 | Windows 先開（即使 idle 不寫）、WSL 任何存取（含唯讀） | **全部失敗 `disk I/O error`**（300/300 insert 失敗；純 SELECT 也失敗） |
| 4 | WSL 先開、Windows 後加入，雙邊各 hammer 500 筆無間隔並發寫入 | **雙邊 500/500 全成功、零遺失、integrity ok**（9p 有把 byte-range lock 轉送給 Windows，鎖協調實際有效） |
| 5 | live `state.db`（gateway 運行中）WSL 唯讀 | 失敗 `disk I/O error`；`immutable=1` 唯讀 **OK**（sessions=59, messages=3753） |

結論：

- **Windows 先持有 → Ubuntu 被完全擋下，且是干淨失敗**（SQLite 拿不到 `-shm` 的 lock region，回 disk I/O error，不寫任何東西，無損壞風險）。Hermes 對此有內建處理：CLI 顯示 `Error: Could not open session database: disk I/O error`，doctor 顯示警告，不會 crash。
- **Ubuntu 先持有 → 雙邊鎖協調正常**，並發寫入實測無遺失、無損壞。
- Hermes 的 `apply_wal_with_fallback()` 的 WAL→DELETE 降級**不會**被觸發（`disk i/o error` 不在 `_WAL_INCOMPAT_MARKERS`，且「on-disk header 是 WAL 就絕不降級」），所以 Ubuntu 端永遠不可能改掉正本的 journal mode。
- 兩端 Hermes 同版（v0.18.0 / 2026.7.1，schema_version=17），無 schema migration 風險。

**淨效果**：Windows Hermes（gateway / Desktop / dashboard）24 小時開著時，Ubuntu 對 state.db 的一般存取（含唯讀）一律干淨失敗；要用 Ubuntu Hermes 的 session 功能，必須先停 Windows Hermes（互斥使用）。非 DB 的共用（skills/、memories/、sessions/ dump 檔）不受影響，隨時可用。

### 為什麼不選其他替代方案

- **Ubuntu 唯讀模式**：Hermes 沒有使用者可設定的 read-only/immutable 開法（原始碼僅內部用途的 `mode=ro`，同樣被 `-shm` lock 擋下）。`immutable=1` 只能用於外部工具（如本 repo 的 HermesSessionAdapter snapshot 模式、驗證腳本），live 寫入時讀到的快照可能不一致，不能當正式讀取途徑。
- **journal_mode 改 DELETE**：Windows 端 Hermes 每次連線都會把它設回 WAL；且跨界 DELETE-mode 鎖行為未驗證。死路。
- **資料搬到 ext4、Windows 走 `\\wsl$`**：違反「Windows 為 SoT」，且把同樣的 9p 問題轉嫁給運作中的 Windows 端。
- **第二份 state.db / import-export**：任務明令禁止。
- **Hermes 官方 remote/shared 機制**：v0.18.0 沒有 CLI 可用的 remote session store。（gateway/api_server 是訊息平台介面，不是 session 儲存共用。）未來若官方加入，優先改用。

## 設定前狀態（2026-07-07）

- Windows：`state.db` 63,434,752 bytes、WAL、**59 sessions / 3,753 messages**、integrity ok；gateway 運行中（pid 10976，telegram connected）、Hermes Desktop 運行中、dashboard 運行中（127.0.0.1:53929）。
- Ubuntu：`~/.hermes/` 有 config.yaml/.env/hermes-agent/auth.json；`sessions/`、`memories/` 空目錄；`skills/` 只有預設 built-in；**沒有 state.db**。

備份（動手前完成）：

```
C:\Users\razer\AppData\Local\hermes\backup_stage0\state.db.stage0.bak
size   = 63434752
sha256 = ac55766c3cb20a19418cfd13dd29c2595fb7e1efca9befdf2bee96bd6563abb2
內容    = 59 sessions / 3753 messages, integrity ok
方式    = SQLite backup API（mode=ro 來源、live WAL 下的官方一致性快照法）
```

重建備份：`py -3.11 scripts/hermes_stage0_backup.py`（Windows 端）。

## 設定後狀態與驗證結果（實際輸出摘要）

執行：`bash scripts/hermes_stage0_bootstrap.sh`（idempotent，重跑安全），驗證：`bash scripts/hermes_stage0_verify.sh`。

| DoD | 結果 | 證據（實際輸出） |
|-----|------|------------------|
| 1. Windows Hermes CLI 正常 | **PASS** | 事前 `hermes sessions stats` → 59/3753；事後 `hermes --profile default sessions stats` → `Total sessions: 60 / Total messages: 3757 / Database size: 60.5 MB`，list 首列即測試 session |
| 2. Ubuntu CLI 正常＋看到 59 sessions | **PASS** | window 內：`hermes sessions stats` → `Total sessions: 59 / Total messages: 3753`、`hermes sessions list` 正常列出；`hermes doctor` 全綠；`hermes skills list` 讀到 Windows 端 local skills。（Windows Hermes 運行中時則為預期的乾淨降級：`Error: Could not open session database: disk I/O error`） |
| 3. Windows Dashboard 正常 | **PASS** | 事前與恢復後皆：root HTTP 200、`/api/sessions` 401（認證保護正常）；恢復後 listening 127.0.0.1:9119 |
| 4. Ubuntu 無第二份 state.db | **PASS** | `~/.hermes/state.db` 是 symlink；無本機 state.db / -wal / -shm 真檔（verify script Phase A 全 PASS） |
| 5. 測試 session 三方可見 | **PASS** | 測試 session `20260707_155920_3f845e`（source=cli，4 msgs，模型回覆 `STAGE0-OK`）由 **Ubuntu** 建立。Ubuntu CLI list 看得到（"just now"）✔；Windows CLI（default profile）list 首列 ✔；adapter `py -3.11 hermes/session_adapter/adapter.py list` 末列 ✔；Dashboard 與 CLI/adapter 讀同一份 state.db（API 需登入 token，畫面確認留給使用者一鍵完成） |
| 6. Windows 資料未破壞 | **PASS** | 最終 `sessions=60 messages=3757`（= baseline 59/3753 + 測試 session 1 筆/4 msgs）、`PRAGMA integrity_check` = ok；所有跨界壓力測試都在 scratch DB 上做 |

### Phase B 實際輸出（2026-07-07 maintenance window）

`bash scripts/hermes_stage0_verify.sh --window` → **PASS=10 FAIL=0**：

```
PASS: normal (locking) read-only open works — window is clear   (sessions = 59)
PASS: hermes sessions stats      (Total sessions: 59 / Total messages: 3753)
STAGE0-OK
PASS: test session created from Ubuntu
—    Stage 0 shared-storage verification te   just now   20260707_155920_3f845e
  integrity: ok
PASS: integrity_check ok after write
```

`hermes doctor`（Ubuntu）關鍵行：

```
✓ ~/.hermes/sessions/ exists  ✓ ~/.hermes/skills/ exists  ✓ ~/.hermes/memories/ exists
✓ MEMORY.md exists (1860 chars)   ← 即 Windows 端的 memories/MEMORY.md
⚠ ~/.hermes/state.db exists but has issues: disk I/O error   ← Windows Hermes 持有中的預期警告
```

## Maintenance window 程序（重複驗證或日後 Ubuntu 端需要寫入時使用，約 2 分鐘）

1. Windows：`hermes gateway stop`（每個運行中的 profile 都要停：`hermes gateway list` 逐一 `hermes --profile <name> gateway stop`）；`hermes dashboard --stop`；關閉 Hermes Desktop。**三者都要**——任何一個 Windows process 持有 state.db，Ubuntu 端就會被擋。
2. WSL：`bash scripts/hermes_stage0_verify.sh --window`
   （自動做：一般唯讀開啟 → `hermes sessions stats/list` → `hermes -z` 建立測試 session → integrity check，任一步失敗即停）
3. Windows：恢復服務（見下節），重開 Desktop。
4. Windows：`hermes --profile default sessions list`、`py -3.11 hermes/session_adapter/adapter.py list`、Dashboard sessions 頁面 → 應看到 Ubuntu 建立的 session。

### 服務恢復的實務注意（2026-07-07 實際踩到）

- **sticky profile 會被切換**：`~/hermes/active_profile` 在 stop/desktop 關閉過程中被改成了 `nemocoding`，之後不帶 flag 的 `hermes sessions ...` 讀到的是 profile 的 db（14 sessions），不是 default 的（60）。驗證時一律用 `hermes --profile default ...` 明確指定；必要時 `hermes profile use default` 還原 sticky。
- **`hermes gateway start` 的 spawn 不會把 `--profile` 傳給子行程**（Hermes issue #18594 類型問題）：子行程讀 sticky active_profile 自行決定身分，可能註冊到錯的 profile。可靠的啟動方式是官方 vbs 模式——明確設 `HERMES_HOME` 指向 profile 目錄後 `pythonw -m hermes_cli.main --profile <name> gateway run`（detached）。default profile 的等效指令（本次實際使用、成功）：

  ```
  HERMES_HOME=C:\Users\razer\AppData\Local\hermes
  HERMES_GATEWAY_DETACHED=1
  pythonw.exe -m hermes_cli.main --profile default gateway run
  ```

- **`gateway start` 的 already-running 誤判**：guard 對 active profile 接受「任何 gateway 命令列」的活行程，其他 profile 的 gateway 在跑就會誤報 already running。用上面的 `gateway run` 直接啟動可繞過 guard（真正的互斥由 gateway.lock flock 保證）。
- **Scheduled Task `Hermes_Gateway` 已壞**：指向不存在的 `gateway-service\Hermes_Gateway.vbs`（上次結果=1）。default gateway 平時是由 Hermes Desktop 啟動的，登入自啟只涵蓋 profiles（Startup 資料夾的 per-profile vbs）。使用者可考慮修復或移除該 task。

## Rollback（已實測完整 round-trip）

```bash
wsl.exe -d Ubuntu -e bash -lc "bash /mnt/c/Users/razer/dev/ClaudeCodeOSWin/scripts/hermes_stage0_bootstrap.sh --rollback"
```

行為（2026-07-07 實測輸出：`removed symlink state.db / restored sessions from sessions.pre-stage0 / ...`）：

1. 移除 `state.db` symlink（只刪 link；若發現是真檔會拒絕動作）。
2. 移除 `sessions`/`skills`/`memories` symlink，把 `*.pre-stage0` 改回原名。
3. Windows 端完全不動。若 db 內容需要還原（本 stage 不曾寫入正本），用 `backup_stage0/state.db.stage0.bak` 覆蓋（先停 Hermes，連同刪除 `-wal`/`-shm`）。

不會留下 broken symlink、空資料夾或第二份 state.db。

## 殘餘風險與注意事項

- SQLite 官方文件宣告 WAL 不支援跨網路檔案系統共用。本設定的安全性建立在**實測的互斥行為**（Windows 持有 → Ubuntu 干淨失敗；Ubuntu 先持有 → 鎖協調有效）之上；這是 WSL2 9p 目前的實作行為，WSL 升級後應重跑 `hermes_stage0_verify.sh`（Phase A 隨時可跑）。
- 並發「雙邊同時互動使用」不是支援情境：正常情況下 Windows Hermes 常駐，Ubuntu 端 db 功能自動被擋；這是 by design 的降級，不是故障。
- Ubuntu 端 `sessions.pre-stage0`（空）、`skills.pre-stage0`（預設 built-in）、`memories.pre-stage0`（空）保留作 rollback 憑據，勿刪。
- kanban.db、cron/、projects.db 不在 Stage 0 範圍，仍是兩端獨立。
