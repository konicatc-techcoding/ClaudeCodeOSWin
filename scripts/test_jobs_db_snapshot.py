#!/usr/bin/env python3
"""scripts/test_jobs_db_snapshot.py — jobs.db 快照產出端測試。

重點命題（每一條都對應一個「做錯就會出事」的地方）：

1. **來源唯讀**：快照跑完，來源檔的 mtime／size／內容雜湊完全不變；
   且來源連線帶 `mode=ro` + `PRAGMA query_only=ON`（靜態鎖定）。
2. **併發寫入下仍能成功**：另一條連線持續寫入來源時取快照仍成功
   ——這就是不用檔案複製、改用 SQLite 線上備份 API 的理由。
3. **落地的快照必須能被「唯讀」開啟**：backup 產物會繼承 WAL 模式，
   若不轉成 rollback journal，Windows 側 `mode=ro` 會開不起來（這是最容易
   漏掉、且只在真實部署才炸的坑）。
4. **失敗不留半份**：來源不存在／驗證不過時 exit 1，且**不寫 manifest**
   ——上一份快照與其時間戳原封不動，讀端會誠實顯示「資料變舊」而不是被騙。
5. **原子換檔**：快照與 manifest 都先寫 .tmp 再 replace，且事後無殘留 .tmp。

執行：.venv/Scripts/python.exe scripts/test_jobs_db_snapshot.py
"""
import hashlib
import json
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import jobs_db_snapshot as snap  # noqa: E402

SCHEMA = """
CREATE TABLE jobs (
    id TEXT PRIMARY KEY, source TEXT NOT NULL, status TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, completed_at TEXT
);
"""


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class SnapshotTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.source = self.tmp / "jobs.db"
        self.dest = self.tmp / "dest"
        conn = sqlite3.connect(self.source)
        conn.execute("PRAGMA journal_mode=WAL")  # 與真實 runtime 一致
        conn.executescript(SCHEMA)
        self._insert(conn, 20)
        conn.commit()
        conn.close()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    @staticmethod
    def _insert(conn, n, prefix="job"):
        now = datetime.now(timezone.utc)
        for i in range(n):
            ts = (now - timedelta(minutes=i)).isoformat()
            conn.execute(
                "INSERT INTO jobs (id, source, status, created_at, updated_at, "
                "completed_at) VALUES (?,?,?,?,?,?)",
                (f"{prefix}-{i}", "rss", "completed", ts, ts, ts))

    def take(self, **kwargs):
        return snap.take_snapshot(source=self.source, dest_dir=self.dest, **kwargs)


class SourceIsUntouchedTests(SnapshotTestCase):
    def test_source_bytes_unchanged(self):
        before_sha, before_stat = _sha(self.source), self.source.stat()
        self.take()
        self.assertEqual(_sha(self.source), before_sha, "來源內容被動到了")
        self.assertEqual(self.source.stat().st_size, before_stat.st_size)

    def test_source_row_count_unchanged(self):
        conn = sqlite3.connect(self.source)
        before = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        conn.close()
        self.take()
        conn = sqlite3.connect(self.source)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], before)
        conn.close()

    def test_source_connection_is_double_guarded(self):
        text = (SCRIPTS_DIR / "jobs_db_snapshot.py").read_text(encoding="utf-8")
        self.assertIn("mode=ro", text)
        self.assertIn("PRAGMA query_only=ON", text)


class SnapshotContentTests(SnapshotTestCase):
    def test_snapshot_matches_source_row_count(self):
        manifest = self.take()
        self.assertEqual(manifest["jobs_count"], 20)
        db = self.dest / snap.SNAPSHOT_NAME
        conn = sqlite3.connect(db)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 20)
        conn.close()

    def test_snapshot_opens_read_only(self):
        """★ backup 產物繼承 WAL 模式的坑：不轉 journal 模式，讀端就開不起來。"""
        self.take()
        db = self.dest / snap.SNAPSHOT_NAME
        conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 20)
            self.assertEqual(
                conn.execute("PRAGMA journal_mode").fetchone()[0].lower(), "delete")
        finally:
            conn.close()
        for suffix in ("-wal", "-shm"):
            self.assertFalse(Path(str(db) + suffix).exists(), f"落點殘留 {suffix} 側檔")

    def test_manifest_fields(self):
        manifest = self.take()
        data = json.loads((self.dest / snap.MANIFEST_NAME).read_text(encoding="utf-8"))
        self.assertEqual(data["schema"], snap.MANIFEST_SCHEMA)
        self.assertEqual(data["jobs_count"], 20)
        self.assertEqual(data["status_counts"], {"completed": 20})
        self.assertEqual(data["sha256"], manifest["sha256"])
        parsed = datetime.fromisoformat(data["captured_at"])
        self.assertIsNotNone(parsed.tzinfo, "captured_at 必須帶時區（讀端要算年齡）")

    def test_no_tmp_files_left_behind(self):
        self.take()
        self.assertEqual(sorted(p.name for p in self.dest.iterdir()),
                         [snap.MANIFEST_NAME, snap.SNAPSHOT_NAME])

    def test_rerun_is_idempotent_and_updates_timestamp(self):
        first = self.take()
        time.sleep(0.01)
        second = self.take()
        self.assertEqual(second["jobs_count"], first["jobs_count"])
        self.assertGreaterEqual(second["captured_at"], first["captured_at"])


