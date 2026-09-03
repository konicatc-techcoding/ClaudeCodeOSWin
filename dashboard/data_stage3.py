#!/usr/bin/env python3
"""dashboard/data_stage3.py — P2:Stage 3 三項觀測功能的唯讀資料層。

設計正本:docs/stage3-dashboard-observability-proposal.md(v2)§2–§4;
載體:docs/webui-migration-proposal.md §4.3(P2)——經 dashboard/api.py 唯讀
API 曝露給 webui/。**不修改既有 dashboard/data.py**(P2 當時的 Streamlit 零改動
鐵律;Streamlit 已於 2026-08-15 退役,data.py 維持不動),Stage 3 新函式全部放這個新模組。路徑 A 的 systemd 狀態原複用
`data.get_systemd_status()`(stage3 提案 §4.4 DoD 第 6 項:不重新兜平行邏輯);
2026-07-28 起改複用 `data_systemd_wsl.get_wsl_systemd_snapshot()`——
readonly-api 跑在 Windows 側,裸 systemctl 不存在,舊路徑永遠 FileNotFoundError
而把「查不到」誤報成「未安裝」。新快照經 `wsl -d` 包裹、分層守門絕不喚醒
distro、帶 5 秒快取;「不另兜平行邏輯」的精神不變(單一共用快照,
data.get_systemd_status() 原為 Streamlit app.py 所用、退役後已無呼叫端,函式原樣保留)。

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

# 相容兩種匯入路徑（1-2 修正）:`dashboard/` 沒有 __init__.py,api.py 是把
# dashboard/ 插進 sys.path 後以 **top-level** 名稱匯入;但從 repo 根做
# `import dashboard.<mod>`（namespace package)時 top-level 名稱不存在 →
# ModuleNotFoundError。故先試相對匯入(有 parent package 時成立),失敗才退回
# top-level。兩條路徑都可用,現行 api.py 啟動方式行為不變。
try:
    from . import data_systemd_wsl  # 路徑 A 複用共用 systemd 快照(WSL 包裹、守門不喚醒、5 秒快取)
    from . import redact  # 憑證掃描共用正本(webui-migration §3.4:共用實作,不複製貼上)
except ImportError:  # 無 parent package(api.py 的 sys.path 匯入方式)
    import data_systemd_wsl
    import redact

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

# ⚠ 2026-08-04:本次「憑證 × 模型交叉檢查」需求**刻意不擴充**上面的 allowlist。
# 交叉檢查只用到 credential_pool 的 key(provider 名稱)、entry_count,以及
# 已在 allowlist 內的 `last_status`——三者都不是敏感值,足以完成判定。

# 「查不到生效模型」的統一呈現文字(fail-soft:給明確語意,不留空白、不噴例外)
UNKNOWN_MODEL_TEXT = "無法查詢"

# 配額耗盡的 last_status 值(Hermes 對 HTTP 429 的既有標記),供交叉檢查計數。
EXHAUSTED_STATUS = "exhausted"

# 全域 store 在 profiles 表中的固定 key(不是 profiles/ 下的目錄名)
GLOBAL_ROOT_KEY = "(global-root)"


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


def _global_model_config() -> dict | None:
    """全域 %LOCALAPPDATA%\\hermes\\config.yaml 的 model 白名單值(fallback 基準)。
    LOCALAPPDATA 未設 → None(呼叫端一律走 unknown 分支,不噴例外)。"""
    if HERMES_HOME is None:
        return None
    return _model_config_from_file(HERMES_HOME / "config.yaml")


def _profile_model_config(profile: str) -> dict | None:
    """單一 named profile 的 config.yaml model 白名單值;profile 名稱先過
    `_PROFILE_NAME_RE`(路徑組裝前的輸入驗證,杜絕 `../` 之類)。"""
    if HERMES_HOME is None or not isinstance(profile, str) or not _PROFILE_NAME_RE.match(profile):
        return None
    return _model_config_from_file(HERMES_HOME / "profiles" / profile / "config.yaml")


def _effective_model_fields(profile_cfg: dict | None, global_cfg: dict | None) -> dict:
    """「生效模型」三欄的**唯一判定處**(lane 表與憑證治理表共用同一套規則,
    不兜兩份平行邏輯):

    - profile 自己的 config.yaml 有 model.default → source "profile"。
    - 否則 fallback 全域 config.yaml → source "global"。
    - 兩邊都查不到 → source "unknown" + `UNKNOWN_MODEL_TEXT`/None
      (本模組 fail-soft 慣例:給明確的「無法查詢」語意,不噴例外、不留空白)。

    `(global-root)` 這條路徑由呼叫端傳 profile_cfg=None——它自己的設定檔
    **就是**全域 config.yaml,故一律落在 "global" 分支(語意正確,不是 fallback)。"""
    if profile_cfg and profile_cfg["default"]:
        return {
            "effective_model": profile_cfg["default"],
            "effective_model_source": "profile",
            "effective_provider": profile_cfg["provider"],
        }
    if global_cfg and global_cfg["default"]:
        return {
            "effective_model": global_cfg["default"],
            "effective_model_source": "global",
            "effective_provider": global_cfg["provider"],
        }
    return {
        "effective_model": UNKNOWN_MODEL_TEXT,
        "effective_model_source": "unknown",
        "effective_provider": None,
    }


def _annotate_effective_models(lanes: list[dict]) -> None:
    """對每條 lane 標注「實際生效模型」(就地補三個欄位,不動 registry 原欄位):

    - effective_model:native lane(無 hermes_profile)→ "(native session)";
      hermes_profile lane → 該 profile config.yaml 的 model.default,
      無 profile 值時 fallback 全域 config.yaml 的 model.default。
    - effective_model_source:"native" | "profile"(profile 自訂)|
      "global"(繼承全域)| "unknown"(兩邊都查不到)。
    - effective_provider:同來源的 model.provider(native/unknown → None)。

    資料來源是 %LOCALAPPDATA%\\hermes\\ 的 config.yaml(profile 實際設定),
    不是 registry——registry 的 model 欄位刻意為 null(不重複記載)。
    profile/global 的判定共用 `_effective_model_fields()`(見該函式)。"""
    global_cfg = _global_model_config()
    for lane in lanes:
        profile = lane.get("hermes_profile")
        if not isinstance(profile, str) or not _PROFILE_NAME_RE.match(profile):
            lane["effective_model"] = "(native session)"
            lane["effective_model_source"] = "native"
            lane["effective_provider"] = None
            continue
        lane.update(_effective_model_fields(_profile_model_config(profile), global_cfg))


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


def _count_exhausted_entries(pool: dict) -> int:
    """整個 store 的 credential_pool 中 last_status == "exhausted" 的條目數。
    `last_status` 早已在 §3.2 allowlist 內(非敏感),不需為此擴充 allowlist。"""
    total = 0
    for provider_block in pool.values():
        if not isinstance(provider_block, dict):
            continue
        for entry in provider_block.get("entries") or []:
            if isinstance(entry, dict) and entry.get("last_status") == EXHAUSTED_STATUS:
                total += 1
    return total


def _credential_model_consistency(row: dict) -> dict:
    """「憑證 × 模型」交叉一致性檢查(唯讀告警,2026-08-04)。

    存在理由:憑證軸(auth.json 存了哪些 provider 的憑證)與模型軸
    (config.yaml 現在設定用哪個 provider)是**兩條不同的軸**,過去 UI 上
    只看得到前者、欄位名又都叫 provider,導致「改了全域 model.provider 卻
    什麼都沒變」被誤判成 bug。這裡把兩軸對上,判定結果以結構化欄位輸出
    (light/text,沿用 data_resident.py / data_update.py 的燈號慣例),
    不留給前端硬算。

    判定(**純告警、零動作**——沒有任何修復/登入/清除入口),嚴重度
    **橙 > 黃 > 綠**,gray 自成一類(無從比對):

    - gray  :生效 provider 查不到、或 auth.json 不存在/壞掉 → 無從比對,
              誠實說「略過檢查」,不臆測。此時連 entry 計數本身都不可信,
              故**即使有 exhausted 條目也維持 gray**。
    - orange:生效 provider 不在本 store 的 credential_pool、或 entry_count
              為 0 → 「本 store 無此 provider 的憑證條目,**可能**依賴環境
              變數」。措辭刻意保守:實測有 provider 就是靠 OPENROUTER_API_KEY
              之類的環境變數在運作,斷言「壞掉」會是假警報。橙比黃嚴重,
              **已是橙者不因 exhausted 降為黃**。
    - yellow:本來會判綠(生效 provider 有憑證條目),但本 store 有
              exhausted 條目 → 降列燈為黃(2026-08-05 拍板)。理由:看板上
              「綠燈 + 紅色 exhausted 條目」的張力會讓人漏看;配額耗盡雖是
              **暫時**狀態(週期重置後自行恢復、非憑證缺失),但期間該 store
              確實不可用,列燈應該反映。黃色語彙與 data_resident.py 的暖機態
              同一顆(暫時性、會自行好轉),不另立新色。
    - green :生效 provider 在本 store 有 N 筆憑證條目,且無 exhausted 條目。

    exhausted 筆數同時以結構化欄位 exhausted_entry_count 輸出(不因改燈而
    移除);entry 層級的紅色標示由 UI 依已在 allowlist 內的 last_status 呈現。"""
    provider = row.get("effective_provider")
    pool = row.get("credential_pool")
    exhausted = _count_exhausted_entries(pool) if isinstance(pool, dict) else 0
    base = {"effective_provider": provider if isinstance(provider, str) else None,
            "entry_count": None, "exhausted_entry_count": exhausted}

    def _out(light: str, text: str) -> dict:
        if exhausted:
            text = (f"{text};本 store 有 {exhausted} 筆憑證條目配額耗盡"
                    "(last_status=exhausted)——屬暫時狀態,配額週期重置後會自行恢復,"
                    "不是憑證缺失")
            # 嚴重度 橙 > 黃 > 綠:只有原本判綠的才降為黃。
            # orange 維持 orange(更嚴重的問題不該被降級);
            # gray 維持 gray(該情形下計數本身就不可信,不據以升級告警)。
            if light == "green":
                light = "yellow"
        return {**base, "light": light, "text": text}

    if not isinstance(provider, str) or not provider:
        return _out("gray", "生效模型 provider 無法查詢,略過憑證交叉檢查")
    if row.get("error"):
        return _out("gray", f"auth.json 無法解析,無從比對生效模型 provider「{provider}」,"
                            "略過憑證交叉檢查")
    if not row.get("auth_json_exists"):
        return _out("orange", f"本 store 無 auth.json,生效模型 provider「{provider}」"
                              "在此無任何憑證條目,可能依賴環境變數或上層設定")
    if not isinstance(pool, dict) or provider not in pool:
        return _out("orange", f"生效模型 provider「{provider}」不在本 store 的 credential_pool 中,"
                              "可能依賴環境變數")
    entry_count = pool[provider].get("entry_count") if isinstance(pool[provider], dict) else None
    base["entry_count"] = entry_count if isinstance(entry_count, int) else None
    if not entry_count:
        return _out("orange", f"生效模型 provider「{provider}」在本 store 的憑證條目數為 0,"
                              "可能依賴環境變數")
    return _out("green", f"生效模型 provider「{provider}」在本 store 有 {entry_count} 筆憑證條目")


def get_hermes_credential_status() -> dict:
    """每個已知 Hermes profile(含 global-root)的憑證治理狀態(§3.3)。

    只含白名單欄位;profile 清單動態掃描 %LOCALAPPDATA%\\hermes\\profiles\\
    子目錄(目錄名稱不敏感),另固定包含 "(global-root)"。
    LOCALAPPDATA 未設 → {"available": False, ...},不噴例外。

    2026-08-04 起每列另補「第三條軸」(見 _effective_model_fields):
    effective_provider / effective_model / effective_model_source——**生效
    模型**的 provider 與 model.default,來源是 config.yaml,與同列既有的
    `providers`(此 store 存了哪些 provider 的**憑證**)是不同軸、不可混為
    一談;以及 credential_model_consistency(兩軸交叉的唯讀告警燈號)。"""
    if HERMES_HOME is None:
        return {"available": False, "reason": "LOCALAPPDATA 未設定,此環境無法查詢", "profiles": {}}

    profiles: dict = {GLOBAL_ROOT_KEY: _profile_credential_status(HERMES_HOME / "auth.json")}
    profiles_dir = HERMES_HOME / "profiles"
    if profiles_dir.is_dir():
        for child in sorted(profiles_dir.iterdir()):
            if child.is_dir():
                profiles[child.name] = _profile_credential_status(child / "auth.json")

    # 生效模型三欄 + 交叉檢查(全域 config.yaml 只讀一次,不逐 profile 重讀)。
    # (global-root) 的「自己的設定檔」就是全域 config.yaml → profile_cfg=None
    # → source 恆為 "global"(語意正確)。
    global_cfg = _global_model_config()
    for name, row in profiles.items():
        profile_cfg = None if name == GLOBAL_ROOT_KEY else _profile_model_config(name)
        row.update(_effective_model_fields(profile_cfg, global_cfg))
        row["credential_model_consistency"] = _credential_model_consistency(row)

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
    config = _global_model_config()
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
