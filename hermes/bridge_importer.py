#!/usr/bin/env python3
"""hermes/bridge_importer.py — v0.1（Stage 2.4c）

Stage 2 session bridge 的「判定與匯入」層：把 bridge_state.db 中
import_status=discovered 的 session（scanner 的產出）依政策判定送進
memory/inbox/，並回寫判定結果。**只做判定與落地**：不 enqueue job、不呼叫
headless CoS、不安裝排程；讀 Hermes state.db 一律經 HermesSessionAdapter 的
snapshot 模式（read-only），絕不寫回。

每個 session 的處理順序（硬約束，測試逐條把關）：

1. **讀取完整內容**：adapter export_session()（snapshot 副本）。
2. **敏感偵測（fail-closed）**：pattern 從 registry/consolidation_policy.yaml
   的 guardrails.sensitive.detection 載入——政策檔讀不到、detection 缺失、
   任一 categories 成員沒有 pattern、pattern 編譯失敗，一律 fail loud
   **整批不跑**（比照 bridge.yaml cutover 慣例：絕不默認放行）。判定對象是
   **完整內容**（含全部訊息、tool 輸出與將落地的完整 render——不能只判截斷
   摘錄，截斷可能把敏感段截掉造成漏判）。命中 → import_status=needs_review：
   **只記類別標籤（sensitive:<category>），決不記命中原文**——decision_reason
   ／error_reason／stdout／stderr 一體適用。選 needs_review 而非 skipped 的
   理由：skipped 語義是「政策判定不值得留」，敏感 session 可能有價值、只是
   headless 無人可確認（taxonomy 4.3 interactive_action=human_confirm）；
   原文永在 Hermes state.db，互動式 session 可人工補匯。
3. **useful 判定（taxonomy 4.2 的結構性排除訊號）**：test_session
   （yaml known_markers＋underscore_prefix_with_test 命名模式）、too_short
   （min_content_chars／min_session_messages 門檻，均取自
   consolidation_policy.yaml）→ import_status=skipped＋exclusion:<label>。
   純閒聊／指令試誤／重複這三種需要語義判斷的排除訊號 v0.1 不自動判，
   留給 consolidate-memory 第二道網（taxonomy 4.2 的既有設計）。
4. **通過者落地**：adapter.write_inbox_file()（deterministic 檔名、只新增
   永不覆寫）→ **落地成功才** upsert import_status=to_inbox（useful_chat=true、
   memory_type=episodic）——「先有檔案、再記狀態」，repository 層對
   to_inbox/imported 必附 inbox 路徑有硬驗證。
5. **檔案已存在（InboxAlreadyImportedError）不是錯誤**：代表前次 DB 更新失敗
   或人工匯過——記 log、DB 維持原狀態，留給 bridge_scanner reconcile 依
   「目錄位置是唯一真相」回填。不當場複製 reconcile 的目錄對帳邏輯，理由：
   回填規則（含 .processed/ 優先序、frontmatter 對帳）只該有一份實作。
   落地成功但 DB 更新失敗同理：檔案已在 inbox，下次 reconcile 回填 to_inbox。
6. **錯誤／判不出來 → fail-closed**：決不落地，mark_failed；error_reason 只記
   階段標籤＋例外類別名（**絕不含 session 內容**，schema 的既有硬約束）。

retry 語義：佇列除了 discovered 也包含前次 failed 的 session（自動重試）；
每次重新嘗試的當下 increment_retry_count（bridge_state 的既定語義），
retry_count 達上限（hermes/config/bridge.yaml 的 max_import_retries，預設 3）
後不再自動嘗試、狀態轉 needs_review。dry-run 不遞增。注意：reconcile 回填的
failed（檔案在 .failed/）也會走重試 → 命中 already-imported → DB 不動，
數次後達上限轉 needs_review——語義正確（消整併失敗本來就該人工看）。

--dry-run 零寫入：不落地、不動 bridge db（db 不存在時連檔案都不建立）、
不遞增 retry_count；只回報將執行的動作（同樣只含類別標籤，不含內容）。

CLI（exit code 慣例同 scanner：0 成功；1 執行期錯誤——含政策檔缺失 fail loud；
2 參數用法錯誤）：
    python3 hermes/bridge_importer.py [--bridge-db PATH] import \
        [--dry-run] [--limit N] [--state-db PATH] [--inbox DIR] \
        [--policy PATH] [--config PATH]
"""
import argparse
import json
import re
import sys
from pathlib import Path

