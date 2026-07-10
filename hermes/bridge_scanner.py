#!/usr/bin/env python3
"""hermes/bridge_scanner.py — v0.1（Stage 2.3）

Stage 2 session bridge 的「偵測與記錄」層：找出 Hermes 新完結的 session，
把處理狀態寫進 bridge_state.db（經 hermes/bridge_state.py 的 repository API）。
**只做偵測與記錄**：不匯入 inbox、不 enqueue job、絕不寫回 Hermes state.db。

兩個子指令（職責分離，互不重疊）：

- ``scan``：讀 Hermes state.db（一律經 HermesSessionAdapter 的 snapshot 模式），
  找出 cutover（--since，含 cutover 當下）之後「已完結」（ended_at 已設）的
  session。首次看到 → upsert ``import_status=discovered``（first_seen_at 從
  首次發現起算，不失真）；已有記錄 → 只呼叫 touch_last_seen() 更新
  last_seen_at，**絕不把 imported/failed/skipped/needs_review 等既有狀態
  重設回 discovered**，也不動 decision_reason / retry_count / updated_at。
- ``reconcile``：掃 memory/inbox/ 本層＋.processed/＋.failed/，依
  「目錄位置是 inbox 檔案狀態的唯一真相」（docs/memory-taxonomy.md §5）回填：
  inbox 本層 → to_inbox、.processed/ → imported、.failed/ → failed。
  對帳依據優先用 frontmatter 的 session_id / event_id_range；無 frontmatter
  （舊時間戳檔名格式的歸檔）退回檔名比對，依據記錄在 decision_reason。
  processed_path 與 db 記錄不一致時，以 .processed/ 實際位置為準回寫。

安全預設（硬條件，測試逐條把關）：
- scan 必須明確指定 --since <ISO8601> 或 --all-history 其中一個，兩者互斥；
  **什麼參數都不給就掃全部歷史是被禁止的**（CLI 直接 usage error exit 2，
  API 丟 ValueError）。--since 為含端點的 cutover：ended_at >= since 才撈。
- 讀 Hermes state.db 只走 HermesSessionAdapter(snapshot=True)：先把 db
  （含 -wal/-shm）複製到 temp 再讀副本，絕不對正在寫入的 live db 直接開連線。
  本模組完全不 import sqlite3，也沒有任何 immutable 連線參數的 code path
  （對 Windows 上正被 Hermes 寫入的 db 開 immutable 會讀到不一致 snapshot）
  ——這兩點由測試靜態把關。
- --dry-run 只印出將要執行的動作摘要，不寫入任何檔案；bridge db 不存在時
  連 db 檔都不建立。
- 本模組對 bridge_state.db 的所有讀寫都經 hermes/bridge_state.py 的 API，
  該層唯一的連線入口只開 bridge 自己的 db。

retry_count 備忘（Stage 2.2 既定語義）：scan/reconcile 都不是「re-attempt」，
所以本模組**不呼叫** increment_retry_count()——那要等之後的匯入重試流程。

CLI（exit code：0 成功；1 執行期錯誤；2 參數用法錯誤）：
    python3 hermes/bridge_scanner.py [--bridge-db PATH] scan \
        (--since ISO8601 | --all-history) [--dry-run] \
        [--state-db PATH] [--source-profile NAME]
    python3 hermes/bridge_scanner.py [--bridge-db PATH] reconcile \
        [--dry-run] [--inbox DIR] [--source-profile NAME]
"""
import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

