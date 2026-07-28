#!/usr/bin/env python3
"""dashboard/data_stage3.py — P2:Stage 3 三項觀測功能的唯讀資料層。

設計正本:docs/stage3-dashboard-observability-proposal.md(v2)§2–§4;
載體:docs/webui-migration-proposal.md §4.3(P2)——經 dashboard/api.py 唯讀
API 曝露給 webui/。**不修改既有 dashboard/data.py**(Streamlit 零改動鐵律),
Stage 3 新函式全部放這個新模組。路徑 A 的 systemd 狀態原複用
`data.get_systemd_status()`(stage3 提案 §4.4 DoD 第 6 項:不重新兜平行邏輯);
2026-07-28 起改複用 `data_systemd_wsl.get_wsl_systemd_snapshot()`——
readonly-api 跑在 Windows 側,裸 systemctl 不存在,舊路徑永遠 FileNotFoundError
而把「查不到」誤報成「未安裝」。新快照經 `wsl -d` 包裹、分層守門絕不喚醒
distro、帶 5 秒快取;「不另兜平行邏輯」的精神不變(單一共用快照,
data.get_systemd_status() 原樣留給 deprecated 的 Streamlit app.py)。

唯讀鐵律(stage3 提案 §0.5,逐條對應):

1. 全部函式只讀取:讀 YAML/JSON/`.timer` 檔案文字、呼叫 systemctl 唯讀查詢、
   經 HermesSessionAdapter(mode=ro + PRAGMA query_only + snapshot)讀 state.db。
   沒有任何寫入路徑、沒有任何「重跑/核准/pin/對齊」入口。
2. 不 import 任何有寫入能力的模組:不 import hermes/db.py、bridge_dispatch、
   cron.jobs(原生 cron 一律「直接讀 jobs.json 檔案文字」);
   HermesSessionAdapter 是唯讀類別(§0.5 判準),以 importlib 由檔案路徑載入,
   不把 hermes/ 目錄整包掛進 sys.path。
3. 憑證白名單+雙重防護(§3.2):
   - 資料層只「組新 dict」——只從原始資料讀取白名單欄位、組進新物件,
     原始完整 dict 讀完立刻超出函式作用域(見 _profile_credential_status)。
   - 輸出前掃描:回傳前對整個結構跑 redact.scan_structure()(共用正本,
     import dashboard/redact.py,不複製貼上);API 層序列化前還有第三道
     (api.py _send_json),UI 層再有欄位白名單——三層。

auth.json schema 驗證證據(stage3 提案 §3.5 DoD 第 7 項,2026-07-23 由
engineering 以安全方式驗證:只印 key 名稱清單,未印任何 value;涵蓋
global-root 與 default/financialresearch/gptcoding/intelligence/nemocoding
五個 profile,default 無 auth.json):

- 頂層 keys:version / providers / credential_pool / updated_at /
  active_provider / suppressed_sources。
- **與提案 §1.2 的落差**:頂層 `providers` 不是「provider 名稱字串清單」,
  而是 dict(provider 名稱 → 含 access_token/refresh_token/agent_key 等
  敏感欄位的設定物件)——本模組依提案精神只取其 key 名稱清單,絕不展開。
- credential_pool.<provider> 為 entry list;entry keys(白名單相關者):
  id / label / auth_type / priority / source / last_status / last_status_at /
  last_refresh(部分 entry 才有)/ last_error_* / base_url / request_count /
  secret_fingerprint;敏感欄位實測存在:access_token / refresh_token /
  agent_key(以及 expires_* / client_id / scope 等),一律不在白名單。
- 提案白名單欄位中的 `provider` 在 entry 內不存在(provider 是
  credential_pool 的 key),其餘 id/priority/last_status/last_refresh/
  source/label 皆與實際 schema 相符。

jobs.json schema 驗證證據(同日、同方式,root store 7 筆 job;
financialresearch/nemocoding 的 profile store 為空清單):

- 頂層 keys:jobs / updated_at。
- job entry keys:id / name / prompt / skills / skill / model / provider /
  provider_snapshot / model_snapshot / base_url / script / no_agent /
  context_from / schedule{kind, expr, display} / schedule_display /
  repeat{times, completed} / enabled / state / paused_at / paused_reason /
  created_at / next_run_at / last_run_at / last_status / last_error /
  last_delivery_error / deliver / origin / enabled_toolsets / workdir /
  fire_claim。
- 本模組**不抽取** prompt / script / workdir 等內容欄位,只抽排程健康
  所需欄位(§4.2 路徑 B)。

全域 config.yaml(同日、同方式):頂層含 model 區塊,model keys =
base_url / default / provider——漂移比對只抽 model.default 一個值。
"""
import importlib.util
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import yaml

