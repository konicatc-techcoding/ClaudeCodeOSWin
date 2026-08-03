#!/usr/bin/env python3
"""hermes/bridge_scanner.py — v0.4（Stage 2.3；2.4a cutover 設定化與 watermark；
2.4d-2 episode 偵測＋manual checkpoint；2.4d-2 複核修正 stale-ended 阻擋
inactivity 的 blocker，見 `_decide_trigger()`；memory-lifecycle A1（2026-07-30）
多 profile episode capture——bridge.yaml `episodes.profiles` 掃描清單驅動：
主 db 的 session 依 `profile_name` 欄分流（空＝default、非空＝named profile
namespace），named profile 另掃各自的 profiles/<name>/state.db；per-profile
cutover 留空＝pending 不擷取；named-profile session 不進 default 的 legacy
ended 掃描（僅 episodes.enabled 時分流，enabled=false 維持位元級舊行為））

Stage 2 session bridge 的「偵測與記錄」層：找出 Hermes 新完結的 session，
把處理狀態寫進 bridge_state.db（經 hermes/bridge_state.py 的 repository API）。
**只做偵測與記錄**：不匯入 inbox、不 enqueue job、絕不寫回 Hermes state.db。

Stage 2.4d-2（episode capture 偵測，規格正本：
docs/stage2.4d-episode-capture-proposal.md）：`scan` 在既有 ended_at 掃描
邏輯之外，`episodes.enabled: true`（hermes/config/bridge.yaml）時額外對每個
session 判斷三種 trigger（ended／archived／inactivity）並呼叫
`bridge_state.create_episode()`；`enabled: false`（本階段預設，交付後仍是
false）或整個 `episodes` 區塊缺失時，`scan` 行為與 2.4c 位元級一致——
episode 偵測只在候選過濾用 `episodes.episode_cutover` 政策底線（不延伸既有
scan watermark 進 episode 語義，見 §5.3 的結構性衝突發現）。`checkpoint`
是獨立的第三個子指令（manual trigger，立即切刀無視門檻，不觸發 importer）。

三個子指令（職責分離，互不重疊）：

- ``scan``：讀 Hermes state.db（一律經 HermesSessionAdapter 的 snapshot 模式），
  找出範圍下界（含端點）之後「已完結」（ended_at 已設）的 session。首次看到
  → upsert ``import_status=discovered``（first_seen_at 從首次發現起算，
  不失真）；已有記錄 → 只呼叫 touch_last_seen() 更新 last_seen_at，
  **絕不把 imported/failed/skipped/needs_review 等既有狀態重設回 discovered**，
  也不動 decision_reason / retry_count / updated_at。

  範圍下界（Stage 2.4a 設定化，排程化的安全預設）：無 --since / --all-history
  時，effective since ＝ max(config cutover, watermark)，輸出明示來源——
  cutover 來自 hermes/config/bridge.yaml（政策底線，版控＋部署同步下發；
  讀不到檔案或欄位缺失一律 fail loud，絕不默認全掃），watermark 來自
  bridge_state.db 的 bridge_meta（部署側可拋棄進度，只前進不後退；語義見
  bridge_state.advance_scan_watermark）。每次**真實**（非 dry-run）scan 成功
  完成後，一律嘗試把 watermark 推進到本次掃描窗口上界（建立 snapshot 之前取的
  時間戳）；--since 人工覆蓋掃舊區間時，只前進語義保證 watermark 不被往回拉。
- ``reconcile``：掃 memory/inbox/ 本層＋.processed/＋.failed/，依
  「目錄位置是 inbox 檔案狀態的唯一真相」（docs/memory-taxonomy.md §5）回填：
  inbox 本層 → to_inbox、.processed/ → imported、.failed/ → failed。
  對帳依據優先用 frontmatter 的 session_id / event_id_range；無 frontmatter
  （舊時間戳檔名格式的歸檔）退回檔名比對，依據記錄在 decision_reason。
  processed_path 與 db 記錄不一致時，以 .processed/ 實際位置為準回寫。
  （episode-aware 回填＋cursor recovery 是 2.4d-3 範圍，本階段 reconcile
  邏輯本身不變。）
- ``checkpoint``：manual trigger（Stage 2.4d-2 新增）。對單一 session 立即
  切一刀（trigger=manual，無視 ended_at／archived／inactivity 門檻），走
  同一條 `bridge_state.create_episode()` 路徑；eligible 為空時明確回報
  「無新訊息、不切」（exit 0，非錯誤）；--dry-run 零寫入。**不觸發
  importer**——scan／import 職責分離不因 manual 破例（§0.1 拍板）。

episode 偵測與 trigger 判斷（提案 §5／§6，`scan` 與 `checkpoint` 共用）：
- 候選過濾**只用 `episodes.episode_cutover` 底線**，不沿用既有 scan
  watermark（§5.3：inactivity trigger 恰恰在「最近沒有活動」時才成立，若用
  watermark 過濾會系統性漏切）；逐 session 以 per-session cursor
  （`bridge_state.get_cursor`）比對決定有無新訊息。
- trigger 判定：`ended`（ended_at 已設，且同時滿足 >= episode_cutover 與
  >= last_message_at〔該 session 目前最新訊息時間，來自
  `list_session_activity()` 的 max_timestamp〕才算有效結束訊號，立即切、不等
  inactivity）／`archived`（Hermes `archived==true`，level-triggered：只看
  當下值，立即切、不等 72 小時，且不受 ended 是否 stale 影響）／
  `inactivity`（ended_at NULL，**或 ended_at 已設但 < last_message_at——即
  session 在 ended 後又有新訊息，這個 ended_at 視為 stale，不得阻擋
  inactivity 判斷，§0.1 拍板：ended 後復活不加特例、統一規則**——且閒置達
  `episodes.inactivity_hours`）。三者任一成立即可切；eligible 為空一律
  不建立空 episode（通用規則、無特例）。
- cursor 遺失防護（矩陣 #8）：session 無 cursor 記錄但 inbox 已有該 sid 的
  `_ep` 落地檔（代表 recovery／reconcile 沒跑過）→ 拒切、回報「請先跑
  reconcile」，不切出與既有 episode 重疊的 boundary。
- profile 邊界（§6.1；A1 起啟用多 profile）：named profile 的擷取由
  `episodes.profiles` 設定清單驅動（不是 `--source-profile` 旗標）；帶非
  default 的 `--source-profile` 且 `episodes.enabled: true` 時仍 fail loud
  （exit 1，矩陣 #27 精神沿用——旗標語義是主 db ended 掃描的標籤，不是
  episode namespace 選擇器）。

安全預設（硬條件，測試逐條把關）：
- **預設不得全掃**：--since 與 --all-history 互斥；兩者都不給時走安全預設
  effective since ＝ max(config cutover, watermark)（Stage 2.4a 取代原本的
  「無參數→exit 2」——原硬條件的精神由 cutover 底線繼續保證：設定檔讀不到或
  cutover 欄位缺失時 fail loud，絕不默認全掃）。下界一律含端點：
  ended_at >= since 才撈。
- 讀 Hermes state.db 只走 HermesSessionAdapter(snapshot=True)：先把 db
  （含 -wal/-shm）複製到 temp 再讀副本，絕不對正在寫入的 live db 直接開連線。
  本模組完全不 import sqlite3，也沒有任何 immutable 連線參數的 code path
  （對 Windows 上正被 Hermes 寫入的 db 開 immutable 會讀到不一致 snapshot）
  ——這兩點由測試靜態把關。
- --dry-run 只印出將要執行的動作摘要，不寫入任何檔案；bridge db 不存在時
  連 db 檔都不建立；**絕不推進 watermark**。
- 本模組對 bridge_state.db 的所有讀寫都經 hermes/bridge_state.py 的 API，
  該層唯一的連線入口只開 bridge 自己的 db。

retry_count 備忘（Stage 2.2 既定語義）：scan/reconcile 都不是「re-attempt」，
所以本模組**不呼叫** increment_retry_count()——那要等之後的匯入重試流程。

CLI（exit code：0 成功；1 執行期錯誤——含 bridge.yaml 缺失、非 default profile
    加 episodes enabled、checkpoint 遇 cursor 缺失防護；2 參數用法錯誤）：
    python3 hermes/bridge_scanner.py [--bridge-db PATH] scan \
        [--since ISO8601 | --all-history] [--dry-run] \
        [--state-db PATH] [--source-profile NAME] [--config PATH] [--inbox DIR]
    python3 hermes/bridge_scanner.py [--bridge-db PATH] reconcile \
        [--dry-run] [--inbox DIR] [--source-profile NAME]
    python3 hermes/bridge_scanner.py [--bridge-db PATH] checkpoint <session_id> \
        [--dry-run] [--state-db PATH] [--config PATH] [--inbox DIR] \
        [--source-profile NAME]
    # checkpoint 的 profile 一律自動推導（A1 驗收修正 2026-08-03：profile db
    # 以 db 歸屬定 profile、主 db 依 profile_name 欄分流，與 scan 一致）；
    # --source-profile 只做交叉驗證（不一致 fail loud），絕不默默落 default。
"""
import argparse
import sys
from datetime import datetime, timezone
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
    has_landed_episode_file,
    profile_state_db_path,
)

