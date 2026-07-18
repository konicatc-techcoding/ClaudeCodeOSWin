#!/usr/bin/env python3
"""hermes/db.py — v0.1

Hermes job queue 的 SQLite 存取層。設計見 hermes/DESIGN.md。

這是未來所有事件來源（Telegram/RSS/cron）共用的介面——它們只需要呼叫
enqueue()，不需要知道 worker 怎麼跑。

CLI 用法（手動測試/操作用）：
    python3 hermes/db.py enqueue --prompt "..." [--source manual] [--thread-id X] [--max-attempts N]
    python3 hermes/db.py list [--status queued]
    python3 hermes/db.py show <job_id>
"""
import argparse
import contextlib
import json
import random
import sqlite3
import sys
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "hermes" / "jobs.db"

SESSION_TTL_SECONDS = 24 * 3600
BACKOFF_BASE_SECONDS = 30
BACKOFF_CAP_SECONDS = 1800

_lock = threading.Lock()


class TriageEnqueueConflict(RuntimeError):
    """同一 identity tuple (source, external_key, prompt_version) 已存在，
    但 payload_hash 不同——代表 artifact 內容漂移，fail closed，需人工調查。
    （提案 stage2.5 §2.2 分支 3）"""


class RequeueRejected(RuntimeError):
    """requeue_dead_letter 拒絕：這個 job 不是（或已不是）dead_letter。
    不論是乾淨的 rowcount=0（§4.1c 分支 3）還是 busy 衝突後重新確認
    （分支 4c），一律用這個例外，呼叫端不需分辨偵測路徑。"""


class DispatchTransitionRejected(RuntimeError):
    """Stage 2.6b（提案 stage2.6 §5.3／§9「2.6b」DoD）：dispatch_records
    狀態機拒絕本次轉換——record 不存在、或目前 status 不是 'proposed'
    （重複 approve、reject 後 approve、approve 後 reject 等非法轉換）。
    一律明確報錯，不靜默 no-op（比照 RequeueRejected 的 fail-visible 慣例）。"""


