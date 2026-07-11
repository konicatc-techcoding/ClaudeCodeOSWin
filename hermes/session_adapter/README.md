# Hermes Session Adapter — v0.1（read-only importer）

讀取 HermesAgent（NousResearch Hermes）的 session 資料，轉成 ClaudeCodeOS 內部的
normalized memory/event 格式。**只讀，絕不寫回 Hermes 原始資料。**

## 來源格式（2026-07-07 實地確認，不是假設）

| 項目 | 內容 |
|---|---|
| 正本位置 | `~/.hermes/state.db`（macOS/Linux/WSL）或 `%LOCALAPPDATA%\hermes\state.db`（Windows） |
| 格式 | SQLite（WAL mode），schema_version table 存在 |
| session table | `sessions(id TEXT PK, source('cli'\|'tui'\|'telegram'\|'cron'), model, started_at REAL epoch, ended_at, title, message_count, chat_id, chat_type, thread_id, …)` |
| message table | `messages(id INTEGER PK, session_id, role('user'\|'assistant'\|'tool'\|'system'\|'session_meta'), content, tool_call_id, tool_calls JSON, tool_name, timestamp REAL epoch, finish_reason, token_count, active, compacted, …)` |
| `sessions/sessions.json` | 只是 gateway routing index（messaging session key → active session id），檔案裡的 `_README` 自己就這麼說。**不是 session 正本，本 adapter 不讀它。** |

## Normalized schema

### `claudecodeos.event.v1`

```json
{
  "schema": "claudecodeos.event.v1",
  "source": "hermes",
  "session_id": "20260630_183709_063b4e40",
  "event_id": "hermes:20260630_183709_063b4e40:102",
  "timestamp": "2026-07-06T22:09:10+00:00",
  "role": "user | assistant | tool | system | session_meta | unknown",
  "type": "message | tool_call | tool_result | meta",
  "content": "文字內容（NULL 補空字串）",
  "metadata": {
    "raw_message_id": 102,
    "session_source": "telegram",
    "session_title": "…",
    "model": "gpt-5.5",
    "tool_name": null,
    "tool_call_id": null,
    "tool_calls": null,
    "finish_reason": "stop",
    "token_count": 12,
    "active": true,
    "compacted": false,
    "warnings": ["（只在有容錯處理時出現）"]
  }
}
```

- `event_id` 是穩定的去重 key（來源 + session + message rowid），重複匯入可以 idempotent。
- `type` 推導：`role=tool` → `tool_result`；`role=assistant` 且 `tool_calls` 可解析 → `tool_call`；`role=session_meta` → `meta`；其餘 → `message`。
- 容錯：壞欄位不中斷整批，全部記在 `metadata.warnings`；`tool_calls` 解析失敗時原始字串保留在 `metadata.tool_calls_raw`。
- `validate_event(event)` 提供 schema 驗證，回傳問題清單（空 = 合法）。

### `claudecodeos.session.v1`

`{schema, source:"hermes", session_id, session_source, title, model, started_at/ended_at(ISO 8601 UTC), end_reason, message_count, metadata{chat_id, chat_type, thread_id, session_key, parent_session_id, archived, user_id}}`

## Read-only 保證（技術上強制）

1. 唯一的 SQLite 連線入口用 URI `mode=ro` + `PRAGMA query_only=ON`——寫入語句直接被 SQLite 拒絕（有測試驗證）。
2. `snapshot=True` 模式：複製 db（含 `-wal`/`-shm`）到 temp 再讀副本，避開 Hermes live 寫入時的鎖競爭；來源仍然只被讀取。**一致性驗證**：複製前後比對來源三檔的 fingerprint（存在與否、size、mtime_ns），不一致（複製期間來源被寫入，副本可能撕裂）→ 清掉該次 temp 目錄重試；副本另跑唯讀 `PRAGMA quick_check`，非 ok 同樣重試。最多 3 次，全失敗丟 `HermesSessionReadError`（fail loud），任何失敗路徑都不留 temp 目錄。副本一律以 `mode=ro` 開啟，不使用 `immutable=1`。
3. `write_inbox_file()` 用 `open(mode="x")` 只新增、永不覆寫，且拒絕把輸出寫進來源資料目錄。
4. 測試含 sha256 前後比對，證明完整讀取流程不改變來源檔任何 byte。

## 對接 memory/inbox/ 慣例

adapter 本身不主動落地。要落地時由呼叫端呼叫 `write_inbox_file(export, "memory/inbox")`，
在 `memory/inbox/` **新增**一個 `hermes_session_<session_id>.md`——完全符合
ARCHITECTURE.md 第 4 節「背景管線只能新增 inbox 檔案，不能編輯既有檔案或 `memory/*.md`
正本」的規則。之後由 `consolidate-memory` skill 把它整併進正本。

### Idempotency（同 session 重跑不產生重複檔）