DEFAULT_INBOX_DIR = ROOT / "memory" / "inbox"

# 政策層設定檔（版控、部署同步下發）：cutover 底線的唯一來源（Stage 2.4a）
DEFAULT_BRIDGE_CONFIG = _HERMES_DIR / "config" / "bridge.yaml"

# 檔名解析（legacy／episode 共用單一實作，Stage 2.4d-3）：
# adapter.HermesSessionAdapter.parse_inbox_filename() ——涵蓋新格式
# hermes_session_<id>.md、舊時間戳格式 <stamp>_hermes_session_<id>.md、與
# episode 格式 hermes_session_<id>_ep<first>-<last>.md。2.4d-2 曾在本模組
# 自建一份 `_FILENAME_RE`（不含 ep 捕獲組），2.4d-3 收斂為呼叫 adapter 的
# 單一實作，不再維護第二份 regex（提案 §2／§3.2）。

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

# episode 檔 frontmatter capture_trigger 的合法值（Stage 2.4d-3 複核修正）：
# 刻意排除 registry enum 裡也允許的 'legacy'——那是 migration 回填 session-level
# 列專用（bridge_state.create_episode 本身也拒絕 capture_trigger='legacy'），
# episode 檔的 frontmatter 若寫了 'legacy' 一樣視為不合法（reconcile 不負責
# 判斷 trigger，只負責誠實回填檔案已記錄的值——見 reconcile() docstring
# 「recovery 不得捏造 trigger provenance」）。
_VALID_EPISODE_CAPTURE_TRIGGERS = frozenset(
    {"ended", "archived", "inactivity", "manual"})


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
    """讀檔案開頭 YAML frontmatter 的 session_id / source / event_id_range /
    episode / capture_trigger（後兩者是 Stage 2.4d-3 的 episode 檔新欄，
    additive，legacy 檔案不會有——見提案 §2）。只掃前 60 行；讀不到、沒有
    frontmatter、格式不對都回空 dict（容錯）。"""
    fields: dict = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            if fh.readline().strip() != "---":
                return {}
            for _ in range(60):
                line = fh.readline()
                if not line or line.strip() == "---":
                    break
                for key in ("session_id", "source", "event_id_range",
                            "episode", "capture_trigger", "source_profile"):
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

def load_cutover(config_path: Path | str = DEFAULT_BRIDGE_CONFIG) -> str:
    """讀取 bridge 設定檔的 cutover（bridge 正式啟用日，掃描範圍的政策底線）。

    fail loud（硬條件）：設定檔不存在丟 FileNotFoundError、cutover 欄位缺失/
    非字串/無法解析為 ISO 8601 丟 ValueError——**任何一種情況都不得默認全掃**。
    """
    import yaml  # lazy import：比照 bridge_state，需要時才要求 pyyaml

    path = Path(config_path)
    if not path.is_file():
        raise FileNotFoundError(
            f"bridge 設定檔不存在：{path}——cutover 底線必須存在（fail loud，"
            "絕不默認全掃）；正本為版控的 hermes/config/bridge.yaml"
        )
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    cutover = doc.get("cutover") if isinstance(doc, dict) else None
    if not isinstance(cutover, str) or not cutover.strip():
        raise ValueError(
            f"bridge 設定檔缺少 cutover 欄位（或不是字串）：{path}"
            "（fail loud，絕不默認全掃）"
        )
    _parse_iso_utc(cutover)  # 驗證可解析；解析失敗丟 ValueError
    return cutover


# ---------- Stage 2.4d-2：episode 偵測（提案 §5／§6） ----------

# episodes.inactivity_hours 缺失時的 fallback——唯一 fail-loud 必填項是
# episode_cutover（矩陣 #19 後半），inactivity_hours 已拍板 72（§0.1），
# 缺欄位時直接退回這個值即可，不需要為此再 fail loud 一次。
DEFAULT_INACTIVITY_HOURS = 72

def load_episodes_config(config_path: Path | str = DEFAULT_BRIDGE_CONFIG) -> dict:
    """讀取 hermes/config/bridge.yaml 的 episodes 區塊（提案 §1.3／§5.1；
    memory-lifecycle A1 加 profiles 掃描清單）。

    - 設定檔不存在、或存在但無 episodes 區塊、或 enabled 非 true
      → {"enabled": False, ...}——scanner 維持 2.4c 位元級行為
      （矩陣 #19 前半，零成本 gate）。
    - enabled: true 但 episode_cutover 缺失/非字串/無法解析
      → ValueError（fail loud，矩陣 #19 後半）：寧可讓「忘記填就翻 true」
      在啟用當下就爆炸，也不要默默當成「什麼都不管」而允許回溯湧入。
    - episode_cutover 即使 enabled=false 也一併回傳（可能是 None）：
      manual checkpoint 子指令獨立於 enabled 閘門之外，對無 cursor 的
      session 一樣需要這個值才能決定擷取起點（見 checkpoint()）。

    A1（2026-07-30）新增回傳鍵：
    - "profiles"：named profile 掃描清單，{name: {"episode_cutover": str|None,
      "state_db": str|None}}。驗證（fail loud）：profiles 非 mapping、entry 非
      mapping、profile 名不合法（bridge_state.validate_source_profile，含
      'default' 重複宣告）、episode_cutover 有值但無法解析——都丟 ValueError。
      **episode_cutover 為 null／缺失是合法的占位**（per-profile cutover 由
      部署時寫入）：該 profile 視為 pending，scanner 誠實回報並跳過擷取，
      不 fail、不影響其他 profile（防歷史湧入的 fail-closed 檔位）。
    """
    import yaml  # lazy import：比照 load_cutover，需要時才要求 pyyaml

    path = Path(config_path)
    if not path.is_file():
        return {"enabled": False, "inactivity_hours": None,
                "episode_cutover": None, "profiles": {}}
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    episodes = doc.get("episodes") if isinstance(doc, dict) else None
    if not isinstance(episodes, dict):
        episodes = {}
    enabled = bool(episodes.get("enabled", False))
    cutover = episodes.get("episode_cutover")
    if enabled:
        if not isinstance(cutover, str) or not cutover.strip():
            raise ValueError(
                f"episodes.enabled=true 但 episode_cutover 缺失（或不是字串）："
                f"{path}——fail loud，絕不默默當成「什麼都不管」（提案 §1.3／§5.1）"
            )
        _parse_iso_utc(cutover)  # 驗證可解析；解析失敗丟 ValueError
    inactivity_hours = episodes.get("inactivity_hours", DEFAULT_INACTIVITY_HOURS)

    raw_profiles = episodes.get("profiles")
    profiles: dict[str, dict] = {}
    if raw_profiles is not None:
        if not isinstance(raw_profiles, dict):
            raise ValueError(
                f"episodes.profiles 必須是 mapping（profile 名 → 設定）：{path}")
        for name, entry in raw_profiles.items():
            bridge_state.validate_source_profile(name)  # 不合法名 fail loud
            if name == bridge_state.DEFAULT_SOURCE_PROFILE:
                raise ValueError(
                    f"episodes.profiles 不得宣告 'default'：{path}——default 的 "
                    "cutover 是頂層 episode_cutover，不在 profiles 清單裡"
                    "（避免兩處各說各話）")
            if entry is None:
                entry = {}
            if not isinstance(entry, dict):
                raise ValueError(
                    f"episodes.profiles.{name} 必須是 mapping（或 null）：{path}")
            p_cutover = entry.get("episode_cutover")
            if p_cutover is not None:
                if not isinstance(p_cutover, str) or not p_cutover.strip():
                    raise ValueError(
                        f"episodes.profiles.{name}.episode_cutover 非法（空字串／"
                        f"非字串）：{path}——留空請用 null（pending 占位）")
                _parse_iso_utc(p_cutover)  # 解析失敗 fail loud
            state_db = entry.get("state_db")
            if state_db is not None and (not isinstance(state_db, str)
                                         or not state_db.strip()):
                raise ValueError(
                    f"episodes.profiles.{name}.state_db 非法（空字串／非字串）："
                    f"{path}——生產環境留 null 走自動定位")
            profiles[name] = {
                "episode_cutover": p_cutover if isinstance(p_cutover, str) else None,
                "state_db": state_db if isinstance(state_db, str) else None,
            }
    return {"enabled": enabled, "inactivity_hours": inactivity_hours,
            "episode_cutover": cutover if isinstance(cutover, str) else None,
            "profiles": profiles}


