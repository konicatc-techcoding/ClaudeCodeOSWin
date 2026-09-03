#!/usr/bin/env python3
"""dashboard/data_repo_guard.py — 未推送 commit 離線保險（repo guard）的唯讀狀態層。

搭配 `scripts/repo_guard_bundle.ps1`（批次 1 止血）:那支 script 會把「不被任何
remote-tracking ref 涵蓋」的本地 commit 打成 git bundle 存到 repo 外
（`%LOCALAPPDATA%/AgentOS/repo-guard/<id>/`;以正斜線書寫,避免字串跳脫陷阱），並寫一份 `_latest.json` manifest。
本模組**只讀那份 manifest**，把「最近一次保險快照」的狀態呈現給 Web UI。

## 第一鐵律:本模組不執行 guard,也不執行任何東西

- **零 subprocess、零 spawn 原語**——全檔只有 `Path.read_text()` 一種 IO。
  端點被打開一百次也不會產生任何 bundle、不會碰任何 repo。
- 使用者刻意**沒有建排程**（2026-09-03 拍板:不做 Task Scheduler），所以這份
  manifest 多半是舊的。**誠實呈現「資料有多舊」比假裝是現況重要**——故一律
  附上 `age_hours` 與新鮮度分級，never/stale 一眼可辨。

## 語意邊界:本卡片講的**不是**「現在有沒有未推送 commit」

同一件事的兩個層次，呈現上不可混為一談:

| 誰 | 問題 | 資料來源 | 燈 |
|----|------|----------|-----|
| 升級預檢 `data_update._classify_comparison()` backup 組 | **現在**本機是否領先私有備份（ahead>0 = 未 push） | live git 查詢 | ahead>0 → **橙** |
| 本模組 | 萬一被 Install 鈕 `reset --hard` 吃掉，**救不救得回來** | 上次 guard 產出的 manifest | 綠/黃/灰，**永不用橙** |

**本模組刻意不使用 orange**——橙專屬於預檢的「未 push」，兩者不搶同一顆燈色。
另外 manifest 只在「當時有暴露」時才會被寫出（無暴露的執行不留檔），所以
`covered_commits` 是**當時保全的數量**，不是現在的暴露數;欄位命名與前端文案
都用「保全/快照」而非「目前暴露」，避免拿舊資料假裝現況。

## fail-soft（比照 data_stage3._effective_model_fields 的 unknown 慣例）

manifest 不存在／JSON 壞掉／權限不足／欄位缺漏 → 一律回明確的
`status="never"` 或 `"error"` + 灰燈 + 可讀說明，**不噴例外**，不讓整頁掛掉。
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

# guard 產物根目錄(與 scripts/repo_guard_bundle.ps1 的 -StoreRoot 預設值一致)。
# LOCALAPPDATA 不存在(非 Windows)→ None，所有函式優雅退化,不噴例外。
_local_app_data = os.environ.get("LOCALAPPDATA")
GUARD_STORE: Path | None = (
    Path(_local_app_data) / "AgentOS" / "repo-guard" if _local_app_data else None
)

# 受保護 target(id 必須與 script 的預設 target id 一致);測試以 fixture 覆寫。
GUARD_TARGETS: tuple[tuple[str, str], ...] = (
    ("hermes-agent", "Hermes Agent（Install 鈕會 reset --hard 的那個 repo）"),
    ("ClaudeCodeOSWin", "ClaudeCodeOSWin（本專案）"),
)

FRESH_HOURS = 24.0  # 24 小時內視為新鮮;超過即誠實標示過期
MANIFEST_NAME = "_latest.json"
# 正斜線書寫:PowerShell 一樣吃,且不會踩到 Python 的 \\r 跳脫(2026-09-03 實測踩過)
GUARD_SCRIPT_HINT = "powershell -ExecutionPolicy Bypass -File scripts/repo_guard_bundle.ps1"
NOTE = (
    "本卡片顯示的是「最近一次離線保險快照」，**不是目前的未推送狀態**——"
    "本端點唯讀，只讀 _latest.json，不會觸發 guard 執行（目前沒有排程，需手動重跑）。"
    "『現在有沒有未推送 commit』請看上方各端的〔私有備份/防重演基準〕組。"
)


def _age_text(hours: float) -> str:
    if hours < 1:
        return f"{int(hours * 60)} 分鐘前"
    if hours < 48:
        return f"{hours:.1f} 小時前"
    return f"{hours / 24:.1f} 天前"


def _read_manifest(path: Path) -> tuple[dict | None, str | None]:
    """回 (manifest, error)。任何讀取/解析問題一律轉成 error 字串,不拋例外。"""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, None  # 不是錯誤,是「從未執行過」
    except OSError as exc:  # 權限/IO——誠實回報,不猜
        return None, f"讀取失敗:{exc.__class__.__name__}"
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None, "manifest 不是合法 JSON（檔案可能損毀）"
    if not isinstance(data, dict):
        return None, "manifest 結構不符（頂層不是物件）"
    return data, None


def _parse_created_at(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    # script 寫出的是本機時間且不帶 tz(PowerShell ToString("s"))→ 補本機時區
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed


def _target_status(target_id: str, label: str, now: datetime) -> dict:
    base = {
        "id": target_id,
        "label": label,
        "status": "never",
        "light": "gray",
        "light_text": "從未執行",
        "summary": (
            "沒有找到保險快照——若此 repo 目前有未推送的 commit，"
            f"被 reset --hard 吃掉就救不回來。手動重跑:{GUARD_SCRIPT_HINT}"
        ),
        "created_at": None,
        "age_hours": None,
        "age_text": None,
        "bundle": None,
        "bundle_bytes": None,
        "covered_commits": None,
        "covered_refs": [],
        "dirty_files": None,
    }
    if GUARD_STORE is None:
        base["status"] = "error"
        base["summary"] = "無法定位保險存放位置（LOCALAPPDATA 未設定）——非 Windows 環境?"
        base["light_text"] = "無法查詢"
        return base

    manifest, error = _read_manifest(GUARD_STORE / target_id / MANIFEST_NAME)
    if error is not None:
        base["status"] = "error"
        base["light_text"] = "無法查詢"
        base["summary"] = f"{error}——保險狀態未知，不臆測。"
        return base
    if manifest is None:
        return base

    created = _parse_created_at(manifest.get("createdAt"))
    covered_refs = manifest.get("exposedRefs")
    base.update({
        "created_at": manifest.get("createdAt"),
        "bundle": manifest.get("bundle"),
        "bundle_bytes": manifest.get("bundleBytes"),
        "covered_commits": manifest.get("exposedCommits"),
        "covered_refs": covered_refs if isinstance(covered_refs, list) else [],
        "dirty_files": manifest.get("dirtyFiles"),
    })
    if created is None:
        base["status"] = "error"
        base["light_text"] = "無法查詢"
        base["summary"] = "manifest 缺少可解析的 createdAt——無法判斷快照有多舊，不臆測。"
        return base

    age_hours = max((now - created).total_seconds() / 3600.0, 0.0)
    covered = base["covered_commits"]
    covered_text = f"{covered} 個 commit" if isinstance(covered, int) else "若干 commit"
    base["age_hours"] = round(age_hours, 2)
    base["age_text"] = _age_text(age_hours)
    if age_hours <= FRESH_HOURS:
        base["status"] = "fresh"
        base["light"] = "green"
        base["light_text"] = "保險新鮮"
        base["summary"] = (
            f"{base['age_text']}保全了 {covered_text}，bundle 可還原。"
            "（此為快照，不代表目前暴露狀態）"
        )
    else:
        base["status"] = "stale"
        base["light"] = "yellow"
        base["light_text"] = "快照可能過期"
        base["summary"] = (
            f"最近一次保險是 {base['age_text']}（保全了 {covered_text}）。"
            f"之後新增的 commit 未必在保險內——手動重跑:{GUARD_SCRIPT_HINT}"
        )
    if isinstance(base["dirty_files"], int) and base["dirty_files"] > 0:
        base["summary"] += f" 註:當時工作樹有 {base['dirty_files']} 個未提交變更，bundle 不涵蓋未提交內容。"
    return base


# 整體燈:取最嚴重者。**不含 orange**(橙保留給預檢的「未 push」,見檔頭表格)。
_SEVERITY = {"gray": 3, "yellow": 2, "green": 1}


def get_repo_guard_status() -> dict:
    """唯讀:讀各 target 的 _latest.json,回保險快照狀態。永不執行 guard。"""
    now = datetime.now().astimezone()
    targets = [_target_status(tid, label, now) for tid, label in GUARD_TARGETS]
    overall = "gray"
    if targets:
        overall = max((t["light"] for t in targets), key=lambda c: _SEVERITY.get(c, 0))
    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "store_root": str(GUARD_STORE) if GUARD_STORE else None,
        "fresh_hours": FRESH_HOURS,
        "scheduled": False,  # 拍板不建 Task Scheduler → 一律手動重跑
        "note": NOTE,
        "overall_light": overall,
        "targets": targets,
    }
