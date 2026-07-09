#!/usr/bin/env python3
"""dashboard/data.py — v0.1

Dashboard 專用的唯讀資料層。**不 import hermes/db.py**——那裡有
enqueue()/mark_completed() 這些寫入函式，就算 dashboard 永遠不呼叫，
import 進來就代表「有能力呼叫」這件事本身存在。這裡的每個函式都只做
SELECT／讀檔案，`hermes/jobs.db` 用 mode=ro 開連線，SQLite 自己會擋掉
任何寫入嘗試，不是只靠「這次沒寫錯」的自律。

任何一個函式都不能回傳 hermes/config/telegram.json 裡的 bot_token
或其他密鑰本身，只能回傳「有沒有設定、設定了幾筆」這類中繼資訊。
"""
import contextlib
import json
import sqlite3
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
JOBS_DB_PATH = ROOT / "hermes" / "jobs.db"
LOG_DIR = ROOT / "logs" / "hermes"
MEMORY_DIR = ROOT / "memory"
REGISTRY_DIR = ROOT / "registry"
CONFIG_DIR = ROOT / "hermes" / "config"

JOB_STATUSES = ["queued", "running", "completed", "failed", "dead_letter"]


def jobs_db_exists() -> bool:
    """jobs.db 是 runtime 資料（gitignored），由 worker/adapter 第一次執行
    init_db() 時建立。環境搬遷後（macOS → Windows）檔案還不存在是正常狀態：
    mode=ro 開不存在的檔案會直接 OperationalError，所以查詢函式都先走這個
    檢查、回傳空結果。dashboard 維持唯讀，不自己建檔——建檔是 hermes 的責任。"""
    return JOBS_DB_PATH.exists()