import data_systemd_wsl  # 路徑 A 複用共用 systemd 快照(WSL 包裹、守門不喚醒、5 秒快取)
import redact  # 憑證掃描共用正本(webui-migration §3.4:共用實作,不複製貼上)

ROOT = Path(__file__).resolve().parent.parent

# 可測試性:以下路徑常數供測試以 tempfile fixture 替換;正式執行用預設值。
CAPABILITY_LANES_PATH = ROOT / "registry" / "capability_lanes.yaml"
SYSTEMD_UNIT_DIR = ROOT / "hermes" / "systemd"
ADAPTER_PATH = ROOT / "hermes" / "session_adapter" / "adapter.py"

# %LOCALAPPDATA%\hermes——data.py 之外第一個 repo 外讀取路徑(stage3 §3.3)。
# LOCALAPPDATA 不存在(非 Windows 環境)→ None,所有函式優雅退化不噴例外。
_local_app_data = os.environ.get("LOCALAPPDATA")
HERMES_HOME: Path | None = Path(_local_app_data) / "hermes" if _local_app_data else None

# 功能一:state.db 路徑覆寫(None = 交由 adapter 自動偵測);測試指向 fixture。
HERMES_STATE_DB: Path | None = None

_adapter_module = None

# ---------------------------------------------------------------------------
# 功能二 — 憑證/Capability Lane 唯讀狀態檢視(stage3 提案 §3)
# ---------------------------------------------------------------------------

# §3.2 白名單(allowlist,不是 blocklist):credential_pool entry 只抽這些
# 欄位,其餘一律捨棄,不論欄位叫什麼名字。`provider` 依實測 schema 不存在於
# entry 內(見模組 docstring 的驗證證據),以 credential_pool 的 key 呈現。
CREDENTIAL_ENTRY_ALLOWLIST = ("id", "priority", "last_status", "last_refresh", "source", "label")


_PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _model_config_from_file(config_path: Path) -> dict | None:
    """從一份 Hermes config.yaml **只抽 model.default / model.provider 兩個
    非敏感設定值**(模型名稱不是秘密),組新 dict 回傳——完整 config dict
    讀完立即超出作用域,其他區塊(slack/platforms 等可能含敏感值)一律不外流。

    schema 驗證證據(2026-07-23,安全方式只印 key 名稱):global-root 與
    financialresearch/gptcoding/intelligence/nemocoding 四個 profile 的
    config.yaml 皆有 model 區塊,keys = base_url/default/provider
    (nemocoding 另有 api_mode),default/provider 皆為 str;
    default profile 無 config.yaml(繼承全域的真實案例)。

    檔案不存在/壞 YAML/無 model 區塊 → None(呼叫端 fallback 全域)。"""
    if not config_path.is_file():
        return None
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, OSError, UnicodeDecodeError):
        return None
    model = raw.get("model") if isinstance(raw, dict) else None
    if not isinstance(model, dict):
        return None
    default = model.get("default")
    provider = model.get("provider")
    return {
        "default": default if isinstance(default, str) and default else None,
        "provider": provider if isinstance(provider, str) and provider else None,
    }
    # raw 到此已超出作用域,只有兩個白名單值離開本函式


