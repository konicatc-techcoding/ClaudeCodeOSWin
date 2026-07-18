#!/usr/bin/env python3
"""hermes/bridge_notifier.py — v0.1（Stage 2.7a）

管線生命週期事件的 **Slack notifier**（規格正本：
docs/stage2.7-notification-scheduling-proposal.md v2，§0／§2／§3／§5／§8／
§9「2.7a」）。oneshot：唯讀掃描 jobs.db → 比對 notification_log（冪等第一
層權威）→ 逐筆以子程序呼叫 `hermes send -t slack:<channel>
--message-key <key>`（冪等第二層：send ledger 同 key no-op）→ **送成功才**
寫入 notification_log。2.7a 不裝 timer、不真送 Slack（timer 是 2.7b、真實
投遞是 2.7c 部署驗收）——本階段全部人工觸發＋測試 mock。

**鐵律（§0.1，凌駕一切）**：notifier 是純唯讀觀察者＋通知發送者。絕不
呼叫 approve/reject/enqueue/requeue、絕不修改 jobs／dispatch_records——
notification_log 是它唯一可寫的表（外加 init_db 的冪等 schema migration）。
通知是 best-effort 旁路：它失敗，管線完全不受影響（§2.2／§5）。

事件清單（§3.1／§3.2 已拍板；memory_only／triage completed 非候選／
superseded／人工操作本身**絕不通知**）：

| event_type            | subject_id       | 偵測來源                              |
|-----------------------|------------------|---------------------------------------|
| candidate_pending     | triage job_id    | scan_triage_results 基準候選，且尚無 approved/rejected/dispatched 決策（record 不存在或仍是 proposed）|
| needs_review          | triage job_id    | scan_triage_results 基準筆            |
| triage_dead_letter    | triage job_id    | jobs：source=bridge_episode_triage、status=dead_letter |
| dispatch_completed    | dispatch job_id  | jobs：source=bridge_domain_dispatch、status=completed  |
| dispatch_dead_letter  | dispatch job_id  | jobs：source=bridge_domain_dispatch、status=dead_letter|
| anomaly               | triage job_id    | scan_triage_results 的 defensive-parse 異常＋stale record（同 episode 既有 record 指向舊版 triage job）|

message-key（§2.4）：`agentos27:<event_type>:<subject_id>`——決定性、
全小寫、無時間戳；同一事件不論重跑幾次 key 恆同。dead_letter 經人工
requeue 後再次死信 → 同 job_id 同 key → 不二次通知（v1 接受，§2.4）。

內容格式（§3.3 已拍板，防洩漏設計）：episode／artifact 原文**絕不**入
通知；summary 截斷 200 字元＋標註「模型摘要，未經人審」（summary 是通知
中唯一允許的模型產出文字——triage 的 reason 與 jobs.result 全文都不入
通知，要看全文走 CLI）；dispatch_completed 只通知「完成＋成本＋job_id」。
訊息組裝是 deterministic 程式碼樣板（快照測試把關）。

失敗語意（§5／§8）：
- send exit != 0 → 該筆**不落** notification_log、繼續處理其他筆、最終
  非零 exit（fail loud、unit failed 可觀測）→ 下輪重掃自動補送
  （message-key 保證補送不重複）。不做行內重試等待（gateway 啟動慢的
  既知事實——一律留待下輪）。
- send CLI 不存在／不可執行／逾時 → SendCliUnavailable，中止剩餘發送、
  非零 exit——不靜默降級成「假裝通知過」。
- 「send 成功但寫 log 前 crash」→ 下輪重送同 key → hermes send ledger
  no-op（第二層兜住，Slack 端恰好一則）→ 本輪補寫 log。

CLI（exit code：0＝成功（含「無事可通知」）；1＝執行期錯誤／至少一筆
send 失敗／send CLI 不可用；2＝參數用法錯誤）：

    python3 hermes/bridge_notifier.py [--jobs-db PATH] \
        notify [--dry-run] [--channel CH] [--send-cli CMD]
    python3 hermes/bridge_notifier.py [--jobs-db PATH] log

- `--dry-run`：**零寫入、零外呼**（不 migrate、不建檔、不呼叫 send CLI），
  但事件偵測與冪等過濾邏輯與真實模式完全相同；逐筆列出將發送的
  message-key 與完整訊息內容（§9 2.7a DoD）。
- `--channel`：預設正式頻道 #agentos（C0BHE9NFW0P）；2.7c 驗收期改帶
  測試頻道 C0BHZC2EG84（§3.4 拍板）。allowlist fail-closed 在 hermes 側，
  本層不驗證、不繞過、不改 config（§5）。
- `--send-cli`：send 指令前綴（預設 `hermes`；以 shlex 規則切割，可含
  參數——測試注入 mock fixture 腳本用，例如 "python mock_send.py"；
  Windows 路徑請用 forward slash 或引號）。實際呼叫形狀固定為
  `<send-cli> send -t slack:<channel> --message-key <key> <message>`。
- `log`：唯讀列出 notification_log（觀測用；表尚未建立＝從沒通知過）。
"""
import argparse
import shlex
import sqlite3
import subprocess
import sys
from pathlib import Path