class RequeueRetryableDBError(RuntimeError):
    """requeue_dead_letter 遇到暫時性 DB 爭用（busy/snapshot 衝突），且
    重新查詢確認 job 當下仍是 dead_letter（§4.1c 分支 4d）。本函式不自動
    重試；要不要重試由呼叫端決定。攜帶 job_id 與底層原始例外供除錯。"""

    def __init__(self, job_id: str, original: BaseException):
        super().__init__(
            f"暫時性 DB 爭用，job {job_id} 仍是 dead_letter，可由呼叫端決定重試：{original}"
        )
        self.job_id = job_id
        self.original = original


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _backoff_seconds(attempts: int) -> float:
    base = min(BACKOFF_BASE_SECONDS * (2 ** max(attempts - 1, 0)), BACKOFF_CAP_SECONDS)
    jitter = base * random.uniform(-0.2, 0.2)
    return base + jitter


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@contextlib.contextmanager
def _db():
    conn = get_connection()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db():
    with _lock, _db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id              TEXT PRIMARY KEY,
                source          TEXT NOT NULL,
                payload         TEXT NOT NULL,
                prompt          TEXT NOT NULL,
                thread_id       TEXT,
                session_id      TEXT,
                status          TEXT NOT NULL DEFAULT 'queued',
                priority        INTEGER NOT NULL DEFAULT 0,
                attempts        INTEGER NOT NULL DEFAULT 0,
                max_attempts    INTEGER NOT NULL DEFAULT 3,
                next_attempt_at TEXT,
                worker_id       TEXT,
                locked_at       TEXT,
                result          TEXT,
                cost_usd        REAL,
                error_message   TEXT,
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL,
                completed_at    TEXT,
                delivered_at    TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                thread_id     TEXT PRIMARY KEY,
                session_id    TEXT NOT NULL,
                last_used_at  TEXT NOT NULL
            )
        """)
        _migrate_schema(conn)


def _migrate_schema(conn: sqlite3.Connection):
    """對既有資料庫做 in-place migration，讓舊的 jobs.db 也能補上新欄位。"""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
    if "cost_usd" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN cost_usd REAL")
    if "delivered_at" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN delivered_at TEXT")
    # Stage 2.5a（提案 §2／§4.1a）：triage identity 三元組五欄＋三欄唯一索引
    # ＋append-only 稽核表 job_requeue_events。全部冪等，對既有列零影響
    # （既有 source 的 external_key/prompt_version 恆為 NULL，SQLite UNIQUE
    # index 對含 NULL 的列不視為互相衝突）。
    if "external_key" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN external_key TEXT")
    if "payload_hash" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN payload_hash TEXT")
    if "prompt_version" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN prompt_version TEXT")
    if "requeue_count" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN requeue_count INTEGER NOT NULL DEFAULT 0")
    if "last_requeued_at" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN last_requeued_at TEXT")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_triage_identity "
        "ON jobs(source, external_key, prompt_version)"
    )
    conn.execute("""
        CREATE TABLE IF NOT EXISTS job_requeue_events (
            job_id            TEXT    NOT NULL,
            requeue_seq       INTEGER NOT NULL,
            requeued_at       TEXT    NOT NULL,
            actor             TEXT    NOT NULL,
            reason            TEXT,
            previous_error    TEXT,
            previous_attempts INTEGER NOT NULL,
            PRIMARY KEY (job_id, requeue_seq)
        )
    """)
    # Stage 2.6b（提案 stage2.6 §5.1）：dispatch 資料層兩張新表。冪等
    # （CREATE TABLE IF NOT EXISTS）、對既有列零影響、不動 jobs 表既有欄位、
    # 不碰 bridge_state.db。dispatch_records 的 UNIQUE(triage_job_id) 是
    # 第一層冪等錨點；dispatch_events 為 append-only 稽核（樣板＝
    # job_requeue_events：複合 PK、actor 必填、與狀態轉換同 transaction 寫入、
    # 只有 INSERT 沒有 UPDATE/DELETE 路徑）。
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dispatch_records (
            triage_job_id    TEXT NOT NULL UNIQUE,
            event_id         TEXT NOT NULL,
            suggested_owner  TEXT,
            status           TEXT NOT NULL DEFAULT 'proposed',
            task_description TEXT,
            dispatch_job_id  TEXT,
            created_at       TEXT NOT NULL,
            updated_at       TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dispatch_events (
            triage_job_id    TEXT    NOT NULL,
            event_seq        INTEGER NOT NULL,
            occurred_at      TEXT    NOT NULL,
            action           TEXT    NOT NULL,
            actor            TEXT    NOT NULL,
            reason           TEXT,
            PRIMARY KEY (triage_job_id, event_seq)
        )
    """)
    # Stage 2.7a（提案 stage2.7 §2.3）：notification_log——通知冪等的第一層
    # 權威（第二層是 hermes send 的 message-key ledger）。append-only 慣例：
    # 只 INSERT、無 UPDATE/DELETE 路徑；UNIQUE(message_key) 是冪等錨點；
    # **送成功才寫入**（send 失敗不落表，下輪重掃自然補送）。這是 notifier
    # 唯一可寫的表——它對 jobs／dispatch_records 一律唯讀（§0.1 鐵律配套）。
    conn.execute("""
        CREATE TABLE IF NOT EXISTS notification_log (
            message_key  TEXT NOT NULL UNIQUE,
            event_type   TEXT NOT NULL,
            subject_id   TEXT NOT NULL,
            channel      TEXT NOT NULL,
            sent_at      TEXT NOT NULL,
            send_result  TEXT
        )
    """)


def enqueue(source: str, prompt: str, payload: dict | None = None,
            thread_id: str | None = None, max_attempts: int = 3,
            priority: int = 0) -> str:
    job_id = str(uuid.uuid4())
    now = _now_iso()
    with _lock, _db() as conn:
        conn.execute(
            "INSERT INTO jobs (id, source, payload, prompt, thread_id, max_attempts, "
            "priority, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (job_id, source, json.dumps(payload or {}), prompt, thread_id,
             max_attempts, priority, now, now),
        )
    return job_id


