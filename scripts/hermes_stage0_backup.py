"""Stage 0 — Hermes state.db 備份（Windows 端執行）。

用 SQLite backup API 對 live（WAL）state.db 做一致性快照，存到
%LOCALAPPDATA%\\hermes\\backup_stage0\\state.db.stage0.bak，
並輸出 baseline counts / sha256，供事後比對（DoD 6）。

用法（Windows）：
    py -3.11 scripts/hermes_stage0_backup.py

安全性：來源一律以 mode=ro 開啟；backup API 對 live WAL db 是官方支援的
一致性快照方式，不需要停止 Hermes。
"""

import hashlib
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    hermes = Path(os.environ["LOCALAPPDATA"]) / "hermes"
    db = hermes / "state.db"
    if not db.exists():
        print(f"ERROR: {db} not found", file=sys.stderr)
        return 1

    backup_dir = hermes / "backup_stage0"
    backup_dir.mkdir(exist_ok=True)
    backup = backup_dir / "state.db.stage0.bak"

    src_ro = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    sessions = src_ro.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    messages = src_ro.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    jmode = src_ro.execute("PRAGMA journal_mode").fetchone()[0]
    print(f"baseline @ {datetime.now(timezone.utc).isoformat()}")
    print(f"baseline: sessions={sessions} messages={messages} journal_mode={jmode}")
    print(f"state.db size={db.stat().st_size}")
    src_ro.close()

    if backup.exists():
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup.rename(backup_dir / f"state.db.stage0.bak.{stamp}")

    src = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    dst = sqlite3.connect(str(backup))
    src.backup(dst)
    dst.close()
    src.close()
    # backup API 建立的 -wal/-shm 殘檔清掉（連線已關閉，安全）
    for suf in ("-wal", "-shm"):
        side = Path(str(backup) + suf)
        if side.exists():
            side.unlink()

    bk = sqlite3.connect(f"file:{backup.as_posix()}?mode=ro", uri=True)
    b_sessions = bk.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    b_messages = bk.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    integrity = bk.execute("PRAGMA integrity_check").fetchone()[0]
    bk.close()

    print(f"backup: {backup}")
    print(f"backup: sessions={b_sessions} messages={b_messages} integrity={integrity}")
    print(f"backup size={backup.stat().st_size}")
    print(f"backup sha256={sha256(backup)}")

    ok = (b_sessions, b_messages) == (sessions, messages) and integrity == "ok"
    print("RESULT:", "OK" if ok else "MISMATCH")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
