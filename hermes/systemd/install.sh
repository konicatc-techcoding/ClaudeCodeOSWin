#!/usr/bin/env bash
# hermes/systemd/install.sh — v0.1（WSL2 版本，取代 macOS 的 hermes/launchd/install.sh）
#
# 把指定的 hermes systemd unit 安裝成 user-level systemd service（systemctl --user，
# 不需要 root/sudo），對應原本 launchd 版本的「user-level LaunchAgent」定位。
#
# 前提：WSL2 distro 要開啟 systemd 支援
#   /etc/wsl.conf 加上：
#     [boot]
#     systemd=true
#   然後在 Windows 端跑 `wsl --shutdown` 重啟 distro 才會生效。
#   需要 Windows 11 22H2 以後、或近期版本的 WSL（wsl --update 到最新）。
#   細節見 repo 根目錄的 WINDOWS_WSL_SETUP.md。
#
# 這只是部署層：Runtime（worker.py／adapters/*.py／db.py）完全不知道自己是被
# systemd 啟動的（只認得標準的 SIGTERM/SIGINT），之後要換成別的部署方式，
# 只需要換掉這個目錄底下的東西，不用動 Runtime 程式碼。
#
# 用法：
#   hermes/systemd/install.sh                       # 預設安裝 worker（常駐）
#   hermes/systemd/install.sh hermes-telegram       # 安裝 telegram adapter（常駐）
#     （telegram 在裝之前要先建好 hermes/config/telegram.json，
#       否則會因為讀不到設定檔一直 crash-loop）
#   hermes/systemd/install.sh hermes-rss            # 安裝 rss adapter（service+timer）
#   hermes/systemd/install.sh hermes-cron-daily-memory-check   # 安裝 cron adapter（service+timer）
#   hermes/systemd/install.sh hermes-bridge         # 安裝 hermes bridge（service+timer）
#   hermes/systemd/install.sh hermes-bridge-scanner # 安裝 bridge scanner（service+timer，每天 08:05）
#   hermes/systemd/install.sh hermes-bridge-pipeline # 安裝 bridge pipeline（import→enqueue，service+timer，每天 08:15，Stage 2.7b）
#   hermes/systemd/install.sh hermes-bridge-notifier # 安裝 bridge notifier（Slack 通知，service+timer，每天 08:25，Stage 2.7b）

set -euo pipefail

NAME="${1:-hermes-worker}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
UNIT_DIR="$ROOT/hermes/systemd"
SERVICE_FILE="$UNIT_DIR/${NAME}.service"
TIMER_FILE="$UNIT_DIR/${NAME}.timer"

if ! command -v systemctl >/dev/null 2>&1; then
    echo "找不到 systemctl——這台 WSL distro 可能還沒開啟 systemd 支援。" >&2
    echo "請先在 /etc/wsl.conf 加上 [boot] systemd=true，然後在 Windows 端跑 wsl --shutdown 重啟 distro。" >&2
    echo "細節見 WINDOWS_WSL_SETUP.md。" >&2
    exit 1
fi

if [[ ! -f "$SERVICE_FILE" ]]; then
    echo "找不到 $SERVICE_FILE" >&2
    exit 1
fi

USER_UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$USER_UNIT_DIR"
mkdir -p "$ROOT/logs/hermes"

ln -sf "$SERVICE_FILE" "$USER_UNIT_DIR/${NAME}.service"
echo "已連結 ${NAME}.service"

if [[ -f "$TIMER_FILE" ]]; then
    ln -sf "$TIMER_FILE" "$USER_UNIT_DIR/${NAME}.timer"
    echo "已連結 ${NAME}.timer"
fi

systemctl --user daemon-reload

if [[ -f "$TIMER_FILE" ]]; then
    # 有 .timer 的是一次性 oneshot service，交給 timer 觸發，不用 enable/start service 本身
    systemctl --user enable --now "${NAME}.timer"
    echo "安裝完成：${NAME}.service（由 ${NAME}.timer 排程觸發）"
    echo "查看排程：systemctl --user list-timers | grep $NAME"
else
    # 沒有 .timer 的是常駐 service，直接 enable + start
    systemctl --user enable --now "${NAME}.service"
    echo "安裝完成：${NAME}.service（常駐）"
    echo "查看狀態：systemctl --user status $NAME"
fi

echo "查看 log：tail -f \"$ROOT/logs/hermes/\"*.log"
echo ""
echo "若要讓 user service 在沒有登入 session 時也能持續運作，"
echo "可能需要跑一次：loginctl enable-linger \$USER（讓 systemd --user 在開機時就啟動，不等實際登入）"
