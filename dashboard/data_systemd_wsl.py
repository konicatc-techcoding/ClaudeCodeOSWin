#!/usr/bin/env python3
"""dashboard/data_systemd_wsl.py — Windows 側經 WSL 查 systemd 狀態的唯讀資料層。

背景(2026-07-28 修正):dashboard/data.py::get_systemd_status() 直接跑裸的
`systemctl --user list-units ...`——那是 dashboard 還跑在 WSL 裡的舊設計。
readonly-api(dashboard/api.py,8799)如今跑在 Windows 側,`systemctl`
不存在 → FileNotFoundError → 回空 dict → UI 把「查不到」誤報成「未安裝」。
data.py 自 P1 起凍結(當時的 Streamlit 零改動鐵律;Streamlit app.py 已於
2026-08-15 退役,舊函式已無呼叫端但原樣保留、data.py 維持不動),
故 Windows 側的等效查詢放在本新模組,由 api.py 與 data_stage3.py 改用。

設計沿用 data_resident.py 的既有模式,不重造:

1. **分層守門、絕不喚醒 distro**:第一層直接複用
   data_resident._distro_state()(只跑 `wsl --list --verbose`,不會喚醒
   distro;data_update.py 已示範同一複用慣例)。distro 非 Running 直接回
   「WSL 未運作」結構,**不下任何 `wsl -d <distro>` 形式的指令**(那會把
   distro 拉起來,「觀測」就變成「改變系統狀態」)。此行為由
   test_data_systemd_wsl.py 以 mock 鎖定:守門未通過時,本模組的
   subprocess 完全未被呼叫。
2. **凍結指令常數**:subprocess 只會執行下方兩個凍結常數的唯讀查詢
   (list-units / list-timers),沒有其他 spawn 位點,無任何 systemd/wsl
   寫入動詞。test_data_systemd_wsl.py 與 scripts/webui_security_check.py
   第 10 項以靜態掃描鎖定位點封閉。
3. **誠實三分支**(status 欄位):
   - "ok"          → distro 在線且查得到;units/timers 為真實狀態
   - "wsl_down"    → distro 未運作(未探測,避免喚醒)
   - "unavailable" → wsl 指令不存在/逾時/查詢失敗/未預期錯誤
   **「查不到」不得再偽裝成「未安裝」**——只有 status == "ok" 且單元不在
   units 裡,才代表該單元真的未安裝。
4. **成本控制**:get_wsl_systemd_snapshot() 帶 5 秒 TTL 快取(比照
   data_resident 慣例);/api/systemd-status 與 /api/schedule-table 共用
   同一份快取,wsl 呼叫不出現在無快取的熱路徑。

parser 鏡像既有格式,不另立標準:list-units 四欄(unit/load/active/sub,
`--no-legend --plain`),輸出結構與 data.get_systemd_status() 相同
({unit: {"pid": "-", "last_exit": "active/sub", "load": ...}}),方便
既有顯示邏輯(webui 總覽、data_stage3 排程表)無痛換源。

list-timers 改用 `--output=json`(2026-07-28 真實環境驗證後修正):文字版
輸出的 LEFT/PASSED 欄右對齊,相鄰欄位間可能只剩**單一空格**(實例:
`CST         10min Tue 2026-...`、`19min ago hermes-rss.timer`),
以 2+ 空格切欄會整行切壞而被略過(data_stage3 原 `_list_timers_status`
的舊解析就踩了這個坑)。JSON 輸出零對齊問題:每項含 next/last(µs epoch
整數,0/null=無)、unit、activates;本模組把 epoch 轉成**本地時區**的
ISO 字串(如 `2026-07-28T10:11:56+08:00`)供前端直接顯示,0/缺失 → "n/a"
(data_stage3 既有語意:n/a=從未觸發)。容錯慣例:任何探測失敗/壞 JSON
一律優雅退化為明確結構,不噴例外、不影響其他 endpoint。
"""
import json
import subprocess
import time
from datetime import datetime, timezone

# 相容兩種匯入路徑（1-2 修正）:`dashboard/` 沒有 __init__.py,api.py 是把
# dashboard/ 插進 sys.path 後以 **top-level** 名稱匯入;但從 repo 根做
# `import dashboard.<mod>`（namespace package)時 top-level 名稱不存在 →
# ModuleNotFoundError。故先試相對匯入(有 parent package 時成立),失敗才退回
# top-level。兩條路徑都可用,現行 api.py 啟動方式行為不變。
try:
    from . import data_resident  # 複用:第一層 distro 守門(不喚醒)與 distro/timeout 常數
    from . import redact  # 輸出前掃描共用正本(慣例統一;本模組無憑證資料,防禦性)
except ImportError:  # 無 parent package(api.py 的 sys.path 匯入方式)
    import data_resident
    import redact

WSL_DISTRO = data_resident.WSL_DISTRO
WSL_TIMEOUT_SECONDS = data_resident.WSL_TIMEOUT_SECONDS

# --- 凍結的唯讀查詢常數(subprocess 只允許這兩個常數,無其他 spawn 位點)---
WSL_LIST_UNITS_COMMAND = (
    "wsl.exe", "-d", WSL_DISTRO, "--",
    "systemctl", "--user", "list-units",
    "--all", "--type=service,timer", "--no-legend", "--plain",
)
WSL_LIST_TIMERS_COMMAND = (
    "wsl.exe", "-d", WSL_DISTRO, "--",
    "systemctl", "--user", "list-timers", "--all", "--output=json",
)