_HERMES_DIR = Path(__file__).resolve().parent
ROOT = _HERMES_DIR.parent
for _p in (_HERMES_DIR, _HERMES_DIR / "session_adapter"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import bridge_state  # noqa: E402
from adapter import (  # noqa: E402
    HermesSessionAdapter,
    HermesSessionReadError,
    InboxAlreadyImportedError,
)

DEFAULT_INBOX_DIR = ROOT / "memory" / "inbox"
DEFAULT_POLICY_PATH = ROOT / "registry" / "consolidation_policy.yaml"
DEFAULT_BRIDGE_CONFIG = _HERMES_DIR / "config" / "bridge.yaml"
DEFAULT_MAX_IMPORT_RETRIES = 3

_UNDERSCORE_TEST_RULE = "underscore_prefix_with_test"


def _rel_to_root(path: Path | str) -> str:
    """repo 內的路徑記相對路徑（posix），repo 外（測試 temp 目錄）記絕對路徑。
    與 bridge_scanner 同一慣例。"""
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError:
        return str(resolved)


# ---------- 政策載入（fail loud：讀不到＝整批不跑） ----------

def load_guardrail_policy(policy_path: Path | str = DEFAULT_POLICY_PATH) -> dict:
    """載入 consolidation_policy.yaml 的 guardrail 參數（機器可讀正本，不寫死）。

    回傳：sensitive_patterns（category → [compiled regex]）、test_markers、
    underscore_rule、min_content_chars、min_session_messages。

    fail loud（硬條件）：政策檔不存在丟 FileNotFoundError；guardrails/usefulness
    欄位缺失、detection 缺失、categories 任一成員沒有 pattern、pattern 編譯失敗
    丟 ValueError——**任何一種情況都不得默認放行**（沒有偵測規則＝無法判定＝
    fail-closed 整批不跑，比照 bridge.yaml cutover 慣例）。
    """
    import yaml  # lazy import：比照 bridge_state / bridge_scanner

    path = Path(policy_path)
    if not path.is_file():
        raise FileNotFoundError(
            f"consolidation 政策檔不存在：{path}——guardrail 偵測 pattern 的唯一"
            "來源（fail loud，整批不跑；正本為 registry/consolidation_policy.yaml）"
        )
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise ValueError(f"政策檔格式不對（頂層不是 mapping）：{path}")
    try:
        sensitive = doc["guardrails"]["sensitive"]
        categories = sensitive["categories"]
        test_detect = doc["guardrails"]["test_session"]["detect"]
        usefulness = doc["usefulness"]
        min_chars = int(usefulness["min_content_chars"])
        min_msgs = int(usefulness["min_session_messages"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            f"政策檔缺少必要欄位或型別不對（{type(exc).__name__}: {exc}）：{path}"
        ) from exc
    detection = sensitive.get("detection")
    if not isinstance(detection, dict):
        raise ValueError(
            f"政策檔缺少 guardrails.sensitive.detection（機器可讀偵測 pattern）：{path}"
            "——沒有 pattern 就無法偵測敏感內容，fail-closed 整批不跑"
        )
    patterns: dict[str, list] = {}
    for cat in categories:
        raw = detection.get(cat)
        if not isinstance(raw, list) or not raw:
            raise ValueError(
                f"敏感類別 {cat!r} 在 detection 裡沒有任何 pattern：{path}"
                "——該類別無法偵測，fail-closed 整批不跑"
            )
        compiled = []
        for pat in raw:
            try:
                compiled.append(re.compile(str(pat)))
            except re.error as exc:
                raise ValueError(
                    f"敏感類別 {cat!r} 的 pattern 無法編譯：{pat!r}（{exc}）"
                ) from exc
        patterns[cat] = compiled

    markers: list[str] = []
    underscore_rule = False
    for entry in test_detect or []:
        if entry == _UNDERSCORE_TEST_RULE:
            underscore_rule = True
        elif isinstance(entry, dict) and isinstance(entry.get("known_markers"), list):
            markers.extend(str(m) for m in entry["known_markers"])
    return {
        "sensitive_patterns": patterns,
        "test_markers": markers,
        "underscore_rule": underscore_rule,
        "min_content_chars": min_chars,
        "min_session_messages": min_msgs,
    }


def load_max_import_retries(config_path: Path | str = DEFAULT_BRIDGE_CONFIG) -> int:
    """讀 bridge 設定檔的 max_import_retries（自動重試上限）。

    設定檔不存在 fail loud（比照 scanner 的 cutover 慣例）；欄位缺失時採
    DEFAULT_MAX_IMPORT_RETRIES（3）——重試上限不是掃描範圍那種安全底線，
    合理預設可接受；欄位存在但不是非負整數丟 ValueError。
    """
    import yaml  # lazy import

    path = Path(config_path)
    if not path.is_file():
        raise FileNotFoundError(
            f"bridge 設定檔不存在：{path}（fail loud；正本為版控的 "
            "hermes/config/bridge.yaml）"
        )
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    value = (doc or {}).get("max_import_retries", DEFAULT_MAX_IMPORT_RETRIES)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"max_import_retries 必須是非負整數：{value!r}")
    return value


# ---------- 判定（純函式；輸出絕不含 session 原文） ----------

def _full_session_text(export: dict) -> str:
    """組出敏感偵測用的「完整內容」：將落地的完整 render（full=True，涵蓋
    frontmatter／session JSON／全部 message 摘錄的同一格式化形式）＋全部
    event 原始 content（含 tool/meta 事件）＋tool_calls 原始資料。
    是「實際會寫出的內容」的超集——寫出的檔案是它的截斷子集，掃它乾淨即安全。"""
    parts = [HermesSessionAdapter._render_markdown(export, 0, full=True)]
    for ev in export["events"]:
        parts.append(str(ev.get("content") or ""))
        md = ev.get("metadata") or {}
        for key in ("tool_calls", "tool_calls_raw", "tool_name", "session_title"):
            val = md.get(key)
            if val:
                parts.append(val if isinstance(val, str)
                             else json.dumps(val, ensure_ascii=False))
    return "\n".join(parts)


def detect_sensitive(full_text: str, sensitive_patterns: dict) -> list[str]:
    """回傳命中的敏感類別清單（sorted）。**只回傳類別名稱，絕不回傳命中片段**。"""
    return sorted(cat for cat, pats in sensitive_patterns.items()
                  if any(p.search(full_text) for p in pats))


def classify_exclusion(export: dict, policy: dict) -> tuple[str, str] | None:
    """taxonomy 4.2 的結構性排除判定。回傳 (label, 說明) 或 None（不排除）。

    只判 test_session 與 too_short（可機械判定）；純閒聊／試誤／重複需要語義
    判斷，v0.1 留給 consolidate-memory 第二道網。說明字串只含政策常數與統計
    數字，不含 session 原文節錄。
    """
    session = export["session"]
    sid = str(session["session_id"])
    title = str(session.get("title") or "")
    all_text = "\n".join(
        [title, sid] + [str(e.get("content") or "") for e in export["events"]])
    for marker in policy["test_markers"]:
        if marker and marker in all_text:
            return ("test_session",
                    f"命中 known_marker {marker!r}（政策常數，非 session 原文節錄）")
    if policy["underscore_rule"]:
        for name in (title, sid):
            if name.startswith("_") and "test" in name.lower():
                return ("test_session", "命中 underscore_prefix_with_test 命名模式")
    message_events = [e for e in export["events"] if e["type"] == "message"]
    content_chars = sum(len(str(e.get("content") or "").strip())
                        for e in message_events)
    if (content_chars < policy["min_content_chars"]
            or len(message_events) < policy["min_session_messages"]):
        return ("too_short",
                f"message 內容 {content_chars} 字元／{len(message_events)} 則，低於門檻 "
                f"{policy['min_content_chars']} 字元／{policy['min_session_messages']} 則"
                "（v0.1 自動判定僅結構性門檻；正面訊號的語義判定留給互動式路徑"
                "或 consolidate-memory）")
    return None


def _event_id_range(export: dict) -> str | None:
    sid = export["session"]["session_id"]
    ids = [e["metadata"]["raw_message_id"] for e in export["events"]
           if isinstance(e.get("metadata"), dict)
           and isinstance(e["metadata"].get("raw_message_id"), int)]
    if not ids:
        return None
    return f"hermes:{sid}:{min(ids)}..{max(ids)}"


# ---------- 匯入主流程 ----------

def _process_one(rec: dict, adapter: HermesSessionAdapter, *, policy: dict,
                 inbox_dir: Path, bridge_db: Path, dry_run: bool,
                 max_retries: int, seen_at: str | None) -> dict:
    event_id = rec["event_id"]
    sid = rec["session_id"]
    base = {"event_id": event_id, "session_id": sid}

    if rec["import_status"] == "failed":  # 重試路徑
        if rec["retry_count"] >= max_retries:
            if not dry_run:
                bridge_state.upsert_session_state(
                    session_id=sid,
                    source_profile=rec["source_profile"],
                    session_source=rec["session_source"],
                    import_status="needs_review",
                    memory_type=rec["memory_type"],
                    useful_chat=rec["useful_chat"],
                    selected_capability_lane=rec["selected_capability_lane"],
                    decision_reason=(
                        f"匯入重試已達上限（retry_count={rec['retry_count']} >= "
                        f"max_import_retries={max_retries}），停止自動重試、"
                        "轉人工檢視（原文仍在 Hermes state.db）"),
                    imported_inbox_path=rec["imported_inbox_path"],
                    processed_path=rec["processed_path"],
                    error_reason=rec["error_reason"],
                    event_id_range=rec["event_id_range"],
                    seen_at=seen_at, db_path=bridge_db)
            return {**base, "action": "retry_exhausted",
                    "label": (f"retry_count={rec['retry_count']} >= "
                              f"{max_retries} → needs_review")}
        if not dry_run:
            bridge_state.increment_retry_count(event_id, db_path=bridge_db)

    # 1. 讀取完整內容（snapshot 副本）
    try:
        export = adapter.export_session(sid)
        full_text = _full_session_text(export)
    except Exception as exc:
        # fail-closed：判不出來＝不匯入。error_reason 只記階段與例外類別名，
        # 絕不引用 str(exc)（例外訊息可能夾帶 session 內容）。
        reason = (f"匯入失敗（階段：export／讀取完整內容；例外類別："
                  f"{type(exc).__name__}）——fail-closed，不落地")
        if not dry_run:
            bridge_state.mark_failed(event_id, reason, db_path=bridge_db)
        return {**base, "action": "failed",
                "label": f"export_error:{type(exc).__name__}"}

    # 2. 敏感偵測（fail-closed；只記類別標籤）
    try:
        hits = detect_sensitive(full_text, policy["sensitive_patterns"])
    except Exception as exc:
        reason = (f"匯入失敗（階段：敏感偵測；例外類別：{type(exc).__name__}）"
                  "——無法判定＝不匯入（fail-closed），不落地")
        if not dry_run:
            bridge_state.mark_failed(event_id, reason, db_path=bridge_db)
        return {**base, "action": "failed",
                "label": f"detect_error:{type(exc).__name__}"}
    if hits:
        labels = ", ".join(f"sensitive:{c}" for c in hits)
        if not dry_run:
            bridge_state.upsert_session_state(
                session_id=sid,
                source_profile=rec["source_profile"],
                session_source=rec["session_source"],
                import_status="needs_review",
                memory_type="none",
                useful_chat=False,
                decision_reason=(
                    f"guardrail 命中 {labels}——headless fail-closed（taxonomy "
                    "4.3）：不落地、不節錄；只記類別標籤，不記命中原文；原文仍在 "
                    "Hermes state.db，可由互動式 session 人工確認補匯"),
                error_reason=rec["error_reason"],
                event_id_range=rec["event_id_range"],
                seen_at=seen_at, db_path=bridge_db)
        return {**base, "action": "blocked_sensitive", "label": labels}

    # 3. taxonomy 4.2 結構性排除訊號
    exclusion = classify_exclusion(export, policy)
    if exclusion:
        label, detail = exclusion
        if not dry_run:
            bridge_state.upsert_session_state(
                session_id=sid,
                source_profile=rec["source_profile"],
                session_source=rec["session_source"],
                import_status="skipped",
                memory_type="none",
                useful_chat=False,
                decision_reason=f"taxonomy 4.2 排除訊號 exclusion:{label}——{detail}",
                error_reason=rec["error_reason"],
                event_id_range=rec["event_id_range"],
                seen_at=seen_at, db_path=bridge_db)
        return {**base, "action": "skipped_exclusion", "label": f"exclusion:{label}"}

    # 4. 落地（先有檔案、再記狀態）
    if dry_run:
        existing = adapter._find_existing_import(inbox_dir, sid)
        if existing is not None:
            return {**base, "action": "already_imported_defer_reconcile",
                    "label": _rel_to_root(existing)}
        return {**base, "action": "to_inbox",
                "label": "（dry-run：將落地並記 to_inbox）"}
    try:
        path = adapter.write_inbox_file(export, inbox_dir)
    except InboxAlreadyImportedError as exc:
        # 不是錯誤：前次 DB 更新失敗或人工匯過——DB 不動，留給 reconcile
        # 依「目錄位置是唯一真相」回填（回填邏輯只有 reconcile 一份實作）。
        return {**base, "action": "already_imported_defer_reconcile",
                "label": _rel_to_root(exc.existing_path)}
    except Exception as exc:
        reason = (f"匯入失敗（階段：inbox 落地；例外類別：{type(exc).__name__}）")
        bridge_state.mark_failed(event_id, reason, db_path=bridge_db)
        return {**base, "action": "failed",
                "label": f"write_error:{type(exc).__name__}"}
    try:
        bridge_state.upsert_session_state(
            session_id=sid,
            source_profile=rec["source_profile"],
            session_source=rec["session_source"],
            import_status="to_inbox",
            memory_type="episodic",
            useful_chat=True,
            decision_reason=(
                "bridge importer：無敏感命中、無 4.2 結構性排除訊號，已落地 inbox "
                "等待 consolidation（語義判定的第二道網在 consolidate-memory）"),
            imported_inbox_path=_rel_to_root(path),
            event_id_range=_event_id_range(export) or rec["event_id_range"],
            seen_at=seen_at, db_path=bridge_db)
    except Exception as exc:
        # 檔案已落地、狀態未更新：可由 reconcile 恢復（to_inbox 回填），不中斷整批
        return {**base, "action": "db_update_failed_recoverable",
                "label": (f"檔案已落地（{_rel_to_root(path)}）但狀態更新失敗"
                          f"（{type(exc).__name__}）——下次 reconcile 依目錄位置回填")}
    return {**base, "action": "to_inbox", "label": _rel_to_root(path)}


def import_discovered(
    *,
    dry_run: bool = False,
    limit: int | None = None,
    state_db: Path | str | None = None,
    bridge_db: Path | str = bridge_state.DEFAULT_DB_PATH,
    inbox_dir: Path | str = DEFAULT_INBOX_DIR,
    policy_path: Path | str = DEFAULT_POLICY_PATH,
    max_retries: int = DEFAULT_MAX_IMPORT_RETRIES,
    seen_at: str | None = None,
) -> dict:
    """把 discovered（與重試中的 failed）session 依政策判定送進 memory/inbox/。

    - 政策檔先載（fail loud 整批不跑），才碰任何資料。
    - bridge db 不存在＝沒有 scanner 產出可處理：直接回報，**不建立 db 檔**
      （dry-run 與真跑一致——importer 只消費 scanner 的產出，不無中生有）。
    - 佇列＝discovered（依 first_seen_at 排序）＋ failed（自動重試）；
      --limit 只截斷佇列長度，不改順序。
    - dry_run=True 零寫入：不落地、不動 db、不遞增 retry_count。
    """
    policy = load_guardrail_policy(policy_path)
    if limit is not None and limit <= 0:
        raise ValueError("--limit 必須是正整數")
    bridge_db = Path(bridge_db)
    result: dict = {"mode": "import", "dry_run": dry_run,
                    "bridge_db_exists": bridge_db.exists(),
                    "queued": 0, "actions": [], "counts": {}}
    if not bridge_db.exists():
        return result
    queue = bridge_state.list_by_import_status("discovered", db_path=bridge_db)
    queue += bridge_state.list_by_import_status("failed", db_path=bridge_db)
    if limit is not None:
        queue = queue[:limit]
    result["queued"] = len(queue)
    if not queue:
        return result
    inbox_dir = Path(inbox_dir)
    if not inbox_dir.is_dir():
        raise FileNotFoundError(f"inbox 目錄不存在：{inbox_dir}（不代建目錄，避免寫錯地方）")
    with HermesSessionAdapter(db_path=state_db, snapshot=True) as adapter:
        for rec in queue:
            result["actions"].append(_process_one(
                rec, adapter, policy=policy, inbox_dir=inbox_dir,
                bridge_db=bridge_db, dry_run=dry_run,
                max_retries=max_retries, seen_at=seen_at))
    counts: dict = {}
    for action in result["actions"]:
        counts[action["action"]] = counts.get(action["action"], 0) + 1
    result["counts"] = counts
    return result


# ---------- CLI ----------

def _print_result(result: dict):
    """輸出只含 event_id／動作／類別標籤與統計數字——絕不印 session 內容或標題。"""
    prefix = "[dry-run] " if result["dry_run"] else ""
    if not result["bridge_db_exists"]:
        print(f"{prefix}bridge db 不存在——沒有 discovered/failed 可處理（不建立 db 檔）")
        return
    for action in result["actions"]:
        print(f"{prefix}{action['action']:<34} {action['event_id']}  "
              f"{action.get('label') or ''}")
    if not result["actions"]:
        print(f"{prefix}(沒有 discovered/failed 需要處理)")
    summary = "、".join(f"{k}={v}" for k, v in sorted(result["counts"].items()))
    tail = "（dry-run，未寫入任何檔案或 db）" if result["dry_run"] else ""
    print(f"{prefix}import 完成：佇列 {result['queued']} 筆"
          + (f"｜{summary}" if summary else "") + tail)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="hermes/bridge_importer.py — 把 bridge_state 的 discovered "
                    "session 依政策判定送進 memory/inbox/（只判定與落地，"
                    "不建 job、不呼叫 headless CoS、不安裝排程）")
    parser.add_argument(
        "--bridge-db", default=None,
        help=f"bridge_state.db 路徑（預設 {bridge_state.DEFAULT_DB_PATH}）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_imp = sub.add_parser(
        "import", help="處理全部 discovered（＋重試 failed）：判定→落地→回寫狀態")
    p_imp.add_argument("--dry-run", action="store_true",
                       help="零寫入（含 bridge db；不遞增 retry_count），只回報將執行的動作")
    p_imp.add_argument("--limit", type=int, default=None, metavar="N",
                       help="最多處理 N 筆（預設處理全部）")
    p_imp.add_argument("--state-db", default=None,
                       help="Hermes state.db 路徑（預設自動偵測；一律 snapshot 讀 temp 副本）")
    p_imp.add_argument("--inbox", default=str(DEFAULT_INBOX_DIR),
                       help=f"inbox 目錄（預設 {DEFAULT_INBOX_DIR}）")
    p_imp.add_argument("--policy", default=str(DEFAULT_POLICY_PATH),
                       help=f"guardrail 政策檔（預設 {DEFAULT_POLICY_PATH}；"
                            "讀不到即整批不跑，絕不默認放行）")
    p_imp.add_argument("--config", default=str(DEFAULT_BRIDGE_CONFIG),
                       help=f"bridge 設定檔（預設 {DEFAULT_BRIDGE_CONFIG}；"
                            "提供 max_import_retries）")
    return parser


def _cli(argv=None) -> int:
    # Windows console 預設 cp950——比照 scanner，stdout/stderr 強制 UTF-8
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    args = _build_parser().parse_args(argv)
    bridge_db = (Path(args.bridge_db) if args.bridge_db
                 else bridge_state.DEFAULT_DB_PATH)
    try:
        max_retries = load_max_import_retries(Path(args.config))
        result = import_discovered(
            dry_run=args.dry_run, limit=args.limit, state_db=args.state_db,
            bridge_db=bridge_db, inbox_dir=Path(args.inbox),
            policy_path=Path(args.policy), max_retries=max_retries)
    except (ValueError, FileNotFoundError, HermesSessionReadError) as exc:
        print(f"錯誤：{exc}", file=sys.stderr)
        return 1
    _print_result(result)
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