_HERMES_DIR = Path(__file__).resolve().parent
ROOT = _HERMES_DIR.parent
if str(_HERMES_DIR) not in sys.path:
    sys.path.insert(0, str(_HERMES_DIR))

import bridge_dispatch  # noqa: E402（複用 scan_triage_results 單一實作，§2.5）
import db  # noqa: E402

TRIAGE_SOURCE = bridge_dispatch.TRIAGE_SOURCE          # bridge_episode_triage
DISPATCH_SOURCE = bridge_dispatch.DISPATCH_SOURCE      # bridge_domain_dispatch

KEY_PREFIX = "agentos27"          # §2.4 message-key 命名空間
SUMMARY_MAX_CHARS = 200           # §3.3 拍板：summary 截斷 200 字元
SUMMARY_LABEL = "summary（模型摘要，未經人審）"

DEFAULT_CHANNEL = "C0BHE9NFW0P"   # §3.4 拍板：正式頻道 #agentos
TEST_CHANNEL = "C0BHZC2EG84"      # §3.4：2.7c 驗收期測試頻道（--channel 帶入）
DEFAULT_SEND_CLI = "hermes"       # §5 唯一介面：hermes send CLI（黑盒）
SEND_TIMEOUT_SECONDS = 120

EVENT_TYPES = ("candidate_pending", "needs_review", "triage_dead_letter",
               "dispatch_completed", "dispatch_dead_letter", "anomaly")

_TITLES = {
    "candidate_pending": "新 triage 候選待人工核准",
    "needs_review": "triage 產出 needs_review，需人工檢視",
    "triage_dead_letter": "triage job 死信，需人工介入",
    "dispatch_completed": "dispatch job 已完成",
    "dispatch_dead_letter": "dispatch job 死信，需人工介入",
    "anomaly": "triage 結果異常（fail-visible 紅旗），需人工調查",
}


class SendCliUnavailable(RuntimeError):
    """§8：send CLI 不存在／不可執行／逾時——fail loud，中止剩餘發送、
    非零 exit；絕不靜默降級成「假裝通知過」。"""


def build_message_key(event_type: str, subject_id: str) -> str:
    """§2.4：`agentos27:<event_type>:<subject_id>`——決定性、全小寫、無
    時間戳（job_id 是 uuid4 本就小寫；lower() 是防禦性正規化，確保同一
    事件不論輸入大小寫 key 恆同）。"""
    return f"{KEY_PREFIX}:{event_type}:{subject_id}".lower()


def truncate_summary(summary: str) -> str:
    """§3.3 拍板：截斷至 200 字元；截斷時附明確標記（不靜默砍字）。"""
    if len(summary) <= SUMMARY_MAX_CHARS:
        return summary
    return summary[:SUMMARY_MAX_CHARS] + "…（已截斷至 200 字元）"


# ---------- 訊息樣板（§3.3：deterministic 程式碼樣板，快照測試把關） ----------

def _header(event_type: str) -> str:
    return f"[AgentOS 2.7] {event_type}：{_TITLES[event_type]}"


def _cost_line(cost_usd) -> str:
    return (f"cost_usd：{cost_usd}" if cost_usd is not None
            else "cost_usd：（無記錄）")