- **檔名是 deterministic key**：`hermes_session_<session_id>.md`，不含落地時間戳。
  同 session 任何時間重跑都對到同一個檔名，`open(mode="x")` 天然擋掉重複落地
  （舊版檔名帶落地時間戳，不同秒重跑會產生 byte-identical 重複檔——已修正）。
- **落地前掃描歸檔**：檢查 inbox 本層、`.processed/`、`.failed/`，檔名含
  `hermes_session_<session_id>` 子字串（涵蓋舊時間戳格式的歸檔）**或** frontmatter
  `session_id` 相符者，都視為已匯入過——已整併的 session 不會重新落地。
- **已存在時的行為**：Python API 丟 `InboxAlreadyImportedError`（帶 `session_id` 與
  `existing_path`）；CLI `to-inbox` 印 `already imported：…` 到 stderr 並以
  **exit code 3** 結束（不靜默成功、不假裝有匯入；0 = 真的新增了檔案）。
- **人工重匯**：`--force`（API `force=True`）只略過已匯入掃描；exclusive create
  仍生效——inbox 本層已有同名檔時 force 也不覆寫，要先手動移走舊檔。預設絕不 force。

### Frontmatter（`claudecodeos.inbox.v1`，見 docs/memory-taxonomy.md §5）

落地檔案開頭帶 YAML frontmatter：`schema`、`source: hermes-session`、`session_id`、
`event_id_range`（對應 `claudecodeos.event.v1` 去重 key 範圍）、`created_at`（落地時間，
UTC）、`usefulness: pending`、`sensitivity: pending`。**usefulness／sensitivity 一律是
pending**——adapter 不做內容判斷與敏感偵測，不假裝判斷完成；評定是落地後呼叫端／
consolidation 的責任（taxonomy §4.2／§4.3）。依政策不設 consolidation 狀態欄位，
待處理／已處理／失敗由目錄位置（`inbox/`、`.processed/`、`.failed/`）表達。

## 用法

`--db` 與 `--snapshot` 是**全域 flag，必須放在子指令前面**
（例：`adapter.py --snapshot list`，不是 `adapter.py list --snapshot`）。

```bash
# Windows
py -3.11 hermes/session_adapter/adapter.py [--snapshot] [--db PATH] list [--source telegram]
py -3.11 hermes/session_adapter/adapter.py [--snapshot] [--db PATH] export <session_id>
py -3.11 hermes/session_adapter/adapter.py [--snapshot] [--db PATH] to-inbox <session_id> \
    [--inbox memory/inbox] [--force] [--full]

# WSL / macOS（state.db 在 ~/.hermes/ 時自動偵測；也可 --db 指定）
python3 hermes/session_adapter/adapter.py list
```

`to-inbox` 的 exit code：`0` 新增成功；`3` 該 session 已匯入過（未重複落地）；
其他非零為一般錯誤。`--full` 讓對話摘錄不截斷（預設尾端 30 則、每則 500 字元，
更聰明的摘錄策略是已知 TODO）。

程式內使用：

```python
from adapter import HermesSessionAdapter  # sys.path 加入 hermes/session_adapter/

with HermesSessionAdapter(snapshot=True) as a:
    for event in a.iter_events(session_id="..."):
        ...
    export = a.export_session("...")
    a.write_inbox_file(export, "memory/inbox")  # 呼叫端自行決定要不要落地
```

## 測試

```bash
py -3.11 hermes/session_adapter/tests/test_adapter.py      # Windows
python3 hermes/session_adapter/tests/test_adapter.py        # WSL/macOS
```

fixtures 在 `tests/fixtures/seed_state_db.sql`（schema 抄自真實 state.db），每個測試
在 temp 目錄建假 db，不碰真實 Hermes 資料。

## 整合點（都是「未來由呼叫端新增」，本模組不改任何既有檔案）

- **hermes_bridge.py 同型的橋接**：新增一個 cron 觸發的 bridge（模式同
  `hermes/adapters/hermes_bridge.py` 的 skills 同步），定期比對 Hermes 新完結的
  session，`export_session()` 後 `enqueue()` 給 headless CoS 決定要不要寫 inbox。
- **knowledge subagent**：整併記憶時可直接呼叫 `to-inbox` CLI 把指定 session 落地成
  inbox 檔案，再走 consolidate-memory。
- **dashboard**：`dashboard/data.py` 同樣是 `mode=ro` 讀 SQLite 的模式，未來可以用
  `list_sessions()` 加一個 Hermes session 檢視頁。

## 限制與風險

- Hermes 是第三方應用，`state.db` schema 沒有相容性承諾——`schema_version` 變動時
  adapter 可能需要跟進（目前對缺欄位的容錯只到訊息層級，table/欄位改名會直接
  `HermesSessionReadError`，fail loud 不 fail silent）。
- live 讀取 WAL db 理論上可能碰到鎖競爭，遇到就用 `--snapshot`。
- `messages.content` 可能含敏感資料（token、健康資料等）——落地到 memory/inbox/ 前
  由呼叫端／CoS 判斷，adapter 不做過濾。
