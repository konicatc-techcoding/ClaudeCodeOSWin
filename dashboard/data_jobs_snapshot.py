#!/usr/bin/env python3
"""dashboard/data_jobs_snapshot.py — jobs.db「資料來源與資料年齡」的唯一判定處

## 問題（2026-09-04 查證：設計在實際部署拓撲上不成立）

runtime `jobs.db` 只存在 **WSL 部署複本**，唯讀 API 跑在 **Windows**。
`data.JOBS_DB_PATH`（= repo/hermes/jobs.db）在 Windows 根本不存在 →
`jobs_db_exists()` 一路回 False → **Jobs 頁、成本頁、`/api/status-counts` 一直是
空的，新鮮度燈號一直是灰的**，只是沒人注意到。經 UNC 直接讀也不行（WAL 鎖跨 SMB
拿不到，`database is locked`）——拓撲限制。

拍板方案：WSL 側定期用 SQLite 線上備份 API 把 db 快照到 Windows 可讀落點
（`scripts/jobs_db_snapshot.py`），Windows 側讀那份快照。**本模組是「現在讀到的
是哪一份、它有多舊」的單一判定處**——data.py（Jobs/成本/status-counts）與
data_jobs_freshness.py（燈號）都只經由這裡取得答案，不各自猜。

## 鐵律：讀到快照時，**絕不可以讓使用者以為是即時資料**

這是本次改動的核心風險。Jobs 頁原本隱含「這是當下狀態」，改讀快照後就不是了。
所以本模組回傳的每一份 payload 都**強制**帶著 `age_hours` / `age_text` /
`status`，呈現層（webui）一律顯示；判定分三級（門檻見下方常數／registry）：

| status    | 條件                    | 對「資料」的意義 | 對「燈號結論」的意義 |
|-----------|-------------------------|------------------|----------------------|
| `live`    | 本機就有 runtime db     | 即時             | 完全可信 |
| `fresh`   | 快照 ≤ fresh_hours      | 近乎即時         | 可信 |
| `stale`   | ≤ expire_hours          | 偏舊             | **綠燈降級為黃**（見下） |
| `expired` | > expire_hours          | 過期             | **整體轉灰、不下結論** |
| `never`   | 沒有任何快照            | 無資料           | 灰 |
| `error`   | manifest/檔案壞掉、位置解析不出 | 未知     | 灰 |

### 為什麼 stale 只降綠燈、不把橙燈也蓋掉

**壞消息不會因為資料舊而失效**：一份 3 小時前的快照顯示「window 內 48 小時零
completed」，那件事**確實發生過**，判「執行端死了」仍然成立。反過來「一切正常」
是關於**現在**的斷言，舊資料證不了它——所以 stale 時綠燈降黃（附「僅供參考」），
橙／黃維持。到 `expired` 就連壞消息的時效性也不敢保證，整體轉灰、不下任何結論
（但**把當時看到的異常寫在文字裡**，不靠顏色，也不假裝沒事）。

這條規則寫在 data_jobs_freshness.py，本模組只提供 status 與年齡。

## 唯讀 / 零副作用

全檔只有 `os.environ` 讀取、`Path.read_text()`、以及對快照檔的一次 `mode=ro`
sqlite 探測（`SELECT ... FROM sqlite_master`，確認確實是可讀的 jobs db）。
**零 subprocess**、不建檔、不觸發快照產生——快照由 WSL 側排程產出，讀端不催生。

## fail-soft

任何讀不到／解析不了 → `status="error"` + 可讀原因，不噴例外（比照
data_repo_guard.py 的慣例）。**灰 ≠ 沒事**，文案上必須講清楚。
"""
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 落點：與 scripts/jobs_db_snapshot.py 的 DEFAULT_DEST_DIR 是同一個目錄的
# 兩側寫法（WSL: /mnt/c/Users/razer/AppData/Local/AgentOS/jobs-snapshot）。
# 沿用 repo_guard 已建立的 %LOCALAPPDATA%\AgentOS\ 產物根；**不放 repo 內**
# （4MB 二進位每 30 分鐘變動會污染工作樹與 guard 的 dirty 計數），
# **更不放 hermes/jobs.db**（那個路徑的語意是 runtime db，放快照會讓人再次
# 誤以為 Windows 側有 runtime——正是這次誤判的成因）。
SNAPSHOT_ENV_VAR = "AGENTOS_JOBS_SNAPSHOT_DIR"
_local_app_data = os.environ.get("LOCALAPPDATA")
SNAPSHOT_DIR: Path | None = (
    Path(os.environ[SNAPSHOT_ENV_VAR]) if os.environ.get(SNAPSHOT_ENV_VAR)
    else (Path(_local_app_data) / "AgentOS" / "jobs-snapshot" if _local_app_data
          else None)
)

