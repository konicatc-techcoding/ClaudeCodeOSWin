#!/usr/bin/env bash
# Stage 0 — Hermes shared storage bootstrap（Ubuntu / WSL 端執行）
#
# 讓 Ubuntu Hermes 共用 Windows Hermes 的 state.db、sessions/、skills/、
# memories/（Windows 為唯一 Source of Truth），Ubuntu 專屬的 config.yaml、
# .env、hermes-agent 保持獨立。
#
# 用法（在 WSL Ubuntu 內，或從 Windows: wsl.exe -d Ubuntu -e bash -lc "..."）：
#   bash scripts/hermes_stage0_bootstrap.sh            # 安裝（idempotent）
#   bash scripts/hermes_stage0_bootstrap.sh --rollback # 完整還原到執行前狀態
#
# 先決條件：先在 Windows 端跑 `py -3.11 scripts/hermes_stage0_backup.py` 備份。
#
# 已知限制（實測，見 docs/hermes-shared-storage-bootstrap.md）：
# Windows Hermes（gateway / Desktop / dashboard）開著 state.db 時，Ubuntu 端
# 任何 sqlite 存取（含唯讀）都會得到 disk I/O error——Hermes CLI 會顯示
# "Could not open session database"，這是預期的安全降級，不是資料損壞。
set -euo pipefail

UBUNTU_HOME="${HERMES_HOME_UBUNTU:-$HOME/.hermes}"
WIN_HERMES="${WIN_HERMES:-/mnt/c/Users/razer/AppData/Local/hermes}"
BAK=".pre-stage0"
SHARED_DIRS=(sessions skills memories)

fail() { echo "ABORT: $*" >&2; exit 1; }

rollback() {
    echo "== Stage 0 rollback =="
    cd "$UBUNTU_HOME"
    # state.db：原始狀態是「不存在」，只移除 symlink，絕不刪真檔
    if [ -L state.db ]; then rm state.db; echo "removed symlink state.db"; fi
    [ -e state.db ] && fail "state.db is a real file — not created by this script; refusing to touch it"
    for d in "${SHARED_DIRS[@]}"; do
        if [ -L "$d" ]; then rm "$d"; echo "removed symlink $d"; fi
        if [ -d "$d$BAK" ] && [ ! -e "$d" ]; then
            mv "$d$BAK" "$d"; echo "restored $d from $d$BAK"
        fi
        [ -d "$d" ] || { mkdir -p "$d"; echo "recreated empty $d"; }
    done
    echo "rollback done. current state:"
    ls -la "$UBUNTU_HOME" | grep -E 'state\.db|sessions|skills|memories' || true
    exit 0
}

[ "${1:-}" = "--rollback" ] && rollback

echo "== Stage 0 bootstrap =="
# ── 先決條件檢查 ──────────────────────────────────────────────
[ -d "$UBUNTU_HOME" ]        || fail "$UBUNTU_HOME not found (run hermes setup first)"
[ -d "$WIN_HERMES" ]         || fail "$WIN_HERMES not mounted (is /mnt/c available?)"
[ -f "$WIN_HERMES/state.db" ] || fail "$WIN_HERMES/state.db not found"
for d in "${SHARED_DIRS[@]}"; do
    [ -d "$WIN_HERMES/$d" ] || fail "$WIN_HERMES/$d not found"
done
# 禁止第二份 state.db：Ubuntu 端若已有「真的」state.db（非 symlink）就停
if [ -e "$UBUNTU_HOME/state.db" ] && [ ! -L "$UBUNTU_HOME/state.db" ]; then
    fail "$UBUNTU_HOME/state.db is a real file — a second state.db must never exist. Resolve manually."
fi

cd "$UBUNTU_HOME"

# ── state.db symlink ─────────────────────────────────────────
# SQLite 會解析 symlink，-wal/-shm sidecar 會建立在 Windows 端真實路徑旁，
# 與 Windows Hermes 共用同一組 WAL 檔——不會產生第二份 db。
if [ -L state.db ]; then
    [ "$(readlink state.db)" = "$WIN_HERMES/state.db" ] || fail "state.db symlink points elsewhere: $(readlink state.db)"
    echo "state.db symlink already correct"
else
    ln -s "$WIN_HERMES/state.db" state.db
    echo "linked state.db -> $WIN_HERMES/state.db"
fi

# ── sessions/ skills/ memories/ symlinks ─────────────────────
for d in "${SHARED_DIRS[@]}"; do
    if [ -L "$d" ]; then
        [ "$(readlink "$d")" = "$WIN_HERMES/$d" ] || fail "$d symlink points elsewhere: $(readlink "$d")"
        echo "$d symlink already correct"
        continue
    fi
    if [ -d "$d" ]; then
        [ -e "$d$BAK" ] && fail "$d$BAK already exists — refusing to overwrite previous rollback data"
        mv "$d" "$d$BAK"
        echo "kept original $d as $d$BAK"
    fi
    ln -s "$WIN_HERMES/$d" "$d"
    echo "linked $d -> $WIN_HERMES/$d"
done

# ── 讀取驗證（不碰 sqlite 鎖，用 immutable 快照讀）──────────────
echo "== verify =="
python3 - <<EOF
import sqlite3
conn = sqlite3.connect("file:$UBUNTU_HOME/state.db?mode=ro&immutable=1", uri=True)
n = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
m = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
conn.close()
print(f"shared state.db visible from Ubuntu (immutable read): sessions={n} messages={m}")
EOF
ls "$UBUNTU_HOME/skills"   > /dev/null && echo "skills/ readable"
ls "$UBUNTU_HOME/sessions" > /dev/null && echo "sessions/ readable"
ls "$UBUNTU_HOME/memories" > /dev/null && echo "memories/ readable"
echo "bootstrap done."
echo "NOTE: full read-write use of state.db from Ubuntu requires Windows Hermes"
echo "      (gateway/Desktop/dashboard) to be stopped — see docs/hermes-shared-storage-bootstrap.md"