def _annotate_effective_models(lanes: list[dict]) -> None:
    """對每條 lane 標注「實際生效模型」(就地補三個欄位,不動 registry 原欄位):

    - effective_model:native lane(無 hermes_profile)→ "(native session)";
      hermes_profile lane → 該 profile config.yaml 的 model.default,
      無 profile 值時 fallback 全域 config.yaml 的 model.default。
    - effective_model_source:"native" | "profile"(profile 自訂)|
      "global"(繼承全域)| "unknown"(兩邊都查不到)。
    - effective_provider:同來源的 model.provider(native/unknown → None)。

    資料來源是 %LOCALAPPDATA%\\hermes\\ 的 config.yaml(profile 實際設定),
    不是 registry——registry 的 model 欄位刻意為 null(不重複記載)。"""
    global_cfg = _model_config_from_file(HERMES_HOME / "config.yaml") if HERMES_HOME else None
    for lane in lanes:
        profile = lane.get("hermes_profile")
        if not isinstance(profile, str) or not _PROFILE_NAME_RE.match(profile):
            lane["effective_model"] = "(native session)"
            lane["effective_model_source"] = "native"
            lane["effective_provider"] = None
            continue
        profile_cfg = None
        if HERMES_HOME is not None:
            profile_cfg = _model_config_from_file(
                HERMES_HOME / "profiles" / profile / "config.yaml")
        if profile_cfg and profile_cfg["default"]:
            lane["effective_model"] = profile_cfg["default"]
            lane["effective_model_source"] = "profile"
            lane["effective_provider"] = profile_cfg["provider"]
        elif global_cfg and global_cfg["default"]:
            lane["effective_model"] = global_cfg["default"]
            lane["effective_model_source"] = "global"
            lane["effective_provider"] = global_cfg["provider"]
        else:
            lane["effective_model"] = "無法查詢"
            lane["effective_model_source"] = "unknown"
            lane["effective_provider"] = None


def get_capability_lane_status() -> list[dict]:
    """回傳 registry/capability_lanes.yaml 的 lanes 全部欄位(§3.3),
    另補三個「實際生效模型」欄位(effective_model/effective_model_source/
    effective_provider,來源:profile/全域 config.yaml 的 model 白名單值,
    見 _annotate_effective_models)。

    lanes 檔已 commit 進 git、本來就是公開治理資料,不需過濾;仍照三道防護
    慣例在輸出前跑一次掃描(防未來欄位變質,誤擋代價低)。
    檔案不存在/壞 YAML → 回傳 [],不噴例外。"""
    if not CAPABILITY_LANES_PATH.exists():
        return []
    try:
        config = yaml.safe_load(CAPABILITY_LANES_PATH.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, OSError):
        return []
    lanes = config.get("lanes") or []
    if not isinstance(lanes, list):
        return []
    lanes = [dict(lane) for lane in lanes if isinstance(lane, dict)]
    _annotate_effective_models(lanes)
    return redact.scan_structure(lanes)


def _extract_credential_entry(entry: dict) -> dict:
    """§3.2 雙重防護第 1 點:**只從原始資料讀取白名單欄位、組進全新 dict**。

    不是先整包 load 再 del/filter——那種寫法漏改一行就把含秘密值的原始
    dict 參照留給後面的程式碼。這裡逐欄位取值組新物件,原始 entry 不外流。"""
    return {key: entry.get(key) for key in CREDENTIAL_ENTRY_ALLOWLIST}


