#!/usr/bin/env bash
# sync_to_wsl.sh — Windows repo ↔ WSL 部署複本同步（ClaudeCodeOSWin）
#
# 方案文件：docs/deployment-sync-plan.md（分類表、選型理由、風險、rollback 都在那裡）
#
# 執行環境：WSL2 Ubuntu 內執行（需要存取 /mnt/c）。從 Windows 呼叫：
#   wsl.exe -d Ubuntu -e bash /mnt/c/Users/razer/dev/ClaudeCodeOSWin/scripts/sync_to_wsl.sh --dry-run
#   wsl.exe -d Ubuntu -e bash /mnt/c/Users/razer/dev/ClaudeCodeOSWin/scripts/sync_to_wsl.sh --apply
#
# 同步模型（詳見方案文件）：
#   Phase 0  前置檢查（路徑、rsync、服務狀態、衝突掃描）
#   Phase 1  inbox 反向合併：WSL → Windows，只新增、絕不覆蓋/刪除（inbox 只增不改）
#   Phase 2  inbox 清理：Windows 端已 consolidation 完（進 .processed/.failed）的檔案，
#            從 WSL inbox 頂層移除，避免下次被當成新件再合併回去
#   Phase 3  正向同步：Windows → WSL 單向（Windows 是開發正本），帶排除清單 + --delete
#
# 冪等：重複執行結果相同。--dry-run 不改任何檔案。
set -euo pipefail

WIN_ROOT="${SYNC_WIN_ROOT:-/mnt/c/Users/razer/dev/ClaudeCodeOSWin}"
WSL_ROOT="${SYNC_WSL_ROOT:-$HOME/dev/ClaudeCodeOSWin}"
BACKUP_DIR="${SYNC_BACKUP_DIR:-$HOME/backups}"

usage() {
  cat <<'EOF'
用法: sync_to_wsl.sh <--dry-run | --apply> [選項]

模式（必選其一）:
  --dry-run        只列出將發生的動作，不改任何檔案
  --apply          真正執行同步（會先在 ~/backups 產生 WSL 側備份 tarball）

選項:
  --force          衝突掃描發現「WSL 側較新且內容不同」的檔案時仍繼續（預設中止）
  --allow-running  hermes-worker / hermes-telegram 運行中仍執行 apply（預設中止；
                   建議先 systemctl --user stop hermes-worker hermes-telegram）
  --no-backup      apply 時跳過備份（不建議）
  -h, --help       顯示說明
EOF
}

MODE="" FORCE=0 ALLOW_RUNNING=0 NO_BACKUP=0
for arg in "$@"; do
  case "$arg" in
    --dry-run)       MODE="dry-run" ;;
    --apply)         MODE="apply" ;;
    --force)         FORCE=1 ;;
    --allow-running) ALLOW_RUNNING=1 ;;
    --no-backup)     NO_BACKUP=1 ;;
    -h|--help)       usage; exit 0 ;;
    *) echo "未知參數: $arg" >&2; usage; exit 2 ;;
  esac
done
if [ -z "$MODE" ]; then usage; exit 2; fi

DRY=()
if [ "$MODE" = "dry-run" ]; then DRY=(--dry-run); fi

log() { printf '%s\n' "$*"; }
hr()  { printf -- '---------------------------------------------------------------\n'; }

