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
2. `snapshot=True` 模式：複製 db（含 `-wal`/`-shm`）到 temp 再讀副本，避開 Hermes live 寫入時的鎖競爭；來源仍然只被讀取。
3. `write_inbox_file()` 用 `open(mode="x")` 只新增、永不覆寫，且拒絕把輸出寫進來源資料目錄。
4. 測試含 sha256 前後比對，證明完整讀取流程不改變來源檔任何 byte。

## 對接 memory/inbox/ 慣例

adapter 本身不主動落地。要落地時由呼叫端呼叫 `write_inbox_file(export, "memory/inbox")`，
在 `memory/inbox/` **新增**一個 `YYYYMMDDTHHMMSSZ_hermes_session_<id>.md`——完全符合
ARCHITECTURE.md 第 4 節「背景管線只能新增 inbox 檔案，不能編輯既有檔案或 `memory/*.md`
正本」的規則。之後由 `consolidate-memory` skill 把它整併進正本。

## 用法

```bash
# Windows
py -3.11 hermes/session_adapter/adapter.py list [--source telegram] [--snapshot]
py -3.11 hermes/session_adapter/adapter.py export <session_id>
py -3.11 hermes/session_adapter/adapter.py to-inbox <session_id> [--inbox memory/inbox]

# WSL / macOS（state.db 在 ~/.hermes/ 時自動偵測；也可 --db 指定）
python3 hermes/session_adapter/adapter.py list
```

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