_HERMES_DIR = Path(__file__).resolve().parent
ROOT = _HERMES_DIR.parent
for _p in (_HERMES_DIR, _HERMES_DIR / "session_adapter"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import bridge_state  # noqa: E402
from adapter import HermesSessionAdapter, HermesSessionReadError  # noqa: E402

DEFAULT_INBOX_DIR = ROOT / "memory" / "inbox"

# 檔名退回比對：涵蓋新格式 hermes_session_<id>.md 與
# 舊時間戳格式 <stamp>_hermes_session_<id>.md（與 adapter 的去重掃描同一慣例）
_FILENAME_RE = re.compile(r"(?:^|_)hermes_session_(?P<sid>.+)\.md$")

# inbox frontmatter（claudecodeos.inbox.v1）中 hermes session 的 source 標記；
# frontmatter 有 session_id 但 source 是別的來源時，不當成 hermes session 對帳
_INBOX_FRONTMATTER_SOURCE = "hermes-session"

# reconcile 掃描的目錄 → 對應 import_status（目錄位置是唯一真相）
_DIR_STATUS = (("", "to_inbox"), (".processed", "imported"), (".failed", "failed"))

# 同一 session 出現在多個目錄時的狀態優先序（force 重匯會造成 inbox 本層與
# .processed/ 並存——本層有檔案代表「又在等整併」，取 to_inbox，
# 但 processed_path 仍記 .processed/ 的實際位置）
_STATUS_PRECEDENCE = ("to_inbox", "imported", "failed")

_LOCATION_LABEL = {
    "to_inbox": "inbox 本層（待整併）",
    "imported": ".processed/（已整併歸檔）",
    "failed": ".failed/（失敗歸檔）",
}

_FAILED_BACKFILL_REASON = (
    "bridge reconcile 回填：檔案位於 .failed/，原始錯誤原因無法自檔案系統還原"
)


def _parse_iso_utc(value: str) -> datetime:
    """ISO 8601 → aware UTC datetime。接受 'Z' 結尾；naive 視為 UTC。"""
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"無法解析 ISO 8601 時間：{value!r}（{exc}）") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _rel_to_root(path: Path | str) -> str:
    """repo 內的路徑記相對路徑（posix），repo 外（測試 temp 目錄）記絕對路徑。"""
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError:
        return str(resolved)


def _read_frontmatter(path: Path) -> dict:
    """讀檔案開頭 YAML frontmatter 的 session_id / source / event_id_range。
    只掃前 60 行；讀不到、沒有 frontmatter、格式不對都回空 dict（容錯）。"""
    fields: dict = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            if fh.readline().strip() != "---":
                return {}
            for _ in range(60):
                line = fh.readline()
                if not line or line.strip() == "---":
                    break
                for key in ("session_id", "source", "event_id_range"):
                    if line.startswith(key + ":"):
                        fields[key] = line.split(":", 1)[1].strip().strip("\"'")
    except OSError:
        return {}
    return fields


def _body_session_source(path: Path) -> str | None:
    """從 adapter 產生的內文找「- 來源：hermes/<session_source>」（前 120 行）。
    找不到回 None（reconcile 回填時 session_source 退成 'unknown'）。"""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for _ in range(120):
                line = fh.readline()
                if not line:
                    break
                stripped = line.strip()
                if stripped.startswith("- 來源：hermes/"):
                    return stripped.split("hermes/", 1)[1].strip() or None
    except OSError:
        pass
    return None


# ---------- scan ----------

