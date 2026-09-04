#!/usr/bin/env python3
"""dashboard/data_jobs_freshness.py — jobs 管線「新鮮度」的唯讀狀態層（Web UI 燈號）。

## 為什麼要有這一塊

2026-08-05 起 CoS 執行鏈全線 dead_letter，31 天無人察覺。機制性原因不只是
「沒有人被通知」（那由 scripts/jobs_freshness_watchdog.py 補上了），還有
**觀測面缺了「新鮮度」這個維度**：`/api/status-counts` 是全時段累計，28 筆
cron dead_letter 混在 758 筆歷史 completed 裡，數字上一點都不刺眼。本模組補
的就是「打開 UI 能不能一眼看出來」。

## 與 repo_guard 卡片刻意不同的作法：即時計算，不讀狀態檔

`data_repo_guard.py` 讀 `_latest.json`、還得誠實標示資料年齡，因為那是腳本
產物。新鮮度不一樣——看門狗**不寫任何狀態檔**（即時評估完就送 Slack），而
dashboard 本來就有 jobs.db 的唯讀權限。所以這裡直接呼叫
`scripts/jobs_freshness_core.py` 的判準函式**即時計算**：

- 門檻只有一份真相（registry/jobs_watchdog.yaml），UI 與告警不會各判各的；
- 永遠反映當下，不依賴看門狗上次何時跑過（沒有「資料多舊」的問題）。

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
    "此區塊是**即時計算**（每次載入直接對 jobs.db 唯讀查詢），不是讀某次腳本"
    "留下的檔案，故沒有「資料多舊」的問題。判準與門檻與 Slack 看門狗"
    "（scripts/jobs_freshness_watchdog.py）共用同一份 registry/jobs_watchdog.yaml，"
    "兩者不會各判各的。本端點唯讀：不送任何通知、不寫任何東西、不觸發任何 job。"
    "灰燈的「進行中」是正常狀態（有進件、還沒有終結結果），不是警告。"
)


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


def _unavailable(reason: str, now: datetime) -> dict:
    """fail-soft 出口:灰燈 + 明確說明。**灰 ≠ 沒事**，文案上必須講清楚。"""
    return {
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
    db_path = Path(jobs_db) if jobs_db else JOBS_DB
    cfg_path = Path(config_path) if config_path else CONFIG_PATH
    try:
        config = core.load_config(cfg_path)
        findings = core.evaluate(db_path, config, now)
    except core.WatchdogError as exc:
        return _unavailable(str(exc), now)
    except Exception as exc:  # 任何非預期問題也不許讓整頁掛掉（fail-soft）
        return _unavailable(f"評估時發生非預期錯誤（{exc.__class__.__name__}）", now)

    sources = [_source_row(f, now) for f in findings]
    overall = "gray"
    if sources:
        overall = max((s["light"] for s in sources), key=lambda c: _SEVERITY.get(c, 0))
    bad = [s for s in sources if s["alerting"]]
    if bad:
        overall_text = f"{len(bad)}／{len(sources)} 個 source 異常"
        summary = "；".join(f"{s['source']}：{s['state_short']}" for s in bad)
        summary = f"異常：{summary}。Slack 看門狗以同一份判準評估，會在排程執行時告警。"
    else:
        overall_text = f"{len(sources)} 個 source 皆無異常"
        summary = "所有受監控 source 都沒有「跑都沒跑／全部失敗」的跡象。"
    defaults = config["defaults"]
    return {
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