# 存在性探測（矩陣 #8）與 hash 輸入映射（提案 §4.5）2.4d-3 起收斂為單一
# 實作，不在本模組維護第二份：
# - has_landed_episode_file(inbox_dir, session_id)——adapter.py（提案 §3.2：
#   「偵測用的『存在性探測』復用 importer 的同一 needle helper（單處實作），
#   不複製 reconcile 的回填邏輯」）；
# - bridge_state.adapter_events_to_hash_input(events)——bridge_state.py。


def _decide_trigger(sess: dict, cutover_dt: datetime, inactivity_hours: float,
                    last_activity_dt: datetime, now_dt: datetime) -> str | None:
    """§6 trigger 表逐條判斷（scan 用；checkpoint 走獨立的 manual 路徑，
    不經這個函式）。優先序：有效 ended（非 stale，見下）> archived
    （level-triggered，不受 ended 是否 stale 影響）> inactivity——三者判斷
    用同一次 eligible（由呼叫端另外算），優先序只影響 capture_trigger
    標籤紀錄哪個名字，不影響「切不切」：任一成立就切一刀。

    stale-ended 修正（2.4d-2 複核，§0.1 拍板「ended 後復活不加特例、統一
    規則」）：`ended_at` 只在**同時滿足** `ended_at >= episode_cutover` 且
    `ended_at >= last_message_at`（last_activity_dt，來自
    `list_session_activity()` 的 max_timestamp）時，才視為目前訊息尾端的
    有效結束訊號、回傳 "ended"。只要 session 有比 `ended_at` 更新的訊息
    （`ended_at < last_message_at`，代表 session 在 ended 後復活），這個
    `ended_at` 就視為 **stale**：不得阻擋後續 archived／inactivity 判斷——
    舊版「只要 ended_at 有值就永遠不再檢查 inactivity」會讓復活後的新訊息
    永久漏擷取，已修正。stale-ended（或 ended_at 本來就是 NULL）的 session
    一律走跟未完結 session 相同的 archived／inactivity 判斷路徑，不加特例。
    """
    ended_at = sess["ended_at"]
    if ended_at:
        ended_dt = _parse_iso_utc(ended_at)
        if ended_dt >= cutover_dt and ended_dt >= last_activity_dt:
            return "ended"
        # ended_at < cutover 或 < last_activity_dt：stale——落到下面的
        # archived／inactivity 判斷，不得被 ended_at 有值這件事擋住
    if bool(sess["metadata"].get("archived")):
        return "archived"
    idle_hours = (now_dt - last_activity_dt).total_seconds() / 3600.0
    if idle_hours >= inactivity_hours:
        return "inactivity"
    return None


def _detect_episodes(
    *, adapter: HermesSessionAdapter, sessions: list[dict], activity_map: dict,
    episode_cutover: str, inactivity_hours, bridge_db: Path, source_profile: str,
    inbox_dir: Path | str, dry_run: bool, seen_at: str | None,
) -> list[dict]:
    """episode 偵測主邏輯（提案 §5／§6）：候選過濾只用 episode_cutover 底線
    （§5.3，不沿用 scan watermark）；逐 session 判斷 trigger、算 eligible、
    呼叫 create_episode()。呼叫端須已確保 episodes enabled 且傳入的
    episode_cutover 是該 profile 的有效 cutover（A1：per-profile cutover——
    default 用頂層 episode_cutover，named profile 用 profiles.<name> 的值；
    cutover pending（null）的 profile 由呼叫端跳過，不會進到這裡）。
    source_profile（A1 多 profile）：決定 event_id namespace（default＝裸
    "hermes:"、named＝"hermes/<profile>:"，由 bridge_state 產生）與 cursor
    的複合主鍵——同 sid 不同 profile 結構性隔離。
    """
    cutover_dt = _parse_iso_utc(episode_cutover)
    inactivity_hours = float(inactivity_hours or DEFAULT_INACTIVITY_HOURS)
    now_dt = _parse_iso_utc(seen_at) if seen_at else datetime.now(timezone.utc)

    actions: list[dict] = []
    for sess in sessions:
        sid = sess["session_id"]
        activity = activity_map.get(sid)
        if not activity or not activity.get("max_timestamp"):
            continue  # 無任何 active 訊息，無從切刀
        last_activity_dt = _parse_iso_utc(activity["max_timestamp"])
        if last_activity_dt < cutover_dt:
            continue  # 候選過濾：episode_cutover 底線（§5.3），不用 watermark

        trigger = _decide_trigger(sess, cutover_dt, inactivity_hours,
                                  last_activity_dt, now_dt)
        if trigger is None:
            continue

        cursor = bridge_state.get_cursor(
            sid, source_profile=source_profile, db_path=bridge_db)
        if cursor is None and has_landed_episode_file(inbox_dir, sid):
            actions.append({
                "action": "cursor_missing_needs_reconcile",
                "session_id": sid, "trigger": trigger,
                "source_profile": source_profile,
            })
            continue  # 拒切（矩陣 #8）：不切出與既有 episode 重疊的 boundary

        events = list(adapter.iter_events(session_id=sid))  # active=1 only
        if cursor is not None:
            eligible = [e for e in events
                       if e["metadata"]["raw_message_id"] >
                       cursor["last_captured_message_id"]]
        else:
            eligible = [e for e in events
                       if e["timestamp"]
                       and _parse_iso_utc(e["timestamp"]) >= cutover_dt]
        if not eligible:
            continue  # eligible 為空一律不建立空 episode（通用規則，無特例）

        rowids = [e["metadata"]["raw_message_id"] for e in eligible]
        first_id, last_id = min(rowids), max(rowids)
        content_hash = bridge_state.episode_content_hash(
            bridge_state.adapter_events_to_hash_input(eligible))
        episode_seq = (cursor["last_episode_seq"] + 1) if cursor else 1
        reason = (f"bridge scan episode 偵測：trigger={trigger}，"
                 f"boundary=[{first_id}..{last_id}]，profile={source_profile}")

        action = {
            "action": "create_episode", "session_id": sid, "trigger": trigger,
            "first_message_id": first_id, "last_message_id": last_id,
            "episode_seq": episode_seq, "source_profile": source_profile,
        }
        if not dry_run:
            result = bridge_state.create_episode(
                session_id=sid,
                source_profile=source_profile,
                session_source=sess["session_source"] or "unknown",
                episode_seq=episode_seq,
                capture_trigger=trigger,
                first_message_id=first_id,
                last_message_id=last_id,
                source_content_hash=content_hash,
                decision_reason=reason,
                seen_at=seen_at,
                db_path=bridge_db,
            )
            action["event_id"] = result["row"]["event_id"]
            action["created"] = result["created"]
        actions.append(action)
    return actions