def scan(
    *,
    since: str | None = None,
    all_history: bool = False,
    dry_run: bool = False,
    state_db: Path | str | None = None,
    bridge_db: Path | str = bridge_state.DEFAULT_DB_PATH,
    source_profile: str = "default",
    seen_at: str | None = None,
) -> dict:
    """偵測 cutover 後新完結的 Hermes session → upsert discovered。

    - since / all_history 恰好指定一個（安全預設：都不給就報錯，絕不掃全歷史）。
    - ended_at 為 NULL（未完結）或壞值（無法解析）的 session 一律不撈。
    - 已有記錄的 session 只 touch last_seen_at，既有狀態原封不動（硬條件）。
    - dry_run=True：只回報將執行的動作，不寫入；bridge db 不存在時不建檔。
    """
    if since and all_history:
        raise ValueError("--since 與 --all-history 互斥，只能指定一個")
    if not since and not all_history:
        raise ValueError(
            "安全預設：scan 必須指定 --since <ISO8601>（cutover，含當下）"
            "或明確的 --all-history——什麼都不給就掃全部歷史是被禁止的"
        )
    cutover = _parse_iso_utc(since) if since else None

    with HermesSessionAdapter(db_path=state_db, snapshot=True) as adapter:
        sessions = adapter.list_sessions()

    bridge_db = Path(bridge_db)
    if not dry_run:
        bridge_state.ensure_schema(bridge_db)
    db_readable = bridge_db.exists()  # dry-run 且 db 不存在時，連檔案都不建立

    actions: list[dict] = []
    candidates = 0
    for sess in sessions:
        ended = sess["ended_at"]
        if not ended:
            continue  # 未完結（ended_at NULL 或壞值）不撈
        if cutover is not None and _parse_iso_utc(ended) < cutover:
            continue  # cutover 之前完結的不撈（>= since 含端點）
        candidates += 1
        sid = sess["session_id"]
        event_id = bridge_state.session_event_id(sid)
        existing = (
            bridge_state.get_session_state(event_id, db_path=bridge_db)
            if db_readable else None
        )
        if existing is None:
            action = {"action": "insert_discovered", "event_id": event_id,
                      "session_id": sid, "ended_at": ended}
            if not dry_run:
                bridge_state.upsert_session_state(
                    session_id=sid,
                    source_profile=source_profile,
                    session_source=sess["session_source"] or "unknown",
                    import_status="discovered",
                    memory_type="none",
                    useful_chat=False,
                    decision_reason=(
                        f"bridge scan 首次發現新完結 session（ended_at={ended}），尚未判定"
                    ),
                    seen_at=seen_at,
                    db_path=bridge_db,
                )
        else:
            action = {"action": "touch_last_seen", "event_id": event_id,
                      "session_id": sid, "ended_at": ended,
                      "import_status": existing["import_status"]}
            if not dry_run:
                bridge_state.touch_last_seen(event_id, seen_at=seen_at,
                                             db_path=bridge_db)
        actions.append(action)

    return {"mode": "scan", "dry_run": dry_run,
            "sessions_seen": len(sessions), "candidates": candidates,
            "bridge_db_exists": db_readable, "actions": actions}


# ---------- reconcile ----------

