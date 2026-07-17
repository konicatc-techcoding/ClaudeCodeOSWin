#!/usr/bin/env bash
# hermes/adapter/invoke_cos_triage.sh — v0.1（Stage 2.5c）
#
# Episode triage 專屬的 headless 呼叫入口點（規格正本：
# docs/stage2.5-episode-triage-proposal.md v6 §7.2／§7.3）。
# 與一般用途的 invoke_cos.sh **完全分離**（獨立腳本本身就是「這條呼叫
# 路徑與其他 job source 不同」的具體證明，§7.2）：
#   - 不載入任何工具（zero-tools，技術強制，見下方旗標組合）
#   - 不給 Agent／subagent 能力
#   - 不做 session resume、不使用 thread_id
#   - 只接受一個 artifact（prompt 由 bridge_triage_handler.py 事先組好）
#   - 只輸出固定 JSON schema（§7.1）；schema 不合法由呼叫端 fail closed
#
# zero-tools 技術強制（§18 唯一 start blocker 已於 2026-07-17 由主 session
# 實機驗證解除），採用已實測的旗標組合（不得增減）：
#   --tools "" --disallowedTools "mcp__*" --allowedTools "StructuredOutput" \
#   --permission-mode dontAsk
# 三個實測事實，本腳本逐一對應：
#   1. 絕不使用 --bare——實測會導致「Not logged in」（憑證載入被跳過）。
#   2. 執行 cwd 的 CLAUDE.md 會被自動載入 context——本腳本一律切換到全新
#      的 mktemp 中性目錄執行，避免 CoS 指令（repo 根目錄的 CLAUDE.md）
#      污染 triage 呼叫。
#   3. 模型可能輸出「假裝呼叫工具」的敘述文字——用 --json-schema 強制固定
#      JSON；呼叫端（bridge_triage_handler.py）另做第二層嚴格 parse＋
#      schema 驗證，不合格視為失敗（fail closed）。
#
# 用法:
#   invoke_cos_triage.sh <prompt_file>
#
# prompt 走檔案傳遞、不走 argv：episode 內容上限 50,000 字元（§8／§17），
# 超過部分平台的 argv／command line 長度限制（例如 Windows 的 32,767
# 字元），檔案傳遞在所有平台上行為一致。

set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "用法: invoke_cos_triage.sh <prompt_file>" >&2
  exit 1
fi

PROMPT_FILE="$1"
if [[ ! -f "$PROMPT_FILE" ]]; then
  echo "prompt file 不存在: $PROMPT_FILE" >&2
  exit 1
fi

PROMPT="$(cat "$PROMPT_FILE")"
if [[ -z "$PROMPT" ]]; then
  echo "prompt file 是空的: $PROMPT_FILE" >&2
  exit 1
fi

# §7.1 固定輸出 schema——五個欄位一個不多一個不少、decision 鎖 enum。
# 呼叫端（bridge_triage_handler.py）仍會獨立做第二層驗證，兩層防護不互相取代。
SCHEMA='{"type":"object","properties":{"decision":{"type":"string","enum":["memory_only","action_candidate","needs_review"]},"summary":{"type":"string"},"suggested_owner":{"type":"string"},"reason":{"type":"string"},"prompt_version":{"type":"string"}},"required":["decision","summary","suggested_owner","reason","prompt_version"],"additionalProperties":false}'

# 中性 cwd：全新空的 mktemp 目錄，保證沒有 CLAUDE.md 會被自動載入（實測事實 2）
NEUTRAL_DIR="$(mktemp -d)"
trap 'rm -rf "$NEUTRAL_DIR"' EXIT
if [[ -e "$NEUTRAL_DIR/CLAUDE.md" ]]; then
  echo "中性目錄內出現 CLAUDE.md（不應發生），fail closed: $NEUTRAL_DIR" >&2
  exit 1
fi
cd "$NEUTRAL_DIR"

# 旗標組合說明（2026-07-17 真實煙霧測試逐一驗證）：
#   --tools ""                   內建工具（Bash/Read/Edit/…）從 tool list 完全移除
#   --disallowedTools "mcp__*"   MCP 工具一律拒絕（中性 cwd 已無專案 .mcp.json，
#                                這裡再擋 user 層設定，雙重保險）
#   --allowedTools "StructuredOutput"  --json-schema 的結構化輸出靠內部
#                                StructuredOutput 工具交付——它只是輸出通道、
#                                無任何副作用。注意不能用 --disallowedTools "*"：
#                                deny 優先權高於 allow，會把 StructuredOutput
#                                一起擋掉（實測：模型產出正確 JSON 也交不出來）
#   --permission-mode dontAsk    headless 下未允許的工具直接拒絕、不 hang
claude -p "$PROMPT" \
  --tools "" \
  --disallowedTools "mcp__*" \
  --allowedTools "StructuredOutput" \
  --permission-mode dontAsk \
  --output-format json \
  --json-schema "$SCHEMA"
