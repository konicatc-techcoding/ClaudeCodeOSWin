#!/usr/bin/env python3
"""dashboard/data_resident.py — 背景常駐狀態燈號的唯讀資料層。

設計正本:docs/webui-service-control-proposal.md(v1)§1——**只實作唯讀
燈號部分**;寫入部分(重啟/關閉鍵)未核准,本模組零任何寫入入口:
subprocess 只會執行下方兩個凍結常數的唯讀查詢指令,沒有其他 spawn 位點。

**探測不得有副作用(硬性設計原則,提案 §1.1/§3 風險表第 1 項)**:
三層探測由粗到細,前一層不通過就止步——

1. WSL distro 層:`wsl --list --verbose`。這個指令只查 WSL 管理層狀態,
   **不會喚醒 distro**;distro 非 Running 直接回報紅燈,絕不執行任何
   `wsl -d <distro>` 形式的指令(那會把 distro 拉起來,「觀測」就變成
   「改變系統狀態」)。此行為由 test_data_resident.py 以 mock 鎖定:
   第一層回 Stopped 時,斷言第二層 subprocess 完全未被呼叫。
2. systemd 單元層:僅當第 1 層 Running 才跑
   `wsl -d Ubuntu -- systemctl --user list-units ...`(唯讀查詢)。
   輸出格式與 data.get_systemd_status() 相同(unit/load/active/sub 四欄,
   `--no-legend --plain`);data.py 自 P1 起凍結(Streamlit 零改動鐵律),
   故 parser 鏡像在本模組,由測試以相同 fixture 格式鎖定。
3. gateway 就緒層:僅當前兩層通過(常駐單元全部 active)才讀
   %LOCALAPPDATA%\\hermes\\gateway_state.json,並(2026-08-04 起)**驗證檔內
   pid 是否還活著**。gateway 啟動後約 3.5 分鐘才寫狀態檔
   (memory/hermes-gateway-init-slow 教訓),state != running / 檔案缺失 →
   黃燈「暖機中」,不誤報綠、也不誤報故障。

## gateway pid 活性驗證(2026-08-04,事故驅動)

**事故**:live gateway 於 08-03 08:04 死亡(pid 16224 不復存在),但本模組與
升級預檢的 service 欄一直顯示「gateway 就緒」約一天半——因為只讀狀態檔內容,
從不驗證 `pid` 是否還活著。狀態檔是 gateway **自己**寫的,它死了就沒人改。

**修正**:`check_gateway_pid_liveness()`(本模組;data_update 經
`_gateway_ready()` 共用同一份,不各寫一份):

- 唯讀、零副作用、便宜:純 ctypes 呼叫
  `OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)` → `GetExitCodeProcess` /
  `GetProcessTimes` / `QueryFullProcessImageNameW` → 立即 `CloseHandle`。
  **只申請查詢權限**,技術上不可能 terminate/suspend;無 subprocess、無新依賴
  (psutil 不在本 venv,不為此新增)。
- **pid 重用防誤判**:主判準比對 process 建立時間與狀態檔 `start_time`——
  寫入方(hermes gateway/status.py)在 Windows 上記的是
  `int(round(psutil.create_time()*100))`(epoch 百分秒),psutil 該值與
  GetProcessTimes 同源,故同一 process 必相符;容差 ±2 秒(比照 hermes 自身
  lifecycle_ledger 的防重用判準)。`start_time` 缺失時退而求其次以映像檔名
  是否為 python 弱驗證,並在 note 誠實標示。
- **語意**:狀態檔宣稱 running 但 pid 死/被重用 → 常駐燈**紅**、預檢 service
  欄 ready=false,detail 寫明「狀態檔宣稱 running 但 pid <N> 不存在(狀態檔
  停更於 <mtime>)」。pid 活且驗證過 → 照舊「gateway 就緒」。
- **刻意不用 mtime 新鮮度當死活訊號**:gateway 啟動後 ~3.5 分鐘才寫檔、平時
  也非高頻更新——mtime 舊不代表死;pid 檢查才是直接訊號。mtime 只在判死後
  用來標註「停更於何時」。
- **fail-open**:活性檢查自身失敗(非 Windows/ctypes 異常)→ 回 None,照舊
  信任狀態檔並在 note 註記「未驗證」——驗證機制故障不得誤報死。

五態(提案 §1.2 狀態機):

| light  | 條件 |
|--------|------|
| green  | distro Running + 常駐單元全 active + gateway 就緒(pid 活性驗證通過或無法驗證)|
| yellow | distro Running +(任一單元 activating,或全 active 但 gateway 未就緒/暖機中)|
| orange | distro Running + 部分常駐單元 active、部分不是 |
| red    | distro 未運作,或常駐單元全部停止,或 **gateway 狀態檔宣稱 running 但 pid 已死/被重用** |
| gray   | 無法查詢(wsl 不存在/逾時/查詢失敗/未預期錯誤)|

容錯慣例(比照 data.py/data_stage3.py):任何探測失敗一律優雅退化為
gray「無法查詢」,不噴例外、不影響其他 endpoint。
快取:get_resident_status() 帶 5 秒 TTL(提案 §1.3 建議值)——wsl 呼叫
比本機檔案讀取重,API 端短快取避免前端輪詢放大探測成本。
"""
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import redact  # 輸出前掃描共用正本(慣例統一過,本模組無憑證資料)

