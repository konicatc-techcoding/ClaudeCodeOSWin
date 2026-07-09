#!/usr/bin/env bash
# hermes/launchd/uninstall.sh — v0.1
#
# 用法：
#   hermes/launchd/uninstall.sh                     # 預設移除 worker
#   hermes/launchd/uninstall.sh hermes-telegram      # 移除 telegram adapter

set -euo pipefail

NAME="${1:-hermes-worker}"
PLIST_NAME="com.zackchiu.claudecodeos.${NAME}.plist"
TARGET="$HOME/Library/LaunchAgents/$PLIST_NAME"

if [[ -e "$TARGET" ]]; then
    launchctl bootout "gui/$(id -u)" "$TARGET" 2>/dev/null || launchctl unload "$TARGET" 2>/dev/null || true
    rm -f "$TARGET"
    echo "已停止並移除：$PLIST_NAME"
else
    echo "沒有找到已安裝的 $PLIST_NAME，可能本來就沒裝"
fi