def scan(
    *,
    since: str | None = None,
    all_history: bool = False,
    dry_run: bool = False,
    state_db: Path | str | None = None,
    bridge_db: Path | str = bridge_state.DEFAULT_DB_PATH,
    source_profile: str = "default",
    seen_at: str | None = None,
    config_path: Path | str = DEFAULT_BRIDGE_CONFIG,
    inbox_dir: Path | str = DEFAULT_INBOX_DIR,
) -> dict:
    """偵測範圍下界後新完結的 Hermes session → upsert discovered；
    `episodes.enabled: true` 時另外做 Stage 2.4d-2 episode 偵測（見模組
    docstring 與 docs/stage2.4d-episode-capture-proposal.md §5／§6）。

    - 範圍下界（含端點，ended_at >= 下界才撈）三種來源，結果的 since_source
      明示是哪一種：--since（人工覆蓋）＞ --all-history（明確全掃、無下界）＞
      安全預設 max(config cutover, watermark)。config 讀不到就 fail loud，
      **絕不默認全掃**（原「無參數→exit 2」硬條件的精神由 cutover 底線接手）。
    - ended_at 為 NULL（未完結）或壞值（無法解析）的 session 一律不撈。
    - 已有記錄的 session 只 touch last_seen_at，既有狀態原封不動（硬條件）。
    - 真實（非 dry-run）scan 成功後一律嘗試把 watermark 推進到本次掃描窗口
      上界（建立 snapshot **之前**取的時間戳，理由見
      bridge_state.advance_scan_watermark）；只前進語義保證 --since 掃舊區間
      不會把 watermark 往回拉。
    - dry_run=True：只回報將執行的動作，不寫入、**不推進 watermark**；
      bridge db 不存在時不建檔。
    - episode 偵測（`episodes.enabled: true`）：候選過濾只用
      `episodes.episode_cutover` 底線（不是這個函式的 since/watermark，
      §5.3 的結構性衝突發現）；`enabled: false` 或區塊缺失時與 2.4c
      位元級一致（矩陣 #19 前半，本函式對 episode 偵測完全零成本：不查
      list_session_activity()、不進 _detect_episodes()）；帶非 default
      profile 時 fail loud（§6.1，矩陣 #27）。
    """
    if since and all_history:
        raise ValueError("--since 與 --all-history 互斥，只能指定一個")

    episodes_cfg = load_episodes_config(config_path)
    if episodes_cfg["enabled"] and source_profile != bridge_state.DEFAULT_SOURCE_PROFILE:
        raise ValueError(
            f"episode capture 啟用時 --source-profile 只能是 default"
            f"（得到 {source_profile!r}）——named profile 的擷取由 bridge.yaml 的 "
            "episodes.profiles 掃描清單驅動（A1），不是這個旗標；用旗標指定"
            "named profile 會讓「主 db 的 ended 掃描標籤」與「episode namespace」"
            "語義混淆，fail-closed 拒絕（矩陣 #27 精神沿用）"
        )

    bridge_db = Path(bridge_db)
    # 純讀取：db 不存在回 None、不建檔（dry-run 安全）
    watermark_before = bridge_state.get_scan_watermark(db_path=bridge_db)

    if since:
        cutover = _parse_iso_utc(since)
        since_source = "--since（人工覆蓋）"
    elif all_history:
        cutover = None
        since_source = "--all-history（明確全掃）"
    else:
        cutover = _parse_iso_utc(load_cutover(config_path))
        since_source = "config cutover"
        if watermark_before is not None and \
                _parse_iso_utc(watermark_before) > cutover:
            cutover = _parse_iso_utc(watermark_before)
            since_source = "bridge_meta watermark"

    # 本次掃描窗口上界＝建立 snapshot 之前取的時間戳（保守：snapshot 之後才
    # 完結的 session 一定 >= 這個值，下次掃描必然涵蓋；邊界重疊冪等無害）
    window_end = datetime.now(timezone.utc).isoformat()

    if not dry_run:
        bridge_state.ensure_schema(bridge_db)
    db_readable = bridge_db.exists()  # dry-run 且 db 不存在時，連檔案都不建立

    episode_actions: list[dict] = []
    profiles_cfg = episodes_cfg["profiles"]
    profiles_pending_cutover: list[str] = []
    profile_dbs_missing: list[str] = []
    unconfigured_profile_sessions = 0
    named_excluded_from_legacy = 0
    with HermesSessionAdapter(db_path=state_db, snapshot=True) as adapter:
        main_db_path = adapter.db_path  # 已 resolve 的實路徑（profile db 定位基準）
        sessions = adapter.list_sessions()
        if episodes_cfg["enabled"]:
            activity_map = adapter.list_session_activity()
            # A1 分流：主 db 的 session 依 profile_name 欄分流——空＝default，
            # 非空＝named profile（namespace 與 per-profile cutover 各自獨立）。
            default_sessions: list[dict] = []
            named_groups: dict[str, list[dict]] = {}
            for sess in sessions:
                pname = sess["metadata"].get("profile_name")
                if not pname:
                    default_sessions.append(sess)
                else:
                    named_groups.setdefault(pname, []).append(sess)
            episode_actions = _detect_episodes(
                adapter=adapter, sessions=default_sessions,
                activity_map=activity_map,
                episode_cutover=episodes_cfg["episode_cutover"],
                inactivity_hours=episodes_cfg["inactivity_hours"],
                bridge_db=bridge_db,
                source_profile=source_profile, inbox_dir=inbox_dir,
                dry_run=dry_run, seen_at=seen_at,
            )
            for pname, group in sorted(named_groups.items()):
                cfg = profiles_cfg.get(pname)
                if cfg is None:
                    # fail-closed：未列入掃描清單的 profile 不擷取也不歸給
                    # default（不污染 namespace）；計數誠實回報。
                    unconfigured_profile_sessions += len(group)
                    continue
                if not cfg["episode_cutover"]:
                    if pname not in profiles_pending_cutover:
                        profiles_pending_cutover.append(pname)
                    continue  # per-profile cutover 尚未寫入（占位）＝不擷取
                episode_actions += _detect_episodes(
                    adapter=adapter, sessions=group, activity_map=activity_map,
                    episode_cutover=cfg["episode_cutover"],
                    inactivity_hours=episodes_cfg["inactivity_hours"],
                    bridge_db=bridge_db, source_profile=pname,
                    inbox_dir=inbox_dir, dry_run=dry_run, seen_at=seen_at,
                )

    # A1：named profile 各自的 profile db（lane session 落在這裡，2026-07-30
    # 查證）——逐顆 snapshot 掃描；db 歸屬即 profile（不看 profile_name 欄）。
    # 只做 episode 偵測，不做 legacy ended 掃描（legacy session-level 的
    # event_id 無 profile 段，named profile 只走 episode namespace）。
    if episodes_cfg["enabled"]:
        for pname, cfg in sorted(profiles_cfg.items()):
            if not cfg["episode_cutover"]:
                if pname not in profiles_pending_cutover:
                    profiles_pending_cutover.append(pname)
                continue  # pending 占位：誠實回報、不擷取（防歷史湧入）
            profile_db = (Path(cfg["state_db"]) if cfg["state_db"]
                          else profile_state_db_path(pname, main_db_path))
            if not profile_db.is_file():
                profile_dbs_missing.append(pname)
                continue  # 誠實回報缺 db，不讓單一 profile 弄倒整輪 scan
            with HermesSessionAdapter(db_path=profile_db, snapshot=True) as padapter:
                psessions = padapter.list_sessions()
                pactivity = padapter.list_session_activity()
                episode_actions += _detect_episodes(
                    adapter=padapter, sessions=psessions, activity_map=pactivity,
                    episode_cutover=cfg["episode_cutover"],
                    inactivity_hours=episodes_cfg["inactivity_hours"],
                    bridge_db=bridge_db, source_profile=pname,
                    inbox_dir=inbox_dir, dry_run=dry_run, seen_at=seen_at,
                )

    actions: list[dict] = []
    candidates = 0
    for sess in sessions:
        if episodes_cfg["enabled"] and sess["metadata"].get("profile_name"):
            # A1 分流：named-profile session 不進 default 的 legacy ended 掃描
            # ——它的內容由對應 namespace 的 episode capture 涵蓋（未設定／
            # pending 的 profile 則 fail-closed 不擷取，不誤歸 default）。
            # 只在 episodes.enabled 時分流：enabled=false 維持 2.4c 位元級
            # 行為（矩陣 #19 前半的 config gate 不變量）。
            named_excluded_from_legacy += 1
            continue
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

    # 真實 scan 成功走到這裡才推進 watermark（中途丟例外不會推進，不會跳漏）；
    # dry-run 絕不推進
    watermark_after = watermark_before
    if not dry_run:
        watermark_after = bridge_state.advance_scan_watermark(
            window_end, db_path=bridge_db)["watermark"]

    actions.extend(episode_actions)

    return {"mode": "scan", "dry_run": dry_run,
            "sessions_seen": len(sessions), "candidates": candidates,
            "bridge_db_exists": db_readable,
            "effective_since": cutover.isoformat() if cutover else None,
            "since_source": since_source,
            "watermark_before": watermark_before,
            "watermark_after": watermark_after,
            "episodes_enabled": episodes_cfg["enabled"],
            "episode_actions": len(episode_actions),
            # A1 多 profile 的誠實回報欄（enabled=false 時全部空/0）：
            "profiles_pending_cutover": sorted(profiles_pending_cutover),
            "profile_dbs_missing": sorted(profile_dbs_missing),
            "unconfigured_profile_sessions": unconfigured_profile_sessions,
            "named_excluded_from_legacy": named_excluded_from_legacy,
            "actions": actions}


# ---------- checkpoint（manual trigger，Stage 2.4d-2） ----------

def _derive_checkpoint_profile(adapter_db_path: Path, session: dict) -> str:
    """checkpoint 的 profile 推導（A1 驗收修正，2026-08-03）——與 scan 的
    db 歸屬邏輯一致，唯一真相是「哪顆 db＋profile_name 欄」：

    - db 路徑符合 profile db 佈局（`<...>/profiles/<name>/state.db`，以
      adapter resolve 後的實路徑判定）→ profile＝目錄名 `<name>`（db 歸屬
      定 profile，不看 profile_name 欄——與 scan 相同）。
    - 主 db → 依該 session 的 `metadata.profile_name` 分流（空＝default）。
    """
    if adapter_db_path.parent.parent.name == "profiles":
        return adapter_db_path.parent.name
    return (session["metadata"].get("profile_name")
            or bridge_state.DEFAULT_SOURCE_PROFILE)


