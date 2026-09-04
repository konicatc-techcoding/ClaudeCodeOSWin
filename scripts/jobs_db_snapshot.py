#!/usr/bin/env python3
"""scripts/jobs_db_snapshot.py — 把 WSL runtime `hermes/jobs.db` 快照到 Windows 可讀位置

## 它為什麼存在（拓撲問題，不是程式 bug）

runtime `jobs.db` **只存在 WSL 部署複本**（`~/dev/ClaudeCodeOSWin/hermes/jobs.db`），
而唯讀 API（`dashboard/api.py`）跑在 **Windows**。Windows 側 repo 根本沒有那個檔，
於是 `jobs_db_exists()` 一路回 False——**Jobs 頁、成本頁、`/api/status-counts`、
新鮮度燈號在 Windows 觀測面本來就一直是空的／灰的**。

直接經 UNC 讀（`\\\\wsl.localhost\\Ubuntu\\...`）不可行：檔案看得到，但 worker 持有
WAL 鎖，**SQLite 的 WAL 索引（-shm）需要跨程序共享記憶體，SMB 拿不到**，
`mode=ro` 開啟實測回 `database is locked`。這是拓撲限制，不是設定問題。

所以走「定期快照」：**WSL 側**（能正常拿到 WAL 鎖的那一側）主動把 db 推到
Windows 可讀的落點，Windows 側只讀那份快照。

## 為什麼不用檔案複製（cp / shutil.copy2）

既有前例 `hermes/session_adapter/adapter.py::_make_snapshot()` 就是檔案複製
（db + -wal + -shm，複製前後比對 fingerprint，不一致就重試三次、全失敗 fail-loud）。
STATUS 記載它**忙時段會撞 WAL、三次重試放棄**——因為它跟寫入端在賽跑：
複製三個檔不是原子操作，來源在複製期間被 checkpoint 就撕裂，只能整組作廢重來。

本檔改用 **SQLite 官方線上備份 API**（`sqlite3.Connection.backup()`）：

- 它在來源上開讀交易、按頁複製，**寫入者可以照常寫**；來源在備份期間被改動時，
  SQLite 自己重啟備份（不是回傳撕裂的檔），一致性由引擎保證而不是靠我們賽跑贏。
- 來源全程唯讀：`file:...?mode=ro` URI ＋ `PRAGMA query_only=ON` 雙保險。
- 因此**沿用 adapter 的三次重試策略、但語意不同**：那邊重試是為了「贏得比賽」，
  這邊重試只是為了扛偶發的 `SQLITE_BUSY`（rss 每 30 分鐘尖峰時可能撞上）。
  重試間隔線性遞增，全失敗 → fail-loud（exit 1），**不留半份舊快照假裝成功**。

## 三個容易踩到、這裡有處理的坑

1. **backup 出來的檔會繼承 WAL 模式**：header 說 WAL、卻沒有 -wal/-shm 側檔時，
   `mode=ro` 開啟會需要建立 -shm 而失敗（唯讀開不了）。所以備份完成後在**暫存檔**
   上跑 `PRAGMA journal_mode=DELETE`，落地的快照是純 rollback-journal 檔，
   Windows 側 `mode=ro` 開得起來、且不會生出側檔。
2. **不要用 sqlite 直接寫 `/mnt/c`**：DrvFs 的鎖語意跟 ext4 不同。這裡的 sqlite
   寫入全部發生在 **WSL 本地暫存目錄**，落地到 `/mnt/c` 只有「一次純檔案複製 ＋
   `os.replace()` 原子換檔」——那份暫存快照沒有任何寫入者，複製它是安全的。
3. **半寫入的快照會被 Windows 讀到**：故一律先寫 `<name>.tmp` 再 `os.replace()`，
   manifest 亦同。讀端永遠看到完整的舊版或完整的新版。

## 落點（為什麼是 %LOCALAPPDATA%\\AgentOS\\jobs-snapshot）

- **不放 `hermes/jobs.db`**：那個路徑的語意是「runtime db」，放快照會讓人以為
  Windows 側有 runtime（正是這次誤判的成因）。
- **不放 repo 內的 gitignored 路徑**：4MB 的二進位檔在工作樹裡每 30 分鐘變動一次，
  會污染 `git status`、`repo_guard` 的 dirty 計數，也可能被 `sync_to_wsl.sh` 之類的
  同步流程波及；快照是 runtime 產物，不該住在原始碼樹裡。
- **`%LOCALAPPDATA%\\AgentOS\\` 已是本專案的 repo 外 runtime 產物根**
  （`repo-guard/` 就在那裡），沿用同一個根，讀端定位邏輯與 `data_repo_guard.py` 同款。

## 唯讀邊界

對來源 `jobs.db` **只讀不寫**（mode=ro + query_only，全檔無任何
INSERT/UPDATE/DELETE/CREATE 針對來源）；不建檔、不 migrate、不 checkpoint、
不重啟任何服務、不觸發任何 job、不送任何通知。唯一的寫入對象是落點目錄裡的
`jobs.snapshot.db` 與 `_latest.json`。

## 用法（WSL 側）

    ~/dev/ClaudeCodeOSWin/.venv/bin/python3 scripts/jobs_db_snapshot.py \\
        [--source PATH] [--dest-dir PATH] [--json] [--max-attempts N]

環境變數 `AGENTOS_JOBS_SNAPSHOT_DEST` 可覆寫落點（測試與非預設部署用）。

## Exit codes（比照 jobs_freshness_watchdog.py 慣例）

    0 = 快照完成並通過驗證（來源唯讀）
    1 = 失敗（來源不存在／備份失敗／驗證不過／落點寫不進去）——fail-loud，
        停在原地印原因，**不寫 manifest**（讀端因此只會看到上一份，
        並且會誠實顯示它有多舊，而不是被騙成「剛更新過」）
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = ROOT / "hermes" / "jobs.db"

# 機器特定路徑刻意寫成凍結常數（單人單機；同 dispatch_domain.py 的
# WINDOWS_HERMES_INTEROP_PATH 慣例）：全檔唯一出處，可被檢查腳本斷言。
# 對應 Windows 端的 %LOCALAPPDATA%\AgentOS\jobs-snapshot。
DEFAULT_DEST_DIR = Path("/mnt/c/Users/razer/AppData/Local/AgentOS/jobs-snapshot")
DEST_ENV_VAR = "AGENTOS_JOBS_SNAPSHOT_DEST"

SNAPSHOT_NAME = "jobs.snapshot.db"
MANIFEST_NAME = "_latest.json"
MANIFEST_SCHEMA = "agentos.jobs-snapshot/1"

# 重試策略沿用 hermes/session_adapter/adapter.py 的形狀（三次、線性遞增間隔、
# 全失敗 fail-loud）。差別見檔頭：這裡重試只為扛偶發 SQLITE_BUSY。
MAX_ATTEMPTS = 3
RETRY_DELAY_S = 2.0
BUSY_TIMEOUT_S = 30.0


class SnapshotError(RuntimeError):
    """快照失敗——fail-loud。絕不降級成「寫了一份可能撕裂的檔」。"""


# ---------- 來源（唯讀） ----------

def _open_source_ro(path: Path) -> sqlite3.Connection:
    """雙保險唯讀：mode=ro URI（driver 層拒絕寫）＋ PRAGMA query_only。"""
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True,
                           timeout=BUSY_TIMEOUT_S)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _jobs_stats(conn: sqlite3.Connection) -> dict:
    """快照內容的可驗證摘要（筆數／各狀態／最後時間戳）——讀端拿來對帳。"""
    total = conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"]
    status_counts = {
        row["status"]: row["n"] for row in
        conn.execute("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status")
    }
    row = conn.execute(
        "SELECT MAX(created_at) AS max_created, "
        "MAX(CASE WHEN status='completed' "
        "         THEN COALESCE(completed_at, updated_at) END) AS last_completed "
        "FROM jobs").fetchone()
    return {
        "jobs_count": total,
        "status_counts": status_counts,
        "max_created_at": row["max_created"],
        "last_completed_at": row["last_completed"],
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ---------- 備份（線上、來源可同時被寫） ----------

def _backup_once(source: Path, dest: Path) -> None:
    """單次線上備份：來源 mode=ro，目的地是 WSL 本地暫存檔。

    備份完成後把暫存檔轉成 rollback-journal 模式（見檔頭坑 1），
    否則 Windows 側 `mode=ro` 開不起來。
    """
    src = _open_source_ro(source)
    try:
        dst = sqlite3.connect(str(dest), timeout=BUSY_TIMEOUT_S)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    fix = sqlite3.connect(str(dest), timeout=BUSY_TIMEOUT_S)
    try:
        fix.execute("PRAGMA journal_mode=DELETE")
        fix.commit()
    finally:
        fix.close()
    for suffix in ("-wal", "-shm"):
        side = Path(str(dest) + suffix)
        if side.exists():
            side.unlink()


def _verify(snapshot: Path, source_stats: dict) -> dict:
    """對暫存快照做三關驗證，全部經 `mode=ro` 開啟（就是讀端會用的方式）。

    1. 唯讀開得起來（若 journal 模式沒轉好，這一關就會擋下）
    2. `PRAGMA quick_check` 回 ok
    3. 筆數不少於備份**開始前**的來源筆數（jobs 只會新增／更新，不會被刪）
    """
    conn = _open_source_ro(snapshot)
    try:
        checks = [str(r[0]) for r in conn.execute("PRAGMA quick_check")]
        if checks != ["ok"]:
            raise SnapshotError("快照 quick_check 未通過：" + "; ".join(checks[:5]))
        stats = _jobs_stats(conn)
    except sqlite3.Error as exc:
        raise SnapshotError(f"快照驗證失敗（唯讀查詢）：{exc}") from exc
    finally:
        conn.close()
    if stats["jobs_count"] < source_stats["jobs_count"]:
        raise SnapshotError(
            f"快照筆數 {stats['jobs_count']} 少於來源備份前的 "
            f"{source_stats['jobs_count']}——內容不完整，不落地")
    return stats


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """先寫 .tmp 再 os.replace()：讀端永遠看到完整的舊版或完整的新版。"""
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def take_snapshot(*, source: Path, dest_dir: Path, now: datetime | None = None,
                  max_attempts: int = MAX_ATTEMPTS,
                  retry_delay_s: float = RETRY_DELAY_S) -> dict:
    """執行一次快照，回傳 manifest dict。失敗一律 raise SnapshotError。"""
    now = now or datetime.now(timezone.utc)
    started = time.monotonic()
    if not source.is_file():
        raise SnapshotError(
            f"來源 jobs.db 不存在：{source}——這支腳本要在**有 runtime db 的那一側**"
            "（WSL 部署複本）執行")
    try:
        src = _open_source_ro(source)
        try:
            source_stats = _jobs_stats(src)
        finally:
            src.close()
    except sqlite3.Error as exc:
        raise SnapshotError(f"來源無法唯讀開啟／查詢（{source}）：{exc}") from exc

    failures: list[str] = []
    work_dir = Path(tempfile.mkdtemp(prefix="agentos_jobs_snapshot_"))
    try:
        staged = work_dir / SNAPSHOT_NAME
        stats: dict | None = None
        attempts_used = 0
        for attempt in range(1, max_attempts + 1):
            attempts_used = attempt
            if attempt > 1 and retry_delay_s > 0:
                time.sleep(retry_delay_s * (attempt - 1))
            try:
                if staged.exists():
                    staged.unlink()
                _backup_once(source, staged)
                stats = _verify(staged, source_stats)
                break
            except (sqlite3.Error, SnapshotError, OSError) as exc:
                failures.append(f"attempt {attempt}/{max_attempts}: {exc}")
                stats = None
        if stats is None:
            raise SnapshotError(
                "線上備份連續失敗，未寫出任何快照（上一份快照與其 manifest 原封不動，"
                "讀端會誠實顯示資料變舊）：" + "；".join(failures))

        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise SnapshotError(f"落點目錄建立失敗（{dest_dir}）：{exc}") from exc

        final_db = dest_dir / SNAPSHOT_NAME
        tmp_db = dest_dir / (SNAPSHOT_NAME + ".tmp")
        try:
            shutil.copyfile(staged, tmp_db)
            os.replace(tmp_db, final_db)
        except OSError as exc:
            with contextlib.suppress(OSError):
                tmp_db.unlink()
            raise SnapshotError(f"快照落地失敗（{final_db}）：{exc}") from exc

        manifest = {
            "schema": MANIFEST_SCHEMA,
            "captured_at": now.astimezone(timezone.utc).isoformat(timespec="seconds"),
            "source_db": str(source),
            "source_bytes": source.stat().st_size,
            "snapshot_file": SNAPSHOT_NAME,
            "snapshot_bytes": final_db.stat().st_size,
            "sha256": _sha256(staged),
            "attempts": attempts_used,
            "retries_note": failures or None,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "producer": "scripts/jobs_db_snapshot.py",
            **stats,
        }
        try:
            _atomic_write_bytes(
                dest_dir / MANIFEST_NAME,
                json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
        except OSError as exc:
            raise SnapshotError(f"manifest 寫出失敗（{dest_dir}）：{exc}") from exc
        return manifest
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _default_dest_dir() -> Path:
    return Path(os.environ.get(DEST_ENV_VAR) or DEFAULT_DEST_DIR)


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        description="把 WSL runtime hermes/jobs.db 以 SQLite 線上備份 API 快照到 "
                    "Windows 可讀落點（來源全程唯讀；不重啟服務、不觸發 job）。",
        epilog="exit：0 成功｜1 失敗（fail-loud，不留半份快照、不更新 manifest）")
    parser.add_argument("--source", default=str(DEFAULT_SOURCE),
                        help=f"來源 jobs.db（預設 {DEFAULT_SOURCE}）")
    parser.add_argument("--dest-dir", default=None,
                        help=f"落點目錄（預設 ${DEST_ENV_VAR} 或 {DEFAULT_DEST_DIR}）")
    parser.add_argument("--max-attempts", type=int, default=MAX_ATTEMPTS,
                        help=f"線上備份重試次數上限（預設 {MAX_ATTEMPTS}）")
    parser.add_argument("--json", action="store_true", help="機器可讀輸出")
    args = parser.parse_args(argv)

    dest_dir = Path(args.dest_dir) if args.dest_dir else _default_dest_dir()
    try:
        manifest = take_snapshot(source=Path(args.source), dest_dir=dest_dir,
                                 max_attempts=max(1, args.max_attempts))
    except SnapshotError as exc:
        print(f"快照失敗：{exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
    else:
        print(f"來源：{manifest['source_db']}（唯讀）")
        print(f"落點：{dest_dir / SNAPSHOT_NAME}")
        print(f"時間（UTC）：{manifest['captured_at']}"
              f"　筆數：{manifest['jobs_count']}"
              f"　大小：{manifest['snapshot_bytes']} bytes"
              f"　嘗試：{manifest['attempts']} 次"
              f"　耗時：{manifest['duration_ms']} ms")
        print(f"各狀態：{manifest['status_counts']}")
        if manifest["retries_note"]:
            print("重試紀錄：" + "；".join(manifest["retries_note"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