def build_message(event: dict) -> str:
    """§3.3：只放結構化欄位（event_type／event_id／job_id／suggested_owner／
    成本／下一步指令提示）＋截斷且標註的 summary。episode／artifact 原文、
    triage reason、jobs.result 全文一律不入通知。"""
    et = event["event_type"]
    eid = event.get("event_id") or "（無）"
    lines = [_header(et), f"event_id：{eid}"]
    if et == "candidate_pending":
        lines.append(f"triage_job：{event['subject_id']}")
        lines.append(f"suggested_owner：{event.get('suggested_owner') or '（無）'}"
                     "（僅供參考，分派仍依 delegation policy）")
        lines.append(f"{SUMMARY_LABEL}：{truncate_summary(event.get('summary') or '（無）')}")
        lines.append("下一步（人工）：python3 hermes/bridge_dispatch.py list "
                     "--actor <identifier>，再逐筆 approve/reject（核准 gate "
                     "100% 人工，§0.1）")
    elif et == "needs_review":
        lines.append(f"triage_job：{event['subject_id']}")
        lines.append(f"{SUMMARY_LABEL}：{truncate_summary(event.get('summary') or '（無）')}")
        lines.append("下一步（人工）：python3 hermes/bridge_dispatch.py list "
                     "--actor <identifier> 檢視 needs_review 佇列（含 reason）")
    elif et == "triage_dead_letter":
        lines.append(f"triage_job：{event['subject_id']}")
        lines.append(f"下一步（人工）：先讀 logs/hermes/{event['subject_id']}.log"
                     f" 評估，再 python3 hermes/db.py requeue "
                     f"{event['subject_id']} --actor <identifier>（唯一重跑"
                     "路徑，§8）")
    elif et == "dispatch_completed":
        lines.append(f"dispatch_job：{event['subject_id']}")
        lines.append(_cost_line(event.get("cost_usd")))
        lines.append("結果全文不入通知（§3.3 拍板）——查看：python3 "
                     "hermes/bridge_dispatch.py status")
    elif et == "dispatch_dead_letter":
        lines.append(f"dispatch_job：{event['subject_id']}")
        lines.append(_cost_line(event.get("cost_usd")))
        lines.append(f"下一步（人工）：先讀 logs/hermes/{event['subject_id']}.log"
                     f" 評估副作用，再 python3 hermes/db.py requeue "
                     f"{event['subject_id']} --actor <identifier>（唯一重跑"
                     "路徑，§8）")
    elif et == "anomaly":
        lines.append(f"triage_job：{event['subject_id']}")
        lines.append(f"原因：{event.get('reason') or '（無）'}")
        lines.append("下一步（人工）：python3 hermes/bridge_dispatch.py list "
                     "--actor <identifier> 檢視異常區塊並人工調查（§5.2 "
                     "fail-visible）")
    else:  # 防禦：不該發生——事件都由本模組自己產生
        raise ValueError(f"未知 event_type：{et!r}")
    return "\n".join(lines)


# ---------- 事件偵測（§2.5：全程唯讀，複用 scan_triage_results） ----------

def _fetch_lifecycle_jobs(jobs_db: Path) -> list[dict]:
    """唯讀查 jobs 表：triage dead_letter＋dispatch completed/dead_letter
    （§2.5）。連 2.5a 的 external_key 欄位都沒有的 legacy DB → 不可能有
    triage/dispatch job → 空表。"""
    with bridge_dispatch._read_only_conn(jobs_db) as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(jobs)")}
        if "external_key" not in cols:
            return []
        rows = conn.execute(
            "SELECT id, source, status, external_key, cost_usd FROM jobs "
            "WHERE (source=? AND status='dead_letter') "
            "   OR (source=? AND status IN ('completed', 'dead_letter')) "
            "ORDER BY created_at ASC",
            (TRIAGE_SOURCE, DISPATCH_SOURCE),
        ).fetchall()
    return [dict(r) for r in rows]


def _make_event(event_type: str, subject_id: str, event_id: str | None,
                **extra) -> dict:
    event = {"event_type": event_type, "subject_id": subject_id,
             "event_id": event_id,
             "message_key": build_message_key(event_type, subject_id)}
    event.update(extra)
    event["message"] = build_message(event)
    return event


