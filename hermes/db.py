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


if __name__ == "__main__":
    _cli()
