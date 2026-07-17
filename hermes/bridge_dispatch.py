#!/usr/bin/env python3
"""hermes/bridge_dispatch.py — v0.1（Stage 2.6b）

Domain dispatch 的**核准 CLI**（規格正本：
docs/stage2.6-domain-dispatch-proposal.md v2，§3／§5／§9「2.6b」）。
掃描 jobs.db 裡 `source='bridge_episode_triage'`、`status='completed'` 的
triage 結果，把 `decision='action_candidate'` 的呈現為待核准候選並冪等登記
`dispatch_records`；使用者逐筆 approve/reject，決定與 actor 留
`dispatch_events` 稽核。

**2.6b 邊界（§9，已拍板的兩段式核准節奏）**：approve 只落 record 與稽核
——**不 enqueue、不呼叫任何模型、不碰 worker、不碰 bridge_state.db**。
「approved → 真的建 dispatch job → headless CoS 執行」的閉環是 2.6c。
本階段不裝任何 timer，全部人工觸發。

CLI（exit code：0＝成功；1＝執行期錯誤/狀態機拒絕/未確認；2＝參數用法錯誤；
3＝list 完成但出現 fail-visible 紅旗——defensive parse 異常或 stale record，
需人工調查）：

    python3 hermes/bridge_dispatch.py [--jobs-db PATH] \
        list    --actor <identifier> [--dry-run]
    python3 hermes/bridge_dispatch.py [--jobs-db PATH] \
        approve <triage_job_id> --actor <identifier> \
                [--task "..."] [--reason "..."] [--yes] [--dry-run]
    python3 hermes/bridge_dispatch.py [--jobs-db PATH] \
        reject  <triage_job_id> --actor <identifier> [--reason "..."] [--dry-run]

設計要點（逐條對應提案）：

- **防禦性 parse（§5.2）**：`jobs.result` 壞 JSON、缺欄/多欄、欄位型別不
  對、decision 不在 enum、job 缺 external_key——一律列入「異常區塊」呈現
  給人看並標不可核准，**不靜默跳過、不 crash、不當候選**（fail-visible；
  這些理論上不該存在，出現即紅旗 → exit 3）。
- **髒 owner（§10）**：`suggested_owner` 不在 registry/agents.yaml 的
  active domain 名單內（例如實測出過的 "na"）**不是**異常——原樣呈現＋
  明確警示，核准時要求人工確認 owner；名單經
  bridge_triage_handler.load_active_domains 讀取（單一真相來源，不硬編碼
  第二份）。registry 讀不到 → 警示「owner 驗證不可用」，不 crash。
- **同 episode 多版本 triage（§5.2 第 5 點，已拍板）**：以最新
  prompt_version 的結果為候選基準，舊筆標 superseded 只供人參照——同一
  episode 不會出現兩筆待核准候選。版本新舊以 `_vN` 數字尾碼比較（無法
  解析時退回字串比較）。若既有 dispatch record 指向已被 supersede 的舊
  triage job，新基準**不另建第二筆 record**——標示 stale 紅旗交人工調查
  （第二層冪等 external_key=event_id 在 2.6c 仍然把關）。
- **needs_review（§5.4）**：只呈現（job_id/event_id/summary/reason），
  不登記、不派工；v1 不提供繞過 triage 的人工直建路徑。
- **decision 計數（§5.4）**：list 順帶輸出三種 decision 的累計計數（含
  superseded 舊筆——監測的是 triage 產出分布）＋異常筆數。
- **`--dry-run`（§9 2.6b，沿用 2.5b 慣例）**：與真實模式邏輯完全相同
  （同樣掃描、parse、superseded 判定、狀態機分類），唯一差異是對 jobs.db
  **零寫入**（不建檔、不 migrate、不登記、不寫稽核）。
- **冪等**：登記走 db.register_dispatch_record（UNIQUE triage_job_id 是
  權威）；approve/reject 走 db 層 proposed-only 狀態機（重複 approve、
  reject 後 approve 一律 DispatchTransitionRejected 明確報錯）。
- **actor**：list/approve/reject 皆 `--actor` 必填、無假預設值（list 的
  actor 用於新登記的 'proposed' 稽核列——dispatch_events.actor NOT NULL，
  每個狀態轉換都要回答「誰」；重跑零登記時 actor 不會被寫入任何地方）。
"""
import argparse
import contextlib
import json
import re
import sqlite3
import sys
from pathlib import Path