def detect_events(jobs_db: Path) -> list[dict]:
    """§2.5／§3：偵測全部「值得通知」的事件（不看 notification_log——冪等
    過濾是 run_notify 的職責，偵測與過濾分離以便測試）。**全程唯讀**。

    回傳逐筆 dict：event_type／subject_id／event_id／message_key／message
    （＋各型別 extra 欄位）。排序 deterministic：(event_type, subject_id)。
    """
    scan = bridge_dispatch.scan_triage_results(jobs_db)
    records = bridge_dispatch._fetch_dispatch_records(jobs_db)
    records_by_event: dict[str, list[dict]] = {}
    for rec in records.values():
        records_by_event.setdefault(rec["event_id"], []).append(rec)

    events: list[dict] = []

    for cand in scan["candidates"]:
        job_id = cand["job_id"]
        rec = records.get(job_id)
        stale = [r for r in records_by_event.get(cand["event_id"], [])
                 if r["triage_job_id"] != job_id]
        if rec is None and stale:
            # 同 episode 既有 record 指向舊版 triage job——與 dispatch CLI 的
            # STALE-RECORD 紅旗同一事實（§3.2 anomaly 涵蓋 stale record）
            listed = "、".join(f"{r['triage_job_id']}（status={r['status']}）"
                               for r in stale)
            events.append(_make_event(
                "anomaly", job_id, cand["event_id"],
                reason=f"stale record：同 episode 既有 dispatch record 指向"
                       f"舊版 triage job {listed}——不另建第二筆，需人工調查"))
        elif rec is None or rec["status"] == "proposed":
            # §3.1：尚無 approved/rejected 決策（record 不存在＝人還沒跑
            # list；仍是 proposed＝登記了但還沒決策）→ 待核准
            events.append(_make_event(
                "candidate_pending", job_id, cand["event_id"],
                suggested_owner=cand["triage"]["suggested_owner"],
                summary=cand["triage"]["summary"]))
        # else：已 approved/rejected/dispatched——人工操作時人在場，不通知
        # （§3.2；且該事實的後續（dispatch job 完成/死信）另有事件涵蓋）

    for item in scan["needs_review"]:
        events.append(_make_event(
            "needs_review", item["job_id"], item["event_id"],
            summary=item["triage"]["summary"]))

    for anom in scan["anomalies"]:
        events.append(_make_event(
            "anomaly", anom["job_id"], anom["event_id"],
            reason=anom["reason"]))

    for job in _fetch_lifecycle_jobs(jobs_db):
        if job["source"] == TRIAGE_SOURCE:
            events.append(_make_event(
                "triage_dead_letter", job["id"], job["external_key"]))
        elif job["status"] == "completed":
            events.append(_make_event(
                "dispatch_completed", job["id"], job["external_key"],
                cost_usd=job["cost_usd"]))
        else:
            events.append(_make_event(
                "dispatch_dead_letter", job["id"], job["external_key"],
                cost_usd=job["cost_usd"]))

    events.sort(key=lambda e: (e["event_type"], e["subject_id"]))
    return events


def _known_message_keys(jobs_db: Path) -> set[str]:
    """唯讀撈 notification_log 既有 key（冪等第一層）。表尚未建立（dry-run
    下從沒 migrate 過）→ 空集合。"""
    with bridge_dispatch._read_only_conn(jobs_db) as conn:
        cols = {r["name"] for r in conn.execute(
            "PRAGMA table_info(notification_log)")}
        if not cols:
            return set()
        rows = conn.execute("SELECT message_key FROM notification_log").fetchall()
    return {r["message_key"] for r in rows}


# ---------- 發送（§5：hermes send CLI 黑盒，exit code＋stdout 判定成敗） ----------

def _send_argv(send_cli: str, channel: str, message_key: str,
               message: str) -> list[str]:
    """§5 唯一介面的固定呼叫形狀。send_cli 以 shlex 切割（可注入 mock，
    §9 2.7a）。"""
    return (shlex.split(send_cli)
            + ["send", "-t", f"slack:{channel}",
               "--message-key", message_key, message])


