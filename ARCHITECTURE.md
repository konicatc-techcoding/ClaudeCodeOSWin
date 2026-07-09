# Claude Code OS — Runtime Architecture

**版本**：v0.1
**里程碑**：`v0.1-alpha`（2026-07-04，穩定基線）— 詳細範圍與下一步見 [ROADMAP.md](ROADMAP.md)
**狀態**：介面設計已定案，實作涵蓋 v0.1-alpha 範圍（見文末「v0.1 實作範圍」）
**最後更新**：2026-07-09

> **部署環境註記（2026-07-09）**：目標環境是 **Windows/WSL2**。常駐服務（worker、
> Telegram、RSS、cron timer）跑在 WSL2 側的 `systemd --user`（`hermes/systemd/`）；
> 文末「v0.1 實作範圍」提到的 launchd 部署是 v0.1-alpha 當時 macOS 環境的驗證記錄，
> `hermes/launchd/` 僅保留為 legacy/reference。Windows 側 bridge／Task Scheduler
> 是未來選項（Stage 2 設計決策），尚未實作。見 [WINDOWS_WSL_SETUP.md](WINDOWS_WSL_SETUP.md)。

## 總覽

系統有兩個入口，最終都匯入同一個 Chief of Staff（CoS）：

- **前台**：你在 Claude Code Desktop 互動式操作
- **背景**：Hermes 接收 Telegram / Cron / RSS / API 事件後，headless 喚醒 CoS

CoS 只負責「決策・分派・整合」，實際工作交給五個領域的 subagent。兩個入口不共用 session，只透過 Shared Memory 交會。

## 已接受的核心決策

- Job state 用 SQLite，memory 用 markdown——兩種資料的一致性需求不同，不用同一套機制硬套
- 背景任務只能 append 寫入 `memory/inbox/`，不可編輯 `memory/` 正本
- Canonical memory（`memory/*.md`）只由互動式 CoS session 或 consolidation pass 寫入
- Desktop 與 Hermes 不共用 session／對話歷史，只透過 Shared Memory 交會
- Hermes 呼叫 CoS 走 CLI headless 模式（`claude -p`），不用 Desktop IPC
- Model Router 先做 script adapter，不做 MCP server
- Agent Registry 不是新機制，就是 Claude Code 原生的 subagents（`.claude/agents/*.md`）

---

## 1. Hermes → Chief of Staff 呼叫方式

**CLI headless 模式，不用 Desktop IPC。** Desktop 沒有穩定的可程式化 IPC 介面，而且背景自動化不該依賴 Desktop 是否剛好開著。

```bash
claude -p "$TASK_PROMPT" \
  --add-dir "$SHARED_CONTEXT_ROOT" \
  --output-format json \
  ${SESSION_ID:+--resume "$SESSION_ID"}
```

CWD 設成專案根目錄，root `CLAUDE.md` 就會自動載入，不需要額外的啟動邏輯。

**未來 API**：不另外設計。呼叫邏輯包成一個 adapter（`hermes/adapter/invoke_cos.sh`），今天內部是 `claude -p`；未來要換 Claude Agent SDK（in-process），只改這個檔案，上層邏輯不用動。

## 2. Job 生命週期 vs Session 生命週期

兩條不會合併的世系，只在 Shared Memory 交會：

- **互動式（Desktop）**：長時間存活、單一對話串、session 狀態在 Desktop 本機，Hermes 碰不到
- **背景（Hermes）**：事件 → job

```
queued → dispatched（喚醒 headless CoS）→ running → completed | failed | timeout → delivered → archived
                                                             │
                                                             └─(failed, attempts<N)→ requeued → …→ dead-letter
```

- Job：`{id, source(telegram|cron|rss|api), payload, thread_id?, session_id?, status, attempts, created_at}`
- 對話性來源（Telegram）：`thread_id → last session_id` 若仍「熱」（如 <24h）則 `--resume`；cron/RSS 永遠無狀態
- 併發：worker pool 上限（先設 2–3），這是成本控制，不是正確性機制——因為 inbox 寫入本身無競態
- Job 狀態存 **SQLite**（`hermes/jobs.db`），需要原子 claim/complete；memory 內容仍是 markdown

## 3. 權責邊界

