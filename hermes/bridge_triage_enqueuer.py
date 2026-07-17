#!/usr/bin/env python3
"""hermes/bridge_triage_enqueuer.py — v0.1（Stage 2.5b）

Episode triage 的**手動 enqueuer CLI**（規格正本：
docs/stage2.5-episode-triage-proposal.md v6，§6／§13「2.5b」／§16 2.5b DoD）。
把 bridge_state.db 裡合格的候選 episode 轉成 `source='bridge_episode_triage'`
的 job 放進 hermes/jobs.db——**人工觸發、不排程、不呼叫任何模型、不執行
triage 本身**（執行是 2.5c 的 worker routing＋invoke_cos_triage.sh 的職責）。

候選資格（§6.5，兩條，缺一不可）：

1. `import_status ∈ {'to_inbox', 'imported'}`（明確排除 needs_review／
   skipped／failed／discovered）。
2. artifact 可在 memory/inbox/ 本層、.processed/、.failed/ 三個目錄中被
   「唯一」定位到——找不到、或同時在超過一個位置找到 → 不列入合格候選，
   列為 ineligible 並給出可行動的原因，交給人工排查。

**明確不做的前置過濾**（§6.5 拿掉的條件）：不查「jobs.db 是否已有相同
identity」來過濾候選——每一個合格候選都無條件呼叫 `enqueue_once()`（或
dry-run 模擬呼叫），由它做唯一權威判斷（created／existing／conflict），
payload_hash 內容漂移必須以 conflict 形式浮上來，不准被候選層靜默掩蓋。

`--dry-run`（§6.6）：與真實模式**邏輯完全相同**（同樣的候選查詢、同樣的
SHA-256 計算、同樣的三分支分類——唯讀查 jobs.db 現況模擬 `enqueue_once`），
唯一差異是最後一步不寫入 jobs.db（連 jobs.db 檔案都不建立）。

Job identity 與 payload（§2／§7.4）：

- `source` 固定 'bridge_episode_triage'；`external_key`＝episode 完整
  event_id（episode 層級 "hermes:<sid>:<first>..<last>" 或 legacy
  "hermes:<sid>"，不截斷、不重組）；`prompt_version` 預設
  bridge_episode_triage_v1（--prompt-version 可覆寫，2.5d 迭代用）。
- `payload_hash`＝artifact 檔案內容（raw bytes）的 SHA-256 hex digest
  （§6.6 步驟 1「依 artifact 內容計算 SHA-256」）。
- payload 只存 event_id、artifact 位置提示、SHA-256、prompt_version
  （§7.4：jobs.db 不存 episode 全文）；位置提示只是提示，2.5c 執行時
  仍會重新定位＋驗證 hash（§7.4，兩層防護不互相取代）。
- `max_attempts=1`（§3.2 Option A）、`thread_id` 恆為 NULL——皆由
  `enqueue_once()` 既有 API 保證，本 CLI 不繞過、不重複實作。

讀寫邊界：

- **bridge_state.db 一律唯讀**（本模組自己的連線一律 `PRAGMA
  query_only=ON`——任何寫入語句都會在 driver 層直接失敗）、零 schema 變更；
  bridge db 不存在＝沒有候選可處理，直接回報，不建立 db 檔。
- jobs.db 只在真實模式寫入，且只透過 2.5a 的 `db.init_db()`（冪等
  migration）＋`db.enqueue_once()`。

dead_letter 重跑的唯一路徑（§4.3，本 CLI 不重複實作）：
    python3 hermes/db.py requeue <job_id> --actor <identifier> [--reason "..."]
（`--actor` 必填、沒有任何預設值——不會用 "anonymous" 之類的假身份頂替。）

CLI（exit code：0＝成功且無 conflict；1＝執行期錯誤；2＝參數用法錯誤；
3＝執行完成但至少一筆 conflict——內容漂移紅旗，需人工調查）：
    python3 hermes/bridge_triage_enqueuer.py [--bridge-db PATH] [--jobs-db PATH] \
        enqueue [--dry-run] [--inbox DIR] [--prompt-version V] [--event-id ID]

`--event-id`（2.6a，2.6 提案 §6）：單筆 enqueue——只處理指定 event_id 的
候選，與整批模式並存、dry-run 相容。event_id 在 bridge_state.db 不存在、
或 import_status 不具候選資格 → 執行期錯誤（exit 1），不建 job。
"""
import argparse
import contextlib
import hashlib
import sqlite3
import sys
from pathlib import Path