def reconcile(
    *,
    inbox_dir: Path | str = DEFAULT_INBOX_DIR,
    dry_run: bool = False,
    bridge_db: Path | str = bridge_state.DEFAULT_DB_PATH,
    source_profile: str = "default",
    seen_at: str | None = None,
) -> dict:
    """掃 inbox 本層＋.processed/＋.failed/，依目錄位置回填 bridge 狀態。

    - 對帳依據：frontmatter session_id 優先；無 frontmatter 退回檔名比對，
      依據寫進 decision_reason。認不出 session 的檔案記 skip_unrecognized，
      不寫任何記錄。
    - 已有記錄且狀態與目錄一致 → 只 touch last_seen_at（既有判定原封不動）；
      processed_path 不一致 → 以 .processed/ 實際位置為準回寫（其餘欄位保留）；
      狀態與目錄不一致 → 以目錄位置為準更新狀態（永不產生 discovered，
      first_seen_at / retry_count 由 repository 層保證不被洗掉）。
    - 回填的新記錄不推斷 memory_type（保持 none）；useful_chat 只在 imported
      （已被 consolidation 接受，事實上有用）時記 true。
    """
    inbox_dir = Path(inbox_dir)
    if not inbox_dir.is_dir():
        raise FileNotFoundError(f"inbox 目錄不存在：{inbox_dir}")

    entries: dict[str, dict] = {}
    skipped: list[str] = []
    files_seen = 0
    for subdir, status in _DIR_STATUS:
        directory = inbox_dir / subdir if subdir else inbox_dir
        if not directory.is_dir():
            continue
        for candidate in sorted(directory.glob("*.md")):
            files_seen += 1
            fm = _read_frontmatter(candidate)
            sid = basis = None
            if fm.get("session_id") and fm.get("source") in (
                None, _INBOX_FRONTMATTER_SOURCE
            ):
                sid = fm["session_id"]
                basis = "frontmatter session_id"
            else:
                match = _FILENAME_RE.search(candidate.name)
                if match:
                    sid = match.group("sid")
                    basis = "檔名比對（檔案無 frontmatter session_id）"
            if sid is None:
                skipped.append(str(candidate))
                continue
            entry = entries.setdefault(
                sid, {"by_status": {}, "event_id_range": None, "session_source": None})
            entry["by_status"].setdefault(status, (candidate, basis))
            if entry["event_id_range"] is None and fm.get("event_id_range"):
                entry["event_id_range"] = fm["event_id_range"]
            if entry["session_source"] is None:
                entry["session_source"] = _body_session_source(candidate)

    bridge_db = Path(bridge_db)
    if not dry_run:
        bridge_state.ensure_schema(bridge_db)
    db_readable = bridge_db.exists()

    actions: list[dict] = [
        {"action": "skip_unrecognized", "path": p} for p in skipped]
    for sid, entry in sorted(entries.items()):
        status = next(s for s in _STATUS_PRECEDENCE if s in entry["by_status"])
        path, basis = entry["by_status"][status]
        processed_hit = entry["by_status"].get("imported")
        processed_path = _rel_to_root(processed_hit[0]) if processed_hit else None
        if status == "to_inbox":
            inbox_path = _rel_to_root(path)
        elif status == "imported":
            # 落地檔名不變地被移入 .processed/，回推原 inbox 落地路徑
            inbox_path = _rel_to_root(inbox_dir / path.name)
        else:
            inbox_path = None
        event_id = bridge_state.session_event_id(sid)
        existing = (
            bridge_state.get_session_state(event_id, db_path=bridge_db)
            if db_readable else None
        )
        reason = (f"bridge reconcile：檔案位於 {_LOCATION_LABEL[status]}，"
                  f"session_id 依據：{basis}")

        if existing is None:
            action_name = f"insert_{status}"
            if not dry_run:
                bridge_state.upsert_session_state(
                    session_id=sid,
                    source_profile=source_profile,
                    session_source=entry["session_source"] or "unknown",
                    import_status=status,
                    memory_type="none",
                    useful_chat=(status == "imported"),
                    decision_reason=reason,
                    imported_inbox_path=inbox_path,
                    processed_path=processed_path,
                    error_reason=(_FAILED_BACKFILL_REASON
                                  if status == "failed" else None),
                    event_id_range=entry["event_id_range"],
                    seen_at=seen_at,
                    db_path=bridge_db,
                )
        else:
            same_status = existing["import_status"] == status
            needs_path_fix = bool(processed_path) and (
                existing["processed_path"] != processed_path)
            if same_status and not needs_path_fix:
                action_name = "touch_last_seen"
                if not dry_run:
                    bridge_state.touch_last_seen(event_id, seen_at=seen_at,
                                                 db_path=bridge_db)
            else:
                action_name = ("fix_processed_path" if same_status
                               else f"update_to_{status}")
                if not dry_run:
                    bridge_state.upsert_session_state(
                        session_id=existing["session_id"],
                        source_profile=existing["source_profile"],
                        session_source=existing["session_source"],
                        import_status=status,
                        memory_type=existing["memory_type"],
                        useful_chat=existing["useful_chat"],
                        selected_capability_lane=existing["selected_capability_lane"],
                        decision_reason=(existing["decision_reason"]
                                         if same_status else reason),
                        imported_inbox_path=(inbox_path
                                             or existing["imported_inbox_path"]),
                        processed_path=(processed_path
                                        or existing["processed_path"]),
                        error_reason=(
                            existing["error_reason"] if same_status
                            else (_FAILED_BACKFILL_REASON
                                  if status == "failed" else None)),
                        event_id_range=(entry["event_id_range"]
                                        or existing["event_id_range"]),
                        seen_at=seen_at,
                        db_path=bridge_db,
                    )
        actions.append({"action": action_name, "event_id": event_id,
                        "session_id": sid, "path": str(path), "basis": basis})

    return {"mode": "reconcile", "dry_run": dry_run, "files_seen": files_seen,
            "bridge_db_exists": db_readable, "actions": actions}