def enqueue_once(source: str, external_key: str, prompt_version: str,
                 payload_hash: str, prompt: str, payload: dict | None = None,
                 priority: int = 0, max_attempts: int = 1) -> tuple[str, bool]:
    """Exactly-once enqueue（提案 stage2.5 §2／§3.1）。

    Identity 是三元組 (source, external_key, prompt_version)。三分支語意：
      1. 查無既有 row → insert 新 job，回傳 (job_id, True)。
      2. 既有 row 且 payload_hash 相符 → idempotent no-op，回傳 (job_id, False)。
      3. 既有 row 但 payload_hash 不同 → fail closed，拋 TriageEnqueueConflict
         （內容漂移紅旗，不靜默覆蓋、不建第二筆）。

    creation exactly-once 由三欄 UNIQUE index 保證；insert 撞到
    IntegrityError 時重查既有 row、走同一組三分支邏輯。整個流程在單一
    DB 交易內完成。max_attempts 預設 1（§3.2 Option A：at-most-one
    automatic attempt，失敗即死信，只能人工 requeue）。thread_id 恆為
    NULL——這個 API 建立的 job 從不使用 session resume。
    """
    now = _now_iso()
    with _lock, _db() as conn:
        def _find_existing():
            return conn.execute(
                "SELECT id, payload_hash FROM jobs "
                "WHERE source=? AND external_key=? AND prompt_version=?",
                (source, external_key, prompt_version),
            ).fetchone()

        row = _find_existing()
        if row is None:
            job_id = str(uuid.uuid4())
            try:
                conn.execute(
                    "INSERT INTO jobs (id, source, payload, prompt, thread_id, "
                    "max_attempts, priority, external_key, payload_hash, "
                    "prompt_version, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?)",
                    (job_id, source, json.dumps(payload or {}), prompt,
                     max_attempts, priority, external_key, payload_hash,
                     prompt_version, now, now),
                )
                return (job_id, True)
            except sqlite3.IntegrityError:
                # 並行 race：撞到三元組 unique index → 重查、走同一組三分支
                row = _find_existing()
                if row is None:
                    raise  # IntegrityError 不是三元組衝突造成的，如實往外拋
        if row["payload_hash"] == payload_hash:
            return (row["id"], False)
        raise TriageEnqueueConflict(
            f"identity (source={source!r}, external_key={external_key!r}, "
            f"prompt_version={prompt_version!r}) 已存在 job {row['id']}，"
            f"但 payload_hash 不同（既有 {row['payload_hash']!r} vs 本次 "
            f"{payload_hash!r}）——artifact 內容漂移，需人工調查"
        )


_REQUEUE_UPDATE_SQL = """
    UPDATE jobs
    SET status='queued', attempts=0, next_attempt_at=NULL, worker_id=NULL,
        locked_at=NULL, requeue_count=requeue_count+1,
        last_requeued_at=?, updated_at=?
    WHERE id=? AND status='dead_letter'
"""


def _execute_requeue_update(conn: sqlite3.Connection, now: str, job_id: str) -> sqlite3.Cursor:
    """執行 requeue 的 conditional UPDATE。

    獨立成模組層函式，是提案 §4.1d 要求的決定性 fault-injection 注入點：
    正常路徑就是單純執行底層的 conn.execute(...)，測試用 monkeypatch 替換
    它來決定性地模擬 SQLITE_BUSY／SQLITE_BUSY_SNAPSHOT。不改動共用
    _db()／連線設定。
    """
    return conn.execute(_REQUEUE_UPDATE_SQL, (now, now, job_id))