_HERMES_DIR = Path(__file__).resolve().parent
ROOT = _HERMES_DIR.parent
if str(_HERMES_DIR) not in sys.path:
    sys.path.insert(0, str(_HERMES_DIR))

import db  # noqa: E402

TRIAGE_SOURCE = "bridge_episode_triage"

# §7.1 固定輸出 schema（與 bridge_triage_handler 同一契約；這裡是消費端的
# 防禦性驗證，不依賴生產端一定正確——§1.1「雙重把關」）
ALLOWED_DECISIONS = ("memory_only", "action_candidate", "needs_review")
REQUIRED_RESULT_FIELDS = ("decision", "summary", "suggested_owner", "reason",
                          "prompt_version")

_PV_SUFFIX_RE = re.compile(r"_v(\d+)$")


def _pv_sort_key(prompt_version: str):
    """prompt_version 新舊比較鍵（§5.2 第 5 點「最新 prompt_version 為準」）。
    `..._vN` 數字尾碼可解析 → 依 N 比較；否則退回字串比較（同一 episode 的
    identity 唯一索引保證同版本不會有兩筆，不需要 tie-break）。"""
    m = _PV_SUFFIX_RE.search(prompt_version or "")
    if m:
        return (1, int(m.group(1)), "")
    return (0, 0, prompt_version or "")


@contextlib.contextmanager
def _read_only_conn(path: Path):
    """唯讀 SQLite 連線（PRAGMA query_only=ON——任何寫入在 driver 層直接
    失敗；比照 bridge_triage_enqueuer 慣例）。呼叫端先確認檔案存在。"""
    conn = sqlite3.connect(path, timeout=30)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        yield conn
    finally:
        conn.close()


def load_owner_enum() -> tuple[tuple[str, ...], str | None]:
    """讀 registry/agents.yaml 的 active domain 名單（owner 防禦性驗證用，
    §10「雙端都做」的消費端）。讀不到 → (空 tuple, 原因)——**不 crash**，
    list/approve 照常運作，只是 owner 驗證降級為「不可用」警示。"""
    import bridge_triage_handler  # lazy：需要 pyyaml
    try:
        return (bridge_triage_handler.load_active_domains(), None)
    except bridge_triage_handler.TriageJobError as exc:
        return ((), str(exc))


def build_suggested_task(summary: str, event_id: str) -> str:
    """§4.2：建議任務描述（預設＝summary 衍生、deterministic）。使用者可用
    --task 覆寫；無論哪種，寫進 record 的文字都經過人眼。"""
    return f"處理 episode triage 候選（event_id={event_id}）：{summary}"


def _parse_triage_result(raw) -> tuple[dict | None, str | None]:
    """§5.2 第 2 點的防禦性 parse。回傳 (triage_dict, None) 或
    (None, 不可核准的原因)。髒 owner **不在**這裡攔——那是候選的警示項，
    不是異常（§10 分工：enum 硬化在生產端 2.6a，消費端呈現＋人工確認）。"""
    if raw is None:
        return (None, "jobs.result 是 NULL——completed triage job 不該無結果")
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        return (None, f"jobs.result 不是合法 JSON：{exc}")
    if not isinstance(obj, dict):
        return (None, f"jobs.result 不是 JSON object（{type(obj).__name__}）")
    keys = set(obj.keys())
    required = set(REQUIRED_RESULT_FIELDS)
    if keys != required:
        missing = sorted(required - keys)
        extra = sorted(keys - required)
        return (None, f"欄位不符固定 schema：缺欄位={missing}、多餘欄位={extra}")
    for field in REQUIRED_RESULT_FIELDS:
        if not isinstance(obj[field], str):
            return (None, f"欄位 {field!r} 不是字串")
    if obj["decision"] not in ALLOWED_DECISIONS:
        return (None, f"decision={obj['decision']!r} 不在 enum "
                      f"{list(ALLOWED_DECISIONS)} 內")
    return (obj, None)