| 項目 | 擁有者 | 備註 |
|---|---|---|
| 互動式 session／歷史紀錄 | **Desktop** | 本機、私有，不寫進 Shared Context |
| Job queue 與狀態 | **Hermes**（`hermes/jobs.db`） | Desktop 不碰 |
| 事件接收（Telegram/cron/RSS/webhook） | **Hermes** | |
| 對外通道投遞 | **Hermes** | |
| 長期記憶正本（`memory/*.md`） | **共享**，有寫入門檻 | 只有互動式 session + consolidation 能寫 |
| 收件匣（`memory/inbox/`） | **共享**，只能新增 | 只有背景 job 能寫 |
| 領域 subagents、skills | **共享，版本控管** | 兩邊都是使用者，不是擁有者 |
| 模型路由規則 | **共享**（`registry/model_router.yaml`） | |

## 4. Shared Context 目錄結構與同步規則

```
ClaudeCodeOS/
├── ARCHITECTURE.md
├── CLAUDE.md                          ← Chief of Staff 入口
├── .claude/
│   ├── agents/                        ← 領域 subagents
│   └── skills/                        ← 共用 skills
├── registry/
│   ├── agents.yaml                    ← 領域能力清單
│   └── model_router.yaml              ← 能力 → 供應商/模型
├── scripts/
│   └── route_model.py                 ← Model Router 的 script adapter
├── memory/
│   ├── MEMORY.md
│   └── inbox/                         ← 背景寫入區，只能新增檔案
├── hermes/
│   ├── adapter/invoke_cos.sh          ← Hermes → CoS 呼叫（已實作）
│   ├── sessions/                      ← thread_id → session_id（規劃中）
│   ├── config/                        ← bot token / cron / rss 設定（gitignore）
│   └── jobs.db                        ← SQLite job queue（規劃中，尚未建立）
└── logs/
```

**關鍵同步規則**：`memory/*.md` 正本只有互動式 session 或 consolidation pass 能編輯。**背景 job 永遠只能在 `memory/inbox/` 新增檔案，不能編輯既有檔案**——因為每次背景寫入都是全新檔案，天生無競態，不需要鎖。之後由排程的 consolidation pass（呼叫既有的 `consolidate-memory` skill）把 `inbox/` 整併進正本。取捨：背景產生的事實是最終一致，不是即時同步。

記憶的三層分類（Procedural / Semantic / Episodic）、consolidation 觸發條件（N-gate）、useful 判定與敏感內容 guardrails，定義在 [docs/memory-taxonomy.md](docs/memory-taxonomy.md)（參數正本：`registry/consolidation_policy.yaml`）——那份政策不改變本節的寫入規則，只補「何時整併、哪些內容不該進來」。

## 5. Agent Registry 與 Model Router

**Agent Registry** = Claude Code 原生 subagents（`.claude/agents/*.md`），`registry/agents.yaml` 是加在上面的中繼資料，供 CoS／Hermes 查詢路由對象與狀態。CoS 的工作只是查表、挑 `subagent_type`、呼叫 `Agent` 工具。

**Model Router** 的介面約定：subagent 只請求「能力」（capability），不直接指定模型名稱。實作先用 `scripts/route_model.py` 這個 script adapter 解析 `registry/model_router.yaml` 並呼叫 OpenRouter；用量變多、需要 streaming 或結構化 tool call 時再升級成 MCP server。

**每個領域預設用哪個 capability，寫在 `registry/agents.yaml` 的 `default_capability` 欄位**（2026-07-04 新增）——這是唯一的真相來源，subagent 檔案本身不寫死 capability 名稱，都是查這個欄位。目前：`intelligence` → `bulk_research`（OpenRouter free tier，nemotron）、`engineering` → `complex_coding`（GPT-5.5）、`automation`／`knowledge`／`planning` → `claude_native`（Claude 原生，不對外呼叫）。`scripts/test_route_model.py` 有一個跨 registry 的一致性測試，確保 `agents.yaml` 裡每個 active 領域的 `default_capability` 在 `model_router.yaml` 裡真的存在。

---

## v0.1 實作範圍