def _ro_connection() -> sqlite3.Connection:
    """開一個唯讀連線——任何 INSERT/UPDATE/DELETE 都會被 SQLite 直接拒絕。"""
    conn = sqlite3.connect(f"file:{JOBS_DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


@contextlib.contextmanager
def _db():
    """sqlite3.Connection 當 context manager 只會 commit/rollback，不會關閉連線——
    這裡才是真的確保連線用完會 close()。"""
    conn = _ro_connection()
    try:
        yield conn
    finally:
        conn.close()


def get_status_counts() -> dict:
    if not jobs_db_exists():
        return {}
    with _db() as conn:
        rows = conn.execute("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status").fetchall()
    return {row["status"]: row["n"] for row in rows}


def get_recent_jobs(limit: int = 50, status: str | None = None, source: str | None = None) -> list[dict]:
    if not jobs_db_exists():
        return []
    with _db() as conn:
        query = "SELECT * FROM jobs"
        clauses = []
        params: list = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if source:
            clauses.append("source = ?")
            params.append(source)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def get_job(job_id: str) -> dict | None:
    if not jobs_db_exists():
        return None
    with _db() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


def get_cost_summary() -> dict:
    if not jobs_db_exists():
        return {"total": 0.0, "avg": 0.0, "count": 0, "by_source": []}
    with _db() as conn:
        total_row = conn.execute(
            "SELECT SUM(cost_usd) AS total, AVG(cost_usd) AS avg, COUNT(*) AS n "
            "FROM jobs WHERE cost_usd IS NOT NULL"
        ).fetchone()
        by_source = conn.execute(
            "SELECT source, SUM(cost_usd) AS total, COUNT(*) AS n FROM jobs "
            "WHERE cost_usd IS NOT NULL GROUP BY source ORDER BY total DESC"
        ).fetchall()
    return {
        "total": total_row["total"] or 0.0,
        "avg": total_row["avg"] or 0.0,
        "count": total_row["n"] or 0,
        "by_source": [dict(r) for r in by_source],
    }


def get_launchd_status() -> dict:
    """跑 `launchctl list`，只留跟這個專案有關的 hermes-* label。

    ClaudeCodeOSWin 複本註記：這台環境（WSL2）預期沒有 `launchctl`，這個函式
    會直接回傳 {}（FileNotFoundError 被吃掉），不會噴錯。複本改用
    `get_systemd_status()` 取代這個函式對應的用途，見下方。這個函式保留只是
    為了跟原始 macOS 版本行為一致、不破壞既有測試。
    """
    try:
        proc = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return {}
    result = {}
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        pid, last_exit, label = parts
        if "claudecodeos.hermes" in label:
            result[label] = {"pid": pid, "last_exit": last_exit}
    return result


def get_systemd_status() -> dict:
    """跑 `systemctl --user list-units --all`，取得 hermes-* service/timer 的狀態。

    ClaudeCodeOSWin 複本新增：WSL2 底下用 systemd user service 取代 launchd，
    這個函式是 `get_launchd_status()` 的等效版本。回傳格式盡量跟 launchd 版本
    對齊（{unit_name: {"pid": "-", "last_exit": active_state}}），方便 app.py
    共用同一套顯示邏輯。若 `systemctl` 不存在（沒開 systemd 支援）或執行失敗，
    回傳空 dict，不噴錯。
    """
    try:
        proc = subprocess.run(
            ["systemctl", "--user", "list-units", "--all", "--type=service,timer", "--no-legend", "--plain"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return {}
    result = {}
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        unit, load, active, sub = parts[0], parts[1], parts[2], parts[3]
        if "hermes-" not in unit:
            continue
        result[unit] = {"pid": "-", "last_exit": f"{active}/{sub}", "load": load}
    return result


def tail_log(name: str, lines: int = 100, max_bytes: int = 65536) -> str:
    """從檔尾取一段，不是整個檔案讀進記憶體再切。"""
    path = LOG_DIR / name
    if not path.exists():
        return f"（找不到 {name}）"
    size = path.stat().st_size
    with open(path, "rb") as f:
        if size > max_bytes:
            f.seek(-max_bytes, 2)
        data = f.read()
    text = data.decode("utf-8", errors="replace")
    return "\n".join(text.splitlines()[-lines:])


def get_memory_inbox_counts() -> dict:
    inbox = MEMORY_DIR / "inbox"

    def count_files(p: Path) -> int:
        if not p.exists():
            return 0
        return sum(1 for f in p.iterdir() if f.is_file() and f.name != ".gitkeep")

    return {
        "pending": count_files(inbox),
        "processed": count_files(inbox / ".processed"),
        "failed": count_files(inbox / ".failed"),
    }


def get_memory_files() -> list[str]:
    if not MEMORY_DIR.exists():
        return []
    return sorted(f.name for f in MEMORY_DIR.glob("*.md"))


def get_domain_status() -> list[dict]:
    path = REGISTRY_DIR / "agents.yaml"
    if not path.exists():
        return []
    config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return config.get("agents", [])


def get_adapter_config_status() -> dict:
    """只回報「有沒有設定、設定了幾筆」，絕對不回傳密鑰本身。"""
    result: dict = {}

    telegram_path = CONFIG_DIR / "telegram.json"
    if telegram_path.exists():
        try:
            config = json.loads(telegram_path.read_text(encoding="utf-8"))
            result["telegram"] = {
                "configured": True,
                "allowed_chat_ids_count": len(config.get("allowed_chat_ids", [])),
            }
        except json.JSONDecodeError:
            result["telegram"] = {"configured": True, "error": "設定檔格式錯誤"}
    else:
        result["telegram"] = {"configured": False}

    cron_path = CONFIG_DIR / "cron_jobs.yaml"
    if cron_path.exists():
        config = yaml.safe_load(cron_path.read_text(encoding="utf-8")) or {}
        result["cron"] = {"configured": True, "job_count": len(config.get("jobs", []))}
    else:
        result["cron"] = {"configured": False}

    rss_path = CONFIG_DIR / "rss_feeds.yaml"
    if rss_path.exists():
        config = yaml.safe_load(rss_path.read_text(encoding="utf-8")) or {}
        result["rss"] = {"configured": True, "feed_count": len(config.get("feeds", []))}
    else:
        result["rss"] = {"configured": False}

    return result