def _artifact_hint(job_row: dict) -> str | None:
    """從 job payload 取 artifact 位置提示（顯示用；payload 壞掉不影響
    候選資格——artifact 提示只是輔助資訊）。"""
    try:
        payload = json.loads(job_row["payload"])
        hint = payload.get("artifact_hint") if isinstance(payload, dict) else None
        return hint if isinstance(hint, str) else None
    except (json.JSONDecodeError, TypeError):
        return None


def _fetch_completed_triage_jobs(jobs_db: Path) -> list[dict]:
    with _read_only_conn(jobs_db) as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(jobs)")}
        if not {"external_key", "prompt_version"} <= cols:
            return []  # 連 2.5a 欄位都沒有 → 不可能有 triage job
        rows = conn.execute(
            "SELECT id, external_key, prompt_version, payload, result, "
            "completed_at FROM jobs WHERE source=? AND status='completed' "
            "ORDER BY completed_at ASC",
            (TRIAGE_SOURCE,),
        ).fetchall()
    return [dict(r) for r in rows]


def _fetch_dispatch_records(jobs_db: Path) -> dict[str, dict]:
    """唯讀撈全部 dispatch_records（keyed by triage_job_id）。表還不存在
    （dry-run 下尚未 migrate）→ 空 dict。"""
    with _read_only_conn(jobs_db) as conn:
        cols = {r["name"] for r in conn.execute(
            "PRAGMA table_info(dispatch_records)")}
        if not cols:
            return {}
        rows = conn.execute("SELECT * FROM dispatch_records").fetchall()
    return {r["triage_job_id"]: dict(r) for r in rows}


def scan_triage_results(jobs_db: Path) -> dict:
    """§5.2 掃描＋防禦性 parse＋superseded 判定（**唯讀**，list 與 dry-run
    共用同一套邏輯——分類結果一致是 2.5b 以來的 --dry-run 鐵律）。

    回傳 dict：
    - anomalies：[{job_id, event_id, reason}]（fail-visible，不可核准）
    - candidates：[{job_id, event_id, prompt_version, triage, artifact}]
      （基準筆、decision=action_candidate）
    - needs_review：[{job_id, event_id, prompt_version, triage}]（基準筆）
    - superseded：[{job_id, event_id, prompt_version, decision,
      superseded_by}]（舊版本筆，只供參照）
    - decision_counts：三種 decision 的累計計數（含 superseded 舊筆）
    """
    jobs = _fetch_completed_triage_jobs(jobs_db)
    anomalies: list[dict] = []
    episodes: dict[str, list[dict]] = {}
    decision_counts = {d: 0 for d in ALLOWED_DECISIONS}

    for job in jobs:
        triage, why = _parse_triage_result(job["result"])
        if why is None and not (isinstance(job["external_key"], str)
                                and job["external_key"].strip()):
            why = "job 缺 external_key（triage job 不該沒有 identity）"
        if why is not None:
            anomalies.append({"job_id": job["id"],
                              "event_id": job["external_key"], "reason": why})
            continue
        decision_counts[triage["decision"]] += 1
        job["triage"] = triage
        episodes.setdefault(job["external_key"], []).append(job)

    candidates: list[dict] = []
    needs_review: list[dict] = []
    superseded: list[dict] = []
    for event_id, rows in episodes.items():
        rows.sort(key=lambda r: _pv_sort_key(r["prompt_version"]))
        basis = rows[-1]  # 最新 prompt_version 為候選基準（§5.2 第 5 點）
        for old in rows[:-1]:
            superseded.append({
                "job_id": old["id"], "event_id": event_id,
                "prompt_version": old["prompt_version"],
                "decision": old["triage"]["decision"],
                "superseded_by": basis["id"],
            })
        entry = {"job_id": basis["id"], "event_id": event_id,
                 "prompt_version": basis["prompt_version"],
                 "triage": basis["triage"],
                 "artifact": _artifact_hint(basis)}
        if basis["triage"]["decision"] == "action_candidate":
            candidates.append(entry)
        elif basis["triage"]["decision"] == "needs_review":
            needs_review.append(entry)
    return {"anomalies": anomalies, "candidates": candidates,
            "needs_review": needs_review, "superseded": superseded,
            "decision_counts": decision_counts}