def invoke_send_cli(send_cli: str, channel: str, message_key: str,
                    message: str) -> tuple[bool, str]:
    """子程序呼叫 send CLI（黑盒）。回傳 (成功與否, 結果摘要——稽核用)。
    CLI 不存在／不可執行／逾時 → SendCliUnavailable（§8 fail loud）。"""
    argv = _send_argv(send_cli, channel, message_key, message)
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=SEND_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError) as exc:
        raise SendCliUnavailable(
            f"send CLI 呼叫失敗（{send_cli!r}）：{exc}——fail loud，"
            "本輪中止；零通知被標記為已送出（§8）") from exc
    summary = (f"exit={proc.returncode} "
               f"stdout={(proc.stdout or '').strip()[:200]!r} "
               f"stderr={(proc.stderr or '').strip()[:200]!r}")
    return (proc.returncode == 0, summary)


# ---------- 主流程 ----------

def run_notify(*, dry_run: bool, channel: str = DEFAULT_CHANNEL,
               send_cli: str = DEFAULT_SEND_CLI,
               jobs_db: Path | str | None = None) -> dict:
    """notify 子指令主體（§2）。

    真實模式：db.init_db()（冪等 migration——確保 notification_log 存在；
    這是對 jobs.db 檔案唯二的寫入路徑之一，另一個是送成功後的
    record_notification）→ 偵測 → 過濾已通知 → 逐筆 send → 成功才落 log。
    dry-run：**零寫入零外呼**——不 migrate、不建檔、不呼叫 send CLI；
    偵測與過濾邏輯與真實模式完全一致。

    回傳 dict：dry_run/channel/jobs_db_exists/detected（全部事件）/
    already_notified（本輪略過的 key）/pending（本輪該送的事件）/
    sent/failed（逐筆 {message_key, event_type, subject_id, send_result}）/
    aborted（SendCliUnavailable 中止時的訊息，否則 None）。
    """
    if jobs_db is not None:
        db.DB_PATH = Path(jobs_db)
    jobs_path = Path(db.DB_PATH)
    result: dict = {
        "dry_run": dry_run, "channel": channel,
        "jobs_db_exists": jobs_path.exists(),
        "detected": [], "already_notified": [], "pending": [],
        "sent": [], "failed": [], "aborted": None,
    }
    if not jobs_path.exists():
        return result  # 沒有 jobs.db＝沒有事件可通知（不建檔）

    if not dry_run:
        db.init_db()  # 冪等 migration（notification_log 在這裡建立）

    events = detect_events(jobs_path)
    known = _known_message_keys(jobs_path)
    result["detected"] = events
    result["already_notified"] = sorted(
        e["message_key"] for e in events if e["message_key"] in known)
    pending = [e for e in events if e["message_key"] not in known]
    result["pending"] = pending

    if dry_run:
        return result  # 零外呼——列清單交給呼叫端呈現

    for event in pending:
        try:
            ok, summary = invoke_send_cli(
                send_cli, channel, event["message_key"], event["message"])
        except SendCliUnavailable as exc:
            # §8：CLI 不可用 → 中止剩餘發送（已成功的維持已落 log 的事實）
            result["aborted"] = str(exc)
            break
        entry = {"message_key": event["message_key"],
                 "event_type": event["event_type"],
                 "subject_id": event["subject_id"], "send_result": summary}
        if ok:
            # §2.3 拍板順序：送成功才寫入。send 成功、寫入前 crash 的縫隙
            # 由第二層（hermes send ledger 同 key no-op）兜住。
            db.record_notification(event["message_key"], event["event_type"],
                                   event["subject_id"], channel,
                                   send_result=summary)
            result["sent"].append(entry)
        else:
            # §5／§8：失敗不落 log、不行內重試——下輪重掃自動補送
            result["failed"].append(entry)
    return result


# ---------- CLI ----------

def _print_notify(result: dict):
    prefix = "[dry-run] " if result["dry_run"] else ""
    if not result["jobs_db_exists"]:
        print(f"{prefix}jobs.db 不存在——沒有事件可通知（不建立 db 檔）")
        return
    n_detected = len(result["detected"])
    n_known = len(result["already_notified"])
    pending = result["pending"]
    for key in result["already_notified"]:
        print(f"{prefix}already-notified    {key}（notification_log 已有，"
              "略過）")
    if result["dry_run"]:
        for i, e in enumerate(pending, 1):
            print(f"{prefix}--- 將發送 {i}/{len(pending)}  "
                  f"message-key: {e['message_key']}  "
                  f"channel: slack:{result['channel']} ---")
            print(e["message"])
        print(f"{prefix}notify 完成：偵測 {n_detected} 筆｜已通知略過 "
              f"{n_known}｜將發送 {len(pending)}（零寫入、零外呼）")
        return
    for e in result["sent"]:
        print(f"sent                {e['message_key']}（已寫入 "
              f"notification_log；{e['send_result']}）")
    for e in result["failed"]:
        print(f"SEND-FAILED         {e['message_key']}  {e['send_result']}"
              "——不落 log，下輪補送（§8）", file=sys.stderr)
    if result["aborted"]:
        print(f"中止：{result['aborted']}", file=sys.stderr)
    print(f"notify 完成：偵測 {n_detected} 筆｜已通知略過 {n_known}｜"
          f"本輪送出 {len(result['sent'])}｜失敗 {len(result['failed'])}"
          f"（channel=slack:{result['channel']}）")