_HERMES_DIR = Path(__file__).resolve().parent
ROOT = _HERMES_DIR.parent
for _p in (_HERMES_DIR, _HERMES_DIR / "session_adapter"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import bridge_state  # noqa: E402
import db  # noqa: E402
from adapter import HermesSessionAdapter  # noqa: E402

SOURCE = "bridge_episode_triage"
# 2.6a（docs/stage2.6-domain-dispatch-proposal.md §6）：預設契約升版為 v2
# （suggested_owner enum 硬化＋固定輸出繁體中文）。既有 v1 job 零改動、
# 不重跑；同一 episode 以 v2 enqueue 會天然建新 job（§2.2 既有行為）。
DEFAULT_PROMPT_VERSION = "bridge_episode_triage_v2"
DEFAULT_INBOX_DIR = ROOT / "memory" / "inbox"

# §6.5 條件 1：合格的 import_status（明確排除 needs_review/skipped/failed/discovered）
CANDIDATE_STATUSES = ("to_inbox", "imported")

# §6.5 條件 2 的搜尋範圍：inbox 本層 → .processed/ → .failed/
_SEARCH_SUBDIRS = ("", ".processed", ".failed")

# 逐筆結果的顯示標籤（dry-run 與真實模式共用同一組 canonical category，
# 只有顯示字樣不同——分類邏輯本身完全相同，§6.6）
_DISPLAY = {
    True: {"created": "would-create", "existing": "already-exists-same-hash",
           "conflict": "CONFLICT-hash-drift", "ineligible": "ineligible"},
    False: {"created": "created", "existing": "existing-no-op",
            "conflict": "CONFLICT-hash-drift", "ineligible": "ineligible"},
}


def _rel_to_root(path: Path | str) -> str:
    """repo 內的路徑記相對路徑（posix），repo 外（測試 temp 目錄）記絕對路徑。
    與 bridge_importer／bridge_scanner 同一慣例。"""
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError:
        return str(resolved)


@contextlib.contextmanager
def _read_only_conn(path: Path):
    """唯讀 SQLite 連線：PRAGMA query_only=ON——任何寫入語句在 driver 層直接
    丟 OperationalError（比照 session_adapter 的雙保險慣例）。呼叫端必須先
    確認檔案存在（sqlite3.connect 對不存在的路徑會建檔）。"""
    conn = sqlite3.connect(path, timeout=30)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        yield conn
    finally:
        conn.close()


def list_candidates(bridge_db: Path) -> list[dict]:
    """§6.5 條件 1 的候選查詢——**唯讀**（零寫入、零 schema 變更）。

    只依「現在的 import_status ∈ {to_inbox, imported}」篩選（§6.1–6.4 查證：
    這個狀態值本身就足以回答「是否曾合法到達 to_inbox」）；**不**查 jobs.db
    做任何 identity 前置過濾（§6.5 明確拿掉的條件）。
    """
    with _read_only_conn(bridge_db) as conn:
        rows = conn.execute(
            "SELECT event_id, session_id, import_status FROM bridge_sessions "
            "WHERE import_status IN (?, ?) ORDER BY first_seen_at ASC",
            CANDIDATE_STATUSES,
        ).fetchall()
    return [dict(r) for r in rows]


def _lookup_single_candidate(bridge_db: Path, event_id: str) -> dict:
    """`--event-id` 單筆模式（2.6a）的候選查詢——**唯讀**。

    fail-visible：查無此列、或 import_status 不在候選資格內 → ValueError
    （CLI 層轉 exit 1），不建 job、不靜默略過。資格判定與整批模式同一條
    規則（§6.5 條件 1）；artifact 唯一定位（條件 2）仍走共用的
    locate_artifact 路徑，不在這裡重複。
    """
    with _read_only_conn(bridge_db) as conn:
        row = conn.execute(
            "SELECT event_id, session_id, import_status FROM bridge_sessions "
            "WHERE event_id=?", (event_id,)).fetchone()
    if row is None:
        raise ValueError(
            f"--event-id {event_id!r} 在 bridge_state.db 找不到對應的列——"
            "不建 job；請確認 event_id 是否正確（完整格式 "
            "hermes:<sid>:<first>..<last> 或 legacy hermes:<sid>）")
    if row["import_status"] not in CANDIDATE_STATUSES:
        raise ValueError(
            f"--event-id {event_id!r} 的 import_status="
            f"{row['import_status']!r} 不具候選資格（合格狀態："
            f"{'、'.join(CANDIDATE_STATUSES)}）——不建 job；若認為應可 "
            "triage，請先人工排查（必要時跑 bridge_scanner.py reconcile）")
    return dict(row)


def _artifact_matches(candidate: Path, *, episode: bool, session_id: str,
                      first: int | None, last: int | None,
                      event_id: str) -> bool:
    """單一檔案是否對應這個 event_id。

    比對依據（§7.4）：deterministic 檔名或 frontmatter event_id_range——
    規則與 adapter._find_existing_import() 逐條相同（檔名解析與 frontmatter
    讀取直接共用 adapter 的單一實作，不自己維護第二份 regex）；差異只在
    呼叫端：這裡要「數出所有相符檔案」以判定唯一性（§6.5 條件 2），
    _find_existing_import 只回傳第一個。
    """
    parsed = HermesSessionAdapter.parse_inbox_filename(candidate.name)
    if episode:
        if (parsed and parsed["session_id"] == session_id
                and parsed["first_message_id"] == first
                and parsed["last_message_id"] == last):
            return True
        fm = HermesSessionAdapter._frontmatter_fields(candidate)
        return fm.get("event_id_range") == event_id
    if (parsed and parsed["session_id"] == session_id
            and parsed["first_message_id"] is None):
        return True
    fm = HermesSessionAdapter._frontmatter_fields(candidate)
    return (fm.get("session_id") == session_id
            and ".." not in (fm.get("event_id_range") or ""))


def locate_artifact(inbox_dir: Path, event_id: str) -> dict:
    """§6.5 條件 2：在三個目錄中「唯一」定位 artifact。

    回傳 dict：
    - {"status": "ok", "path": Path, "matches": [Path]}         恰好一個
    - {"status": "not_found", "matches": [], "reason": str}     找不到
    - {"status": "ambiguous", "matches": [...], "reason": str}  超過一個
    - {"status": "unsupported", "matches": [], "reason": str}   event_id
      無法解析或不是 episode/legacy session 層級（fail-closed，不猜）
    """
    try:
        parsed = bridge_state.parse_event_id(event_id)
    except ValueError as exc:
        return {"status": "unsupported", "path": None, "matches": [],
                "reason": f"event_id 無法解析（fail-closed，不列入候選）：{exc}"}
    if parsed["kind"] == "message":
        return {"status": "unsupported", "path": None, "matches": [],
                "reason": (f"event_id {event_id!r} 是訊息層級——artifact 定位"
                           "規則只定義了 episode 與 legacy session 兩種層級"
                           "（§7.4），fail-closed 不列入候選，請人工排查")}
    episode = parsed["kind"] == "episode"
    sid = parsed["session_id"]
    first = parsed.get("first_message_id")
    last = parsed.get("last_message_id")

    searched: list[str] = []
    matches: list[Path] = []
    for sub in _SEARCH_SUBDIRS:
        directory = inbox_dir if sub == "" else inbox_dir / sub
        searched.append(_rel_to_root(directory))
        if not directory.is_dir():
            continue
        for candidate in sorted(directory.glob("*.md")):
            if _artifact_matches(candidate, episode=episode, session_id=sid,
                                 first=first, last=last, event_id=event_id):
                matches.append(candidate)
    if len(matches) == 1:
        return {"status": "ok", "path": matches[0], "matches": matches}
    if not matches:
        return {"status": "not_found", "path": None, "matches": [],
                "reason": (f"artifact 在 {'、'.join(searched)} 皆定位不到"
                           "——不列入合格候選；請人工確認檔案是否被移動/刪除，"
                           "必要時先跑 bridge_scanner.py reconcile 對帳")}
    listed = "、".join(_rel_to_root(m) for m in matches)
    return {"status": "ambiguous", "path": None, "matches": matches,
            "reason": (f"artifact 同時在多處定位到（{listed}）——唯一性不成立，"
                       "不列入合格候選；請人工排查重複檔案（正常流程下同一 "
                       "episode 只該存在於三個目錄之一）")}


def compute_payload_hash(artifact_path: Path) -> str:
    """payload_hash＝artifact 檔案內容（raw bytes）的 SHA-256 hex digest
    （§6.6 步驟 1；2.5c 執行時以同一定義重算比對，§7.4）。"""
    return hashlib.sha256(artifact_path.read_bytes()).hexdigest()


def build_prompt(event_id: str, prompt_version: str) -> str:
    """job.prompt——只含 identity 資訊與執行指引，不含 episode 內容
    （§7.4：jobs.db 不存 episode 全文）。deterministic：同一 identity
    任何時候 enqueue 都得到同一字串。"""
    return (
        f"bridge episode triage（{prompt_version}）：對 episode {event_id} "
        "做唯讀結構化分診。artifact 於執行時依提案 §7.4 重新定位並驗證 "
        "SHA-256；輸出固定 JSON schema（§7.1）。本 job 由 Stage 2.5c 的 "
        "worker source routing 經 invoke_cos_triage.sh 執行。"
    )


def build_payload(event_id: str, artifact_hint: str, payload_hash: str,
                  prompt_version: str) -> dict:
    """§7.4 的 payload 內容：event_id、artifact 位置提示、SHA-256、
    prompt_version——不含 episode 全文。位置提示只是提示（reconcile 未排程，
    檔案可能被 N-gate 移到 .processed/），2.5c 執行時重新定位。"""
    return {"event_id": event_id, "artifact_hint": artifact_hint,
            "payload_hash": payload_hash, "prompt_version": prompt_version}


def _classify_against_jobs(external_key: str, prompt_version: str,
                           payload_hash: str) -> tuple[str, str | None, str | None]:
    """dry-run 用：唯讀查 jobs.db 現況，依 §2.2 三分支邏輯模擬
    `enqueue_once` 的分類（§6.6 步驟 2–3）。回傳
    (category, existing_job_id, existing_payload_hash)。

    jobs.db 不存在、或還沒有 2.5a 的三元組欄位（不可能有任何 triage job）
    → 一律分類 created。零寫入：不建檔、不 migrate、連線 query_only=ON。
    """
    jobs_path = Path(db.DB_PATH)
    if not jobs_path.exists():
        return ("created", None, None)
    with _read_only_conn(jobs_path) as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(jobs)")}
        if not {"external_key", "payload_hash", "prompt_version"} <= cols:
            return ("created", None, None)
        row = conn.execute(
            "SELECT id, payload_hash FROM jobs "
            "WHERE source=? AND external_key=? AND prompt_version=?",
            (SOURCE, external_key, prompt_version),
        ).fetchone()
    if row is None:
        return ("created", None, None)
    if row["payload_hash"] == payload_hash:
        return ("existing", row["id"], row["payload_hash"])
    return ("conflict", row["id"], row["payload_hash"])


def _conflict_advice(event_id: str) -> str:
    return (
        "——同一 identity（同 episode、同 prompt_version）下 artifact 內容"
        "漂移（episode 理論上 immutable），這是需要人工調查的紅旗，不是正常"
        "重新處理路徑：請比對 artifact 現況與既有 job 的 payload；確認為"
        "預期變更後，可用新的 --prompt-version 建立新 job（§2.2），"
        f"不要手動竄改既有 job（event_id={event_id}）"
    )


def enqueue_candidates(*, dry_run: bool = False,
                       bridge_db: Path | str = bridge_state.DEFAULT_DB_PATH,
                       inbox_dir: Path | str = DEFAULT_INBOX_DIR,
                       prompt_version: str = DEFAULT_PROMPT_VERSION,
                       jobs_db: Path | str | None = None,
                       event_id: str | None = None) -> dict:
    """對每個合格候選呼叫（或 dry-run 模擬呼叫）`enqueue_once`（§6.5／§6.6）。

    - jobs_db 非 None 時把 hermes.db 模組的 DB_PATH 指到該路徑（process
      層級生效——給 CLI 沙箱測試/實機驗證用；預設用 hermes/jobs.db）。
    - dry_run=True：對 jobs.db 零寫入（不建檔、不 migrate），但對每個候選
      執行與真實模式完全相同的候選查詢、hash 計算、三分支分類。
    - bridge_state.db 兩種模式下都唯讀；不存在時直接回報（不建檔）。
    - event_id 非 None（2.6a `--event-id` 單筆模式）：只處理該筆候選；
      查無此列或 import_status 不具資格 → ValueError（fail-visible，
      不靜默略過）；後續分類/寫入邏輯與整批模式完全共用（含 dry-run）。
    - 回傳 dict：mode/dry_run/prompt_version/bridge_db_exists/event_id_filter/
      candidates/items（逐筆：event_id/category/display/label/artifact/job_id）/
      counts（created/existing/conflict/ineligible）。
    """
    if jobs_db is not None:
        db.DB_PATH = Path(jobs_db)
    bridge_db = Path(bridge_db)
    inbox_dir = Path(inbox_dir)
    result: dict = {
        "mode": "enqueue", "dry_run": dry_run, "prompt_version": prompt_version,
        "bridge_db_exists": bridge_db.exists(), "event_id_filter": event_id,
        "candidates": 0, "items": [],
        "counts": {"created": 0, "existing": 0, "conflict": 0, "ineligible": 0},
    }
    if not bridge_db.exists():
        if event_id is not None:
            raise FileNotFoundError(
                f"--event-id 指定了單筆 enqueue，但 bridge db 不存在："
                f"{bridge_db}（不建立 db 檔）")
        return result
    if not inbox_dir.is_dir():
        raise FileNotFoundError(
            f"inbox 目錄不存在：{inbox_dir}（不代建目錄，避免對錯位置定位 artifact）")

    if event_id is not None:
        candidates = [_lookup_single_candidate(bridge_db, event_id)]
    else:
        candidates = list_candidates(bridge_db)
    result["candidates"] = len(candidates)
    if candidates and not dry_run:
        db.init_db()  # 2.5a 的冪等 migration——真實模式寫入前確保 schema

    display = _DISPLAY[dry_run]
    for cand in candidates:
        event_id = cand["event_id"]
        item: dict = {"event_id": event_id, "import_status": cand["import_status"],
                      "artifact": None, "job_id": None}
        loc = locate_artifact(inbox_dir, event_id)
        if loc["status"] != "ok":
            item.update(category="ineligible", display=display["ineligible"],
                        label=loc["reason"])
            result["items"].append(item)
            result["counts"]["ineligible"] += 1
            continue
        artifact_path = loc["path"]
        artifact_rel = _rel_to_root(artifact_path)
        payload_hash = compute_payload_hash(artifact_path)
        item["artifact"] = artifact_rel

        if dry_run:
            category, existing_id, existing_hash = _classify_against_jobs(
                event_id, prompt_version, payload_hash)
            if category == "created":
                label = f"將新建 job（artifact={artifact_rel}）"
            elif category == "existing":
                item["job_id"] = existing_id
                label = f"既有 job {existing_id} 且 payload_hash 相符——將是 no-op"
            else:
                item["job_id"] = existing_id
                label = (f"既有 job {existing_id} 的 payload_hash={existing_hash!r} "
                         f"與本次 artifact 的 {payload_hash!r} 不同，真跑將 fail "
                         f"closed（TriageEnqueueConflict）{_conflict_advice(event_id)}")
        else:
            try:
                job_id, created = db.enqueue_once(
                    SOURCE, event_id, prompt_version, payload_hash,
                    build_prompt(event_id, prompt_version),
                    payload=build_payload(event_id, artifact_rel,
                                          payload_hash, prompt_version),
                )
                item["job_id"] = job_id
                if created:
                    category = "created"
                    label = f"job {job_id}（artifact={artifact_rel}）"
                else:
                    category = "existing"
                    label = f"既有 job {job_id} 且 payload_hash 相符——no-op，未建第二筆"
            except db.TriageEnqueueConflict as exc:
                category = "conflict"
                label = f"{exc}{_conflict_advice(event_id)}"
        item.update(category=category, display=display[category], label=label)
        result["items"].append(item)
        result["counts"][category] += 1
    return result


# ---------- CLI ----------

def _print_result(result: dict):
    prefix = "[dry-run] " if result["dry_run"] else ""
    if not result["bridge_db_exists"]:
        print(f"{prefix}bridge db 不存在——沒有候選 episode 可處理（不建立 db 檔）")
        return
    for item in result["items"]:
        print(f"{prefix}{item['display']:<26} {item['event_id']}  "
              f"{item.get('label') or ''}")
    if not result["items"]:
        print(f"{prefix}(沒有候選 episode——bridge_state.db 沒有 "
              "to_inbox/imported 的列)")
    c = result["counts"]
    tail = ("（dry-run，jobs.db 零寫入）" if result["dry_run"] else "")
    print(f"{prefix}enqueue 完成：候選 {result['candidates']} 筆｜"
          f"created={c['created']}、existing={c['existing']}、"
          f"conflict={c['conflict']}、ineligible={c['ineligible']}"
          f"｜prompt_version={result['prompt_version']}{tail}")
    if c["conflict"]:
        print(f"{prefix}注意：{c['conflict']} 筆 CONFLICT（payload_hash 漂移）"
              "需要人工調查——見上方逐筆訊息", file=sys.stderr)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="hermes/bridge_triage_enqueuer.py — Stage 2.5b 手動 "
                    "enqueuer：把 bridge_state 裡合格的候選 episode enqueue 成 "
                    "bridge_episode_triage job（人工觸發；不呼叫模型、不排程、"
                    "不執行 triage）",
        epilog="dead_letter 重跑的唯一路徑：python3 hermes/db.py requeue "
               "<job_id> --actor <identifier> [--reason \"...\"]（--actor 必填、"
               "沒有任何預設值——本 CLI 不重複實作 requeue）。exit code：0 成功"
               "且無 conflict；1 執行期錯誤；2 參數用法錯誤；3 有 conflict "
               "（payload_hash 漂移，需人工調查）。")
    parser.add_argument(
        "--bridge-db", default=None,
        help=f"bridge_state.db 路徑（預設 {bridge_state.DEFAULT_DB_PATH}；一律唯讀）")
    parser.add_argument(
        "--jobs-db", default=None,
        help=f"jobs.db 路徑（預設 {db.DB_PATH}；dry-run 唯讀、真跑經 "
             "enqueue_once 寫入）")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_enq = sub.add_parser(
        "enqueue", help="列候選並對每筆呼叫（或 --dry-run 模擬）enqueue_once")
    p_enq.add_argument("--dry-run", action="store_true",
                       help="jobs.db 零寫入；仍執行完整三分支分類（§6.6）")
    p_enq.add_argument("--inbox", default=str(DEFAULT_INBOX_DIR),
                       help=f"inbox 目錄（預設 {DEFAULT_INBOX_DIR}）")
    p_enq.add_argument("--prompt-version", default=DEFAULT_PROMPT_VERSION,
                       help=f"triage 契約版本字串（預設 {DEFAULT_PROMPT_VERSION}；"
                            "升版對同一 episode 會建立新 job，§2.2）")
    p_enq.add_argument("--event-id", default=None,
                       help="只 enqueue 指定 event_id 的單筆候選（2.6a；與 "
                            "--dry-run 相容）。查無此列或狀態不具候選資格 → "
                            "exit 1，不建 job")
    return parser


def _cli(argv=None) -> int:
    # Windows console 預設 cp950——比照 scanner/importer，stdout/stderr 強制 UTF-8
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    args = _build_parser().parse_args(argv)
    bridge_db = (Path(args.bridge_db) if args.bridge_db
                 else bridge_state.DEFAULT_DB_PATH)
    try:
        result = enqueue_candidates(
            dry_run=args.dry_run, bridge_db=bridge_db,
            inbox_dir=Path(args.inbox), prompt_version=args.prompt_version,
            jobs_db=Path(args.jobs_db) if args.jobs_db else None,
            event_id=args.event_id)
    except (ValueError, FileNotFoundError, sqlite3.Error) as exc:
        print(f"錯誤：{exc}", file=sys.stderr)
        return 1
    _print_result(result)
    return 3 if result["counts"]["conflict"] else 0


if __name__ == "__main__":
    sys.exit(_cli())