def requeue_dead_letter(job_id: str, actor: str, reason: str | None = None) -> dict:
    """把一筆 dead_letter job 原子性地重置回 queued，並寫入一筆稽核事件。

    只對 status='dead_letter' 生效（並行安全狀態機見提案 §4.1c 四分支）；
    不建立第二筆 job；identity／payload／payload_hash／prompt_version 不變；
    只能由明確的人工動作觸發。actor 為必填且不得為空或純空白（任何 DB
    操作之前驗證）；寫入稽核列的是 strip() 後的正規化字串。

    絕不自動重試：每次呼叫只執行一輪嘗試序列，遇到暫時性 DB 爭用拋
    RequeueRetryableDBError，由呼叫端決定是否重試。
    """
    if not isinstance(actor, str) or actor.strip() == "":
        raise ValueError("actor 不得為空或純空白")
    actor = actor.strip()
    now = _now_iso()
    try:
        with _lock, _db() as conn:
            # 同一 transaction 內、UPDATE 之前先捕捉 previous_* 值（§4.1a）
            prev = conn.execute(
                "SELECT error_message, attempts FROM jobs WHERE id=?", (job_id,)
            ).fetchone()
            cur = _execute_requeue_update(conn, now, job_id)
            if cur.rowcount == 1:
                # 分支 2：同一 transaction 內插入稽核列後 commit
                seq_row = conn.execute(
                    "SELECT COALESCE(MAX(requeue_seq), 0) + 1 AS seq "
                    "FROM job_requeue_events WHERE job_id=?",
                    (job_id,),
                ).fetchone()
                requeue_seq = seq_row["seq"]
                conn.execute(
                    "INSERT INTO job_requeue_events (job_id, requeue_seq, "
                    "requeued_at, actor, reason, previous_error, previous_attempts) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (job_id, requeue_seq, now, actor, reason,
                     prev["error_message"], prev["attempts"]),
                )
                return {
                    "job_id": job_id,
                    "requeue_seq": requeue_seq,
                    "requeued_at": now,
                    "actor": actor,
                    "reason": reason,
                    "previous_error": prev["error_message"],
                    "previous_attempts": prev["attempts"],
                }
            # 分支 3：乾淨的 rowcount=0——job 不存在或已不是 dead_letter。
            # 不寫任何稽核列（例外往外拋，with conn: 自動 rollback）。
            raise RequeueRejected(
                f"job {job_id} 不是（或已不是）dead_letter，requeue 被拒絕"
            )
    except sqlite3.OperationalError as exc:
        # 分支 4：busy/snapshot 衝突。失敗的 transaction 已 rollback；
        # 開一個全新、獨立的唯讀查詢確認 job 目前的 status。
        with _db() as conn:
            row = conn.execute(
                "SELECT status FROM jobs WHERE id=?", (job_id,)
            ).fetchone()
        if row is None or row["status"] != "dead_letter":
            # 分支 4c：另一個並行呼叫已搶先成功——正規化成 RequeueRejected，
            # 呼叫端不需（也不能）分辨輸家是撞到分支 3 還是 4c。
            raise RequeueRejected(
                f"job {job_id} 不是（或已不是）dead_letter，requeue 被拒絕"
            ) from exc
        # 分支 4d：job 仍是 dead_letter，純暫時性 DB 爭用——不自動重試、
        # 不誤報成「已被別人 requeue 過」。
        raise RequeueRetryableDBError(job_id, exc) from exc


# ---------- Stage 2.6b：dispatch 資料層（提案 stage2.6 §5） ----------

def _normalize_actor(actor) -> str:
    """actor 驗證（§5.1：沿用 requeue_dead_letter 慣例）——任何 DB 操作之前
    拒絕空/純空白；寫入稽核列的是 strip() 後的正規化字串。"""
    if not isinstance(actor, str) or actor.strip() == "":
        raise ValueError("actor 不得為空或純空白")
    return actor.strip()


def _insert_dispatch_event(conn: sqlite3.Connection, triage_job_id: str,
                           action: str, actor: str, reason: str | None,
                           now: str) -> int:
    """在**呼叫端的同一 transaction 內**append 一筆 dispatch_events 稽核列
    （比照 job_requeue_events：MAX(seq)+1、只 INSERT）。回傳 event_seq。"""
    seq_row = conn.execute(
        "SELECT COALESCE(MAX(event_seq), 0) + 1 AS seq "
        "FROM dispatch_events WHERE triage_job_id=?",
        (triage_job_id,),
    ).fetchone()
    event_seq = seq_row["seq"]
    conn.execute(
        "INSERT INTO dispatch_events (triage_job_id, event_seq, occurred_at, "
        "action, actor, reason) VALUES (?, ?, ?, ?, ?, ?)",
        (triage_job_id, event_seq, now, action, actor, reason),
    )
    return event_seq


def register_dispatch_record(triage_job_id: str, event_id: str,
                             suggested_owner: str | None, actor: str) -> bool:
    """冪等登記一筆 dispatch record（§5.2 第 3 點）。

    INSERT ... ON CONFLICT(triage_job_id) DO NOTHING——比照 enqueue_once 的
    exactly-once 精神，UNIQUE(triage_job_id) 是權威。只有**真的新建**時才
    append 一筆 action='proposed' 的稽核列（同 transaction）；已存在 →
    回傳 False、零寫入（重跑 list 零重複登記，§9 2.6b DoD）。
    suggested_owner 原樣保存（可能是髒值如 "na"，§5.1——驗證與人工確認在
    CLI 呈現層，資料層不清洗）。
    """
    actor = _normalize_actor(actor)
    now = _now_iso()
    with _lock, _db() as conn:
        cur = conn.execute(
            "INSERT INTO dispatch_records (triage_job_id, event_id, "
            "suggested_owner, status, created_at, updated_at) "
            "VALUES (?, ?, ?, 'proposed', ?, ?) "
            "ON CONFLICT(triage_job_id) DO NOTHING",
            (triage_job_id, event_id, suggested_owner, now, now),
        )
        if cur.rowcount == 1:
            _insert_dispatch_event(conn, triage_job_id, "proposed", actor,
                                   None, now)
            return True
        return False


