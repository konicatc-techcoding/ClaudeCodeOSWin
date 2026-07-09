-- hermes/session_adapter/tests/fixtures/seed_state_db.sql
-- 測試用的 Hermes state.db 種子資料。
-- schema 抄自真實的 ~/.hermes/state.db（2026-07-07 實地 dump，只保留
-- sessions/messages 兩張 adapter 會讀的 table；FTS 相關 table 省略）。
-- 注意：SQLite 是動態型別，REAL NOT NULL 欄位塞文字不會報錯——
-- 下面刻意利用這點放了一筆 timestamp 壞值來測容錯。

CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    user_id TEXT,
    model TEXT,
    model_config TEXT,
    system_prompt TEXT,
    parent_session_id TEXT,
    started_at REAL NOT NULL,
    ended_at REAL,
    end_reason TEXT,
    message_count INTEGER DEFAULT 0,
    tool_call_count INTEGER DEFAULT 0,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cache_read_tokens INTEGER DEFAULT 0,
    cache_write_tokens INTEGER DEFAULT 0,
    reasoning_tokens INTEGER DEFAULT 0,
    cwd TEXT,
    billing_provider TEXT,
    billing_base_url TEXT,
    billing_mode TEXT,
    estimated_cost_usd REAL,
    actual_cost_usd REAL,
    cost_status TEXT,
    cost_source TEXT,
    pricing_version TEXT,
    title TEXT,
    api_call_count INTEGER DEFAULT 0,
    handoff_state TEXT,
    handoff_platform TEXT,
    handoff_error TEXT,
    rewind_count INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0,
    "git_branch" TEXT,
    "git_repo_root" TEXT,
    "session_key" TEXT,
    "chat_id" TEXT,
    "chat_type" TEXT,
    "thread_id" TEXT,
    "compression_failure_cooldown_until" REAL,
    "compression_failure_error" TEXT,
    FOREIGN KEY (parent_session_id) REFERENCES sessions(id)
);

CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    role TEXT NOT NULL,
    content TEXT,
    tool_call_id TEXT,
    tool_calls TEXT,
    tool_name TEXT,
    timestamp REAL NOT NULL,
    token_count INTEGER,
    finish_reason TEXT,
    reasoning TEXT,
    reasoning_content TEXT,
    reasoning_details TEXT,
    codex_reasoning_items TEXT,
    codex_message_items TEXT,
    platform_message_id TEXT,
    observed INTEGER DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    compacted INTEGER NOT NULL DEFAULT 0
);

-- ==== session 1：telegram 對話，包含完整 tool-call 流程 ====
INSERT INTO sessions (id, source, model, started_at, ended_at, end_reason,
                      message_count, title, session_key, chat_id, chat_type)
VALUES ('20260630_183709_063b4e40', 'telegram', 'gpt-5.5',
        1782815829.832352, NULL, NULL, 5, 'Garmin 健康日報',
        'agent:main:telegram:dm:1034113120', '1034113120', 'dm');

INSERT INTO messages (id, session_id, role, content, tool_call_id, tool_calls, tool_name,
                      timestamp, token_count, finish_reason, active, compacted)
VALUES
(101, '20260630_183709_063b4e40', 'session_meta',
 '{"kind":"origin","platform":"telegram"}', NULL, NULL, NULL,
 1782815829.9, NULL, NULL, 1, 0),
(102, '20260630_183709_063b4e40', 'user',
 '請給我今天的健康日報', NULL, NULL, NULL,
 1783390150.0, 12, NULL, 1, 0),
(103, '20260630_183709_063b4e40', 'assistant',
 '', NULL,
 '[{"id": "call_abc123", "type": "function", "function": {"name": "terminal", "arguments": "{\"command\":\"cat daily.json\"}"}}]',
 NULL, 1783390193.1, 40, 'tool_calls', 1, 0),
(104, '20260630_183709_063b4e40', 'tool',
 '{"output": "{\"date\": \"2026-07-07\", \"steps\": 112}"}',
 'call_abc123', NULL, 'terminal',
 1783390195.0, 80, NULL, 1, 0),
(105, '20260630_183709_063b4e40', 'assistant',
 '## 今日健康日報 — 2026-07-07（步數 112，HRV 34.0）', NULL, NULL, NULL,
 1783390221.5, 200, 'stop', 1, 0);

-- ==== session 2：cli session，含 compacted/inactive 訊息 ====
INSERT INTO sessions (id, source, model, started_at, ended_at, end_reason,
                      message_count, title)
VALUES ('20260706_155721_18145a', 'cli', 'nvidia/nemotron-3-ultra-550b-a55b:free',
        1783324649.0958667, 1783324702.9512482, 'new_session',
        2, 'Exploring Home Directory and Workspace');

INSERT INTO messages (id, session_id, role, content, timestamp, active, compacted)
VALUES
(201, '20260706_155721_18145a', 'user', 'list my home directory', 1783324650.0, 1, 0),
(202, '20260706_155721_18145a', 'assistant', '（舊回覆，已被 compact 掉）', 1783324660.0, 0, 1);

-- ==== session 3：壞資料測容錯 ====
-- content NULL、tool_calls 不是 JSON、role 未知、timestamp 是垃圾文字
INSERT INTO sessions (id, source, model, started_at, message_count, title)
VALUES ('20260707_000000_baddata', 'cron', NULL, 1783400000.0, 3, NULL);

INSERT INTO messages (id, session_id, role, content, tool_calls, timestamp, active, compacted)
VALUES
(301, '20260707_000000_baddata', 'assistant', NULL, NULL, 1783400001.0, 1, 0),
(302, '20260707_000000_baddata', 'assistant', 'tool_calls 欄位壞掉的訊息',
 '{{{not-valid-json', 1783400002.0, 1, 0),
(303, '20260707_000000_baddata', 'weird_role', '未知角色的訊息', NULL,
 'garbage-timestamp', 1, 0);
