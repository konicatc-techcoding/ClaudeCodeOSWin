#!/usr/bin/env python3
"""dashboard/data_jobs_freshness.py — jobs 管線「新鮮度」的唯讀狀態層（Web UI 燈號）。

## 為什麼要有這一塊

2026-08-05 起 CoS 執行鏈全線 dead_letter，31 天無人察覺。機制性原因不只是
「沒有人被通知」（那由 scripts/jobs_freshness_watchdog.py 補上了），還有
**觀測面缺了「新鮮度」這個維度**：`/api/status-counts` 是全時段累計，28 筆
cron dead_letter 混在 758 筆歷史 completed 裡，數字上一點都不刺眼。本模組補
的就是「打開 UI 能不能一眼看出來」。

## 資料來源：Windows 側讀的是**快照**，不是 runtime db（2026-09-04 拓撲修正）

原本這裡寫著「即時計算，沒有資料多舊的問題」——**那句話在實際部署位置上是錯的**。
runtime `jobs.db` 只存在 WSL 部署複本，而這支 API 跑在 Windows：先前
`jobs_db_exists()` 一路回 False，燈號其實一直是灰的（Jobs／成本／status-counts
也一直是空的）。現在改為：WSL 側定期以 SQLite 線上備份 API 推一份快照到
`%LOCALAPPDATA%\AgentOS\jobs-snapshot\`，本模組讀那一份。

判準與門檻仍只有一份真相（registry/jobs_watchdog.yaml ＋
`scripts/jobs_freshness_core.py`），UI 與 Slack 看門狗不會各判各的（看門狗在
WSL 側跑，讀的是 runtime db——**它才是權威告警路徑**）。

### 快照年齡必須進入判準（本次改動的重點）

「rss 9 分鐘前成功」若是從 6 小時前的快照算出來的，那個結論就是假的。故：

| 資料狀態 | 對燈號的處理 |
|----------|--------------|
| `live`（本機有 runtime db）／`fresh` | 照常判定 |
| `stale`（超過 fresh 門檻） | **綠燈降黃**、文字註明「僅供參考」；橙／黃不動——
  壞消息不會因為資料舊而失效（那件事確實發生過），但「一切正常」是關於**現在**
  的斷言，舊資料證不了它 |
| `expired`（超過 expire 門檻） | **整體轉灰、每列轉灰、不下任何結論**，但把當時
  看到的異常寫進 summary 文字（不靠顏色，也不假裝沒事） |
| `error`／`never`（快照壞掉／不存在） | 灰燈 + 誠實原因（fail-soft） |

資料年齡的判定住在 `dashboard/data_jobs_snapshot.py`（單一判定處，data.py 的
Jobs／成本／status-counts 也用它），本模組只消費它的結論。

## 唯讀 / 零副作用

core 是純函式 + sqlite `mode=ro` 唯讀查詢，**零 subprocess**（送 Slack 的
`send_alert` 留在 watchdog 那一半，本模組不 import 它）。因此本端點：
**不會送任何告警、不會寫任何東西、不會觸發任何 job**。

## fail-soft（比照 data_stage3._effective_model_fields 的 unknown 慣例）

core 對設定/DB 問題是 fail-closed（raise）——那是對的，看門狗絕不能靜默降級
成「沒事」。但 UI 需要的是 fail-soft：設定檔缺檔／jobs.db 不存在／解析失敗
→ 一律轉成 `status="unavailable"` + **灰燈** + 可讀原因，不噴例外、不讓整頁掛掉。
灰燈的語意是「無法判斷」，**不是**「沒事」，文案上明講。

## 五態 → 燈色（與看門狗的五態一對一，不新增狀態）

| state             | 燈     | 理由 |
|-------------------|--------|------|
| trigger_dead      | orange | 觸發端死了——最高嚴重度 |
| executor_dead     | orange | 執行端死了（2026-08 的情況）——同上 |
| executor_degraded | yellow | 部分退化，還有 completed |
| healthy           | green  | 健康 |
| inconclusive      | gray   | **正常的「還在跑」**——刻意不亮警示色（不是警告） |

刻意不使用 red：紅在本系統既有語意是常駐/服務層級的「不可用」
（data_resident / update card），管線新鮮度不搶那顆燈。
本模組的 orange 與升級預檢的「未 push」橙不在同一頁、也不同 class 命名空間
（`fresh-light-*` vs `update-light-*`／`guard-light-*`），語意不打架。
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# 判準的單一真相。**只 import core**（純函式 + sqlite 唯讀），不 import
# jobs_freshness_watchdog（那一半持有 subprocess 與 Slack 送信）。
import jobs_freshness_core as core  # noqa: E402

import data_jobs_snapshot  # noqa: E402（jobs 資料來源與資料年齡的單一判定處）

CONFIG_PATH = core.DEFAULT_CONFIG
JOBS_DB = core.DEFAULT_JOBS_DB

# 五態 → 燈色（見檔頭表格）。UI 不自行推導顏色，一律用後端這份對應。
STATE_LIGHTS = {
    core.STATE_TRIGGER_DEAD: "orange",
    core.STATE_EXECUTOR_DEAD: "orange",
    core.STATE_EXECUTOR_DEGRADED: "yellow",
    core.STATE_HEALTHY: "green",
    core.STATE_INCONCLUSIVE: "gray",
}

# 整體燈取最嚴重者。gray（inconclusive/無法判斷）排在 green 之下——
# 「還在跑」不該把一整面板拉成灰，但全部都無結論時整體就是灰。
_SEVERITY = {"orange": 4, "yellow": 3, "green": 2, "gray": 1}

_STATE_SHORT = {
    core.STATE_TRIGGER_DEAD: "觸發端死了",
    core.STATE_EXECUTOR_DEAD: "執行端死了",
    core.STATE_EXECUTOR_DEGRADED: "執行端退化",
    core.STATE_HEALTHY: "健康",
    core.STATE_INCONCLUSIVE: "進行中",
}

NOTE = (
    "判準與門檻與 Slack 看門狗（scripts/jobs_freshness_watchdog.py）共用同一份 "
    "registry/jobs_watchdog.yaml，兩者不會各判各的。**資料年齡**見上方標示："
    "Windows 觀測面讀的是 WSL 定期推來的 jobs.db 快照（runtime db 只存在 WSL，"
    "經 UNC 直接讀會被 WAL 鎖擋下），快照偏舊時綠燈會降級、過期時整體轉灰——"
    "舊資料可以證明「出過事」，但證明不了「現在還好」。本端點唯讀：不送任何通知、"
    "不寫任何東西、不觸發任何 job。灰燈的「進行中」是正常狀態（有進件、還沒有"
    "終結結果），不是警告。"
)

# 資料年齡對「結論」的處理（理由見檔頭表格）。這裡只有語意，沒有門檻數字
# ——門檻住在 data_jobs_snapshot.py／registry/jobs_watchdog.yaml。
_DATA_STALE_NOTE = (
    "⚠️ 這份結論算自偏舊的快照：異常（橙／黃）仍然成立（那件事確實發生過），"
    "但「健康」只降級為黃色的『僅供參考』——舊資料證明不了現在還好。")
_DATA_EXPIRED_NOTE = (
    "⚠️ 資料已過期，**整體轉灰、不下任何結論**。連快照產出本身可能都停了"
    "（WSL 沒開？掛載快照的單元沒跑？）。下方數字只能當歷史追溯看。")


def _age_text(hours: float) -> str:
    if hours < 1:
        return f"{int(hours * 60)} 分鐘前"
    if hours < 48:
        return f"{hours:.1f} 小時前"
    return f"{hours / 24:.1f} 天前"


def _last_completed_age(value, now: datetime) -> tuple[float | None, str]:
    """把 last_completed_at 轉成年齡（小時）與人話。解析不了就誠實留白。"""
    if not isinstance(value, str) or not value:
        return None, "（從未成功過）"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None, "（時間戳無法解析）"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    hours = max((now - parsed).total_seconds() / 3600.0, 0.0)
    return round(hours, 2), _age_text(hours)


def _data_block(info: dict) -> dict:
    """把資料來源/年齡攤平成 payload 欄位（呈現層一律顯示，不可省略）。"""
    return {
        "data_source": info["kind"],            # runtime | snapshot | missing
        "data_status": info["status"],          # live | fresh | stale | expired | never | error
        "data_captured_at": info["captured_at"],
        "data_age_hours": info["age_hours"],
        "data_age_text": info["age_text"],
        "data_age_label": info["age_label"],
        "data_trusted": info["trusted_for_verdict"],
        "data_summary": info["summary"],
        "data_note": info["note"],
        "data_fresh_hours": info["fresh_hours"],
        "data_expire_hours": info["expire_hours"],
        "data_snapshot_dir": info["snapshot_dir"],
        "data_jobs_count": info["jobs_count"],
    }


def _unavailable(reason: str, now: datetime, info: dict | None = None) -> dict:
    """fail-soft 出口:灰燈 + 明確說明。**灰 ≠ 沒事**，文案上必須講清楚。"""
    payload = {
        "checked_at": now.isoformat(),
        "status": "unavailable",
        "available": False,
        "reason": reason,
        "note": NOTE,
        "config_path": str(CONFIG_PATH),
        "jobs_db": str(JOBS_DB),
        "overall_light": "gray",
        "overall_text": "無法判斷",
        "summary": f"無法評估 jobs 管線新鮮度：{reason}（灰燈代表「無法判斷」，不代表沒事）",
        "thresholds": None,
        "alerting_states": list(core.ALERTING_STATES),
        "sources": [],
    }
    if info is not None:
        payload.update(_data_block(info))
        payload["jobs_db"] = info["db_path"] or payload["jobs_db"]
    return payload


def _source_row(finding: dict, now: datetime) -> dict:
    state = finding["state"]
    counts = finding["counts"]
    age_hours, age_text = _last_completed_age(counts.get("last_completed_at"), now)
    return {
        "source": finding["source"],
        "description": finding.get("description"),
        "state": state,
        "state_label": core.STATE_LABEL.get(state, state),
        "state_short": _STATE_SHORT.get(state, state),
        # 燈色由後端決定（與看門狗五態一對一）；UI 只渲染，不重算規則。
        "light": STATE_LIGHTS.get(state, "gray"),
        # 資料年齡造成的降級（見檔頭表格）由呼叫端覆寫這兩欄；預設是「沒降級」。
        # 保留原始燈色，讓「為什麼變黃/變灰」可被追溯，而不是無聲改掉。
        "light_before_data_age": None,
        "data_stale": False,
        "alerting": state in core.ALERTING_STATES,
        "reason": finding["reason"],
        "expect_enqueue": finding["expect_enqueue"],
        "lookback_hours": finding["lookback_hours"],
        "enqueued": counts["enqueued"],
        "completed": counts["completed"],
        "dead_letter": counts["dead_letter"],
        "stuck": counts["stuck"],
        "last_completed_at": counts["last_completed_at"],
        "last_completed_age_hours": age_hours,
        "last_completed_age_text": age_text,
    }


def get_jobs_freshness(*, jobs_db: Path | None = None,
                       config_path: Path | None = None,
                       now: datetime | None = None) -> dict:
    """唯讀:即時評估各 source 的管線新鮮度，回燈號 payload。

    參數只給測試注入用；正式路徑一律走 registry/jobs_watchdog.yaml 與
    hermes/jobs.db。**永不送告警、永不寫入。**
    """
    now = now or datetime.now(timezone.utc)
    # 資料從哪來、有多舊：單一判定處（data_jobs_snapshot）。呼叫端顯式指定
    # jobs_db 時仍走同一支——它若存在就會被判成 runtime/live，測試因此不受影響。
    info = data_jobs_snapshot.resolve_jobs_source(
        runtime_db=Path(jobs_db) if jobs_db else JOBS_DB, now=now)
    if not info["usable"]:
        # 沒有可查的資料（Windows 無 runtime、快照從未產出／壞掉／不見了）。
        return _unavailable(
            info["reason"] or "找不到任何可查詢的 jobs.db（本機無 runtime db，也沒有 WSL 推來的快照）",
            now, info)
    db_path = Path(info["db_path"])
    cfg_path = Path(config_path) if config_path else CONFIG_PATH
    try:
        config = core.load_config(cfg_path)
        findings = core.evaluate(db_path, config, now)
    except core.WatchdogError as exc:
        return _unavailable(str(exc), now, info)
    except Exception as exc:  # 任何非預期問題也不許讓整頁掛掉（fail-soft）
        return _unavailable(f"評估時發生非預期錯誤（{exc.__class__.__name__}）", now, info)

    sources = [_source_row(f, now) for f in findings]
    bad = [s for s in sources if s["alerting"]]
    data_status = info["status"]
    if bad:
        overall_text = f"{len(bad)}／{len(sources)} 個 source 異常"
        summary = "；".join(f"{s['source']}：{s['state_short']}" for s in bad)
        summary = f"異常：{summary}。Slack 看門狗以同一份判準評估，會在排程執行時告警。"
    else:
        overall_text = f"{len(sources)} 個 source 皆無異常"
        summary = "所有受監控 source 都沒有「跑都沒跑／全部失敗」的跡象。"

    # --- 快照年齡進入判準（見檔頭表格）---
    if data_status == "expired":
        # 過期：整體與每一列都轉灰、不下結論；但異常內容照樣寫在文字裡。
        for row in sources:
            row["light_before_data_age"] = row["light"]
            row["light"] = "gray"
            row["data_stale"] = True
        overall = "gray"
        overall_text = "資料過期，無法判斷"
        seen = ("；".join(f"{s['source']}：{s['state_short']}" for s in bad)
                if bad else "當時所有 source 都沒有異常跡象")
        summary = (f"{info['summary']} {_DATA_EXPIRED_NOTE} "
                   f"快照當時看到的狀態：{seen}。"
                   "權威判定請看 WSL 側 Slack 看門狗（它讀的是 runtime db）。")
    elif data_status == "stale":
        # 偏舊：綠降黃（「現在還好」證不了），橙／黃維持（壞消息不會過期）。
        for row in sources:
            row["data_stale"] = True
            if row["light"] == "green":
                row["light_before_data_age"] = "green"
                row["light"] = "yellow"
                row["state_short"] = f"{row['state_short']}（資料偏舊，僅供參考）"
        overall = max((s["light"] for s in sources),
                      key=lambda c: _SEVERITY.get(c, 0)) if sources else "gray"
        summary = f"{info['summary']} {_DATA_STALE_NOTE} {summary}"
    else:
        overall = max((s["light"] for s in sources),
                      key=lambda c: _SEVERITY.get(c, 0)) if sources else "gray"
        if info["kind"] == "snapshot":
            summary = f"（依據 {info['age_text']}的快照）{summary}"

    defaults = config["defaults"]
    return {
        **_data_block(info),
        "checked_at": now.isoformat(),
        "status": "ok",
        "available": True,
        "reason": None,
        "note": NOTE,
        "config_path": str(cfg_path),
        "jobs_db": str(db_path),
        "overall_light": overall,
        "overall_text": overall_text,
        "summary": summary,
        # 門檻原樣回傳(來自 registry/jobs_watchdog.yaml)——UI 顯示用，
        # 讓「為什麼是這個燈」可被使用者自行驗證，而不是黑盒。
        "thresholds": {
            "lookback_hours": defaults["lookback_hours"],
            "min_expected_enqueued": defaults["min_expected_enqueued"],
            "stuck_backlog_hours": defaults["stuck_backlog_hours"],
            "dead_letter_ratio_threshold": defaults["dead_letter_ratio_threshold"],
            "min_terminal_sample": defaults["min_terminal_sample"],
        },
        "alerting_states": list(core.ALERTING_STATES),
        "sources": sources,
    }
