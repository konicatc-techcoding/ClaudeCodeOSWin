# Hermes — Background Runtime（v0.1）

> **目標環境是 Windows/WSL2**：這份 README 大部分內容（含下方的驗證紀錄）沿用自原始 macOS 版 `ClaudeCodeOS`，當時常駐部署用的是 `launchd`。**目前的 live runtime 是 WSL2 側的 `systemd --user`（`hermes/systemd/`）**——worker／Telegram 常駐 service、RSS 與 `daily-memory-check` 排程 timer 都跑在那裡；下方提到 `hermes/launchd/install.sh` 的地方，一律改用 `hermes/systemd/install.sh`（用法一致，只是底層從 `launchctl` 換成 `systemctl --user`）。細節見根目錄的 [WINDOWS_WSL_SETUP.md](../WINDOWS_WSL_SETUP.md) 跟 [systemd/README.md](systemd/README.md)。`hermes/launchd/` 目錄僅為 macOS legacy/reference，在 WSL2 環境下不會作用（沒有 `launchctl`），保留不刪。Windows 側的 bridge／Task Scheduler 排程是未來選項（Stage 2 設計決策），尚未實作。

Job queue 設計見 [DESIGN.md](DESIGN.md)。系統整體架構見 [ARCHITECTURE.md](../ARCHITECTURE.md)。

## 現在有的

- `adapter/invoke_cos.sh` — Hermes 呼叫 Chief of Staff 的 adapter，shell 出去呼叫 `claude -p`。
- `db.py` — job queue 的 SQLite 存取層：`enqueue()`、`claim_next_job()`（atomic 搶佔+attempts 遞增）、`mark_completed()`/`mark_failed()`（含 retry/dead-letter 判斷、`cost_usd` 記錄）、`reap_stale_jobs()`（回收 worker crash 留下的 stale job）、session TTL（`get_resumable_session()`/`upsert_session()`）。內建 schema migration（`_migrate_schema()`），舊的 `jobs.db` 也能自動補上新欄位。也是一支 CLI，見下方「手動操作」。
- `worker.py` — job queue 的 worker：`--once` 跑一輪就結束（手動測試用），不帶參數是常駐模式（之後給 launchd 用）。目前 `MAX_CONCURRENT_JOBS = 1`，等穩定後再調到 2–3。
- `test_db.py` — `db.py` 邏輯的 unittest（16 個案例：搶佔互斥/排序、retry vs dead-letter、reaper、session TTL）。
- `jobs.db` — SQLite 檔案（gitignore，不進版控）。

## 手動操作