def run_list(*, dry_run: bool, actor: str, jobs_db: Path | str | None = None) -> dict:
    """list 子指令主體（§5.2／§5.4）。真實模式：init_db（冪等 migration）＋
    對「基準且 action_candidate 且尚無 record」逐筆 register_dispatch_record；
    dry-run：零寫入，分類結果與真實模式一致。"""
    actor = db._normalize_actor(actor)
    if jobs_db is not None:
        db.DB_PATH = Path(jobs_db)
    jobs_path = Path(db.DB_PATH)
    result: dict = {
        "dry_run": dry_run, "jobs_db_exists": jobs_path.exists(),
        "items": [], "needs_review": [], "superseded": [], "anomalies": [],
        "decision_counts": {d: 0 for d in ALLOWED_DECISIONS},
        "counts": {"registered": 0, "already_registered": 0, "stale": 0},
    }
    if not jobs_path.exists():
        return result  # 沒有 jobs.db＝沒有 triage 結果可掃（不建檔）

    if not dry_run:
        db.init_db()  # 冪等 migration（2.6b：dispatch 兩張表在這裡建立）

    owner_enum, owner_enum_error = load_owner_enum()
    result["owner_enum"] = list(owner_enum)
    result["owner_enum_error"] = owner_enum_error

    scan = scan_triage_results(jobs_path)
    result["needs_review"] = scan["needs_review"]
    result["superseded"] = scan["superseded"]
    result["anomalies"] = scan["anomalies"]
    result["decision_counts"] = scan["decision_counts"]

    records = _fetch_dispatch_records(jobs_path)
    records_by_event: dict[str, list[dict]] = {}
    for rec in records.values():
        records_by_event.setdefault(rec["event_id"], []).append(rec)

    for cand in scan["candidates"]:
        item = dict(cand)
        owner = cand["triage"]["suggested_owner"]
        if owner_enum_error is not None:
            item["owner_warning"] = ("owner 驗證不可用（registry 讀取失敗："
                                     f"{owner_enum_error}）——核准時請人工確認")
        elif owner not in owner_enum:
            item["owner_warning"] = (
                f"suggested_owner={owner!r} 不在 active domain 名單 "
                f"{list(owner_enum)} 內（髒值，§10）——核准時請人工確認 owner")
        else:
            item["owner_warning"] = None
        existing = records.get(cand["job_id"])
        stale = [r for r in records_by_event.get(cand["event_id"], [])
                 if r["triage_job_id"] != cand["job_id"]]
        if existing is not None:
            item["category"] = "already_registered"
            item["record_status"] = existing["status"]
        elif stale:
            # 同 episode 的既有 record 指向舊版 triage job——不建第二筆，
            # fail-visible 交人工調查（§5.2 註記；2.6c 的第二層冪等
            # external_key=event_id 也會擋住重複派工）
            item["category"] = "stale"
            item["record_status"] = None
            item["stale_records"] = [
                {"triage_job_id": r["triage_job_id"], "status": r["status"]}
                for r in stale]
        elif dry_run:
            item["category"] = "registered"  # 分類與真實模式一致（would-register）
            item["record_status"] = "proposed"
        else:
            created = db.register_dispatch_record(
                cand["job_id"], cand["event_id"], owner, actor)
            item["category"] = "registered" if created else "already_registered"
            item["record_status"] = "proposed" if created else (
                db.get_dispatch_record(cand["job_id"])["status"])
        result["items"].append(item)
        result["counts"][item["category"]] += 1
    return result


def _classify_transition(jobs_db: Path, triage_job_id: str,
                         target: str) -> tuple[str, str | None]:
    """dry-run 用：唯讀模擬 db 層狀態機的分類（would-ok / would-fail），
    邏輯與 _dispatch_transition 的判定完全一致。"""
    with _read_only_conn(jobs_db) as conn:
        cols = {r["name"] for r in conn.execute(
            "PRAGMA table_info(dispatch_records)")}
        row = None
        if cols:
            row = conn.execute(
                "SELECT status FROM dispatch_records WHERE triage_job_id=?",
                (triage_job_id,)).fetchone()
    if row is None:
        return ("would-fail", f"查無 triage_job_id={triage_job_id} 的 "
                              "dispatch record——請先跑 list 完成冪等登記")
    if row["status"] != "proposed":
        return ("would-fail", f"目前 status={row['status']!r}，不允許轉換為 "
                              f"{target!r}（只有 'proposed' 可以）")
    return ("would-ok", None)