SNAPSHOT_NAME = "jobs.snapshot.db"
MANIFEST_NAME = "_latest.json"

# 年齡門檻。掛在既有 hermes-rss.service（每 30 分鐘）上產出，所以：
#   fresh_hours = 1.5 → 容忍連續錯過兩輪仍算新鮮
#   expire_hours = 6  → 錯過約 12 輪；到這裡已不可能只是偶發，
#                       資料不足以支撐任何「現在還好」的結論
# 可由 registry/jobs_watchdog.yaml 的 snapshot 區塊覆寫（見 _load_thresholds）。
DEFAULT_FRESH_HOURS = 1.5
DEFAULT_EXPIRE_HOURS = 6.0
_CONFIG_PATH = ROOT / "registry" / "jobs_watchdog.yaml"

SNAPSHOT_SCRIPT_HINT = (
    "WSL 端手動重跑：~/dev/ClaudeCodeOSWin/.venv/bin/python3 "
    "scripts/jobs_db_snapshot.py")

NOTE_SNAPSHOT = (
    "Windows 觀測面讀的是 WSL 定期推來的 **jobs.db 快照**，不是 runtime db"
    "（runtime 只存在 WSL；經 UNC 直接讀會被 WAL 鎖擋下）。因此這裡的數字有"
    "「資料年齡」——上方標示的時間就是快照拍攝時刻，之後發生的事不在裡面。"
)
NOTE_RUNTIME = (
    "本機就有 runtime jobs.db，直接唯讀查詢，無資料年齡問題。"
)


def _age_text(hours: float) -> str:
    # 「0 分鐘前」讀起來像壞掉的欄位；不到一分鐘就直說「不到 1 分鐘前」。
    if hours * 60 < 1:
        return "不到 1 分鐘前"
    if hours < 1:
        return f"{int(hours * 60)} 分鐘前"
    if hours < 48:
        return f"{hours:.1f} 小時前"
    return f"{hours / 24:.1f} 天前"


def _load_thresholds() -> tuple[float, float]:
    """讀 registry/jobs_watchdog.yaml 的 snapshot 區塊；缺就用本檔常數。

    這裡刻意 fail-soft（與 core.load_config 的 fail-closed 不同）：門檻缺漏時
    退回的預設值是**更保守**的一邊（較早判定過期＝較早轉灰），不會讓 UI 因為
    設定沒寫就樂觀地相信舊資料。
    """
    try:
        import yaml
        doc = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
        block = doc.get("snapshot") or {}
        fresh = float(block.get("fresh_hours", DEFAULT_FRESH_HOURS))
        expire = float(block.get("expire_hours", DEFAULT_EXPIRE_HOURS))
        if fresh <= 0 or expire < fresh:
            return DEFAULT_FRESH_HOURS, DEFAULT_EXPIRE_HOURS
        return fresh, expire
    except Exception:
        return DEFAULT_FRESH_HOURS, DEFAULT_EXPIRE_HOURS


def snapshot_db_path() -> Path | None:
    """快照 db 的完整路徑（不保證存在）。SNAPSHOT_DIR 解析不出來 → None。"""
    return (SNAPSHOT_DIR / SNAPSHOT_NAME) if SNAPSHOT_DIR else None