def _dispatch_transition(triage_job_id: str, actor: str, reason: str | None,
                         new_status: str,
                         task_description: str | None) -> dict:
    """proposed → approved/rejected 的共用狀態機（§5.3 第一層的簡化版：
    conditional UPDATE + rowcount 檢查；rowcount=0 一律明確報錯不靜默）。
    只有 status='proposed' 的 record 允許轉換——重複 approve、reject 後
    approve、approve 後 reject 全部走 DispatchTransitionRejected。
    稽核列與狀態轉換在同一 transaction 內寫入。"""
    actor = _normalize_actor(actor)
    now = _now_iso()
    with _lock, _db() as conn:
        if task_description is not None:
            cur = conn.execute(
                "UPDATE dispatch_records SET status=?, task_description=?, "
                "updated_at=? WHERE triage_job_id=? AND status='proposed'",
                (new_status, task_description, now, triage_job_id),
            )
        else:
            cur = conn.execute(
                "UPDATE dispatch_records SET status=?, updated_at=? "
                "WHERE triage_job_id=? AND status='proposed'",
                (new_status, now, triage_job_id),
            )
        if cur.rowcount == 1:
            event_seq = _insert_dispatch_event(
                conn, triage_job_id, new_status, actor, reason, now)
            return {
                "triage_job_id": triage_job_id, "status": new_status,
                "task_description": task_description, "actor": actor,
                "reason": reason, "occurred_at": now, "event_seq": event_seq,
            }
        row = conn.execute(
            "SELECT status FROM dispatch_records WHERE triage_job_id=?",
            (triage_job_id,),
        ).fetchone()
        if row is None:
            raise DispatchTransitionRejected(
                f"查無 triage_job_id={triage_job_id} 的 dispatch record——"
                "請先跑 dispatch CLI list 完成冪等登記（不提供繞過 triage 的"
                "人工直建路徑，提案 §5.4）")
        raise DispatchTransitionRejected(
            f"dispatch record {triage_job_id} 目前 status={row['status']!r}，"
            f"不允許轉換為 {new_status!r}（只有 'proposed' 可以 approve/"
            "reject；重複 approve、reject 後 approve 等一律明確拒絕，"
            "提案 §9 2.6b DoD）")


def approve_dispatch(triage_job_id: str, actor: str, task_description: str,
                     reason: str | None = None) -> dict:
    """proposed → approved（§9「2.6b」：**只落 record 與稽核，不 enqueue、
    不呼叫模型**——執行閉環是 2.6c）。task_description 必填非空
    （§5.1：approved 起非空；內容是核准當下經人確認的任務描述）。"""
    if not isinstance(task_description, str) or task_description.strip() == "":
        raise ValueError("task_description 不得為空或純空白（approved 的 "
                         "record 必須帶人工確認過的任務描述，提案 §5.1）")
    return _dispatch_transition(triage_job_id, actor, reason, "approved",
                                task_description.strip())


def reject_dispatch(triage_job_id: str, actor: str,
                    reason: str | None = None) -> dict:
    """proposed → rejected（到此為止，不建任何 job，§3）。"""
    return _dispatch_transition(triage_job_id, actor, reason, "rejected", None)