# ---------------------------------------------------------------
# 正向同步（Windows → WSL）的 rsync filter 規則。
# rsync 規則順序敏感：include 要放在對應的 exclude 前面。
# 每一條的理由見 docs/deployment-sync-plan.md 的內容分類表。
# ---------------------------------------------------------------
RSYNC_FILTERS=(
  # memory/inbox 頂層不走正向同步（由 Phase 1/2 專責處理），
  # 但 .processed/.failed 歸檔要下發，讓兩側 inbox 生命週期一致
  --include='/memory/inbox/.processed/***'
  --include='/memory/inbox/.failed/***'
  --include='/memory/inbox/.gitkeep'
  --exclude='/memory/inbox/*'
  # 平台原生 venv：絕不同步
  --exclude='/.venv/'
  # runtime 產物：WSL 本地所有
  --exclude='/logs/'
  --exclude='/hermes/jobs.db'
  --exclude='/hermes/jobs.db-*'
  --exclude='/hermes/state/'
  # WSL 側機密（Windows 沒有這個檔）
  --exclude='/hermes/config/.env'
  # Telegram bot token/白名單：部署側本地維護的密鑰，不經 repo 同步。
  # bot 邊界決策（2026-07-09）：ClaudeCodeOS bot（AgentOS 控制入口）與
  # Hermes profile bots（profile 對話入口）分開，本 repo 不管理/不同步/
  # 不覆寫任何 profile bot 設定。見 hermes/README.md「Bot 邊界」。
  # （rsync 排除項預設受 --delete 保護，不會被刪。）
  --exclude='/hermes/config/telegram.json'
  # 機器本地 / 平台分側維護的 .claude 檔案
  --exclude='/.claude/settings.local.json'
  --exclude='/.claude/settings.json'
  --exclude='/.claude/launch.json'
  # 雜項產物
  --exclude='/.git/'
  --exclude='__pycache__/'
  --exclude='*.pyc'
  --exclude='.DS_Store'
)

