#!/usr/bin/env bash
# hermes/systemd/uninstall.sh — v0.1（WSL2 版本，取代 macOS 的 hermes/launchd/uninstall.sh）
#
# 用法：
#   hermes/systemd/uninstall.sh                     # 預設移除 worker
#   hermes/systemd/uninstall.sh hermes-telegram     # 移除 telegram adapter
#   hermes/systemd/uninstall.sh hermes-rss          # 移除 rss adapter（連 timer 一起移除）
#   hermes/systemd/uninstall.sh hermes-bridge-scanner   # 移除 bridge scanner（連 timer 一起移除）

set -euo pipefail

NAME="${1:-hermes-worker}"
USER_UNIT_DIR="$HOME/.config/systemd/user"
SERVICE_LINK="$USER_UNIT_DIR/${NAME}.service"
TIMER_LINK="$USER_UNIT_DIR/${NAME}.timer"

if ! command -v systemctl >/dev/null 2>&1; then
    echo "找不到 systemctl，這台環境可能沒有啟用 systemd，略過移除步驟。" >&2
    exit 1
fi

FOUND=0

if [[ -e "$TIMER_LINK" ]]; then
    systemctl --user disable --now "${NAME}.timer" 2>/dev/null || true
    rm -f "$TIMER_LINK"
    echo "已停止並移除：${NAME}.timer"
    FOUND=1
fi

if [[ -e "$SERVICE_LINK" ]]; then
    systemctl --user disable --now "${NAME}.service" 2>/dev/null || true
    rm -f "$SERVICE_LINK"
    echo "已停止並移除：${NAME}.service"
    FOUND=1
fi

systemctl --user daemon-reload 2>/dev/null || true

if [[ "$FOUND" -eq 0 ]]; then
    echo "沒有找到已安裝的 ${NAME}，可能本來就沒裝"
fi