def _read_manifest(path: Path) -> tuple[dict | None, str | None]:
    """回 (manifest, error)。任何讀取/解析問題轉成 error 字串，不拋例外。"""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, None  # 不是錯誤，是「從未產出過」
    except OSError as exc:
        return None, f"manifest 讀取失敗：{exc.__class__.__name__}"
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None, "manifest 不是合法 JSON（檔案可能損毀）"
    if not isinstance(data, dict):
        return None, "manifest 結構不符（頂層不是物件）"
    return data, None


def _parse_captured_at(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    # 產出端寫的是 UTC ISO（帶 tz）；沒帶就保守當 UTC。
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _probe_readable(path: Path) -> str | None:
    """確認這份快照真的是「可唯讀查詢、且有 jobs 表」的 SQLite db。

    回 None＝可用；否則回失敗原因。**這一關是「快照損壞」的守門員**——沒有它，
    壞掉的檔會讓 /api/jobs 直接 500，而不是誠實地說「資料不可用」。
    刻意不用 PRAGMA integrity_check（要掃全檔）；開得起來 + jobs 表在 + 能數得
    出筆數，已足以擋掉截斷／非 db／schema 不符這三種實際會發生的壞法。
    """
    try:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error as exc:
        return f"快照無法唯讀開啟：{exc}"
    try:
        conn.execute("PRAGMA query_only=ON")
        row = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='jobs'"
        ).fetchone()
        if not row or row[0] == 0:
            return "快照裡沒有 jobs 表（檔案不是預期的 jobs.db 快照）"
        conn.execute("SELECT COUNT(*) FROM jobs").fetchone()
    except sqlite3.Error as exc:
        return f"快照讀取失敗（可能損毀）：{exc}"
    finally:
        conn.close()
    return None


def _base(now: datetime, fresh_hours: float, expire_hours: float) -> dict:
    return {
        "checked_at": now.isoformat(),
        "kind": "missing",          # runtime | snapshot | missing
        "status": "never",          # live | fresh | stale | expired | never | error
        "usable": False,            # 有沒有可查詢的 db
        "trusted_for_verdict": False,  # 能不能拿來下「現在還好」的結論
        "db_path": None,
        "captured_at": None,
        "age_hours": None,
        "age_text": None,
        "age_label": "無資料",
        "jobs_count": None,
        "snapshot_dir": str(SNAPSHOT_DIR) if SNAPSHOT_DIR else None,
        "fresh_hours": fresh_hours,
        "expire_hours": expire_hours,
        "reason": None,
        "note": NOTE_SNAPSHOT,
        "summary": (
            "找不到任何 jobs.db 快照——Windows 觀測面沒有資料可看"
            f"（runtime db 只在 WSL）。{SNAPSHOT_SCRIPT_HINT}"
        ),
    }