# 衝突掃描時要跳過的路徑（與上面排除清單對應；這些檔案本來就分側維護）
scan_excluded() {
  case "$1" in
    ./.venv/*|./logs/*|./hermes/jobs.db*|./hermes/state/*|./hermes/config/.env) return 0 ;;
    ./hermes/config/telegram.json) return 0 ;;
    ./.claude/settings.local.json|./.claude/settings.json|./.claude/launch.json) return 0 ;;
    ./.git/*|*__pycache__*|*.pyc|*.DS_Store) return 0 ;;
    ./memory/inbox/*) return 0 ;;
  esac
  return 1
}

# ================================================================
# Phase 0 — 前置檢查
# ================================================================
hr; log "[Phase 0] 前置檢查（mode=$MODE）"; hr

command -v rsync >/dev/null || { log "錯誤: 找不到 rsync"; exit 1; }
[ -f "$WIN_ROOT/CLAUDE.md" ] || { log "錯誤: Windows repo 不存在或 /mnt/c 未掛載: $WIN_ROOT"; exit 1; }
[ -f "$WSL_ROOT/CLAUDE.md" ] || { log "錯誤: WSL 部署複本不存在: $WSL_ROOT"; exit 1; }

# 服務狀態（worker / telegram 是常駐 process，同步中換檔建議先停）
RUNNING_UNITS=""
for u in hermes-worker.service hermes-telegram.service; do
  if systemctl --user is-active --quiet "$u" 2>/dev/null; then
    RUNNING_UNITS="$RUNNING_UNITS $u"
  fi
done
if [ -n "$RUNNING_UNITS" ]; then
  log "注意: 下列常駐服務運行中:$RUNNING_UNITS"
  if [ "$MODE" = "apply" ] && [ "$ALLOW_RUNNING" -ne 1 ]; then
    log "中止。建議先: systemctl --user stop hermes-worker hermes-telegram"
    log "（或加 --allow-running 強制執行）"
    exit 1
  fi
else
  log "服務狀態: hermes-worker / hermes-telegram 未在運行"
fi

# 衝突掃描：WSL 側「內容不同且 mtime 較新」的檔案（正常不該存在——
# 背景任務只能寫 inbox，不能改其他檔案；出現即代表部署側被手動改過）
log ""
log "衝突掃描（WSL 側較新且內容不同的檔案）..."
CONFLICTS=0
while IFS= read -r f; do
  rel="${f#"$WSL_ROOT"/}"
  scan_excluded "./$rel" && continue
  w="$WIN_ROOT/$rel"
  if [ -f "$w" ]; then
    if ! cmp -s "$w" "$f"; then
      wm=$(stat -c %Y "$w"); lm=$(stat -c %Y "$f")
      if [ "$lm" -gt "$wm" ]; then
        log "  CONFLICT: $rel (WSL 較新，正向同步會覆蓋它)"
        CONFLICTS=$((CONFLICTS + 1))
      fi
    fi
  fi
done < <(find "$WSL_ROOT" -type f)
if [ "$CONFLICTS" -gt 0 ]; then
  log "發現 $CONFLICTS 個衝突。請人工確認 WSL 側修改是否要保留（手動搬回 Windows 側）。"
  if [ "$MODE" = "apply" ] && [ "$FORCE" -ne 1 ]; then
    log "中止（加 --force 可強制以 Windows 側為準覆蓋）。"
    exit 1
  fi
else
  log "  無衝突。"
fi

# ================================================================
# 備份（僅 apply）
# ================================================================
if [ "$MODE" = "apply" ] && [ "$NO_BACKUP" -ne 1 ]; then
  hr; log "[備份] WSL 側快照（排除 .venv）"; hr
  mkdir -p "$BACKUP_DIR"
  TS=$(date +%Y%m%dT%H%M%S)
  TARBALL="$BACKUP_DIR/ClaudeCodeOSWin-wsl-pre-sync-$TS.tar.gz"
  tar -C "$(dirname "$WSL_ROOT")" --exclude="$(basename "$WSL_ROOT")/.venv" \
      -czf "$TARBALL" "$(basename "$WSL_ROOT")"
  log "備份完成: $TARBALL"
fi

# ================================================================
# Phase 1 — inbox 反向合併（WSL → Windows，只新增）
# ================================================================
hr; log "[Phase 1] inbox 反向合併 WSL → Windows（--ignore-existing，絕不覆蓋/刪除）"; hr
rsync "${DRY[@]}" -rt --ignore-existing --itemize-changes \
  --exclude='.processed/' --exclude='.failed/' --exclude='.gitkeep' \
  "$WSL_ROOT/memory/inbox/" "$WIN_ROOT/memory/inbox/" \
  | sed 's/^/  /' || { log "Phase 1 失敗"; exit 1; }
log "  (無輸出 = 沒有新 inbox 檔案需要合併)"

# ================================================================
# Phase 2 — inbox 清理（Windows 端已歸檔者，從 WSL inbox 頂層移除）
# ================================================================
hr; log "[Phase 2] inbox 清理（已進 Windows .processed/.failed 的檔案）"; hr
CLEANED=0
for f in "$WSL_ROOT"/memory/inbox/*; do
  [ -f "$f" ] || continue
  base=$(basename "$f")
  [ "$base" = ".gitkeep" ] && continue
  if [ -f "$WIN_ROOT/memory/inbox/.processed/$base" ] || [ -f "$WIN_ROOT/memory/inbox/.failed/$base" ]; then
    if [ "$MODE" = "apply" ]; then
      rm "$f"; log "  已移除: memory/inbox/$base"
    else
      log "  (dry-run) 將移除: memory/inbox/$base"
    fi
    CLEANED=$((CLEANED + 1))
  fi
done
[ "$CLEANED" -eq 0 ] && log "  無需清理。"

# ================================================================
# Phase 3 — 正向同步（Windows → WSL，單向、含 --delete）
# ================================================================
hr; log "[Phase 3] 正向同步 Windows → WSL（排除清單見上方 RSYNC_FILTERS）"; hr
# -rlt: 遞迴/符號連結/時間戳；不帶 -p（/mnt/c 的 DrvFs 權限是假的 777，
# 不能讓它污染 ext4 側；新檔案會依 umask 取得合理權限）
# --delete: Windows 側已刪除/改名的檔案，同步移除 WSL 側殘留；
#           被 exclude 的路徑預設受保護，不會被刪
rsync "${DRY[@]}" -rlt --delete --itemize-changes "${RSYNC_FILTERS[@]}" \
  "$WIN_ROOT/" "$WSL_ROOT/" \
  | sed 's/^/  /' || { log "Phase 3 失敗"; exit 1; }

# ================================================================
# 收尾
# ================================================================
hr
if [ "$MODE" = "apply" ]; then
  log "同步完成。後續手動步驟："
  log "  1. systemctl --user daemon-reload   # hermes/systemd/ 是 live units 的 symlink 目標"
  log "  2. systemctl --user start hermes-worker hermes-telegram   # 若先前有停"
  log "  3. 抽查: diff <(cat $WSL_ROOT/hermes/config/cron_jobs.yaml) <(cat $WIN_ROOT/hermes/config/cron_jobs.yaml)"
else
  log "dry-run 完成，未修改任何檔案。實際執行請用 --apply。"
fi
