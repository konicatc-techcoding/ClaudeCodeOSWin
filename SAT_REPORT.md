# System Acceptance Test — v0.1

**執行日期**：2026-07-04
**範圍**：目前的 Runtime 核心（CoS + 五個 domain subagent + delegation policy + consolidate-memory + Hermes job queue），不含 Telegram/RSS/cron adapter 與 launchd 常駐部署（尚未建立）。
**結論**：**全數通過**，一項非阻塞性發現（memory 索引內容過期，見第 8 項）。

---

## 1. 多筆 job 連續 enqueue（12 筆）

塞了 12 筆刻意設計過的 job，涵蓋全部 5 個領域 + 2 個 direct category + 同/跨 thread session resume + 一個舊修復的回歸檢查，用 `worker.py --once` 一次跑完（含前一輪測試留下的 1 筆，共處理 13 筆）：

| Job | 內容分類 | 結果 |
|---|---|---|
| J1 | meta_system_question | 正確直接回答，未分派 |
| J2 | engineering（code 語法檢查） | 分派 `engineering`，`py_compile` 通過 |
| J3 | intelligence（外部研究） | 分派 `intelligence`，真的查了 SQLite 官方文件並附來源 |
| J4 | automation（排程設計） | 分派 `automation`，只產出設計文件，沒有建立任何實際檔案/排程 |
| J5 | knowledge（inbox 檢查） | 分派 `knowledge`，正確回報 inbox 空、先前記錄仍在 `.processed/` |
| J6 | planning（roadmap） | 分派 `planning`，產出有優先順序的準備清單 |
| J7 | general_conversation | 正確直接回答，未分派 |
| J8 | thread-A 第 1 輪 | 記住代號 SAT-Alpha |
| J9 | thread-B 第 1 輪 | 記住代號 SAT-Beta |
| J10 | thread-A 第 2 輪 | 正確回想 SAT-Alpha（沒有跟 B 混淆） |
| J11 | thread-B 第 2 輪 | 正確回想 SAT-Beta（沒有跟 A 混淆） |
| J12 | 安全回歸檢查 | 確認 `route_model.py` 路徑邊界檢查仍存在、邏輯正確 |

全部 `status=completed`，全部一次通過。

## 2. Retry / Dead-letter / Reaper 回歸測試

- `hermes/test_db.py` 16 個 unittest：**全過，無回歸**。
- 真實情境重跑一輪（技巧同前：暫時把 `invoke_cos.sh` 換成會失敗的 stub，測完換回來）：
  - **Retry-then-success**：`max_attempts=3`，第一次失敗 → `queued` + backoff（`attempts=1`），還原腳本、強制提前重試時間 → `completed`（`attempts=2`）。
  - **Dead-letter**：`max_attempts=1`，失敗一次直接 → `dead_letter`。
  - **Reaper**：手動塞一筆 `locked_at` 過舊的 `running` job，下一輪 `--once` 啟動時的 reaper 正確把它 `requeue`（且沒有因為還在 backoff 期間而立刻被重跑）。

## 3. Session Resume（同 thread / 不同 thread）

見第 1 項 J8–J11。額外用 `sessions` 表跟 log 交叉確認：`sat:thread-A` 全程只對應 `52a0acb6...`，`sat:thread-B` 全程只對應 `7d4b9da3...`，兩條線交錯執行（A1→B1→A2→B2）也沒有互相污染。

## 4. Worker 長時間運行（45 分鐘）

背景跑一個自我限時的 poll 迴圈（`worker.py --once` 每 5 秒一次），**2026-07-04T03:50:19Z → 04:35:29Z，共 45 分 10 秒、531 次迭代**：

- 全程 `grep` 找不到任何 error/exception/traceback。
- 自然結束（時間到就跳出迴圈），不是被砍掉的。
- 結束後立刻塞一筆新 job 驗證：45 分鐘 idle 之後系統馬上正常處理，沒有任何「醒不過來」的跡象。
- `hermes/jobs.db`（44K）、`logs/hermes/`（140K）在這段時間內大小正常，沒有 `-wal`/`-shm` lock 檔案殘留（連線有正確關閉）。

## 5. jobs.db 一致性檢查

```
還卡在 running 的 job：0
attempts 超過 max_attempts：0
dead_letter 但 attempts < max_attempts：0
completed 但 result 是空的：0
曾失敗過但 next_attempt_at 是 NULL 的 queued job：0
```

五項檢查全部 0 違規。目前 `jobs.db` 共 21 筆（19 completed + 2 dead_letter，皆為本次刻意製造的回歸測試 fixture），`sessions` 表 3 筆，狀態都跟預期一致。

## 6. Logs 完整性

- 21 筆 job，21 個 `logs/hermes/<job_id>.log` 全部存在，沒有缺漏。
- `logs/hermes/worker.log`（592 行）涵蓋整個測試期間的 claim/complete/reap 事件，時間軸連續無斷點。

## 7. CoS Delegation 是否仍符合 Policy

逐筆檢查 transcript 裡的 `Agent` tool 呼叫，7 種分類全部正確：

```
J1 meta_system_question   -> 未分派（正確）
J2 engineering             -> Agent(subagent_type=engineering)
J3 intelligence            -> Agent(subagent_type=intelligence)
J4 automation              -> Agent(subagent_type=automation)
J5 knowledge               -> Agent(subagent_type=knowledge)
J6 planning                -> Agent(subagent_type=planning)
J7 general_conversation    -> 未分派（正確）
```

沒有任何一筆「任務很小就自己做」的違規，`delegation_policy.yaml` 修復後的行為維持穩定。

## 8. Memory Consolidation 是否正常

`knowledge` subagent（J5）正確回報 `memory/inbox/` 目前沒有新內容、先前的整併紀錄仍完好地在 `.processed/`——consolidate-memory 流程本身沒有壞掉。

**發現（非阻塞）**：`memory/MEMORY.md` 的索引文字還停留在「Hermes/job queue... 尚未實作」，是這次 SAT 之前寫的，現在已經過期（job queue 已經建好且通過驗證）。這不是系統壞掉，是內容沒有隨最新進度更新——正好是 `consolidate-memory`／`knowledge` 該處理的事，但目前沒有東西會主動觸發它去做「更新既有事實」這件事（現有機制只處理 inbox 有新檔案的情況）。建議之後手動或透過排程觸發一次整併來更新這則記錄，這次先不動手改，留給你決定。

## 9. 成本（Token / USD）統計

- 19 筆有實際呼叫 API 的 job，`logs/hermes/*.log` 個別記錄的 `total_cost_usd` 加總 = **$3.8228**。
- 交叉比對 `worker.log` 裡每筆 `completed (cost=$X)` 的加總：**$3.8228**，兩邊完全一致。
- 2 筆 `dead_letter` job 正確地沒有任何成本紀錄——它們在 `invoke_cos.sh` 這層就失敗了，從未真的呼叫到 Claude Code，符合預期。

**觀察**：成本目前只能從 log 逐筆解析加總，`jobs` 表本身沒有 `cost_usd` 欄位。現在這樣算是對的，但規模變大以後手動解析 log 不好擴充；之後如果需要例行性的成本報表，可以考慮在 `mark_completed()` 多存一個 `cost_usd` 欄位，這次先不動 schema。

---

## 總結

九項檢查全部通過，本次 SAT 期間累計花費 **$3.82**。兩個非阻塞性發現（memory 索引過期、成本沒有結構化存欄位）都記錄在案，不影響「核心穩定」的結論。