**已建立**：
- 目錄骨架
- root `CLAUDE.md`（CoS entry point）
- 5 個 domain subagents：`intelligence` / `engineering` / `automation` / `knowledge` / `planning`（`planning` 透過 `depends_on` 明確依賴 `knowledge` 提供上下文，見 delegation_policy.md）
- `registry/agents.yaml`、`registry/model_router.yaml`
- `delegation_policy.md` + `registry/delegation_policy.yaml`（分派政策：任務分類 → 查 owner → 強制分派，不讓模型用「任務很小」自己覆蓋規則；CoS、未來 Hermes 前置分類、未來 Job Queue 共用同一份表）
- `.claude/settings.json`（允許 WebSearch/WebFetch，以及 headless 背景任務常用到的最小 Bash 白名單：`python3 -m py_compile`、`mkdir`、`mv`、`ls`、`find`、`cat`——不是全面放開 Bash，其他指令仍會在 headless 模式下被擋）
- `INTEGRATION_TEST.md`（v0.1 整合測試紀錄與已修復的缺口）
- `.claude/skills/consolidate-memory/SKILL.md`（inbox 整併流程，已用真實/假造兩種內容驗證過完整路徑；`memory/` 已有第一筆真正的正本內容）
- `hermes/db.py` + `hermes/worker.py`（SQLite job queue，設計見 `hermes/DESIGN.md`；狀態機先簡化成 `queued/running/completed/failed/dead_letter` 五種，併發先 `MAX_CONCURRENT_JOBS=1`）——已驗證正常完成、session resume、retry 後成功、dead-letter、reaper 回收 stale job 五種情境
- `scripts/route_model.py`（Model Router script adapter，可實際呼叫 OpenRouter）
- `hermes/adapter/invoke_cos.sh`（Hermes → CoS 呼叫，可實際執行）
- `memory/MEMORY.md`、`memory/inbox/`（空的，尚未累積記憶）

**刻意延後（規劃中，未實作）**：
- `knowledge` 的排程觸發串接（automation 何時該叫它去整併 inbox）——現在 `daily-memory-check` 這個 cron job 本身就是在做這件事了，這條差不多可以視為完成，之後再確認
- Model Router 的 MCP server 版本

RSS Adapter（`hermes/adapters/rss.py`）已完成並常駐（每 30 分鐘；目前為 WSL2 systemd timer，launchd 版本為當時 macOS 環境的部署）。無狀態、不做排程判斷，跟 Cron 同一個原則；v0.1 範圍只做抓取/去重/`feedparser` 解析/`enqueue()`，不做摘要邏輯。用真實 feed（`hnrss.org/frontpage`）驗證過完整流程，含 launchd 真實觸發（臨時 30 秒 plist 觀察到 3 次自動觸發）。

Dashboard（`dashboard/app.py` + `dashboard/data.py`）已完成：localhost-only、read-only，手動啟動（不裝 launchd）。Read-only 是技術上強制的——`data.py` 用 `mode=ro` 開 SQLite 連線、完全不 import `hermes/db.py`，不是只靠程式碼自律。涵蓋 worker/adapter 狀態、五種 job 狀態統計、最近 jobs、單筆 job detail、成本統計、memory inbox 數量、log 檢視。

Telegram Polling Adapter（`hermes/adapters/telegram.py`）已完成，含 `delivered_at` 回覆追蹤與 12 個 mock 過網路呼叫的單元測試。**Live 驗證已通過**：用真的 bot token 收發過真實訊息，使用者在 Telegram 上確認收到回覆，細節見 `hermes/README.md`。目前已在 WSL2 側以 systemd 常駐（`hermes-telegram.service`）。

Cron Adapter（`hermes/adapters/cron.py`）已完成並常駐（`daily-memory-check`，每天 08:00；目前為 WSL2 systemd timer `hermes-cron-daily-memory-check.timer`）。刻意設計成無狀態、不做排程判斷，排程完全交給部署層（systemd timer；當時 macOS 為 launchd）。**當時驗證特別確認了「排程器真的自己觸發」，不是只手動跑**：裝了一個 30 秒間隔的臨時 plist，觀察到 5 次自動觸發、5 筆 job 全部處理成功，才移除臨時設定改裝正式的每日排程。

`worker.py` 的常駐部署已完成——目前是 WSL2 systemd（`hermes/systemd/`，`hermes-worker.service`）；當時 macOS 的 launchd 部署（`hermes/launchd/`，現為 legacy/reference）驗證過 crash 自動重啟，過程中發現並修好兩個 launchd 環境特有的問題（`PATH` 不繼承、`KeepAlive:{Crashed:true}` 不涵蓋 `SIGKILL`），細節見 `hermes/README.md`。部署層刻意跟 Runtime 解耦：`worker.py`/`db.py` 不依賴任何特定部署機制存在，換 launchd/systemd/Docker 只需要換部署目錄。

五個領域全部建立完成。目前所有跨領域依賴（`planning` → `knowledge`）都還是靠 CoS 在分派層手動處理，沒有自動化的依賴解析。headless 模式下的權限缺口（WebSearch/WebFetch、以及 py_compile/mkdir/mv/ls/find/cat 這組最小 Bash 白名單）都已解決；其他未列在白名單裡的 Bash 指令，headless 模式下仍會被擋，之後有新需求再個別評估是否加進白名單。