def mark_dispatched(triage_job_id: str, dispatch_job_id: str, actor: str,
                    reason: str | None = None) -> dict:
    """Stage 2.6c（提案 stage2.6 §5.3／§8）：approved → dispatched ＋
    `dispatch_job_id` 回填。

    只有 status='approved' 且 dispatch_job_id IS NULL 的 record 允許回填
    （conditional UPDATE + rowcount 檢查，rowcount=0 一律明確報錯不靜默，
    比照 _dispatch_transition）。稽核列（action='dispatched'）與狀態轉換在
    同一 transaction 內寫入。§8 拍板順序「先寫 approved 稽核、再 enqueue、
    再回填 dispatch_job_id」的第三步就是本函式——enqueue 成功但本函式失敗
    只會留下「approved 未派工」的可補跑中間態，不會出現「已派工無稽核」。
    """
    actor = _normalize_actor(actor)
    if not isinstance(dispatch_job_id, str) or dispatch_job_id.strip() == "":
        raise ValueError("dispatch_job_id 不得為空或純空白")
    now = _now_iso()
    with _lock, _db() as conn:
        cur = conn.execute(
            "UPDATE dispatch_records SET status='dispatched', "
            "dispatch_job_id=?, updated_at=? "
            "WHERE triage_job_id=? AND status='approved' "
            "AND dispatch_job_id IS NULL",
            (dispatch_job_id, now, triage_job_id),
        )
        if cur.rowcount == 1:
            event_seq = _insert_dispatch_event(
                conn, triage_job_id, "dispatched", actor, reason, now)
            return {
                "triage_job_id": triage_job_id, "status": "dispatched",
                "dispatch_job_id": dispatch_job_id, "actor": actor,
                "reason": reason, "occurred_at": now, "event_seq": event_seq,
            }
        row = conn.execute(
            "SELECT status, dispatch_job_id FROM dispatch_records "
            "WHERE triage_job_id=?",
            (triage_job_id,),
        ).fetchone()
        if row is None:
            raise DispatchTransitionRejected(
                f"查無 triage_job_id={triage_job_id} 的 dispatch record——"
                "無法回填 dispatch_job_id")
        raise DispatchTransitionRejected(
            f"dispatch record {triage_job_id} 目前 status={row['status']!r}、"
            f"dispatch_job_id={row['dispatch_job_id']!r}，不允許回填為 "
            "dispatched（只有 status='approved' 且尚未回填的 record 可以，"
            "提案 §5.3／§8）")


def list_approved_undispatched() -> list[sqlite3.Row]:
    """Stage 2.6c（§8）：找出「approved 未派工」中間態的 record（approve 後
    enqueue 失敗／process crash 留下的可補跑狀態）。"""
    with _db() as conn:
        return conn.execute(
            "SELECT * FROM dispatch_records "
            "WHERE status='approved' AND dispatch_job_id IS NULL "
            "ORDER BY created_at ASC"
        ).fetchall()


def get_dispatch_record(triage_job_id: str) -> sqlite3.Row | None:
    with _db() as conn:
        return conn.execute(
            "SELECT * FROM dispatch_records WHERE triage_job_id=?",
            (triage_job_id,),
        ).fetchone()


def list_dispatch_records() -> list[sqlite3.Row]:
    with _db() as conn:
        return conn.execute(
            "SELECT * FROM dispatch_records ORDER BY created_at ASC"
        ).fetchall()


def list_dispatch_events(triage_job_id: str) -> list[sqlite3.Row]:
    with _db() as conn:
        return conn.execute(
            "SELECT * FROM dispatch_events WHERE triage_job_id=? "
            "ORDER BY event_seq ASC",
            (triage_job_id,),
        ).fetchall()


# ---------- Stage 2.7a：notification_log（提案 stage2.7 §2.3） ----------

def record_notification(message_key: str, event_type: str, subject_id: str,
                        channel: str, send_result: str | None = None) -> dict:
    """append 一筆通知稽核列（**送成功之後**才呼叫——§2.3 拍板順序）。

    只 INSERT；同 message_key 重複寫入會撞 UNIQUE 直接拋
    sqlite3.IntegrityError（fail-visible——notifier 本來就該先過濾已通知的
    key，走到重複代表流程有 bug，不靜默吞掉）。"""
    now = _now_iso()
    with _lock, _db() as conn:
        conn.execute(
            "INSERT INTO notification_log (message_key, event_type, "
            "subject_id, channel, sent_at, send_result) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (message_key, event_type, subject_id, channel, now, send_result),
        )
    return {"message_key": message_key, "event_type": event_type,
            "subject_id": subject_id, "channel": channel, "sent_at": now,
            "send_result": send_result}