def _load_candidate_context(jobs_path: Path, triage_job_id: str) -> dict | None:
    """approve/reject 顯示用：撈該筆 triage job 並 parse（§4.2：核准時顯示
    summary/reason/suggested_owner 與 artifact 相對路徑）。"""
    with _read_only_conn(jobs_path) as conn:
        row = conn.execute(
            "SELECT id, external_key, prompt_version, payload, result "
            "FROM jobs WHERE id=? AND source=?",
            (triage_job_id, TRIAGE_SOURCE)).fetchone()
    if row is None:
        return None
    job = dict(row)
    triage, why = _parse_triage_result(job["result"])
    return {"job": job, "triage": triage, "parse_error": why,
            "artifact": _artifact_hint(job)}


# ---------- CLI ----------

def _confirm(prompt: str) -> bool:
    """互動式確認（§9 2.6b：approve 顯示建議任務描述「要求確認」）。
    非互動環境（stdin 關閉）→ 視為未確認，不寫入。"""
    try:
        return input(prompt).strip().lower() in ("y", "yes")
    except EOFError:
        return False


def _print_list(result: dict):
    prefix = "[dry-run] " if result["dry_run"] else ""
    if not result["jobs_db_exists"]:
        print(f"{prefix}jobs.db 不存在——沒有 triage 結果可掃描（不建立 db 檔）")
        return
    if result.get("owner_enum_error"):
        print(f"{prefix}警告：registry/agents.yaml 讀取失敗，owner 驗證不可用"
              f"（{result['owner_enum_error']}）", file=sys.stderr)

    display = {"registered": "would-register" if result["dry_run"] else "registered",
               "already_registered": "already-registered",
               "stale": "STALE-RECORD"}
    print(f"{prefix}== 待核准候選（decision=action_candidate，基準筆）==")
    if not result["items"]:
        print(f"{prefix}(沒有候選)")
    for item in result["items"]:
        t = item["triage"]
        line = (f"{prefix}{display[item['category']]:<19} "
                f"triage_job={item['job_id']}  event={item['event_id']}  "
                f"pv={item['prompt_version']}  owner={t['suggested_owner']!r}  "
                f"record_status={item['record_status']}")
        print(line)
        print(f"{prefix}    summary: {t['summary']}")
        if item.get("artifact"):
            print(f"{prefix}    artifact: {item['artifact']}")
        if item.get("owner_warning"):
            print(f"{prefix}    警示: {item['owner_warning']}")
        if item["category"] == "stale":
            for r in item["stale_records"]:
                print(f"{prefix}    紅旗: 同 episode 既有 record 指向舊版 "
                      f"triage_job={r['triage_job_id']}（status={r['status']}）"
                      "——不另建第二筆，需人工調查", file=sys.stderr)

    if result["superseded"]:
        print(f"{prefix}== superseded（舊 prompt_version，僅供參照）==")
        for s in result["superseded"]:
            print(f"{prefix}superseded          triage_job={s['job_id']}  "
                  f"event={s['event_id']}  pv={s['prompt_version']}  "
                  f"decision={s['decision']}  superseded_by={s['superseded_by']}")

    print(f"{prefix}== needs_review 人工佇列（只呈現，不登記、不派工，§5.4）==")
    if not result["needs_review"]:
        print(f"{prefix}(空)")
    for n in result["needs_review"]:
        t = n["triage"]
        print(f"{prefix}needs_review        triage_job={n['job_id']}  "
              f"event={n['event_id']}  pv={n['prompt_version']}")
        print(f"{prefix}    summary: {t['summary']}")
        print(f"{prefix}    reason:  {t['reason']}")

    if result["anomalies"]:
        print(f"{prefix}== 異常區塊（defensive parse 失敗——不可核准，"
              "需人工調查，§5.2）==", file=sys.stderr)
        for a in result["anomalies"]:
            print(f"{prefix}ANOMALY             triage_job={a['job_id']}  "
                  f"event={a['event_id']}  原因: {a['reason']}", file=sys.stderr)

    c = result["decision_counts"]
    k = result["counts"]
    tail = "（dry-run，jobs.db 零寫入）" if result["dry_run"] else ""
    print(f"{prefix}decision 累計：memory_only={c['memory_only']}、"
          f"action_candidate={c['action_candidate']}、"
          f"needs_review={c['needs_review']}｜異常={len(result['anomalies'])}")
    print(f"{prefix}list 完成：registered={k['registered']}、"
          f"already-registered={k['already_registered']}、"
          f"stale={k['stale']}{tail}")