def _profile_credential_status(auth_path: Path) -> dict:
    """單一 profile 的 auth.json 白名單讀取(§3.3 讀取邏輯逐步對應)。

    任何一個環節失敗只標記該 profile,不噴例外、不中斷其他 profile。"""
    if not auth_path.is_file():
        return {"auth_json_exists": False}
    try:
        raw = json.loads(auth_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        # 比照 get_adapter_config_status() 對壞 JSON 的既有處理:標記錯誤,
        # **不嘗試用其他方式讀取內容去除錯**(教訓一)。
        return {"auth_json_exists": True, "error": "設定檔格式錯誤"}
    if not isinstance(raw, dict):
        return {"auth_json_exists": True, "error": "設定檔格式錯誤"}

    try:
        mtime = datetime.fromtimestamp(auth_path.stat().st_mtime, tz=timezone.utc).isoformat()
    except OSError:
        mtime = None

    result: dict = {"auth_json_exists": True, "mtime": mtime}

    # 頂層 providers:實測是 dict(值含敏感欄位)——只取 key 名稱清單,
    # 絕不展開任何 entry 內容(§3.2;schema 落差見模組 docstring)。
    providers = raw.get("providers")
    if isinstance(providers, dict):
        result["providers"] = sorted(str(name) for name in providers.keys())
    elif isinstance(providers, list):
        # 提案原始理解(名稱字串清單)的相容分支
        result["providers"] = sorted(str(name) for name in providers if isinstance(name, str))
    else:
        result["providers"] = []

    # credential_pool:逐 provider、逐 entry 組新 dict + entry_count。
    # suppressed_sources 依 §3.2 保守立場不在白名單,整個不讀取。
    pool_out: dict = {}
    pool = raw.get("credential_pool")
    if isinstance(pool, dict):
        for provider, entries in pool.items():
            entry_list = entries if isinstance(entries, list) else []
            pool_out[str(provider)] = {
                "entry_count": len(entry_list),
                "entries": [
                    _extract_credential_entry(e) for e in entry_list if isinstance(e, dict)
                ],
            }
    result["credential_pool"] = pool_out
    # 原始 raw dict 到此超出作用域,完整憑證內容不外流(§3.2 防護第 1 點)
    return result


def get_hermes_credential_status() -> dict:
    """每個已知 Hermes profile(含 global-root)的憑證治理狀態(§3.3)。

    只含白名單欄位;profile 清單動態掃描 %LOCALAPPDATA%\\hermes\\profiles\\
    子目錄(目錄名稱不敏感),另固定包含 "(global-root)"。
    LOCALAPPDATA 未設 → {"available": False, ...},不噴例外。"""
    if HERMES_HOME is None:
        return {"available": False, "reason": "LOCALAPPDATA 未設定,此環境無法查詢", "profiles": {}}

    profiles: dict = {"(global-root)": _profile_credential_status(HERMES_HOME / "auth.json")}
    profiles_dir = HERMES_HOME / "profiles"
    if profiles_dir.is_dir():
        for child in sorted(profiles_dir.iterdir()):
            if child.is_dir():
                profiles[child.name] = _profile_credential_status(child / "auth.json")

    # §3.2 雙重防護第 2 點:輸出前對整個最終回傳結構跑遞迴掃描(共用正本)。
    # 防的是白名單本身寫錯——寧可誤擋治理欄位,不放過一個真正的秘密值。
    return redact.scan_structure({"available": True, "profiles": profiles})


# ---------------------------------------------------------------------------
# 功能三 — 統一排程健康表(systemd timer + Hermes 原生 cron)+模型漂移旗標
# (stage3 提案 §4)
# ---------------------------------------------------------------------------

# 排程表達式:OnCalendar=(wall-clock)或 OnBootSec=/OnUnitActiveSec= 等
# 間隔型 key(實際落差:提案 §4.2 只提 OnCalendar,但 repo 內 hermes-rss.timer
# 實測用 OnBootSec+OnUnitActiveSec——兩種形態都要能顯示,否則 6 缺 1)。
_ON_CALENDAR_RE = re.compile(r"^\s*OnCalendar\s*=\s*(?P<value>.+?)\s*$", re.MULTILINE)
_ON_INTERVAL_RE = re.compile(
    r"^\s*(?P<key>On(?:Boot|UnitActive|UnitInactive|Startup|Active)Sec)\s*=\s*(?P<value>.+?)\s*$",
    re.MULTILINE,
)
_UNIT_RE = re.compile(r"^\s*Unit\s*=\s*(?P<value>\S+)\s*$", re.MULTILINE)

# cost_tier 粗略排序(僅供 drift_cost_direction 標示;§4.2:純標示、可不精確,
# 誤判最壞後果只是提示不準,不觸發任何動作)
_COST_TIER_RANK = {"free": 0, "included": 1, "paid": 2}


def _parse_timer_files() -> list[dict]:
    """路徑 A 靜態層:讀 repo 內 hermes/systemd/*.timer 檔案文字(git 版本
    控制、非秘密),抽 OnCalendar= 與 Unit=。**不呼叫 systemctl**——即使
    環境完全沒有 systemd,這層資訊永遠可得(§4.2 路徑 A 第 1 步)。"""
    rows = []
    if not SYSTEMD_UNIT_DIR.is_dir():
        return rows
    for timer_path in sorted(SYSTEMD_UNIT_DIR.glob("*.timer")):
        try:
            text = timer_path.read_text(encoding="utf-8")
        except OSError:
            continue
        calendar = _ON_CALENDAR_RE.search(text)
        if calendar:
            schedule_expr = calendar.group("value")
        else:
            intervals = [f"{m.group('key')}={m.group('value')}"
                         for m in _ON_INTERVAL_RE.finditer(text)]
            schedule_expr = "; ".join(intervals) if intervals else "無法查詢"
        unit = _UNIT_RE.search(text)
        rows.append({
            "job_name": timer_path.stem,
            "schedule_expr": schedule_expr,
            "timer_unit": timer_path.name,
            "service_unit": unit.group("value") if unit else f"{timer_path.stem}.service",
        })
    return rows


def _service_last_result(service_info: dict | None) -> str:
    """§4.2 路徑 A 第 4 步:複用 systemd 快照已取得的 service
    active/sub state 當「上次執行結果」——systemd 對 oneshot service 的
    既有語意(成功回 dead/exited、失敗進 failed),不另發查詢。"""
    if not service_info:
        return "無法查詢"
    state = service_info.get("last_exit", "")
    active, _, sub = state.partition("/")
    if "failed" in (active, sub):
        return "失敗"
    if sub in ("dead", "exited"):
        return "成功"
    if sub == "running":
        return "執行中"
    return "無法查詢"


def _systemd_rows() -> list[dict]:
    """路徑 A(source="systemd"):靜態 .timer 解析 + 共用 systemd 快照
    (data_systemd_wsl.get_wsl_systemd_snapshot():Windows 側經 `wsl -d`
    包裹的唯讀查詢,分層守門絕不喚醒 distro,list-units 與 list-timers
    NEXT/LAST 同一份 5 秒快取——wsl 呼叫不在無快取的熱路徑)。

    誠實三分支退化(不得把「查不到」顯示成「未安裝」):
    - snapshot "ok"          → 真實狀態(查得到、單元不存在才是「未安裝」)
    - snapshot "wsl_down"    → 動態欄位全部「WSL 未運作」
    - snapshot "unavailable" → 動態欄位全部「無法查詢」
    job_name/schedule_expr 永遠可得(§4.2 退化)。"""
    static_rows = _parse_timer_files()
    snapshot = data_systemd_wsl.get_wsl_systemd_snapshot()
    queryable = snapshot.get("status") == "ok"
    unit_status = snapshot.get("units") or {}
    timers = snapshot.get("timers") or {}
    degraded_text = "WSL 未運作" if snapshot.get("status") == "wsl_down" else "無法查詢"

    rows = []
    for item in static_rows:
        if not queryable:
            deployed: object = degraded_text
            timer_active = degraded_text
            last_result = degraded_text
            next_trigger = degraded_text
            last_trigger = degraded_text
        else:
            timer_info = unit_status.get(item["timer_unit"])
            service_info = unit_status.get(item["service_unit"])
            deployed = timer_info is not None
            if timer_info is None:
                timer_active = "未安裝"
                last_result = "無法查詢"
            else:
                active = timer_info.get("last_exit", "").partition("/")[0]
                timer_active = "active" if active == "active" else "inactive"
                last_result = _service_last_result(service_info)
            timer_times = timers.get(item["timer_unit"], {})
            next_raw = timer_times.get("next")
            last_raw = timer_times.get("last")
            next_trigger = next_raw if next_raw and next_raw not in ("n/a", "-") else "無法查詢"
            if last_raw and last_raw not in ("n/a", "-"):
                last_trigger = last_raw
            elif last_raw in ("n/a", "-"):
                last_trigger = "從未觸發"
            else:
                last_trigger = "無法查詢"
        rows.append({
            "source": "systemd",
            "job_name": item["job_name"],
            "schedule_expr": item["schedule_expr"],
            "deployed": deployed,
            "timer_active": timer_active,
            "last_result": last_result,
            "next_trigger": next_trigger,
            "last_trigger": last_trigger,
            "model_drift": "n/a",  # systemd 路徑一律 n/a(§4.2 合併輸出結構)
            "drift_cost_direction": None,
        })
    return rows


def _global_model_default() -> str | None:
    """全域 config.yaml 只抽 model.default 一個值(漂移比對基準)。
    白名單讀取實作共用 _model_config_from_file(功能二效模型同一入口)。"""
    if HERMES_HOME is None:
        return None
    config = _model_config_from_file(HERMES_HOME / "config.yaml")
    return config["default"] if config else None


def _model_cost_tier_map() -> dict:
    """model 名稱 → cost_tier(來自 capability_lanes.yaml,公開治理資料),
    供 drift_cost_direction 粗略判斷。查不到就「未知」,純標示不精確無妨。"""
    mapping: dict = {}
    for lane in get_capability_lane_status():
        model = lane.get("model")
        tier = lane.get("cost_tier")
        if isinstance(model, str) and model and isinstance(tier, str):
            mapping.setdefault(model, tier)
    return mapping


def _drift_cost_direction(snapshot: str, default: str, cost_map: dict) -> str:
    snap_rank = _COST_TIER_RANK.get(cost_map.get(snapshot, ""), None)
    default_rank = _COST_TIER_RANK.get(cost_map.get(default, ""), None)
    if snap_rank is None or default_rank is None:
        return "未知"
    if default_rank > snap_rank:
        return "更貴"
    if default_rank < snap_rank:
        return "更便宜"
    return "相同"


def _native_cron_row(job: dict, default_model: str | None, cost_map: dict) -> dict:
    """單筆原生 cron job → 統一表格列(§4.2 路徑 B 第 2/3 步+漂移旗標)。

    只抽排程健康所需欄位——prompt/script/workdir 等內容欄位一律不抽取。
    缺欄位以「無法查詢」/「從未觸發」呈現,不因單一 job 欄位缺失中斷整表。"""
    schedule = job.get("schedule")
    schedule_expr = None
    if isinstance(schedule, dict):
        schedule_expr = schedule.get("expr") or schedule.get("display")
    if not schedule_expr:
        raw_display = job.get("schedule_display")
        schedule_expr = raw_display if isinstance(raw_display, str) and raw_display else "無法查詢"

    last_error = job.get("last_error")
    last_status = job.get("last_status")
    if last_error:
        last_result = "失敗"
    elif isinstance(last_status, str) and last_status:
        last_result = last_status
    else:
        last_result = "尚未執行"

    next_run = job.get("next_run_at")
    last_run = job.get("last_run_at")

    # 模型漂移旗標(v2 新增;只偵測、只標示,絕無任何 pin/對齊寫入,§4.7):
    # script job(no_agent)不做 inference → 恆 "n/a";agent job 已 pin
    # (model 有值)或沒有 model_snapshot / 全域 default 不可得 → "n/a"。
    model_drift = "n/a"
    drift_cost_direction = None
    if not job.get("no_agent"):
        snapshot = job.get("model_snapshot")
        pinned = job.get("model") is not None
        if not pinned and isinstance(snapshot, str) and snapshot and default_model:
            if snapshot == default_model:
                model_drift = "aligned"
            else:
                model_drift = "DRIFTED"
                drift_cost_direction = _drift_cost_direction(snapshot, default_model, cost_map)

    job_name = job.get("name") or job.get("id")
    return {
        "source": "hermes-native",
        "job_name": job_name if isinstance(job_name, str) and job_name else "無法查詢",
        "schedule_expr": schedule_expr,
        "deployed": True,  # 存在於 jobs.json 即代表已註冊(§4.2 路徑 B 第 3 步)
        "timer_active": "active" if job.get("enabled") else "inactive",
        "last_result": last_result,
        "next_trigger": next_run if isinstance(next_run, str) and next_run else "無法查詢",
        "last_trigger": last_run if isinstance(last_run, str) and last_run else "從未觸發",
        "model_drift": model_drift,
        "drift_cost_direction": drift_cost_direction,
    }


def _native_cron_rows() -> list[dict]:
    """路徑 B(source="hermes-native"):**純檔案讀取** root 與各 profile 的
    cron store jobs.json——不 import cron.jobs 的任何寫入函式(§0.5)。
    store 不存在/LOCALAPPDATA 未設 → 回傳空清單,不影響路徑 A(§4.2 退化)。"""
    if HERMES_HOME is None:
        return []
    stores = [HERMES_HOME / "cron" / "jobs.json"]
    profiles_dir = HERMES_HOME / "profiles"
    if profiles_dir.is_dir():
        for child in sorted(profiles_dir.iterdir()):
            if child.is_dir():
                stores.append(child / "cron" / "jobs.json")

    default_model = _global_model_default()
    cost_map = _model_cost_tier_map()
    rows = []
    for store in stores:
        if not store.is_file():
            continue
        try:
            raw = json.loads(store.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            continue  # 單一 store 壞 JSON 不中斷其他 store
        jobs = raw.get("jobs") if isinstance(raw, dict) else None
        if not isinstance(jobs, list):
            continue
        for job in jobs:
            if isinstance(job, dict):
                rows.append(_native_cron_row(job, default_model, cost_map))
    return rows


def get_cron_schedule_table() -> list[dict]:
    """統一排程健康表(§4.2):路徑 A(systemd)+路徑 B(hermes-native)
    合併,每筆帶 source 欄位。**兩路徑彼此獨立退化**——一邊失敗(含未預期
    例外)不影響另一邊,確保「至少看得到一半排程」優於「整表壞掉」。"""
    try:
        systemd_rows = _systemd_rows()
    except Exception:
        systemd_rows = []
    try:
        native_rows = _native_cron_rows()
    except Exception:
        native_rows = []
    return redact.scan_structure(systemd_rows + native_rows)


# ---------------------------------------------------------------------------
# 功能一 — Hermes session 列表(stage3 提案 §2)
# ---------------------------------------------------------------------------

# §2.3 UI 欄位白名單:不含 metadata 的 session_key/chat_id 等路由中繼資料
# (在資料層就收斂,API 回應與 UI 渲染兩層自然不含);list_sessions() 本身
# 不含 messages.content,iter_events() 一律不呼叫(DoD 第 4 條:不做點進看全文)。
SESSION_FIELD_ALLOWLIST = (
    "session_id", "session_source", "title", "model",
    "started_at", "ended_at", "message_count",
)


def _load_adapter_module():
    """以 importlib 從檔案路徑載入 hermes/session_adapter/adapter.py。

    HermesSessionAdapter 是唯讀類別(mode=ro + PRAGMA query_only,
    §0.5 判準「可安全 import」);用檔案路徑載入避免把 hermes/ 目錄掛進
    sys.path(那會讓 db.py 等寫入模組變成一個 import 語句的距離)。"""
    global _adapter_module
    if _adapter_module is None:
        spec = importlib.util.spec_from_file_location(
            "claudecodeos_hermes_session_adapter", ADAPTER_PATH
        )
        if spec is None or spec.loader is None:
            raise FileNotFoundError(f"無法載入 {ADAPTER_PATH}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _adapter_module = module
    return _adapter_module


def get_hermes_sessions(source: str | None = None, limit: int = 200) -> list[dict]:
    """唯讀列出 Hermes sessions(§2.2)。一律 snapshot=True——不假設 Hermes
    有沒有在跑(WAL 鎖競爭由 adapter 內部處理,DoD 第 2 條)。
    找不到 state.db(環境沒裝 Hermes/路徑不同)回傳 [],不噴例外。
    回傳結構只含 SESSION_FIELD_ALLOWLIST 欄位,遞迴不含 content。"""
    try:
        module = _load_adapter_module()
    except (FileNotFoundError, OSError, SyntaxError):
        return []
    try:
        adapter = module.HermesSessionAdapter(db_path=HERMES_STATE_DB, snapshot=True)
    except FileNotFoundError:
        return []  # 缺檔不是例外(比照 jobs_db_exists 慣例)
    except module.HermesSessionReadError:
        return []
    try:
        sessions = adapter.list_sessions(source=source)
    except module.HermesSessionReadError:
        return []
    finally:
        adapter.close()  # 清掉 snapshot temp 目錄(§2.2)

    # list_sessions() 不支援分頁:Python 層依 started_at 反排序取前 limit 筆
    sessions.sort(key=lambda s: s.get("started_at") or "", reverse=True)
    slim = [
        {key: session.get(key) for key in SESSION_FIELD_ALLOWLIST}
        for session in sessions[:limit]
    ]
    return redact.scan_structure(slim)