def list_notification_log() -> list[sqlite3.Row]:
    """唯讀列出全部通知稽核列（觀測／測試斷言用——§2.3 否決「只靠 hermes
    send ledger」的理由 (i)：本 repo 要能自己回答「哪些已通知」）。"""
    with _db() as conn:
        return conn.execute(
            "SELECT * FROM notification_log ORDER BY sent_at ASC, message_key ASC"
        ).fetchall()


def claim_next_job(worker_id: str) -> dict | None:
    now = _now_iso()
    with _lock, _db() as conn:
        cur = conn.execute(
            """
            UPDATE jobs
            SET status='running', worker_id=?, locked_at=?, updated_at=?, attempts=attempts+1
            WHERE id = (
                SELECT id FROM jobs
                WHERE status='queued' AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                ORDER BY priority DESC, created_at ASC
                LIMIT 1
            )
            RETURNING id, source, payload, prompt, thread_id, session_id, attempts, max_attempts
            """,
            (worker_id, now, now, now),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def mark_completed(job_id: str, result: str, cost_usd: float | None = None):
    now = _now_iso()
    with _lock, _db() as conn:
        conn.execute(
            "UPDATE jobs SET status='completed', result=?, cost_usd=COALESCE(?, cost_usd), "
            "updated_at=?, completed_at=? WHERE id=?",
            (result, cost_usd, now, now, job_id),
        )


def mark_failed(job_id: str, error_message: str, cost_usd: float | None = None):
    """失敗（或 dead-letter）時也記錄成本——is_error/非 success 這類 CoS 有回應但視為
    失敗的情況，Claude 是真的有執行、可能真的有花費，不該漏記。exit code 非 0／逾時／
    JSON 解析失敗這幾種在呼叫 invoke_cos.sh 這層就出錯，沒有成本可記，cost_usd 傳 None
    即可——COALESCE 保留上一次已知的成本，不會被 None 洗掉。
    """
    now = _now_iso()
    with _lock, _db() as conn:
        row = conn.execute(
            "SELECT attempts, max_attempts FROM jobs WHERE id=?", (job_id,)
        ).fetchone()
        if row is None:
            return
        if row["attempts"] >= row["max_attempts"]:
            conn.execute(
                "UPDATE jobs SET status='dead_letter', error_message=?, "
                "cost_usd=COALESCE(?, cost_usd), updated_at=? WHERE id=?",
                (error_message, cost_usd, now, job_id),
            )
        else:
            next_at = (
                datetime.now(timezone.utc)
                + timedelta(seconds=_backoff_seconds(row["attempts"]))
            ).isoformat()
            conn.execute(
                "UPDATE jobs SET status='queued', error_message=?, "
                "cost_usd=COALESCE(?, cost_usd), next_attempt_at=?, updated_at=? WHERE id=?",
                (error_message, cost_usd, next_at, now, job_id),
            )


def list_undelivered_completed(source: str) -> list[sqlite3.Row]:
    """給需要「回覆」的 adapter 用（目前只有 telegram）——找出已完成但還沒回覆過的 job。
    只是 status='completed' 之外多記一個 delivered_at 時間戳，不恢復 delivered 狀態、
    不影響現有的五種 status。
    """
    with _db() as conn:
        return conn.execute(
            "SELECT * FROM jobs WHERE source=? AND status='completed' AND delivered_at IS NULL "
            "ORDER BY completed_at ASC",
            (source,),
        ).fetchall()


def mark_delivered(job_id: str):
    now = _now_iso()
    with _lock, _db() as conn:
        conn.execute("UPDATE jobs SET delivered_at=? WHERE id=?", (now, job_id))


def reap_stale_jobs(stale_after_seconds: int = 600) -> int:
    threshold = (
        datetime.now(timezone.utc) - timedelta(seconds=stale_after_seconds)
    ).isoformat()
    with _lock, _db() as conn:
        rows = conn.execute(
            "SELECT id, attempts, max_attempts FROM jobs WHERE status='running' AND locked_at < ?",
            (threshold,),
        ).fetchall()
        now = _now_iso()
        for row in rows:
            if row["attempts"] >= row["max_attempts"]:
                conn.execute(
                    "UPDATE jobs SET status='dead_letter', error_message=?, updated_at=? WHERE id=?",
                    ("worker crashed or hung；reaper 偵測到已超過 max_attempts", now, row["id"]),
                )
            else:
                next_at = (
                    datetime.now(timezone.utc)
                    + timedelta(seconds=_backoff_seconds(row["attempts"]))
                ).isoformat()
                conn.execute(
                    "UPDATE jobs SET status='queued', next_attempt_at=?, error_message=?, "
                    "updated_at=? WHERE id=?",
                    (next_at, "worker crashed or hung；reaper 已 requeue", now, row["id"]),
                )
        return len(rows)


def get_resumable_session(thread_id: str | None) -> str | None:
    if thread_id is None:
        return None
    with _db() as conn:
        row = conn.execute(
            "SELECT session_id, last_used_at FROM sessions WHERE thread_id=?", (thread_id,)
        ).fetchone()
    if row is None:
        return None
    last_used = datetime.fromisoformat(row["last_used_at"])
    if (datetime.now(timezone.utc) - last_used).total_seconds() > SESSION_TTL_SECONDS:
        return None
    return row["session_id"]


def upsert_session(thread_id: str | None, session_id: str | None):
    if thread_id is None or session_id is None:
        return
    now = _now_iso()
    with _lock, _db() as conn:
        conn.execute(
            "INSERT INTO sessions (thread_id, session_id, last_used_at) VALUES (?, ?, ?) "
            "ON CONFLICT(thread_id) DO UPDATE SET session_id=excluded.session_id, "
            "last_used_at=excluded.last_used_at",
            (thread_id, session_id, now),
        )


def list_jobs(status: str | None = None) -> list[sqlite3.Row]:
    with _db() as conn:
        if status:
            return conn.execute(
                "SELECT * FROM jobs WHERE status=? ORDER BY created_at DESC", (status,)
            ).fetchall()
        return conn.execute("SELECT * FROM jobs ORDER BY created_at DESC").fetchall()


def show_job(job_id: str) -> sqlite3.Row | None:
    with _db() as conn:
        return conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()


def _cli():
    parser = argparse.ArgumentParser(description="hermes/db.py — job queue CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_enqueue = sub.add_parser("enqueue", help="手動塞一筆 job")
    p_enqueue.add_argument("--source", default="manual")
    p_enqueue.add_argument("--prompt", required=True)
    p_enqueue.add_argument("--thread-id", default=None)
    p_enqueue.add_argument("--max-attempts", type=int, default=3)
    p_enqueue.add_argument("--priority", type=int, default=0)

    p_list = sub.add_parser("list", help="列出 job")
    p_list.add_argument("--status", default=None)

    p_show = sub.add_parser("show", help="顯示單一 job 的完整內容")
    p_show.add_argument("job_id")

    p_requeue = sub.add_parser(
        "requeue", help="把一筆 dead_letter job 重置回 queued（寫入稽核事件）"
    )
    p_requeue.add_argument("job_id")
    # --actor 必填、無預設值——不用任何假預設值頂替身份（提案 §4.3）。
    # API 層（requeue_dead_letter）仍會再驗證一次空/純空白，雙重保險。
    p_requeue.add_argument("--actor", required=True)
    p_requeue.add_argument("--reason", default=None)

    args = parser.parse_args()
    init_db()

    if args.cmd == "enqueue":
        job_id = enqueue(
            args.source, args.prompt, thread_id=args.thread_id,
            max_attempts=args.max_attempts, priority=args.priority,
        )
        print(job_id)
    elif args.cmd == "list":
        rows = list_jobs(args.status)
        if not rows:
            print("(沒有符合的 job)")
        for r in rows:
            print(
                f"{r['id']}  {r['status']:<12} attempts={r['attempts']}/{r['max_attempts']}  "
                f"source={r['source']:<8} thread_id={r['thread_id']}  created_at={r['created_at']}"
            )
    elif args.cmd == "show":
        row = show_job(args.job_id)
        if row is None:
            print("找不到這個 job id", file=sys.stderr)
            sys.exit(1)
        for k in row.keys():
            print(f"{k}: {row[k]}")
    elif args.cmd == "requeue":
        try:
            event = requeue_dead_letter(args.job_id, args.actor, reason=args.reason)
        except (ValueError, RequeueRejected, RequeueRetryableDBError) as exc:
            print(f"requeue 失敗：{exc}", file=sys.stderr)
            sys.exit(1)
        print(
            f"requeued job {event['job_id']} (requeue_seq={event['requeue_seq']}, "
            f"actor={event['actor']})"
        )


if __name__ == "__main__":
    _cli()
