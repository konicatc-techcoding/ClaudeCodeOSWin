# Hermes Runtime / Job Queue — Design v0.1（已確認，見 commit history）

語言：Python（沿用專案既有的 `scripts/` 慣例與 `.venv`，SQLite 用標準庫 `sqlite3`，不需要額外依賴）。

**這一版刻意簡化的兩點**（跟原始草案的差異）：
- 狀態只有 `queued / running / completed / failed / dead_letter` 五種，先不做 `delivered` / `archived`——目前沒有真的投遞管道，`completed` 本身已經連同結果一起寫進 `logs/hermes/<job_id>.log`，之後真的接 Telegram 等管道時再加回 `delivered`。
- 併發 `MAX_CONCURRENT_JOBS = 1`，等穩定後再調到 2–3（`hermes/worker.py` 裡的一個常數）。

## 1. Job Schema

```sql
CREATE TABLE jobs (
  id              TEXT PRIMARY KEY,
  source          TEXT NOT NULL,      -- telegram | cron | rss | api | manual
  payload         TEXT NOT NULL,      -- JSON，原始素材
  prompt          TEXT NOT NULL,      -- 已經轉譯好、可直接丟給 CoS 的字串
  thread_id       TEXT,               -- "<source>:<外部id>"，無狀態任務是 NULL
  session_id      TEXT,               -- 保留欄位；實際 resume 判斷在執行前即時查 sessions 表，不預先寫入
  status          TEXT NOT NULL DEFAULT 'queued',
                    -- queued | running | completed | failed | dead_letter
  priority        INTEGER NOT NULL DEFAULT 0,
  attempts        INTEGER NOT NULL DEFAULT 0,
  max_attempts    INTEGER NOT NULL DEFAULT 3,
  next_attempt_at TEXT,
  worker_id       TEXT,
  locked_at       TEXT,
  result          TEXT,
  cost_usd        REAL,   -- v0.2：從 invoke_cos.sh 回傳 JSON 的 total_cost_usd 寫入，
                          -- 成功/失敗都寫（COALESCE，不會被沒有成本資訊的失敗蓋成 NULL）
  error_message   TEXT,
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL,
  completed_at    TEXT
);

CREATE TABLE sessions (
  thread_id     TEXT PRIMARY KEY,
  session_id    TEXT NOT NULL,
  last_used_at  TEXT NOT NULL
);
```

## 2. 狀態流轉

```
queued ──(worker 搶到，attempts+1)──> running ──成功──> completed
                              │
                              ├──失敗/逾時──> failed 邏輯：
                              │       attempts < max_attempts → queued（next_attempt_at = 退避後時間）
                              │       attempts >= max_attempts → dead_letter
                              │
                              └──worker crash（reaper 偵測 locked_at 過舊）──> 走跟上面失敗一樣的判斷
```

`failed` 不是一個會停留的狀態——判斷完立刻轉成 `queued`（帶 backoff）或 `dead_letter`，所以資料庫裡實際會看到的狀態是這五種裡的 `queued / running / completed / dead_letter`，`failed` 只出現在 log 裡當作瞬間事件的描述。

## 3. Worker Lifecycle

- `hermes/worker.py`，啟動先跑一次 reaper（`locked_at` 超過 `STALE_AFTER_SECONDS`=600 秒還在 `running` 的 job，視為 worker crash 過）。
- 常駐模式：每 5 秒 poll 一次；`--once` 模式：跑一輪，處理完目前所有 eligible 的 queued job就結束（手動測試用）。
- 併發：`ThreadPoolExecutor(max_workers=MAX_CONCURRENT_JOBS)`，v0.1 = 1。
- 搶佔：單一 atomic SQL（`UPDATE ... WHERE id = (SELECT ... LIMIT 1) RETURNING ...`），同時把 `attempts+1`。
- 逾時：`subprocess.run(timeout=JOB_TIMEOUT_SECONDS)`，600 秒。
- 關閉：SIGTERM/SIGINT。
- 常駐方式：部署層決定，Runtime 不感知——目前（Windows/WSL2 環境）是 WSL2 `systemd --user`（`hermes/systemd/`）；`hermes/launchd/` 是 macOS legacy/reference。

## 4. Session Resume

- `thread_id` 格式 `"<source>:<外部id>"`；無狀態來源（cron/rss）是 `NULL`。
- 執行前（不是 enqueue 時）即時查 `sessions` 表：`last_used_at` 在 24 小時內才回傳 `session_id` 給 `invoke_cos.sh` 當 `--resume` 參數；否則視為新對話。
- Job 完成後，若 `thread_id` 不是 NULL：把 `invoke_cos.sh` 回傳 JSON 裡的 `session_id`（不論是延續舊的還是新開的）`UPSERT` 進 `sessions` 表。

## 5. Log

- `logs/hermes/<job_id>.log`：單一 job 的完整記錄（metadata、prompt、exit code、stdout/stderr 節錄、最終結果）。
- `logs/hermes/worker.log`：worker 程序層級事件（啟動/關閉/reaper/claim/錯誤），同時輸出到 stdout。

## 6. 錯誤處理與 Retry / Dead-letter

失敗來源：`invoke_cos.sh` exit 非 0／逾時／JSON 解析失敗／JSON 內容顯示 `is_error`或 `subtype != "success"`／worker crash（reaper 偵測）。

Retry：`next_attempt_at = now + min(30s * 2^(attempts-1), 1800s) ± 20% jitter`；`max_attempts` 預設 3，每筆 job 可各自覆寫。超過就進 `dead_letter`，不自動刪除、不自動通知，留著等人工查。

## 7. 跟 invoke_cos.sh 的介面

不改動既有腳本。Worker 傳 `prompt`（+ 視情況帶 `session_id`），解析回傳 JSON 取 `result` / `session_id` / `is_error` / `subtype` / `total_cost_usd`。Worker 不繞過這支腳本直接呼叫 `claude`。

## 檔案

```
hermes/
├── adapter/invoke_cos.sh   （既有，不變）
├── db.py                   （schema、enqueue()、claim_next_job()、mark_completed()/mark_failed()、reap_stale_jobs()、CLI）
├── worker.py               （常駐主迴圈 + --once）
├── test_db.py              （db.py 邏輯的 unittest）
└── jobs.db                 （SQLite，gitignore）
```
