#!/usr/bin/env bash
# hermes/launchd/install.sh — v0.1
#
# 把指定的 hermes plist 安裝成 launchd LaunchAgent（user-level，不需要 root）。
# 這只是部署層：Runtime（worker.py／adapters/*.py／db.py）完全不知道自己是被
# launchd 啟動的（只認得 SIGTERM/SIGINT），之後要換成 systemd/Docker/supervisord，
# 只需要換掉這個目錄底下的東西，不用動 Runtime 程式碼。
#
# 用法：
#   hermes/launchd/install.sh                       # 預設安裝 worker
#   hermes/launchd/install.sh hermes-telegram        # 安裝 telegram adapter
#     （telegram 在裝之前要先建好 hermes/config/telegram.json，
#       否則會因為讀不到設定檔一直 crash-loop）

set -euo pipefail

NAME="${1:-hermes-worker}"
PLIST_NAME="com.zackchiu.claudecodeos.${NAME}.plist"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SOURCE="$ROOT/hermes/launchd/$PLIST_NAME"
TARGET="$HOME/Library/LaunchAgents/$PLIST_NAME"

if [[ ! -f "$SOURCE" ]]; then
    echo "找不到 $SOURCE" >&2
    exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents"
ln -sf "$SOURCE" "$TARGET"

if launchctl bootstrap "gui/$(id -u)" "$TARGET" 2>/dev/null; then
    echo "已用 bootstrap 載入"
else
    launchctl load "$TARGET"
    echo "已用 load 載入（bootstrap 不支援，退回舊指令）"
fi

echo "安裝完成：$PLIST_NAME"
echo "查看狀態：launchctl list | grep $NAME"
echo "查看 log：tail -f \"$ROOT/logs/hermes/\"*.log"