def checkpoint(
    session_id: str,
    *,
    dry_run: bool = False,
    state_db: Path | str | None = None,
    bridge_db: Path | str = bridge_state.DEFAULT_DB_PATH,
    seen_at: str | None = None,
    config_path: Path | str = DEFAULT_BRIDGE_CONFIG,
    inbox_dir: Path | str = DEFAULT_INBOX_DIR,
    source_profile: str | None = None,
) -> dict:
    """manual checkpoint（提案 §6 CLI 雛形）：對單一 session 立即切一刀
    （trigger=manual，無視 ended_at／archived／inactivity 門檻），走同一條
    `bridge_state.create_episode()` 路徑。**scan／import 職責分離不因
    manual 破例**——本函式只建立 discovered episode 列，不觸發 importer
    （§0.1 拍板）。

    - **profile-aware（A1 驗收修正，2026-08-03——取代 2.4d-2 的「固定
      default」）**：profile 一律自動推導（`_derive_checkpoint_profile`，
      與 scan 的 db 歸屬邏輯一致：profile db 以 db 歸屬定 profile、主 db 依
      session 的 profile_name 欄分流），event_id／cursor／cutover 全部跟著
      推導結果走——**絕不默默落 default**。`source_profile` 參數（CLI
      `--source-profile`）是「明確宣告＋交叉驗證」：與推導結果不一致時
      fail loud（ValueError），不是覆蓋機制——db 歸屬與 profile_name 是
      唯一真相，人工標籤說了不算。
    - 獨立於 `episodes.enabled` 閘門之外（manual 是人工明確操作，不受
      自動管線是否開啟影響）；但無 cursor 的 session 仍需要 cutover 作為
      擷取起點，缺失時 fail loud（ValueError）：default 用頂層
      `episodes.episode_cutover`；named profile 用
      `episodes.profiles.<name>.episode_cutover`（未宣告或仍為 null 占位
      → fail loud，不猜、不借 default 的 cutover——per-profile 底線）。
    - cursor 遺失防護（矩陣 #8）同 scan：session 無 cursor（查的是推導出的
      (profile, sid)）但 inbox 已有該 sid 的 `_ep` 落地檔 → 回報
      `cursor_missing_needs_reconcile`，不切。
    - eligible 為空 → 回報 `no_new_messages`（呼叫端 CLI 對應 exit 0，
      非錯誤——「無新訊息、不切」不是失敗）。
    - --dry-run：零寫入（不建 bridge db、不呼叫 create_episode）。
    """
    episodes_cfg = load_episodes_config(config_path)

    with HermesSessionAdapter(db_path=state_db, snapshot=True) as adapter:
        try:
            export = adapter.export_session(session_id)
        except KeyError as exc:
            raise ValueError(
                f"Hermes state.db 裡沒有 session：{session_id}") from exc
        derived_profile = _derive_checkpoint_profile(adapter.db_path,
                                                     export["session"])
    if source_profile is not None:
        bridge_state.validate_source_profile(source_profile)
        if source_profile != derived_profile:
            raise ValueError(
                f"--source-profile {source_profile!r} 與推導結果 "
                f"{derived_profile!r} 不一致——profile 的唯一真相是 db 歸屬"
                "（profiles/<name>/state.db）與主 db 的 profile_name 欄，"
                "人工標籤不得覆蓋（fail loud，絕不錯登 namespace）")
    profile = derived_profile

    if not dry_run:
        bridge_state.ensure_schema(bridge_db)

    cursor = bridge_state.get_cursor(
        session_id, source_profile=profile, db_path=bridge_db)

    if cursor is None and has_landed_episode_file(inbox_dir, session_id):
        return {"mode": "checkpoint", "dry_run": dry_run,
                "session_id": session_id, "source_profile": profile,
                "action": "cursor_missing_needs_reconcile", "created": False}

    events = export["events"]  # export_session 預設 include_inactive=False

    if cursor is not None:
        eligible = [e for e in events
                   if e["metadata"]["raw_message_id"] >
                   cursor["last_captured_message_id"]]
    else:
        if profile == bridge_state.DEFAULT_SOURCE_PROFILE:
            cutover_raw = episodes_cfg["episode_cutover"]
            if not cutover_raw:
                raise ValueError(
                    "checkpoint 需要 hermes/config/bridge.yaml 的 "
                    "episodes.episode_cutover——session 無 cursor 記錄時，"
                    "擷取起點以此為底線，缺失時不猜、不預設全掃（fail loud）"
                )
        else:
            entry = episodes_cfg["profiles"].get(profile)
            if entry is None:
                raise ValueError(
                    f"session 屬於 named profile {profile!r}，但它不在 "
                    "episodes.profiles 掃描清單裡——請先在 "
                    "hermes/config/bridge.yaml 宣告（無 cursor 的 session 需要 "
                    "per-profile cutover 作擷取起點；不猜、不借 default 的 "
                    "cutover，fail loud）")
            cutover_raw = entry["episode_cutover"]
            if not cutover_raw:
                raise ValueError(
                    f"named profile {profile!r} 的 episode_cutover 仍是 null "
                    "占位（尚未 cutover）——無 cursor 的 session 擷取起點以 "
                    "per-profile cutover 為底線，請先依 A1 部署 runbook 寫入"
                    "實值（fail loud，不猜、不借 default 的 cutover）")
        cutover_dt = _parse_iso_utc(cutover_raw)
        eligible = [e for e in events
                   if e["timestamp"]
                   and _parse_iso_utc(e["timestamp"]) >= cutover_dt]

    if not eligible:
        return {"mode": "checkpoint", "dry_run": dry_run,
                "session_id": session_id, "source_profile": profile,
                "action": "no_new_messages", "created": False}

    rowids = [e["metadata"]["raw_message_id"] for e in eligible]
    first_id, last_id = min(rowids), max(rowids)
    content_hash = bridge_state.episode_content_hash(
        bridge_state.adapter_events_to_hash_input(eligible))
    episode_seq = (cursor["last_episode_seq"] + 1) if cursor else 1
    session_source = export["session"]["session_source"] or "unknown"
    reason = (f"bridge checkpoint（manual）：boundary=[{first_id}..{last_id}]，"
              f"profile={profile}")

    result = {"mode": "checkpoint", "dry_run": dry_run, "session_id": session_id,
              "action": "create_episode", "source_profile": profile,
              "first_message_id": first_id, "last_message_id": last_id,
              "episode_seq": episode_seq,
              "event_id": bridge_state.episode_event_id(
                  session_id, first_id, last_id, source_profile=profile),
              "created": False}
    if not dry_run:
        created = bridge_state.create_episode(
            session_id=session_id,
            source_profile=profile,
            session_source=session_source,
            episode_seq=episode_seq,
            capture_trigger="manual",
            first_message_id=first_id,
            last_message_id=last_id,
            source_content_hash=content_hash,
            decision_reason=reason,
            seen_at=seen_at,
            db_path=bridge_db,
        )
        result["event_id"] = created["row"]["event_id"]
        result["created"] = created["created"]
    return result


# ---------- reconcile ----------

