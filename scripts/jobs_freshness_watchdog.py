#!/usr/bin/env python3
"""scripts/jobs_freshness_watchdog.py — v0.1（F5：jobs.db 新鮮度看門狗）

**它存在的唯一理由**：抓「跑都沒跑」。2026-08-05 起 CoS 執行鏈（claude
登入失效）全線 dead_letter，整整 28 天無人察覺——既有通知（bridge_notifier）
只看得到管線內部事件，看不到「整條鏈死了」。看門狗獨立於那條鏈之外，
在鏈死掉時仍然活著並發聲。

## 硬性設計約束（違反其一，這個 script 就失去意義）

1. **絕不依賴 `claude -p` / `invoke_cos.sh` / 任何 CoS 呼叫**——全文不
   出現這些字串以外的引用；本檔只做 sqlite 唯讀查詢 ＋ 一次 `hermes send`
   子程序呼叫。
2. **告警走 hermes-agent CLI 送 Slack**（作法比照 hermes/bridge_notifier.py
   的 `_send_argv`／`invoke_send_cli`：`<send-cli> send -t slack:<channel>
   --message-key <key> <message>`）。刻意**不 import** bridge_notifier
   ——那條 import 鏈會拉進 hermes/db.py（可寫 DB 的模組）與 bridge_dispatch，
   看門狗要能在管線程式碼壞掉時照樣跑，故自帶最小實作。
3. **對 jobs.db 唯讀**：`file:...?mode=ro` URI ＋ `PRAGMA query_only=ON`
   雙保險；全文無任何 INSERT/UPDATE/DELETE/CREATE。不建檔、不 migrate。

## 判準住在哪裡（2026-09-04 拆分）

判準與計數（`classify`／`evaluate`／`collect_counts`／`load_config`）已抽到
`scripts/jobs_freshness_core.py`——**純函式 + sqlite 唯讀、零 subprocess**，
Web UI 的新鮮度燈號（dashboard/data_jobs_freshness.py → /api/jobs-freshness）
與本檔共用同一份判準與同一份門檻，杜絕「UI 說綠、Slack 說死」。本檔仍是唯一
持有 subprocess（送 Slack）的那一半，上述硬性約束 1–3 完全不變；下方名稱皆
自 core 匯入後原樣再匯出（既有呼叫端與測試 `wd.classify(...)` 不受影響）。

## 五態判準（門檻全在 registry/jobs_watchdog.yaml，本檔不硬編數字）

「最近 N 小時內有沒有 completed」單獨用會誤報（本來就沒 enqueue 時零
completed 是正常的），只看 dead_letter 又會漏掉「連 enqueue 都沒發生」。
所以每個 source 各自算三個量（enqueued／completed／dead_letter＋卡住的
backlog），再分類：

| state             | 條件                                               | 意義          |
|-------------------|----------------------------------------------------|---------------|
| trigger_dead      | expect_enqueue 且 window 內 enqueued < 門檻        | 觸發端死了    |
| executor_dead     | 有進件或有終結中的 job，但 completed == 0，且（有 dead_letter 或有卡住 > stuck_backlog_hours 的 queued/running）| 執行端死了（2026-08 的情況）|
| executor_degraded | 有 completed，但 dead_letter/終結數 ≥ 比例門檻（且樣本足夠）| 部分退化      |
| inconclusive      | 有進件、還沒有任何終結結果、也沒有卡住的 backlog   | 正常「還在跑」，不告警 |
| healthy           | 有進件、有 completed、比例正常                     | 健康          |

`expect_enqueue: false` 的 source（telegram／hermes／triage 這類事件驅動）
零進件時直接判 healthy——不會因為「今天沒人傳訊息」而誤報。

## 告警內容與隱私

訊息只含：source id、state、window、計數、最後一次 completed 的時間戳。
**絕不**含 prompt／result／error_message／episode 內容——那些可能有使用者
資料，要看細節走 CLI。

## 冪等

message-key＝`<prefix>:<date>:<fingerprint>`（fingerprint＝各異常 source
與其 state 的排序雜湊）。同一天、同一組異常狀態重跑 → 同 key → hermes
send ledger no-op（第二層冪等，與 bridge_notifier 同一機制）。狀態改變
（例如又多死一個 source）→ fingerprint 變 → 會再送一次。

## 用法

    python3 scripts/jobs_freshness_watchdog.py [--jobs-db PATH]
        [--config PATH] [--dry-run] [--json] [--channel CH] [--send-cli CMD]
        [--now ISO8601]

- `--dry-run`：零外呼（不呼叫 send CLI），其餘判定邏輯完全相同。
- `--now`：把「現在」固定成指定時刻（測試用；預設 UTC now）。

## Exit codes（比照 scripts/repo_guard_bundle.ps1 慣例）

    0 = 全部受監控 source 健康（含 inconclusive）
    3 = 偵測到異常，且告警已送出（或 --dry-run 下判定為要送）
    1 = 看門狗本身失敗（設定檔壞掉、jobs.db 不存在／讀不了、send CLI 不
        可用或非零 exit）——fail-closed：**停在原地印原因**，不靜默降級成
        「沒事」。exit 1 時如果還能送 Slack，會先試著把失敗本身送出去。

冪等、可重跑、無副作用（除了那一則 Slack 通知）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# 判準與門檻的單一真相住在 jobs_freshness_core.py（2026-09-04 抽出）——
# 這支看門狗與 Web UI 的燈號（dashboard/data_jobs_freshness.py）都只經由
# 那個模組判定，不各判各的。core 是**純函式 + sqlite 唯讀**（零 subprocess），
# 因此「絕不依賴 CoS 執行鏈」的硬性約束不受這次拆分影響：本檔仍是唯一持有
# subprocess（送 Slack）的那一半。
# sys.path 補 scripts/ 目錄：直接執行時 sys.path[0] 已是本目錄，
# 但被別處 import 時不一定，故明確補上（不依賴呼叫方的工作目錄）。
sys.path.insert(0, str(Path(__file__).resolve().parent))

from jobs_freshness_core import (  # noqa: E402
    ALERTING_STATES,
    DEFAULT_CONFIG,
    DEFAULT_JOBS_DB,
    ROOT,
    STATE_EXECUTOR_DEAD,
    STATE_EXECUTOR_DEGRADED,
    STATE_HEALTHY,
    STATE_INCONCLUSIVE,
    STATE_TRIGGER_DEAD,
    STATE_LABEL as _STATE_LABEL,
    WatchdogError,
    _read_only_conn,
    _source_setting,
    classify,
    collect_counts,
    evaluate,
    load_config,
)

SEND_TIMEOUT_SECONDS = 120


# ---------- 告警 ----------

def build_fingerprint(findings: list[dict]) -> str:
    payload = "|".join(f"{f['source']}={f['state']}"
                       for f in sorted(findings, key=lambda f: f["source"])
                       if f["state"] in ALERTING_STATES)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def build_message_key(prefix: str, findings: list[dict], now: datetime) -> str:
    return (f"{prefix}:{now.date().isoformat()}:"
            f"{build_fingerprint(findings)}").lower()


def build_message(findings: list[dict], now: datetime, jobs_db: Path) -> str:
    """只放結構化欄位（source／state／計數／時間戳）——prompt／result／
    error_message 一律不入通知（可能含使用者資料）。"""
    bad = [f for f in findings if f["state"] in ALERTING_STATES]
    lines = ["[AgentOS 看門狗] jobs 管線新鮮度異常",
             f"檢查時間（UTC）：{now.isoformat(timespec='seconds')}",
             f"jobs.db：{jobs_db}",
             f"異常 source：{len(bad)}／受監控 {len(findings)}"]
    for f in bad:
        c = f["counts"]
        lines.append(
            f"- {f['source']}：{f['state']}（{_STATE_LABEL[f['state']]}）"
            f"｜window {f['lookback_hours']:g}h：enqueued={c['enqueued']}"
            f" completed={c['completed']} dead_letter={c['dead_letter']}"
            f" stuck={c['stuck']}"
            f"｜最後一次 completed：{c['last_completed_at'] or '（從未）'}"
            f"｜{f['reason']}")
    lines.append("下一步（人工）：先確認 worker／CoS 執行鏈是否活著"
                 "（wsl -d Ubuntu systemctl --user status hermes-worker），"
                 "再看 logs/hermes/worker.log 與 python3 hermes/db.py list "
                 "--status dead_letter；細節不入通知（可能含使用者資料）。")
    return "\n".join(lines)


def build_error_message(reason: str, now: datetime) -> str:
    return "\n".join([
        "[AgentOS 看門狗] 看門狗本身失敗（無法判斷管線狀態）",
        f"檢查時間（UTC）：{now.isoformat(timespec='seconds')}",
        f"原因：{reason}",
        "下一步（人工）：這代表『沒有消息』不等於『沒事』——請人工確認 "
        "jobs.db 與 registry/jobs_watchdog.yaml。",
    ])


def _send_argv(send_cli: str, channel: str, message_key: str, message: str) -> list[str]:
    """比照 hermes/bridge_notifier.py 的固定呼叫形狀（唯一介面，黑盒）。"""
    return (shlex.split(send_cli)
            + ["send", "-t", f"slack:{channel}", "--message-key", message_key, message])


def send_alert(send_cli: str, channel: str, message_key: str, message: str) -> tuple[bool, str]:
    """回傳 (成功與否, 稽核摘要)。CLI 不存在／逾時 → WatchdogError（fail loud）。"""
    argv = _send_argv(send_cli, channel, message_key, message)
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=SEND_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError) as exc:
        raise WatchdogError(
            f"告警 CLI 呼叫失敗（{send_cli!r}）：{exc}"
            "——看門狗無法發聲，停在原地（fail-closed）") from exc
    summary = (f"exit={proc.returncode} "
               f"stdout={(proc.stdout or '').strip()[:200]!r} "
               f"stderr={(proc.stderr or '').strip()[:200]!r}")
    return (proc.returncode == 0, summary)


# ---------- 主流程 ----------

def run(*, jobs_db: Path, config_path: Path, dry_run: bool, now: datetime,
        channel: str | None = None, send_cli: str | None = None) -> dict:
    config = load_config(config_path)
    alert_cfg = config["alert"]
    channel = channel or alert_cfg["channel"]
    send_cli = send_cli or alert_cfg["send_cli"]

    findings = evaluate(jobs_db, config, now)
    bad = [f for f in findings if f["state"] in ALERTING_STATES]
    result = {
        "now": now.isoformat(), "jobs_db": str(jobs_db), "dry_run": dry_run,
        "channel": channel, "findings": findings,
        "alerting": [f["source"] for f in bad],
        "message_key": None, "message": None, "sent": False, "send_result": None,
    }
    if not bad:
        return result

    result["message_key"] = build_message_key(
        alert_cfg["message_key_prefix"], findings, now)
    result["message"] = build_message(findings, now, jobs_db)
    if dry_run:
        return result
    ok, summary = send_alert(send_cli, channel, result["message_key"], result["message"])
    result["sent"] = ok
    result["send_result"] = summary
    if not ok:
        raise WatchdogError(
            f"告警送出失敗（{summary}）——異常已偵測到但沒能通知出去，"
            "fail-closed（exit 1）")
    return result


def _print_human(result: dict):
    print(f"jobs.db：{result['jobs_db']}")
    print(f"檢查時間（UTC）：{result['now']}"
          f"{'  [dry-run]' if result['dry_run'] else ''}")
    for f in result["findings"]:
        c = f["counts"]
        mark = "!!" if f["state"] in ALERTING_STATES else "ok"
        print(f"  [{mark}] {f['source']:<24} {f['state']:<18} "
              f"window={f['lookback_hours']:g}h enqueued={c['enqueued']} "
              f"completed={c['completed']} dead_letter={c['dead_letter']} "
              f"stuck={c['stuck']}")
        print(f"       理由：{f['reason']}")
    if not result["alerting"]:
        print("結論：全部健康——不送告警")
        return
    print(f"結論：異常 source＝{', '.join(result['alerting'])}")
    print(f"message-key：{result['message_key']}  channel：slack:{result['channel']}")
    print("--- 訊息內容 ---")
    print(result["message"])
    if result["dry_run"]:
        print("--- （dry-run：未呼叫 send CLI） ---")
    else:
        print(f"--- 已送出：{result['send_result']} ---")


def _parse_now(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        description="jobs.db 新鮮度看門狗：獨立於 CoS 執行鏈之外，偵測"
                    "『觸發端死了／執行端死了』並經 hermes send CLI 告警。"
                    "對 jobs.db 全程唯讀。",
        epilog="exit：0 健康｜3 偵測到異常（已告警）｜1 看門狗本身失敗")
    parser.add_argument("--jobs-db", default=str(DEFAULT_JOBS_DB))
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--dry-run", action="store_true",
                        help="零外呼：不呼叫 send CLI，其餘判定完全相同")
    parser.add_argument("--json", action="store_true", help="機器可讀輸出")
    parser.add_argument("--channel", default=None, help="覆寫 alert.channel")
    parser.add_argument("--send-cli", default=None,
                        help="覆寫 alert.send_cli（shlex 切割，可含參數——測試 mock 用）")
    parser.add_argument("--now", default=None,
                        help="把『現在』固定成 ISO8601 時刻（測試用）")
    args = parser.parse_args(argv)

    try:
        now = _parse_now(args.now)
    except ValueError as exc:
        print(f"--now 格式錯誤：{exc}", file=sys.stderr)
        return 2

    try:
        result = run(jobs_db=Path(args.jobs_db), config_path=Path(args.config),
                     dry_run=args.dry_run, now=now, channel=args.channel,
                     send_cli=args.send_cli)
    except WatchdogError as exc:
        print(f"看門狗失敗：{exc}", file=sys.stderr)
        _best_effort_error_alert(args, now, str(exc))
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        _print_human(result)
    return 3 if result["alerting"] else 0


def _best_effort_error_alert(args, now: datetime, reason: str):
    """看門狗自己失敗時，仍試著把「我壞了」送出去——沉默是這次事故的元兇。
    這一步失敗只印在 stderr，不改變 exit code（已經是 1）。"""
    if args.dry_run:
        return
    channel, send_cli, prefix = args.channel, args.send_cli, "agentos-watchdog"
    if not channel or not send_cli:
        try:
            cfg = load_config(Path(args.config))["alert"]
            channel = channel or cfg["channel"]
            send_cli = send_cli or cfg["send_cli"]
            prefix = cfg["message_key_prefix"]
        except WatchdogError:
            return  # 連設定都讀不到 → 沒有可信的告警目標，不亂送
    key = (f"{prefix}:{now.date().isoformat()}:selffail:"
           f"{hashlib.sha256(reason.encode('utf-8')).hexdigest()[:12]}").lower()
    try:
        ok, summary = send_alert(send_cli, channel, key,
                                 build_error_message(reason, now))
        print(f"（已嘗試送出看門狗自身失敗告警：ok={ok} {summary}）", file=sys.stderr)
    except WatchdogError as exc:
        print(f"（看門狗自身失敗告警也送不出去：{exc}）", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