def resolve_jobs_source(*, runtime_db: Path | None = None,
                        now: datetime | None = None) -> dict:
    """判定「這台機器上，jobs 資料從哪裡來、有多舊」。唯讀、fail-soft。

    參數 runtime_db 只給呼叫端注入（data.py 會傳它的 JOBS_DB_PATH，測試也用它）。
    """
    now = now or datetime.now(timezone.utc)
    fresh_hours, expire_hours = _load_thresholds()
    info = _base(now, fresh_hours, expire_hours)

    runtime = Path(runtime_db) if runtime_db else (ROOT / "hermes" / "jobs.db")
    if runtime.exists():
        # 本機就是 runtime 所在（WSL 部署複本／未來若 API 搬過去）——無年齡問題。
        info.update({
            "kind": "runtime", "status": "live", "usable": True,
            "trusted_for_verdict": True, "db_path": str(runtime),
            "age_hours": 0.0, "age_text": "即時", "age_label": "即時（runtime db）",
            "note": NOTE_RUNTIME,
            "summary": "直接讀本機 runtime jobs.db，資料即時。",
        })
        return info

    snapshot = snapshot_db_path()
    if snapshot is None:
        info.update({
            "status": "error", "reason": "無法定位快照位置（LOCALAPPDATA 未設定）",
            "age_label": "無法查詢",
            "summary": "無法定位 jobs.db 快照位置——非 Windows 環境？資料狀態未知，不臆測。",
        })
        return info

    manifest, error = _read_manifest(SNAPSHOT_DIR / MANIFEST_NAME)
    if error is not None:
        info.update({
            "status": "error", "reason": error, "age_label": "無法查詢",
            "summary": f"{error}——無法確認快照有多舊，一律不當成現況使用。",
        })
        return info
    if manifest is None:
        if snapshot.exists():
            info.update({
                "status": "error",
                "reason": "有快照檔但沒有 manifest（無法判斷資料年齡）",
                "age_label": "無法查詢",
                "summary": "找到快照檔卻沒有 manifest——不知道它有多舊，"
                           "**寧可不用也不假裝是現況**。" + SNAPSHOT_SCRIPT_HINT,
            })
        return info  # manifest 與檔案都沒有 → never（_base 已是這個狀態）

    captured = _parse_captured_at(manifest.get("captured_at"))
    if captured is None:
        info.update({
            "status": "error", "reason": "manifest 缺少可解析的 captured_at",
            "age_label": "無法查詢",
            "summary": "manifest 沒有可解析的拍攝時間——無法判斷資料年齡，不臆測。",
        })
        return info

    if not snapshot.exists():
        info.update({
            "status": "error", "reason": f"manifest 存在但快照檔不見了（{snapshot.name}）",
            "captured_at": manifest.get("captured_at"), "age_label": "無法查詢",
            "summary": "manifest 說有快照，但檔案不在——資料不可用。" + SNAPSHOT_SCRIPT_HINT,
        })
        return info

    probe_error = _probe_readable(snapshot)
    age_hours = max((now - captured).total_seconds() / 3600.0, 0.0)
    age_text = _age_text(age_hours)
    info.update({
        "kind": "snapshot",
        "db_path": str(snapshot),
        "captured_at": manifest.get("captured_at"),
        "age_hours": round(age_hours, 2),
        "age_text": age_text,
        "jobs_count": manifest.get("jobs_count"),
    })
    if probe_error is not None:
        info.update({
            "status": "error", "usable": False, "trusted_for_verdict": False,
            "reason": probe_error, "age_label": "快照損壞",
            "summary": f"{probe_error}（快照拍攝於 {age_text}）——資料不可用，"
                       "不會拿壞掉的檔硬算出任何結論。" + SNAPSHOT_SCRIPT_HINT,
        })
        return info

    info["usable"] = True
    if age_hours <= fresh_hours:
        info.update({
            "status": "fresh", "trusted_for_verdict": True,
            "age_label": f"{age_text}的快照",
            "summary": f"資料為 {age_text}拍攝的 jobs.db 快照（{fresh_hours:g} 小時內視為新鮮）。",
        })
    elif age_hours <= expire_hours:
        info.update({
            "status": "stale", "trusted_for_verdict": False,
            "age_label": f"{age_text}的快照（偏舊）",
            "summary": (
                f"資料為 {age_text}拍攝的快照，已超過 {fresh_hours:g} 小時"
                "——之後發生的事不在裡面；「一切正常」這種結論不能只靠它下。"),
        })
    else:
        info.update({
            "status": "expired", "trusted_for_verdict": False,
            "age_label": f"{age_text}的快照（過期）",
            "summary": (
                f"資料為 {age_text}拍攝的快照，已超過 {expire_hours:g} 小時"
                "——快照產出本身可能也停了（WSL 沒開？rss 單元沒跑？）。"
                "數字僅供追溯，不代表現況。" + SNAPSHOT_SCRIPT_HINT),
        })
    return info