def _resolve_file_identity(fm: dict, filename: str) -> dict | None:
    """對帳身份判定（提案 §2）：legacy 或 episode，回傳
    {"event_id", "kind", "session_id", "first_message_id", "last_message_id",
    "source_profile", "basis"}，認不出時回 None。

    source_profile（A1 多 profile）：episode 檔的 profile 依 frontmatter
    還原——`event_id_range` 的 namespace 前綴（"hermes/<profile>:"）優先，
    其次 `source_profile:` 欄；兩者都有但不一致＝檔案自相矛盾，fail-closed
    回 None（skip_unrecognized，不猜）。都沒有＝default（裸 "hermes:" 恆等
    default 的向後相容規則）。legacy 檔一律 default（named profile 不產生
    legacy 檔）。

    **kind 判定的結構性訊號（重要，2.4d-3 判斷細節）**：episode 檔的判別
    不能只看 frontmatter `event_id_range` 是否為 "hermes:<sid>:<first>..<last>"
    格式——2.4c 起 legacy（整個 session）匯入的 inbox 檔案本來就會帶這個格式
    的 event_id_range（`bridge_importer._event_id_range()`：對整個 session
    的 min/max raw_message_id 算一個「內容範圍」註記，跟 event_id 本身
    仍是 legacy 的 "hermes:<sid>" 無關）。若把「event_id_range 含 '..'」
    當成「這是 episode 檔」的判準，會把**所有既有 legacy 檔案**誤判成
    episode，寫壞 cursor 與 DB（本階段發現的規格釐清，非本文件字面陷阱—
    -但根源相同：查重/對帳範圍的誤判）。

    真正能區分 legacy／episode 的結構性訊號只有兩個，兩者都是 2.4d-3 才
    出現、legacy 檔案絕不會有：(a) 檔名 `_ep<first>-<last>` 捕獲組；
    (b) frontmatter 的 `episode:` 欄位。只要其中之一存在即判定為 episode，
    然後才輪到「用哪個欄位還原精確 boundary」的優先序：frontmatter
    event_id_range（可直接還原完整 event_id）→ 檔名 ep 捕獲組（提案 §2）。
    """
    fm_sid = fm.get("session_id")
    fm_source_ok = fm.get("source") in (None, _INBOX_FRONTMATTER_SOURCE)
    parsed_name = HermesSessionAdapter.parse_inbox_filename(filename)
    name_sid = parsed_name["session_id"] if parsed_name else None
    name_is_episode = bool(parsed_name and parsed_name["first_message_id"] is not None)
    fm_is_episode = bool(fm.get("episode"))

    sid = fm_sid if (fm_sid and fm_source_ok) else name_sid
    if sid is None:
        return None

    if not (name_is_episode or fm_is_episode):
        basis = ("frontmatter session_id" if (fm_sid and fm_source_ok)
                 else "檔名比對（檔案無 frontmatter session_id）")
        return {"event_id": bridge_state.session_event_id(sid), "kind": "legacy",
                "session_id": sid, "first_message_id": None,
                "last_message_id": None,
                "source_profile": bridge_state.DEFAULT_SOURCE_PROFILE,
                "basis": basis}

    # episode 的 profile 還原（A1）：frontmatter source_profile 欄（default 檔
    # 不帶此欄）。event_id_range 的 namespace 前綴會再交叉驗證（見下）。
    fm_profile = fm.get("source_profile") or bridge_state.DEFAULT_SOURCE_PROFILE
    try:
        bridge_state.validate_source_profile(fm_profile)
    except ValueError:
        return None  # 檔案聲稱的 profile 名不合法——不猜，交給 skip_unrecognized

    # episode：優先 frontmatter event_id_range 還原 boundary（可直接還原
    # 完整 event_id，含 namespace）；其次檔名 ep 捕獲組。
    range_raw = fm.get("event_id_range")
    if range_raw:
        try:
            parsed_range = bridge_state.parse_event_id(range_raw)
        except ValueError:
            parsed_range = None
        if (parsed_range and parsed_range["kind"] == "episode"
                and parsed_range["session_id"] == sid):
            range_profile = parsed_range["source_profile"]
            if (fm.get("source_profile")
                    and range_profile != fm_profile):
                # 檔案自相矛盾（range namespace 與 source_profile 欄不一致）
                # ——fail-closed 不猜，交給 skip_unrecognized。
                return None
            first, last = (parsed_range["first_message_id"],
                           parsed_range["last_message_id"])
            return {"event_id": bridge_state.episode_event_id(
                        sid, first, last, source_profile=range_profile),
                    "kind": "episode", "session_id": sid,
                    "first_message_id": first, "last_message_id": last,
                    "source_profile": range_profile,
                    "basis": "frontmatter event_id_range"}
    if name_is_episode and parsed_name["session_id"] == sid:
        first, last = (parsed_name["first_message_id"],
                       parsed_name["last_message_id"])
        return {"event_id": bridge_state.episode_event_id(
                    sid, first, last, source_profile=fm_profile),
                "kind": "episode", "session_id": sid,
                "first_message_id": first, "last_message_id": last,
                "source_profile": fm_profile,
                "basis": "檔名 ep 捕獲組"}
    # 判定為 episode（frontmatter 有 episode 欄位）卻拿不到可信 boundary數值
    # ——理論上不該發生（episode 檔必有 _ep 檔名或一致的 event_id_range）；
    # fail-closed：不猜 boundary，交給 skip_unrecognized。
    return None


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

    Stage 2.4d-3（episode-aware 回填＋cursor recovery，提案 §2／§3.2）：
    - 對帳單位以 event_id 為 key（legacy＝`hermes:<sid>`；episode＝
      `hermes:<sid>:<first>..<last>`），同一 session 的多個 episode 檔各自
      獨立回填（`session_id_range` 是合法的，提案 §2）。
    - episode 列若既有 DB 記錄不存在（db 全空或部分重建）→ 用
      `bridge_state.create_episode(allow_null_hash=True)` 建立（hash 無法自
      檔案還原，記 NULL，decision_reason 註明，不影響去重）；已存在則跟
      legacy 一樣用 `upsert_session_state` 校正狀態／路徑。
    - 每個 session 的所有已落地 episode 檔（跨 to_inbox／.processed／
      .failed，即「有實體檔案」的意思——需求檔／skipped 從不落地，本來就
      不在這裡的掃描範圍內）處理完後，**重建 cursor**：取
      `max(last_message_id)` upsert 進 `bridge_cursors`（只前進不後退，
      對健康 db 重跑天然 no-op，提案 §3.2。**2026-07-30 冪等性修正**：
      no-op 時不再列 `cursor_recovery` 動作——動作只在 cursor 真的落後於
      落地檔目標時出現（如 bridge_cursors 遺失／部分重建），健康 db 重跑
      因此可用「輸出零 cursor_recovery」機械驗證；本輪 create_episode 插入
      已在同 transaction 推進的 cursor 不重放，dry-run 用 predicted_cursor
      補「將插入」效果、與真實模式判定一致）；`last_episode_seq` 優先取
      frontmatter `episode` 欄位的最大值，缺 frontmatter 時退回「依
      first_message_id 排序的序位」保守估計（只影響序號可讀性，不影響
      boundary 正確性）。
    - profile fail-closed（§6.1）：`source_profile` 參數非 default 時，
      episode 相關的建立／更新／cursor 重建一律記錄並跳過（不寫入、不動
      cursor）；legacy 回填不受此限（維持既有行為）。

    episode metadata 誠實性 fail-closed（2.4d-3 複核修正，取代舊版
    「capture_trigger 缺值時以 'manual' 佔位」的做法——那個佔位會在
    `capture_trigger` 這個結構化欄位裡製造假事實：下游只讀欄位的人會把
    「recovery 猜測」誤判成「使用者真的按了 manual checkpoint」）：
    - episode 檔缺 `capture_trigger`，或帶非法值（不在
      `ended/archived/inactivity/manual` 之內，含 `legacy`——那是 migration
      回填 session-level 列專用，episode 檔用它一樣不合法）→ 一律
      `episode_metadata_incomplete`：不猜測任何 trigger 值、不呼叫
      `create_episode()`、不建立或更新該 episode 的 `bridge_sessions` 列、
      不修改既有 DB 狀態（含 touch_last_seen）、不動 inbox/.processed/.failed
      實體檔案、不降級成 legacy。reason/category 只記
      `metadata:capture_trigger_missing` 或 `metadata:capture_trigger_invalid`
      （不含 session 內容）。
    - **同一 session 的 cursor recovery 語義**：只要該 session 任何一個
      落地 episode 檔 metadata 不完整，本輪 cursor recovery 對這個 session
      **整體 fail-closed**——不得只跳過壞檔、取其餘合法檔案的
      max(last_message_id) 推進 cursor（那等於在不確定的資料基礎上假裝
      recovery 完整）。其他 session 不受影響，照常 reconcile／cursor
      recovery。
    - legacy 檔案不要求 `capture_trigger`，這條規則完全不適用（既有行為
      不變）。
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
            identity = _resolve_file_identity(fm, candidate.name)
            if identity is None:
                skipped.append(str(candidate))
                continue
            event_id = identity["event_id"]
            entry = entries.setdefault(event_id, {
                "kind": identity["kind"],
                "session_id": identity["session_id"],
                "first_message_id": identity["first_message_id"],
                "last_message_id": identity["last_message_id"],
                "source_profile": identity["source_profile"],  # A1：per-file 還原
                "by_status": {},
                "event_id_range": None,
                "session_source": None,
                "episode_seq": None,
                "capture_trigger": None,
            })
            entry["by_status"].setdefault(status, (candidate, identity["basis"]))
            if entry["event_id_range"] is None and fm.get("event_id_range"):
                entry["event_id_range"] = fm["event_id_range"]
            if entry["session_source"] is None:
                entry["session_source"] = _body_session_source(candidate)
            if entry["episode_seq"] is None and fm.get("episode"):
                try:
                    entry["episode_seq"] = int(fm["episode"])
                except ValueError:
                    pass
            if entry["capture_trigger"] is None and fm.get("capture_trigger"):
                entry["capture_trigger"] = fm["capture_trigger"]

    # episode_seq 保守估計（缺 frontmatter 時）：同 session 依 first_message_id
    # 排序給序位，只影響可讀性，不影響 boundary 正確性（提案 §3.2）。
    # A1：分組 key 帶 source_profile——同 sid 不同 profile 的 cursor 結構性
    # 隔離（bridge_cursors 複合主鍵），recovery 也必須分開算。
    episode_session_groups: dict[tuple, list[dict]] = {}
    for entry in entries.values():
        if entry["kind"] == "episode":
            episode_session_groups.setdefault(
                (entry["source_profile"], entry["session_id"]), []).append(entry)
    for group in episode_session_groups.values():
        ordered = sorted(group, key=lambda e: e["first_message_id"])
        for idx, e in enumerate(ordered, start=1):
            if not e["episode_seq"]:
                e["episode_seq"] = idx

    episode_profile_ok = source_profile == bridge_state.DEFAULT_SOURCE_PROFILE

    bridge_db = Path(bridge_db)
    if not dry_run:
        bridge_state.ensure_schema(bridge_db)
    db_readable = bridge_db.exists()

    # 該輪 reconcile 裡，metadata 不完整（缺／非法 capture_trigger）的
    # episode 檔所屬 session——用來讓 cursor recovery 對整個 session
    # fail-closed（見 reconcile() docstring「同一 session 的 cursor
    # recovery 語義」）。key 是 (source_profile, session_id)（A1：與
    # cursor 複合主鍵同構）；value 是不完整檔案的相對路徑清單，讓使用者
    # 知道要修哪個檔案。
    metadata_incomplete_sessions: dict[tuple, list[str]] = {}

    # cursor recovery 冪等性修正（2026-07-30，A1 部署 runbook 步驟 4 發現）：
    # 本輪由 create_episode 插入（或 dry-run 下「將插入」）的 episode 列，
    # 其 cursor 前進已在同 transaction 內完成（提案 §1.2 核心不變量）——
    # recovery 迴圈不需要、也不應該再對這些 session 重放一次動作。這裡
    # 逐 (source_profile, session_id) 記錄插入路徑會把 cursor 推到哪，
    # recovery 迴圈據此判斷「cursor 是否真的落後」：dry-run（插入未真的
    # 發生）與真實模式（cursor 已被插入推進）因此判定一致。
    predicted_cursor: dict[tuple, tuple] = {}

    actions: list[dict] = [
        {"action": "skip_unrecognized", "path": p} for p in skipped]
    for event_id, entry in sorted(entries.items()):
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
        sid = entry["session_id"]
        is_episode = entry["kind"] == "episode"
        existing = (
            bridge_state.get_session_state(event_id, db_path=bridge_db)
            if db_readable else None
        )
        reason = (f"bridge reconcile：檔案位於 {_LOCATION_LABEL[status]}，"
                  f"session_id 依據：{basis}")

        metadata_category = None
        if is_episode:
            trigger = entry["capture_trigger"]
            if trigger is None:
                metadata_category = "metadata:capture_trigger_missing"
            elif trigger not in _VALID_EPISODE_CAPTURE_TRIGGERS:
                metadata_category = "metadata:capture_trigger_invalid"

        action_category = None
        if is_episode and not episode_profile_ok:
            action_name = "unsupported_profile_fail_closed"
        elif is_episode and metadata_category is not None:
            # fail-closed（2.4d-3 複核修正）：不猜測 trigger、不呼叫
            # create_episode()、不建立/更新 DB 列、不動既有狀態、不動
            # inbox 實體檔案、不降級成 legacy——見 reconcile() docstring。
            action_name = "episode_metadata_incomplete"
            action_category = metadata_category
            metadata_incomplete_sessions.setdefault(
                (entry["source_profile"], sid), []).append(
                inbox_path or str(path))
        elif existing is None:
            if is_episode:
                action_name = f"insert_episode_{status}"
                # 插入路徑會（在真實模式）同 transaction 推進 cursor——記下
                # 推進目標，recovery 迴圈據此免除重放（冪等性修正，見上）。
                pkey = (entry["source_profile"], sid)
                prev_last, prev_seq = predicted_cursor.get(pkey, (0, 0))
                predicted_cursor[pkey] = (
                    max(prev_last, entry["last_message_id"]),
                    max(prev_seq, entry["episode_seq"] or 0))
                if not dry_run:
                    bridge_state.create_episode(
                        session_id=sid,
                        source_profile=entry["source_profile"],  # A1：per-file 還原
                        session_source=entry["session_source"] or "unknown",
                        episode_seq=entry["episode_seq"],
                        capture_trigger=entry["capture_trigger"],
                        first_message_id=entry["first_message_id"],
                        last_message_id=entry["last_message_id"],
                        source_content_hash=None,
                        allow_null_hash=True,
                        decision_reason=(
                            reason + "；episode 列由 reconcile 從檔案系統回填"
                            "（source_content_hash 無法自檔案還原，記 NULL，"
                            "不影響去重，提案 §3.2）"),
                        import_status=status,
                        memory_type="none",
                        useful_chat=(status == "imported"),
                        imported_inbox_path=inbox_path,
                        processed_path=processed_path,
                        error_reason=(_FAILED_BACKFILL_REASON
                                      if status == "failed" else None),
                        seen_at=seen_at,
                        db_path=bridge_db,
                    )
            else:
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
                        event_id=event_id,
                        event_id_range=(entry["event_id_range"]
                                        or existing["event_id_range"]),
                        seen_at=seen_at,
                        db_path=bridge_db,
                    )
        action_entry = {"action": action_name, "event_id": event_id,
                        "session_id": sid, "path": str(path), "basis": basis}
        if action_category is not None:
            action_entry["category"] = action_category
        actions.append(action_entry)

    # cursor 重建（提案 §3.2，核心 recovery 邏輯）：對每個有落地 episode 檔的
    # session，取所有落地檔 max(last_message_id) upsert 進 bridge_cursors
    # （只前進不後退，對健康 db 重跑無害、天然冪等）。
    #
    # fail-closed（2.4d-3 複核修正）：該 session 只要有任一落地 episode 檔
    # metadata 不完整（缺／非法 capture_trigger），本輪 cursor recovery 對
    # 這個 session 整體停止——不得只取其餘合法檔案的 max(last_message_id)
    # 推進（那等於在不確定的資料基礎上假裝 recovery 完整）。其他 session
    # 不受影響，照常 recovery。
    for (entry_profile, sid), group in sorted(episode_session_groups.items()):
        incomplete_paths = metadata_incomplete_sessions.get((entry_profile, sid))
        if incomplete_paths:
            actions.append({
                "action": "cursor_recovery_fail_closed", "session_id": sid,
                "source_profile": entry_profile,
                "category": "metadata:capture_trigger_missing_or_invalid",
                "incomplete_paths": sorted(incomplete_paths),
            })
            continue
        max_last = max(e["last_message_id"] for e in group)
        seq_candidates = [e["episode_seq"] for e in group if e["episode_seq"]]
        max_seq = max(seq_candidates) if seq_candidates else len(group)
        if not episode_profile_ok:
            actions.append({"action": "unsupported_profile_fail_closed",
                            "session_id": sid, "last_captured_message_id": max_last,
                            "last_episode_seq": max_seq})
            continue
        # 冪等性修正（2026-07-30，A1 部署驗證發現）：cursor 已在（或超過）
        # 重建目標＝真 no-op——不執行 advance、不列動作。舊行為對每個有落地
        # episode 檔的 session 無條件 append `cursor_recovery`（advanced=False
        # 只藏在結果 dict，CLI 輸出看不到），健康 db 連跑兩次會永遠列出同樣
        # 的 recovery 動作，「對健康 db 應為 no-op」的部署預期無從用輸出
        # 驗證。判定基準＝max(db 現值, 本輪插入路徑的推進目標)——真實模式
        # 下 create_episode 已推進 cursor、dry-run 下用 predicted_cursor 補上
        # 「將插入」的效果，兩種模式判定一致。get_cursor 是純讀取（db 不存在
        # 回 None、不建檔），dry-run 依然零寫入。
        current = bridge_state.get_cursor(
            sid, source_profile=entry_profile, db_path=bridge_db)
        cur_last = current["last_captured_message_id"] if current else 0
        cur_seq = current["last_episode_seq"] if current else 0
        pred_last, pred_seq = predicted_cursor.get((entry_profile, sid), (0, 0))
        if max(cur_last, pred_last) >= max_last and \
                max(cur_seq, pred_seq) >= max_seq:
            continue  # cursor 不落後於落地檔目標：真 no-op，零動作
        advanced = None
        if not dry_run:
            result = bridge_state.advance_cursor(
                sid, max_last, last_episode_seq=max_seq,
                source_profile=entry_profile,  # A1：per-file 還原的 profile
                seen_at=seen_at, db_path=bridge_db)
            advanced = result["advanced"]
        actions.append({"action": "cursor_recovery", "session_id": sid,
                        "source_profile": entry_profile,
                        "last_captured_message_id": max_last,
                        "last_episode_seq": max_seq, "advanced": advanced})

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
        "scan", help="偵測範圍下界後新完結的 session → upsert discovered"
                     "（無參數時安全預設：max(config cutover, watermark)）")
    group = p_scan.add_mutually_exclusive_group()
    group.add_argument(
        "--since", default=None, metavar="ISO8601",
        help="人工覆蓋範圍下界（含當下；ended_at >= since 才撈）。與 --all-history"
             " 互斥；不給時用 max(config cutover, watermark) 安全預設")
    group.add_argument(
        "--all-history", action="store_true",
        help="明確要求掃全部歷史完結 session（安全預設絕不全掃，必須明確指定）")
    p_scan.add_argument("--dry-run", action="store_true",
                        help="只印出將執行的動作，不寫入任何檔案、不推進 watermark")
    p_scan.add_argument(
        "--config", default=None, metavar="PATH",
        help=f"bridge 設定檔路徑（預設 {DEFAULT_BRIDGE_CONFIG}；提供 cutover 底線，"
             "讀不到即失敗、絕不默認全掃）")
    p_scan.add_argument(
        "--state-db", default=None,
        help="Hermes state.db 路徑（預設自動偵測；一律 snapshot 讀 temp 副本）")
    p_scan.add_argument("--source-profile", default="default",
                        help="來源 Hermes profile（本階段只支援主 db，預設 default；"
                             "episodes.enabled=true 時非 default 會 fail loud）")
    p_scan.add_argument(
        "--inbox", default=str(DEFAULT_INBOX_DIR),
        help=f"inbox 目錄（預設 {DEFAULT_INBOX_DIR}；episode 偵測的 cursor 遺失"
             "防護用來探測既有落地檔，矩陣 #8）")

    p_rec = sub.add_parser(
        "reconcile", help="掃 inbox 本層＋.processed/＋.failed/ 回填既有狀態")
    p_rec.add_argument("--dry-run", action="store_true",
                       help="只印出將執行的動作，不寫入任何檔案")
    p_rec.add_argument("--inbox", default=str(DEFAULT_INBOX_DIR),
                       help=f"inbox 目錄（預設 {DEFAULT_INBOX_DIR}）")
    p_rec.add_argument("--source-profile", default="default",
                       help="回填新記錄時使用的 source_profile（預設 default）")

    p_ckpt = sub.add_parser(
        "checkpoint",
        help="manual trigger：對單一 session 立即切一刀（trigger=manual，"
             "無視門檻），不觸發 importer")
    p_ckpt.add_argument("session_id")
    p_ckpt.add_argument("--dry-run", action="store_true",
                        help="只印出將執行的動作，零寫入")
    p_ckpt.add_argument(
        "--state-db", default=None,
        help="Hermes state.db 路徑（預設自動偵測；一律 snapshot 讀 temp 副本）")
    p_ckpt.add_argument(
        "--config", default=None, metavar="PATH",
        help=f"bridge 設定檔路徑（預設 {DEFAULT_BRIDGE_CONFIG}；session 無 cursor "
             "時需要 episodes.episode_cutover 決定擷取起點，缺失 fail loud）")
    p_ckpt.add_argument(
        "--inbox", default=str(DEFAULT_INBOX_DIR),
        help=f"inbox 目錄（預設 {DEFAULT_INBOX_DIR}；cursor 遺失防護探測用）")
    p_ckpt.add_argument(
        "--source-profile", default=None,
        help="明確宣告 session 所屬 profile（A1 驗收修正，2026-08-03）。"
             "profile 一律自動推導（profile db 以 db 歸屬定 profile、主 db 依 "
             "profile_name 欄），此旗標只做交叉驗證：與推導結果不一致時 "
             "fail loud，不是覆蓋機制——絕不默默錯登 namespace")
    return parser