CACHE_TTL_SECONDS = 5.0

STATUS_TEXT = {
    "ok": "查詢成功",
    "wsl_down": "WSL 未運作",
    "unavailable": "無法查詢",
}

_cache: tuple[float, dict] | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_list_units(stdout: str) -> dict:
    """與 data.get_systemd_status() 同一種 `list-units --no-legend --plain`
    四欄格式(unit/load/active/sub),只收 hermes-* 單元,輸出結構亦相同。"""
    result: dict = {}
    for line in stdout.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        unit, load, active, sub = parts[0], parts[1], parts[2], parts[3]
        if "hermes-" not in unit:
            continue
        result[unit] = {"pid": "-", "last_exit": f"{active}/{sub}", "load": load}
    return result


def _format_timer_timestamp(value) -> str:
    """systemd JSON 的 next/last 是 µs epoch 整數;0/null/缺失/非數值 → "n/a"
    (data_stage3 既有語意:n/a=從未觸發)。有值轉成**本地時區** ISO 字串
    (如 `2026-07-28T10:11:56+08:00`)——時區明確標注在 offset 裡,
    前端字串直接顯示,不受 locale 影響。"""
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        return "n/a"
    try:
        moment = datetime.fromtimestamp(value / 1_000_000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return "n/a"
    return moment.astimezone().isoformat(timespec="seconds")


def _parse_list_timers(stdout: str) -> dict:
    """`list-timers --all --output=json` 的 JSON 陣列(每項含 next/last µs
    epoch、unit、activates)。**刻意不用文字版輸出**:文字版 LEFT/PASSED 欄
    右對齊,相鄰欄位間可能只剩單一空格,以 2+ 空格切欄會整行切壞
    (2026-07-28 真實環境教訓,test_data_systemd_wsl.py 有負面測試固定)。
    壞 JSON/非預期結構 → 回 {},不噴例外。"""
    try:
        entries = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(entries, list):
        return {}
    result: dict = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        unit = entry.get("unit")
        if not isinstance(unit, str) or not unit.startswith("hermes-") \
                or not unit.endswith(".timer"):
            continue
        result[unit] = {
            "next": _format_timer_timestamp(entry.get("next")),
            "last": _format_timer_timestamp(entry.get("last")),
        }
    return result


def _probe_units() -> dict | None:
    """第二層:僅在 distro Running 時由 _probe() 呼叫。失敗回 None。"""
    try:
        proc = subprocess.run(
            WSL_LIST_UNITS_COMMAND,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=WSL_TIMEOUT_SECONDS,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    return _parse_list_units(proc.stdout or "")


def _probe_timers() -> dict | None:
    """第二層(timer 的 NEXT/LAST):僅在 distro Running 時由 _probe() 呼叫。
    失敗回 None——由 _probe() 退化為空 dict(next/last 顯示無法查詢),
    不拖垮整份快照。"""
    try:
        proc = subprocess.run(
            WSL_LIST_TIMERS_COMMAND,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=WSL_TIMEOUT_SECONDS,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    return _parse_list_timers(proc.stdout or "")


def _payload(status: str, distro: dict, reason: str | None,
             units: dict | None = None, timers: dict | None = None) -> dict:
    return {
        "checked_at": _now_iso(),
        "status": status,
        "status_text": STATUS_TEXT[status],
        "reason": reason,
        "distro": distro,
        "units": units,
        "timers": timers,
    }


def _probe() -> dict:
    """守門 + 三分支(未快取;get_wsl_systemd_snapshot() 才是對外入口)。"""
    running, distro_detail = data_resident._distro_state()
    distro = {"name": WSL_DISTRO, "running": running, "detail": distro_detail}
    if running is None:
        return _payload("unavailable", distro, f"WSL 狀態無法查詢:{distro_detail}")
    if not running:
        # 硬性原則:distro 未運作就止步,不執行任何 `wsl -d` 指令(不喚醒)
        return _payload(
            "wsl_down", distro,
            f"WSL distro {WSL_DISTRO} 未運作(未探測 systemd,避免喚醒 distro)",
        )
    units = _probe_units()
    if units is None:
        return _payload("unavailable", distro,
                        "wsl -d systemctl --user 查詢失敗,單元狀態無法查詢")
    timers = _probe_timers()
    return _payload("ok", distro, None, units=units,
                    timers=timers if timers is not None else {})


def get_wsl_systemd_snapshot() -> dict:
    """對外唯一入口(api.py /api/systemd-status 與 data_stage3 排程表共用):
    5 秒 TTL 快取 + 全域容錯(任何未預期例外 → unavailable)。"""
    global _cache
    now = time.monotonic()
    if _cache is not None and now - _cache[0] < CACHE_TTL_SECONDS:
        return _cache[1]
    try:
        payload = _probe()
    except Exception:
        payload = _payload(
            "unavailable",
            {"name": WSL_DISTRO, "running": None, "detail": "無法查詢"},
            "探測發生未預期錯誤",
        )
    payload = redact.scan_structure(payload)
    _cache = (now, payload)
    return payload