def _cmd_log(jobs_path: Path) -> int:
    if not jobs_path.exists():
        print(f"jobs.db 不存在（{jobs_path}）——沒有任何通知紀錄")
        return 0
    with bridge_dispatch._read_only_conn(jobs_path) as conn:
        cols = {r["name"] for r in conn.execute(
            "PRAGMA table_info(notification_log)")}
        rows = (conn.execute(
            "SELECT * FROM notification_log "
            "ORDER BY sent_at ASC, message_key ASC").fetchall()
            if cols else [])
    if not cols:
        print("（notification_log 表尚未建立——還沒送出過任何通知）")
        return 0
    if not rows:
        print("（notification_log 是空的——還沒送出過任何通知）")
        return 0
    for r in rows:
        print(f"{r['sent_at']}  {r['event_type']:<20} "
              f"key={r['message_key']}  channel={r['channel']}  "
              f"send_result={r['send_result']}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="hermes/bridge_notifier.py — Stage 2.7a notifier：唯讀"
                    "掃描 jobs.db 生命週期事件（候選待核准/needs_review/"
                    "死信/dispatch 完成/anomaly），比對 notification_log "
                    "冪等，經 hermes send CLI 投遞 Slack。純觀察者——絕不 "
                    "approve/enqueue/requeue（§0.1 鐵律）；2.7a 不裝 timer、"
                    "不真送（測試全 mock）",
        epilog="exit code：0 成功（含無事可通知）；1 執行期錯誤/至少一筆 "
               "send 失敗/send CLI 不可用（失敗筆不落 log，下輪補送）；"
               "2 參數用法錯誤。")
    parser.add_argument("--jobs-db", default=None,
                        help=f"jobs.db 路徑（預設 {db.DB_PATH}）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_notify = sub.add_parser(
        "notify", help="偵測事件→冪等過濾→逐筆 send→成功才落 notification_log")
    p_notify.add_argument("--dry-run", action="store_true",
                          help="零寫入、零外呼；逐筆列出將發送的 message-key "
                               "與完整訊息內容")
    p_notify.add_argument("--channel", default=DEFAULT_CHANNEL,
                          help=f"Slack 頻道 id（預設正式頻道 #agentos＝"
                               f"{DEFAULT_CHANNEL}；2.7c 驗收期帶測試頻道 "
                               f"{TEST_CHANNEL}，§3.4）")
    p_notify.add_argument("--send-cli", default=DEFAULT_SEND_CLI,
                          help=f"send 指令前綴（預設 {DEFAULT_SEND_CLI!r}；"
                               "shlex 切割、可含參數——測試 mock 注入用）")

    sub.add_parser("log", help="唯讀列出 notification_log（觀測用）")
    return parser


def _cli(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    args = _build_parser().parse_args(argv)
    if args.jobs_db is not None:
        db.DB_PATH = Path(args.jobs_db)
    jobs_path = Path(db.DB_PATH)

    if args.cmd == "log":
        try:
            return _cmd_log(jobs_path)
        except sqlite3.Error as exc:
            print(f"錯誤：{exc}", file=sys.stderr)
            return 1

    try:
        result = run_notify(dry_run=args.dry_run, channel=args.channel,
                            send_cli=args.send_cli)
    except (ValueError, sqlite3.Error, SendCliUnavailable) as exc:
        print(f"錯誤：{exc}", file=sys.stderr)
        return 1
    _print_notify(result)
    return 1 if (result["failed"] or result["aborted"]) else 0


if __name__ == "__main__":
    sys.exit(_cli())