> 2026-07-08 起本 repo 位於原生 Windows（`C:\Users\razer\dev\ClaudeCodeOSWin`），
> 專案唯一的 `.venv` 是 Windows 原生 venv（Python 3.11，`Scripts\` 結構），下方指令
> 一律用 `.venv/Scripts/python.exe`。注意：`worker.py` 會 subprocess 執行
> `adapter/invoke_cos.sh`（POSIX shell script），在原生 Windows 需要從 Git Bash 之類的
> POSIX shell 環境跑，或等「worker 跑哪一側」的 Stage 2 決策定案。
> `hermes/systemd/*.service` 仍是 WSL2 側的 Linux 路徑（`%h/dev/ClaudeCodeOSWin/.venv/bin/python3`），
> 與目前的 Windows venv 不相容——那是 WSL 部署層的既有設計，等 Stage 2 一併處理，這裡不改。

```bash
# 塞一筆 job（source/thread-id/max-attempts 都可選）
.venv/Scripts/python.exe hermes/db.py enqueue --prompt "你的任務內容"
.venv/Scripts/python.exe hermes/db.py enqueue --prompt "..." --thread-id "telegram:12345"   # 對話性任務

# 查狀態
.venv/Scripts/python.exe hermes/db.py list                 # 全部
.venv/Scripts/python.exe hermes/db.py list --status queued # 篩選
.venv/Scripts/python.exe hermes/db.py show <job_id>         # 單筆完整內容（含 result/error_message）

# 跑一輪（處理目前所有 eligible 的 queued job；見上方 invoke_cos.sh 的注意事項）
.venv/Scripts/python.exe hermes/worker.py --once

# 查 log
cat logs/hermes/<job_id>.log     # 單筆 job 的完整記錄
cat logs/hermes/worker.log       # worker 程序層級事件
```

**手動測試時選 prompt 要小心**：`prompt` 會真的經過 CoS 的 delegation policy 分類，含糊或帶有「test」「job」這類字眼的內容可能被判斷成需要分派給真實 subagent 執行（花真的 token/時間），不是單純的 echo。想驗證 queue 機制本身、不想觸發實際工作時，用明確、無害、能被歸類成 `meta_system_question` 或不會觸發分派的句子。

## 常駐部署（已完成；目前為 WSL2 systemd）

目前的部署層是 `systemd/`（WSL2 側的 `systemctl --user`），細節見 [systemd/README.md](systemd/README.md)。**這只是部署層**——`worker.py`/`db.py` 完全不依賴 systemd（或 launchd）的存在，之後要換 Docker 或其他機制只需要換這個目錄。

```bash
hermes/systemd/install.sh     # 安裝並啟動（WSL2 內執行）
hermes/systemd/uninstall.sh   # 停止並移除
```

`launchd/` 底下是當時 macOS 環境的等效部署設定（[launchd/README.md](launchd/README.md)），僅為 legacy/reference。當時安裝過程中發現並修好兩個 launchd 環境特有的問題（都跟「launchd 起的程序環境跟你互動式 shell 不一樣」有關；systemd 版本已在 `.service` 檔對應處理）：

1. **`PATH` 不會繼承使用者 shell 設定**——launchd 啟動的程序預設 `PATH` 很精簡，找不到 `~/.local/bin/claude`，導致 `invoke_cos.sh` 直接 `exit 127`。修法：plist 裡明確加 `EnvironmentVariables.PATH`。
2. **`KeepAlive: {Crashed: true}` 不會在 `kill -9` 後重啟**——launchd 對「crash」的定義不含 `SIGKILL`（人工強殺或 OOM killer 都是這個訊號），只用 `Crashed:true` 的話這種情況不會被拉起來。改成無條件 `KeepAlive: true`，靠 `ThrottleInterval` 擋 crash-loop；真的要停用 `uninstall.sh`（`launchctl bootout`），不要用 `kill`。

## Telegram Polling Adapter（已完成，live 驗證通過）

`adapters/telegram.py`——長輪詢 `getUpdates`，不開 webhook。只做兩件事：把白名單內 chat 的訊息轉成 job（`db.enqueue()`），以及把已完成的 telegram job 結果送回對應 chat（`delivered_at` 追蹤有沒有回覆過，不是新的 status）。

**Bot 邊界（2026-07-09 設計決策）**：

- **ClaudeCodeOS 使用自己的 Telegram bot**，作為 CoS / AgentOS 的**控制入口**——就是這個 adapter 輪詢的 bot。
- **Hermes 各 profile 維持自己的 Telegram bots**，是 profile 的**對話入口**，使用者可能直接對 profile bot 下命令。這些 bots **不由本 repo 管理**——ClaudeCodeOS 不管理、不同步、不覆寫任何 profile bot 設定。
- Hermes profile sessions 只透過 `hermes/session_adapter/`（read-only）與未來的 Stage 2 bridge 被唯讀取用，本 repo 不寫入。
- 因此本 adapter 的 `hermes/config/telegram.json`（bot token + 白名單）屬於**部署側本地維護的密鑰**：gitignore 不進版控，**也不經 repo 同步機制下發**（`scripts/sync_to_wsl.sh` 已排除，見 [docs/deployment-sync-plan.md](../docs/deployment-sync-plan.md) §2.1）——部署側自己建、自己改。

**設定**（`hermes/config/telegram.json`，gitignore，自己建）：
```json
{
  "bot_token": "123456:ABC-DEF...",
  "allowed_chat_ids": [123456789],
  "poll_timeout_seconds": 30
}
```

```bash
.venv/Scripts/python.exe hermes/adapters/telegram.py --once   # 手動跑一輪
hermes/systemd/install.sh hermes-telegram               # 常駐部署（WSL2 內執行；要先建好上面的設定檔）
```

**Live 驗證紀錄**（2026-07-04）：用真的 bot `@CCAgenticOSbot` 測過完整流程——`getMe` 確認 token 有效 → 使用者傳 `/start`、`hi` 兩則訊息 → `--once` 抓下來 enqueue 成兩筆 job（`thread_id=telegram:1034113120`）→ 已常駐的 launchd worker 自動處理完成 → 再跑一次 `--once` 觸發 `deliver_completed_jobs()` → 兩則結果都成功送回 Telegram，使用者在手機上確認收到。（以上為當時 macOS 環境的驗證紀錄；目前這個 adapter 已在 WSL2 側以 systemd 常駐輪詢——`hermes-telegram.service`。）

**手動測試時的提醒**（跟 `worker.py` 那條一樣）：真的傳到 Telegram 的訊息一樣會經過 CoS 的 delegation policy 分類，含糊的內容可能被當成真實任務分派執行，不是單純 echo。

## Cron Adapter（已完成，含真實 launchd 觸發驗證）

`adapters/cron.py`——完全不自己做排程判斷，無狀態，一次呼叫只做「查 `hermes/config/cron_jobs.yaml`、`enqueue()` 一次、結束」。「多久跑一次」全部交給部署層的排程器——目前是 WSL2 systemd 的 `.timer`（一個 cron job 對應一個 timer；當時 macOS 為 launchd 項目）。`daily-memory-check` 的 N-gate prompt 定義在 `cron_jobs.yaml`，由這條 WSL2 排程每天觸發讀取。

```yaml
# hermes/config/cron_jobs.yaml（不是密鑰，會進版控）
jobs:
  - name: daily-memory-check
    prompt: "請檢查 memory/inbox 有沒有新內容需要整併進正本。"
    max_attempts: 3
```

```bash
.venv/Scripts/python.exe hermes/adapters/cron.py --job-name daily-memory-check   # 手動跑一次
hermes/systemd/install.sh hermes-cron-daily-memory-check                   # 裝成每天 08:00 觸發（WSL2 內執行）
```

**驗證紀錄**（2026-07-04）：先手動跑過一次確認 enqueue→worker 處理→completed→log 全部正確；接著另外裝了一個 `StartInterval:30` 的臨時 plist，實際觀察 launchd 在沒有人手動介入的情況下連續觸發 5 次、5 筆 job 全部處理成功，證明是 launchd 真的在觸發、不是我手動跑的錯覺——驗證完就把臨時 plist 移除，正式改裝 `StartCalendarInterval`（每天 08:00）的版本。

## RSS Adapter（已完成，含真實 feed 與真實 launchd 觸發驗證）

`adapters/rss.py`——無狀態，一次呼叫就是「整批檢查一次所有 feed」，跟 `cron.py` 同一個原則：排程完全交給部署層的排程器（目前是 WSL2 systemd `.timer` 的 `OnUnitActiveSec`；當時 macOS 為 launchd 的 `StartInterval`），不自己判斷「該不該跑」。v0.1 範圍刻意只做抓取、去重、`feedparser` 解析、`enqueue()`——不做摘要優化或其他進階功能，摘要是 prompt_template 請 CoS/intelligence 做的事。

```yaml
# hermes/config/rss_feeds.yaml（不是密鑰，會進版控）
feeds:
  - name: hn-frontpage
    url: "https://hnrss.org/frontpage"
    prompt_template: "Hacker News 前頁有一篇新文章：「{title}」（{link}）。幫我摘要重點。"
```

**去重機制**：`hermes/state/rss_seen.json` 記錄每個 feed 已經看過的 guid（每個 feed 最多留 500 筆，滿了擠掉最舊的）。**第一次看到一個新設定的 feed 時只建立基準線、不 enqueue**——不然新增 feed 當下就會把它過去所有文章當成新內容塞滿 queue。單一 feed 抓取/解析失敗只跳過那個 feed，不影響其他 feed。

```bash
.venv/Scripts/python.exe hermes/adapters/rss.py      # 手動跑一輪（整批檢查所有 feed）
hermes/systemd/install.sh hermes-rss            # 裝成每 30 分鐘檢查一次（WSL2 內執行）
```

**驗證紀錄**（2026-07-04）：用真實的 `hnrss.org/frontpage` feed 跑過完整流程——第一次執行正確建立基準線（20 篇文章，沒有 enqueue 任何一筆）；手動移除一筆已讀紀錄模擬「新文章」，確認真的被偵測到、正確 enqueue、CoS 分派給 `intelligence`、產出真正的摘要。跟 Cron 一樣，另外裝了一個 `StartInterval:30` 的臨時 plist，觀察到 launchd 自動觸發 3 次（去重正確運作，沒有重複 enqueue），驗證完移除，正式改裝 `StartInterval:1800`（30 分鐘）的版本。

### `config/`（尚未使用）

規劃放 cron 排程設定、RSS 來源清單、bot token 等——這個目錄已加進 `.gitignore`，內容不會進版控。

### `state/`（規劃中，尚未建立）

Adapter 自己維護的執行狀態（Telegram 的 offset、RSS 的已讀 guid），跟 `config/`（使用者提供的設定）分開。
