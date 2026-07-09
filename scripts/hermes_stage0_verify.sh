#!/usr/bin/env bash
# Stage 0 — 共用儲存驗證（Ubuntu / WSL 端執行）
#
# Phase A（隨時可跑）：symlink 佈局、無第二份 state.db、immutable 讀取。
# Phase B（--window，需 Windows Hermes 完全停止）：一般唯讀開啟、
#         Hermes CLI 實際讀取、建立測試 session（寫入同一份 state.db）。
#
# 用法：
#   bash scripts/hermes_stage0_verify.sh            # Phase A
#   bash scripts/hermes_stage0_verify.sh --window   # Phase A + B
#
# Phase B 前，在 Windows 端先執行：
#   hermes gateway stop
#   hermes dashboard --stop        # 若 dashboard 在跑
#   （必要時關閉 Hermes Desktop 應用程式）
# 驗證完在 Windows 端 `hermes gateway start`（或原本的啟動方式）恢復。
set -euo pipefail

UBUNTU_HOME="${HERMES_HOME_UBUNTU:-$HOME/.hermes}"
WIN_HERMES="${WIN_HERMES:-/mnt/c/Users/razer/AppData/Local/hermes}"
PASS=0; FAIL=0
ok()   { echo "PASS: $*"; PASS=$((PASS+1)); }
bad()  { echo "FAIL: $*"; FAIL=$((FAIL+1)); }

echo "===== Phase A: layout & read-only ====="
for f in state.db sessions skills memories; do
    if [ -L "$UBUNTU_HOME/$f" ] && [ "$(readlink "$UBUNTU_HOME/$f")" = "$WIN_HERMES/$f" ]; then
        ok "$f is a symlink to $WIN_HERMES/$f"
    else
        bad "$f is not the expected symlink"
    fi
done
# 不可有第二份 state.db（本機真檔或本機 sidecar）
for f in state.db state.db-wal state.db-shm; do
    if [ -e "$UBUNTU_HOME/$f" ] && [ ! -L "$UBUNTU_HOME/$f" ]; then
        bad "local (non-symlink) $f exists — second db!"
    fi
done
[ -L "$UBUNTU_HOME/state.db" ] && ok "no local second state.db"

python3 - <<EOF && ok "immutable read sees shared data" || bad "immutable read failed"
import sqlite3
c = sqlite3.connect("file:$UBUNTU_HOME/state.db?mode=ro&immutable=1", uri=True)
print("  sessions =", c.execute("SELECT COUNT(*) FROM sessions").fetchone()[0])
print("  messages =", c.execute("SELECT COUNT(*) FROM messages").fetchone()[0])
c.close()
EOF

if [ "${1:-}" = "--window" ]; then
    echo "===== Phase B: maintenance window (Windows Hermes must be stopped) ====="
    if python3 - <<EOF
import sqlite3
try:
    c = sqlite3.connect("file:$UBUNTU_HOME/state.db?mode=ro", uri=True, timeout=5)
    print("  normal RO open OK, sessions =", c.execute("SELECT COUNT(*) FROM sessions").fetchone()[0])
    c.close()
except Exception as e:
    print("  normal RO open failed:", e)
    raise SystemExit(1)
EOF
    then ok "normal (locking) read-only open works — window is clear"
    else bad "state.db still held by a Windows process — stop gateway/Desktop/dashboard first"; echo "aborting Phase B"; exit 1
    fi

    echo "-- Hermes CLI reads shared sessions --"
    if hermes sessions stats; then ok "hermes sessions stats"; else bad "hermes sessions stats"; fi
    hermes sessions list 2>&1 | head -5 || true

    echo "-- create test session (writes to the shared state.db) --"
    if hermes -z "Stage 0 shared-storage verification test — reply with exactly: STAGE0-OK"; then
        ok "test session created from Ubuntu"
    else
        bad "test session creation failed"
    fi
    hermes sessions list 2>&1 | head -3 || true

    python3 - <<EOF && ok "integrity_check ok after write" || bad "integrity_check failed"
import sqlite3
c = sqlite3.connect("file:$UBUNTU_HOME/state.db?mode=ro", uri=True, timeout=10)
r = c.execute("PRAGMA integrity_check").fetchone()[0]
print("  integrity:", r)
c.close()
assert r == "ok"
EOF
    echo "-- now restart Windows Hermes, then on Windows run: --"
    echo "   hermes sessions list                                    # CLI sees test session"
    echo "   py -3.11 hermes/session_adapter/adapter.py list         # adapter sees test session"
    echo "   （Dashboard/Desktop 開啟 sessions 頁面確認測試 session）"
fi

echo "===== result: PASS=$PASS FAIL=$FAIL ====="
[ "$FAIL" -eq 0 ]