def _print_result(result: dict):
    prefix = "[dry-run] " if result["dry_run"] else ""
    if result["mode"] == "scan":
        bound = result["effective_since"] or "（無下界，明確全掃）"
        print(f"{prefix}scan 範圍下界：{bound}｜來源：{result['since_source']}")
        print(f"{prefix}episode 偵測：{'啟用' if result['episodes_enabled'] else '關閉'}"
              f"（episodes.enabled，動作 {result['episode_actions']} 筆）")
        if result.get("profiles_pending_cutover"):
            print(f"{prefix}named profile 尚未 cutover（占位、暫不擷取）："
                  + ", ".join(result["profiles_pending_cutover"]))
        if result.get("profile_dbs_missing"):
            print(f"{prefix}named profile db 不存在（略過）："
                  + ", ".join(result["profile_dbs_missing"]))
        if result.get("unconfigured_profile_sessions"):
            print(f"{prefix}主 db 有 {result['unconfigured_profile_sessions']} 個 "
                  "session 屬於未列入 episodes.profiles 的 profile——不擷取、"
                  "也不歸 default（fail-closed）")
    for action in result["actions"]:
        target = (action.get("event_id") or action.get("path")
                  or action.get("session_id"))
        extra = ""
        if action.get("import_status"):
            extra += f"（既有狀態 {action['import_status']} 不變）"
        if action.get("basis"):
            extra += f"｜依據：{action['basis']}"
        if action.get("trigger"):
            extra += f"｜trigger={action['trigger']}"
        profile = action.get("source_profile")
        if profile and profile != "default":
            extra += f"｜profile={profile}"
        print(f"{prefix}{action['action']:<28} {target}{extra}")
    if not result["actions"]:
        print(f"{prefix}(沒有需要處理的項目)")
    tail = "（dry-run，未寫入任何檔案）" if result["dry_run"] else ""
    if result["dry_run"] and not result["bridge_db_exists"]:
        tail += "（bridge db 尚不存在，所有既有狀態查詢視為無記錄，也不建立 db 檔）"
    if result["mode"] == "scan":
        print(f"{prefix}scan 完成：檢視 {result['sessions_seen']} 個 session，"
              f"符合條件的完結 session {result['candidates']} 個，"
              f"動作 {len(result['actions'])} 筆{tail}")
        if result["dry_run"]:
            print(f"{prefix}watermark 不推進（dry-run），"
                  f"維持：{result['watermark_before'] or '（未設定）'}")
        else:
            print(f"{prefix}watermark：{result['watermark_before'] or '（未設定）'}"
                  f" → {result['watermark_after']}")
    else:
        print(f"{prefix}reconcile 完成：檢視 {result['files_seen']} 個檔案，"
              f"動作 {len(result['actions'])} 筆{tail}")


