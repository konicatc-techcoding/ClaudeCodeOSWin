#!/usr/bin/env bash
# hermes/adapter/invoke_cos.sh — v0.1
#
# Hermes → Chief of Staff 的呼叫 adapter。
# 今天：shell 出去呼叫 `claude -p`（CLI headless 模式）。
# 未來：若要換成 Claude Agent SDK（in-process），只改這個檔案內部實作，
#       上層的 job queue／事件處理／投遞邏輯不用動。
#
# 用法:
#   invoke_cos.sh "<prompt>" [session_id]

set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "用法: invoke_cos.sh <prompt> [session_id]" >&2
  exit 1
fi

PROMPT="$1"
SESSION_ID="${2:-}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

ARGS=(-p "$PROMPT" --add-dir "$ROOT" --output-format json)
if [[ -n "$SESSION_ID" ]]; then
  ARGS+=(--resume "$SESSION_ID")
fi

claude "${ARGS[@]}"