# --- 凍結的探測指令(唯讀;subprocess 只允許這兩個常數,無其他 spawn 位點)---
WSL_DISTRO = "Ubuntu"
WSL_LIST_COMMAND = ("wsl.exe", "--list", "--verbose")  # 不會喚醒 distro
WSL_SYSTEMCTL_COMMAND = (
    "wsl.exe", "-d", WSL_DISTRO, "--",
    "systemctl", "--user", "list-units",
    "--type=service", "--all", "--no-legend", "--plain",
)
WSL_TIMEOUT_SECONDS = 10

# 「常駐」判準單元清單(提案 §1.2:不硬編在前端,由資料層一份常數定義;
# 來源:hermes/systemd/README.md——常駐 service 為 worker 與 telegram,
# timer 驅動的單元不屬於「應該隨時在線」的判準)。
RESIDENT_UNITS = ("hermes-worker.service", "hermes-telegram.service")

# gateway 狀態檔(hermes-agent 側,Windows 本機檔案;讀取零副作用)
_local_app_data = os.environ.get("LOCALAPPDATA")
GATEWAY_STATE_PATH: Path | None = (
    Path(_local_app_data) / "hermes" / "gateway_state.json" if _local_app_data else None
)

CACHE_TTL_SECONDS = 5.0

LIGHT_TEXT = {
    "green": "背景常駐中",
    "yellow": "啟動中/暖機中",
    "orange": "部分服務未運作",
    "red": "背景服務未運作",
    "gray": "無法查詢",
}

_cache: tuple[float, dict] | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decode_wsl_output(raw: bytes) -> str:
    """`wsl.exe` 管理指令(--list 等)在 Windows 上輸出 UTF-16LE;
    帶 BOM 或含 NUL byte 就照 UTF-16LE 解,否則當 UTF-8(防未來版本改變)。"""
    if raw.startswith(b"\xff\xfe") or b"\x00" in raw:
        return raw.decode("utf-16-le", errors="replace").lstrip("﻿")
    return raw.decode("utf-8", errors="replace")