def _print_checkpoint_result(result: dict) -> int:
    """checkpoint 結果印出＋決定 exit code：
    - create_episode：成功切刀（或 dry-run 預覽），exit 0。
    - no_new_messages：eligible 為空，明確回報「不切」，exit 0（非錯誤）。
    - cursor_missing_needs_reconcile：矩陣 #8 的拒切防護，印到 stderr，exit 1
      （manual 呼叫端明確要求對這個 session 動作，防護擋下時應可從 exit code
      辨別「沒做到」，不是靜默假裝成功）。
    """
    prefix = "[dry-run] " if result["dry_run"] else ""
    sid = result["session_id"]
    if result["action"] == "no_new_messages":
        print(f"{prefix}checkpoint {sid}：無新訊息、不切（exit 0，非錯誤）")
        return 0
    if result["action"] == "cursor_missing_needs_reconcile":
        print(f"checkpoint {sid}：cursor 缺失但已有 episode 落地檔——"
              "請先跑 reconcile（拒切，矩陣 #8）", file=sys.stderr)
        return 1
    print(f"{prefix}checkpoint {sid}：{result['action']}｜"
          f"event_id={result['event_id']}｜"
          f"boundary=[{result['first_message_id']}..{result['last_message_id']}]"
          f"｜episode_seq={result['episode_seq']}"
          + ("（dry-run，未寫入任何檔案）" if result["dry_run"] else
             f"｜created={result['created']}"))
    return 0


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
            config_path = (Path(args.config) if args.config
                           else DEFAULT_BRIDGE_CONFIG)
            result = scan(since=args.since, all_history=args.all_history,
                          dry_run=args.dry_run, state_db=args.state_db,
                          bridge_db=bridge_db,
                          source_profile=args.source_profile,
                          config_path=config_path,
                          inbox_dir=Path(args.inbox))
        elif args.cmd == "reconcile":
            result = reconcile(inbox_dir=Path(args.inbox), dry_run=args.dry_run,
                               bridge_db=bridge_db,
                               source_profile=args.source_profile)
        else:  # checkpoint
            config_path = (Path(args.config) if args.config
                           else DEFAULT_BRIDGE_CONFIG)
            result = checkpoint(args.session_id, dry_run=args.dry_run,
                               state_db=args.state_db, bridge_db=bridge_db,
                               config_path=config_path,
                               inbox_dir=Path(args.inbox),
                               source_profile=args.source_profile)
    except (ValueError, FileNotFoundError, HermesSessionReadError) as exc:
        print(f"錯誤：{exc}", file=sys.stderr)
        return 1
    if args.cmd == "checkpoint":
        return _print_checkpoint_result(result)
    _print_result(result)
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