# ---------- CLI ----------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="hermes/bridge_scanner.py — 偵測 Hermes 新完結 session 並記錄"
                    "到 bridge_state.db（只偵測與記錄，不匯入、不 enqueue）")
    parser.add_argument(
        "--bridge-db", default=None,
        help=f"bridge_state.db 路徑（預設 {bridge_state.DEFAULT_DB_PATH}）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_scan = sub.add_parser(
        "scan", help="偵測 cutover 後新完結的 session → upsert discovered")
    group = p_scan.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--since", default=None, metavar="ISO8601",
        help="cutover 時間（含當下；ended_at >= since 才撈）。與 --all-history 互斥")
    group.add_argument(
        "--all-history", action="store_true",
        help="明確要求掃全部歷史完結 session（預設禁止全掃，必須明確指定）")
    p_scan.add_argument("--dry-run", action="store_true",
                        help="只印出將執行的動作，不寫入任何檔案")
    p_scan.add_argument(
        "--state-db", default=None,
        help="Hermes state.db 路徑（預設自動偵測；一律 snapshot 讀 temp 副本）")
    p_scan.add_argument("--source-profile", default="default",
                        help="來源 Hermes profile（本階段只支援主 db，預設 default）")

    p_rec = sub.add_parser(
        "reconcile", help="掃 inbox 本層＋.processed/＋.failed/ 回填既有狀態")
    p_rec.add_argument("--dry-run", action="store_true",
                       help="只印出將執行的動作，不寫入任何檔案")
    p_rec.add_argument("--inbox", default=str(DEFAULT_INBOX_DIR),
                       help=f"inbox 目錄（預設 {DEFAULT_INBOX_DIR}）")
    p_rec.add_argument("--source-profile", default="default",
                       help="回填新記錄時使用的 source_profile（預設 default）")
    return parser


def _print_result(result: dict):
    prefix = "[dry-run] " if result["dry_run"] else ""
    for action in result["actions"]:
        target = action.get("event_id") or action.get("path")
        extra = ""
        if action.get("import_status"):
            extra += f"（既有狀態 {action['import_status']} 不變）"
        if action.get("basis"):
            extra += f"｜依據：{action['basis']}"
        print(f"{prefix}{action['action']:<20} {target}{extra}")
    if not result["actions"]:
        print(f"{prefix}(沒有需要處理的項目)")
    tail = "（dry-run，未寫入任何檔案）" if result["dry_run"] else ""
    if result["dry_run"] and not result["bridge_db_exists"]:
        tail += "（bridge db 尚不存在，所有既有狀態查詢視為無記錄，也不建立 db 檔）"
    if result["mode"] == "scan":
        print(f"{prefix}scan 完成：檢視 {result['sessions_seen']} 個 session，"
              f"符合條件的完結 session {result['candidates']} 個，"
              f"動作 {len(result['actions'])} 筆{tail}")
    else:
        print(f"{prefix}reconcile 完成：檢視 {result['files_seen']} 個檔案，"
              f"動作 {len(result['actions'])} 筆{tail}")


def _cli(argv=None) -> int:
    # Windows console 預設 cp950——比照 adapter，stdout/stderr 強制 UTF-8
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    args = _build_parser().parse_args(argv)
    bridge_db = (Path(args.bridge_db) if args.bridge_db
                 else bridge_state.DEFAULT_DB_PATH)
    try:
        if args.cmd == "scan":
            result = scan(since=args.since, all_history=args.all_history,
                          dry_run=args.dry_run, state_db=args.state_db,
                          bridge_db=bridge_db,
                          source_profile=args.source_profile)
        else:
            result = reconcile(inbox_dir=Path(args.inbox), dry_run=args.dry_run,
                               bridge_db=bridge_db,
                               source_profile=args.source_profile)
    except (ValueError, FileNotFoundError, HermesSessionReadError) as exc:
        print(f"錯誤：{exc}", file=sys.stderr)
        return 1
    _print_result(result)
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