def _print_candidate_context(ctx: dict | None, triage_job_id: str,
                             owner_enum: tuple[str, ...],
                             owner_enum_error: str | None):
    if ctx is None:
        print(f"（jobs.db 裡查無 triage job {triage_job_id}——record 可能"
              "指向已被刪改的 job，請人工調查）", file=sys.stderr)
        return
    if ctx["parse_error"] is not None:
        print(f"（triage 結果 parse 失敗：{ctx['parse_error']}）", file=sys.stderr)
        return
    t = ctx["triage"]
    print(f"event_id:        {ctx['job']['external_key']}")
    print(f"prompt_version:  {ctx['job']['prompt_version']}")
    print(f"summary:         {t['summary']}")
    print(f"reason:          {t['reason']}")
    owner = t["suggested_owner"]
    note = ""
    if owner_enum_error is not None:
        note = "（owner 驗證不可用——registry 讀取失敗，請人工確認）"
    elif owner not in owner_enum:
        note = f"（警示：不在 active domain 名單 {list(owner_enum)} 內，請人工確認）"
    print(f"suggested_owner: {owner!r}{note}（僅供參考，分派仍依 delegation policy）")
    print(f"artifact:        {ctx['artifact']}")


def _cmd_approve(args, jobs_path: Path) -> int:
    ctx = _load_candidate_context(jobs_path, args.triage_job_id)
    owner_enum, owner_err = load_owner_enum()
    _print_candidate_context(ctx, args.triage_job_id, owner_enum, owner_err)

    if args.task is not None:
        task = args.task
        if not task.strip():
            print("錯誤：--task 不得為空或純空白", file=sys.stderr)
            return 1
    else:
        if ctx is None or ctx["parse_error"] is not None:
            print("錯誤：無法生成建議任務描述（triage job 缺失或 parse 失敗）"
                  "——請以 --task 明確提供，或先人工調查", file=sys.stderr)
            return 1
        task = build_suggested_task(ctx["triage"]["summary"],
                                    ctx["job"]["external_key"])
        print(f"建議任務描述（--task 可覆寫）：{task}")
        if not args.yes:
            if not _confirm("接受這個任務描述並核准？[y/N] "):
                print("未確認——不寫入，approve 取消", file=sys.stderr)
                return 1

    if args.dry_run:
        category, why = _classify_transition(jobs_path, args.triage_job_id,
                                             "approved")
        if category == "would-ok":
            print(f"[dry-run] 將核准 {args.triage_job_id}（status → approved，"
                  f"task_description={task!r}，寫入稽核列）——零寫入")
            return 0
        print(f"[dry-run] approve 將失敗：{why}", file=sys.stderr)
        return 1

    try:
        event = db.approve_dispatch(args.triage_job_id, args.actor, task,
                                    reason=args.reason)
    except (ValueError, db.DispatchTransitionRejected) as exc:
        print(f"approve 失敗：{exc}", file=sys.stderr)
        return 1
    print(f"approved {event['triage_job_id']}（event_seq={event['event_seq']}, "
          f"actor={event['actor']}）——2.6b 只落資料與稽核，不派工；"
          "派工閉環是 2.6c")
    return 0