def _distro_state() -> tuple[bool | None, str]:
    """第一層:distro 是否 Running。回傳 (running, detail)。

    running 為 None = 無法查詢(gray);False = 未運作(red,**止步**,
    不進行第二層);True 才允許第二層探測。"""
    try:
        proc = subprocess.run(
            WSL_LIST_COMMAND, capture_output=True, timeout=WSL_TIMEOUT_SECONDS
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None, "wsl 指令不存在或逾時"
    if proc.returncode != 0:
        return None, f"wsl --list 失敗(exit {proc.returncode})"
    text = _decode_wsl_output(proc.stdout or b"")
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("*"):
            stripped = stripped[1:].strip()
        parts = stripped.split()
        if len(parts) >= 2 and parts[0] == WSL_DISTRO:
            state = parts[1]
            return state.lower() == "running", f"distro 狀態:{state}"
    return False, f"wsl --list 清單中找不到 {WSL_DISTRO}"


def _parse_wsl_systemctl(stdout: str) -> dict:
    """第二層輸出解析:與 data.get_systemd_status() 同一種
    `list-units --no-legend --plain` 四欄格式(unit/load/active/sub),
    只收 hermes-* 單元。data.py 凍結,故 parser 鏡像於此(docstring 說明)。"""
    result: dict = {}
    for line in stdout.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        unit, load, active, sub = parts[0], parts[1], parts[2], parts[3]
        if "hermes-" not in unit:
            continue
        result[unit] = {
            "state": active,
            "sub": sub,
            "load": load,
            "resident": unit in RESIDENT_UNITS,
        }
    return result


def _probe_units() -> dict | None:
    """第二層:僅在 distro Running 時由 _probe() 呼叫。失敗回 None(gray)。"""
    try:
        proc = subprocess.run(
            WSL_SYSTEMCTL_COMMAND,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=WSL_TIMEOUT_SECONDS,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    return _parse_wsl_systemctl(proc.stdout or "")


# --- gateway pid 活性驗證(2026-08-04;設計理由見模組 docstring 專節)--------
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000  # 只申請查詢權限,不可能操作 process
_STILL_ACTIVE_EXIT_CODE = 259                # GetExitCodeProcess:仍在運行
_WINERROR_ACCESS_DENIED = 5                  # OpenProcess 拒絕存取 = process 存在
_FILETIME_UNIX_EPOCH_100NS = 116_444_736_000_000_000  # 1601→1970 epoch 位移
_START_TIME_TOLERANCE_CS = 200               # ±2 秒(比照 hermes lifecycle_ledger)


def _windows_process_probe(pid: int) -> dict | None:
    """唯讀查詢一個 Windows process:存在?仍在運行?建立時間?映像檔?

    只用 ctypes 呼叫 OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION) →
    GetExitCodeProcess / GetProcessTimes / QueryFullProcessImageNameW →
    **立即 CloseHandle**。查詢權限技術上無法對 process 做任何操作;
    無 subprocess、無第三方依賴。回傳 dict 或 None(本平台/本次無法查詢,
    呼叫端 fail-open)。create_time_cs 為 epoch 百分秒(與狀態檔 start_time
    同單位,見 check_gateway_pid_liveness)。"""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(
            _PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            if ctypes.get_last_error() == _WINERROR_ACCESS_DENIED:
                # 存在但無權查詢(非本使用者的 process——gateway 同使用者,
                # 正常情況不會走到這;誠實回報「存在、身分未知」)
                return {"exists": True, "running": None,
                        "create_time_cs": None, "image": None}
            return {"exists": False, "running": False,
                    "create_time_cs": None, "image": None}
        try:
            running = None
            exit_code = wintypes.DWORD()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                running = exit_code.value == _STILL_ACTIVE_EXIT_CODE
            create_time_cs = None
            creation, exited, kernel_t, user_t = (wintypes.FILETIME() for _ in range(4))
            if kernel32.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exited),
                                        ctypes.byref(kernel_t), ctypes.byref(user_t)):
                filetime = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
                create_time_cs = int(round((filetime - _FILETIME_UNIX_EPOCH_100NS) / 1e5))
            image = None
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(len(buf))
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                image = buf.value or None
            return {"exists": True, "running": running,
                    "create_time_cs": create_time_cs, "image": image}
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return None


def check_gateway_pid_liveness(pid, expected_start_time) -> tuple[bool | None, str]:
    """**共用 helper**(常駐燈第三層與 data_update 預檢 service 欄同用一份,
    經 _gateway_ready() 收斂,不各寫一份):狀態檔宣稱的 gateway pid 是否
    還活著、且**是同一個 process**(防 pid 重用誤判)。

    回傳 (alive, note):
    - False → 確定死了/pid 已被別的 process 重用(呼叫端轉紅)
    - True  → 活著(note 說明驗證強度:start_time 相符/映像檔名弱驗證)
    - None  → 無法驗證(pid 欄缺失/非 Windows/查詢失敗)——fail-open,
      呼叫端照舊信任狀態檔,note 誠實註記未驗證(驗證機制故障不得誤報死)

    主判準 = 建立時間比對:狀態檔 start_time 由 gateway 寫入方以
    psutil create_time()*100(epoch 百分秒)記錄,與 GetProcessTimes 同源,
    同一 process 必相符;容差 ±2 秒。start_time 缺失 → 映像檔名弱驗證。"""
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return None, "狀態檔無有效 pid 欄位,活性未驗證"
    info = _windows_process_probe(pid)
    if info is None:
        return None, f"pid {pid} 活性無法驗證(平台不支援或查詢失敗)"
    if not info["exists"]:
        return False, f"pid {pid} 不存在"
    if info["running"] is False:
        return False, f"pid {pid} 已結束(process 物件殘留但已非運行中)"
    expected = expected_start_time \
        if isinstance(expected_start_time, (int, float)) \
        and not isinstance(expected_start_time, bool) else None
    create = info.get("create_time_cs")
    if expected is not None and create is not None:
        if abs(create - expected) <= _START_TIME_TOLERANCE_CS:
            return True, f"pid {pid} 運行中,建立時間與狀態檔 start_time 相符"
        return False, (f"pid {pid} 存在但建立時間與狀態檔 start_time 不符"
                       f"——pid 已被其他 process 重用")
    image = (info.get("image") or "")
    if image:
        name = image.replace("\\", "/").rsplit("/", 1)[-1].lower()
        if "python" in name:
            return True, (f"pid {pid} 運行中(start_time 不可比對,"
                          f"僅以映像檔名 {name} 弱驗證)")
        return False, f"pid {pid} 存在但映像檔為 {name}(非 python)——pid 已被重用"
    return True, f"pid {pid} 運行中(身分無法交叉驗證)"


def _gateway_ready() -> dict:
    """第三層:gateway 就緒判定(常駐燈在常駐單元全 active 時呼叫;
    data_update._probe_windows 亦直接呼叫本函式——活性驗證一份共用)。

    讀 gateway_state.json 的 gateway_state 欄位(白名單單值抽取):
    == "running" → **再驗證 pid 活性**(2026-08-04 事故修正,見模組 docstring):
    pid 死/被重用 → ready=False + dead=True(常駐燈紅);活著/無法驗證 → 就緒。
    其他值/檔案缺失/無法解析 → 未就緒(黃燈暖機中,3.5 分鐘慢啟動教訓內建
    於文字)。純檔案讀取 + ctypes 唯讀查詢,零副作用。"""
    path = GATEWAY_STATE_PATH
    if path is None:
        return {"ready": False, "state": None,
                "detail": "LOCALAPPDATA 未設定,無法讀取 gateway 狀態檔"}
    try:
        if not path.is_file():
            return {"ready": False, "state": None,
                    "detail": "gateway 狀態檔不存在——可能暖機中(啟動後約 3.5 分鐘才寫入)"}
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return {"ready": False, "state": None, "detail": "gateway 狀態檔無法解析"}
    state = raw.get("gateway_state") if isinstance(raw, dict) else None
    if state == "running":
        pid = raw.get("pid")
        alive, note = check_gateway_pid_liveness(pid, raw.get("start_time"))
        if alive is False:
            # 狀態檔是 gateway 自己寫的——它死了就沒人改,內容不可信
            return {
                "ready": False, "state": "running", "dead": True,
                "pid": pid if isinstance(pid, int) else None, "pid_alive": False,
                "detail": f"狀態檔宣稱 running 但{note}(狀態檔停更於 {mtime})",
                "mtime": mtime,
            }
        detail = "gateway 就緒" if alive else f"gateway 就緒(注意:{note})"
        return {"ready": True, "state": "running",
                "pid": pid if isinstance(pid, int) else None,
                "pid_alive": alive, "pid_note": note,
                "detail": detail, "mtime": mtime}
    return {
        "ready": False,
        "state": state if isinstance(state, str) else None,
        "detail": f"gateway 狀態:{state if isinstance(state, str) else '未知'}"
                  "(可能暖機中,約需 3.5 分鐘)",
        "mtime": mtime,
    }


def _payload(base: dict, light: str, detail: str) -> dict:
    return {**base, "light": light, "text": LIGHT_TEXT[light], "detail": detail}


def _probe() -> dict:
    """三層探測 + 五態狀態機(未快取;get_resident_status() 才是對外入口)。"""
    running, distro_detail = _distro_state()
    base = {
        "checked_at": _now_iso(),
        "distro": {"name": WSL_DISTRO, "running": running, "detail": distro_detail},
        "resident_units": list(RESIDENT_UNITS),
        "units": None,
        "gateway": None,
    }
    if running is None:
        return _payload(base, "gray", f"WSL 狀態無法查詢:{distro_detail}")
    if not running:
        # 硬性原則:distro 未運作就止步,不執行任何 `wsl -d` 指令(不喚醒)
        return _payload(
            base, "red",
            f"WSL distro {WSL_DISTRO} 未運作(未進行服務層探測,避免喚醒 distro)",
        )

    units = _probe_units()
    if units is None:
        return _payload(base, "gray", "systemctl --user 查詢失敗,服務狀態無法查詢")
    base["units"] = units

    states = {u: (units.get(u) or {}).get("state") for u in RESIDENT_UNITS}
    active = [u for u, s in states.items() if s == "active"]
    activating = [u for u, s in states.items() if s == "activating"]
    others = [u for u in RESIDENT_UNITS if u not in active and u not in activating]

    if len(active) == len(RESIDENT_UNITS):
        gateway = _gateway_ready()
        base["gateway"] = gateway
        if gateway["ready"]:
            return _payload(base, "green", "常駐單元全部 active,gateway 就緒")
        if gateway.get("dead"):
            # 2026-08-04 事故修正:狀態檔宣稱 running 但 pid 已死/被重用 → 紅
            # (不是黃「暖機中」——暖機是「還沒起來」,這是「起來過但死了」)
            return _payload(base, "red", f"gateway 已死:{gateway['detail']}")
        return _payload(base, "yellow", f"常駐單元全部 active;{gateway['detail']}")
    if activating and not others:
        return _payload(base, "yellow", f"單元啟動中:{'、'.join(activating)}")
    if active or activating:
        broken = [f"{u}({states[u] or '未載入'})" for u in others]
        return _payload(base, "orange", f"部分服務未運作:{'、'.join(broken)}")
    broken = [f"{u}({states[u] or '未載入'})" for u in RESIDENT_UNITS]
    return _payload(base, "red", f"常駐單元全部停止:{'、'.join(broken)}")


def get_resident_status() -> dict:
    """對外唯一入口:5 秒 TTL 快取 + 全域容錯(任何未預期例外 → gray)。"""
    global _cache
    now = time.monotonic()
    if _cache is not None and now - _cache[0] < CACHE_TTL_SECONDS:
        return _cache[1]
    try:
        payload = _probe()
    except Exception:
        payload = {
            "light": "gray",
            "text": LIGHT_TEXT["gray"],
            "detail": "探測發生未預期錯誤",
            "checked_at": _now_iso(),
            "distro": {"name": WSL_DISTRO, "running": None, "detail": "無法查詢"},
            "resident_units": list(RESIDENT_UNITS),
            "units": None,
            "gateway": None,
        }
    payload = redact.scan_structure(payload)
    _cache = (now, payload)
    return payload