class ConcurrentWriterTests(SnapshotTestCase):
    """★ worker 正在寫的時候也要拿得到快照——這就是不用檔案複製的理由。"""

    def test_snapshot_succeeds_while_source_is_being_written(self):
        stop = threading.Event()
        errors: list[Exception] = []

        def writer():
            conn = sqlite3.connect(self.source, timeout=30)
            try:
                i = 0
                while not stop.is_set():
                    i += 1
                    self._insert(conn, 1, prefix=f"live-{i}")
                    conn.commit()
                    time.sleep(0.002)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
            finally:
                conn.close()

        thread = threading.Thread(target=writer, daemon=True)
        thread.start()
        try:
            time.sleep(0.05)
            manifest = self.take(retry_delay_s=0.05)
        finally:
            stop.set()
            thread.join(timeout=10)
        self.assertEqual(errors, [], "快照期間寫入端不應失敗（來源不被鎖死）")
        self.assertGreaterEqual(manifest["jobs_count"], 20)
        db = self.dest / snap.SNAPSHOT_NAME
        conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
        try:
            conn.execute("PRAGMA quick_check").fetchone()
        finally:
            conn.close()


class FailLoudTests(SnapshotTestCase):
    def test_missing_source_fails_and_writes_nothing(self):
        with self.assertRaises(snap.SnapshotError):
            snap.take_snapshot(source=self.tmp / "nope.db", dest_dir=self.dest)
        self.assertFalse(self.dest.exists())

    def test_failure_keeps_previous_snapshot_and_manifest(self):
        """失敗時**不動**上一份——讀端因此會顯示「資料變舊」而不是假裝剛更新。"""
        self.take()
        db_sha = _sha(self.dest / snap.SNAPSHOT_NAME)
        manifest_before = (self.dest / snap.MANIFEST_NAME).read_text(encoding="utf-8")
        self.source.unlink()
        with self.assertRaises(snap.SnapshotError):
            self.take()
        self.assertEqual(_sha(self.dest / snap.SNAPSHOT_NAME), db_sha)
        self.assertEqual((self.dest / snap.MANIFEST_NAME).read_text(encoding="utf-8"),
                         manifest_before)

    def test_cli_exit_codes(self):
        self.assertEqual(snap.main(["--source", str(self.source),
                                    "--dest-dir", str(self.dest), "--json"]), 0)
        self.assertEqual(snap.main(["--source", str(self.tmp / "nope.db"),
                                    "--dest-dir", str(self.dest)]), 1)

    def test_verify_rejects_truncated_snapshot(self):
        source_stats = {"jobs_count": 20}
        bad = self.tmp / "bad.db"
        bad.write_bytes(b"not a database")
        with self.assertRaises(snap.SnapshotError):
            snap._verify(bad, source_stats)

    def test_verify_rejects_row_count_regression(self):
        staged = self.tmp / "staged.db"
        snap._backup_once(self.source, staged)
        with self.assertRaises(snap.SnapshotError):
            snap._verify(staged, {"jobs_count": 999})


class NoSideEffectsStaticTests(unittest.TestCase):
    """靜態鎖定：這支腳本不觸發 job、不送通知、不碰服務。"""

    def test_no_service_or_notification_calls(self):
        text = (SCRIPTS_DIR / "jobs_db_snapshot.py").read_text(encoding="utf-8")
        code = "\n".join(line for line in text.splitlines()
                         if not line.strip().startswith("#"))
        for token in ("subprocess", "systemctl", "hermes send", "enqueue",
                      "invoke_cos", "claude -p"):
            self.assertNotIn(token, code, f"出現不該有的呼叫：{token}")

    def test_frozen_dest_constant(self):
        self.assertEqual(
            str(snap.DEFAULT_DEST_DIR).replace("\\", "/"),
            "/mnt/c/Users/razer/AppData/Local/AgentOS/jobs-snapshot")


if __name__ == "__main__":
    unittest.main(verbosity=1)