def _cmd_reject(args, jobs_path: Path) -> int:
    ctx = _load_candidate_context(jobs_path, args.triage_job_id)
    owner_enum, owner_err = load_owner_enum()
    _print_candidate_context(ctx, args.triage_job_id, owner_enum, owner_err)

    if args.dry_run:
        category, why = _classify_transition(jobs_path, args.triage_job_id,
                                             "rejected")
        if category == "would-ok":
            print(f"[dry-run] 將拒絕 {args.triage_job_id}（status → rejected，"
                  "寫入稽核列）——零寫入")
            return 0
        print(f"[dry-run] reject 將失敗：{why}", file=sys.stderr)
        return 1

    try:
        event = db.reject_dispatch(args.triage_job_id, args.actor,
                                   reason=args.reason)
    except (ValueError, db.DispatchTransitionRejected) as exc:
        print(f"reject 失敗：{exc}", file=sys.stderr)
        return 1
    print(f"rejected {event['triage_job_id']}（event_seq={event['event_seq']}, "
          f"actor={event['actor']}）——到此為止，不建任何 job")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="hermes/bridge_dispatch.py — Stage 2.6b 核准 CLI：呈現 "
                    "action_candidate 候選、冪等登記 dispatch_records、"
                    "approve/reject 留稽核（人工觸發；不派工、不呼叫模型、"
                    "不裝 timer——執行閉環是 2.6c）",
        epilog="exit code：0 成功；1 執行期錯誤/狀態機拒絕/未確認；2 用法錯誤；"
               "3 list 出現 fail-visible 紅旗（parse 異常或 stale record）")
    parser.add_argument("--jobs-db", default=None,
                        help=f"jobs.db 路徑（預設 {db.DB_PATH}）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="掃描 triage 結果＋防禦性 parse＋"
                                         "冪等登記＋needs_review 佇列＋計數")
    p_list.add_argument("--actor", required=True,
                        help="操作者身份（新登記的 'proposed' 稽核列用；必填、"
                             "無預設值）")
    p_list.add_argument("--dry-run", action="store_true",
                        help="jobs.db 零寫入；分類結果與真實模式一致")

    p_appr = sub.add_parser("approve", help="核准候選（proposed → approved；"
                                            "只落資料與稽核，不派工）")
    p_appr.add_argument("triage_job_id")
    p_appr.add_argument("--actor", required=True, help="必填、無預設值")
    p_appr.add_argument("--reason", default=None)
    p_appr.add_argument("--task", default=None,
                        help="任務描述（未給則顯示 summary 衍生的建議值並"
                             "要求確認）")
    p_appr.add_argument("--yes", action="store_true",
                        help="接受建議任務描述，跳過互動確認（明確旗標，"
                             "非預設）")
    p_appr.add_argument("--dry-run", action="store_true",
                        help="零寫入；狀態機分類與真實模式一致")

    p_rej = sub.add_parser("reject", help="拒絕候選（proposed → rejected；"
                                          "到此為止）")
    p_rej.add_argument("triage_job_id")
    p_rej.add_argument("--actor", required=True, help="必填、無預設值")
    p_rej.add_argument("--reason", default=None)
    p_rej.add_argument("--dry-run", action="store_true",
                       help="零寫入；狀態機分類與真實模式一致")
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

    if args.cmd == "list":
        try:
            result = run_list(dry_run=args.dry_run, actor=args.actor)
        except (ValueError, sqlite3.Error) as exc:
            print(f"錯誤：{exc}", file=sys.stderr)
            return 1
        _print_list(result)
        red_flags = len(result["anomalies"]) + result["counts"]["stale"]
        return 3 if red_flags else 0

    # approve / reject：actor 先過 API 同款驗證（雙重保險——argparse required
    # 擋不住 --actor "  "），jobs.db 必須已存在（沒有 triage 結果就沒有 record）
    try:
        db._normalize_actor(args.actor)
    except ValueError as exc:
        print(f"錯誤：{exc}", file=sys.stderr)
        return 1
    if not jobs_path.exists():
        print(f"錯誤：jobs.db 不存在（{jobs_path}）——沒有任何 triage 結果，"
              "無 record 可操作（不建立 db 檔）", file=sys.stderr)
        return 1
    if not args.dry_run:
        db.init_db()  # 冪等 migration（確保 dispatch 兩張表存在）
    if args.cmd == "approve":
        return _cmd_approve(args, jobs_path)
    return _cmd_reject(args, jobs_path)


if __name__ == "__main__":
    sys.exit(_cli())
