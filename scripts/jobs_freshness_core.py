#!/usr/bin/env python3
"""scripts/jobs_freshness_core.py — jobs 管線新鮮度判準的**單一真相**（純函式核心）

原本這些函式全部住在 scripts/jobs_freshness_watchdog.py 裡。2026-09-04 把
「判準」與「告警」切開，理由只有一個：**Web UI 的燈號與 Slack 告警必須用
同一套判準與同一份門檻**——否則會出現「UI 說綠、Slack 說死」這種比沒有燈
更糟的狀態。抽出後的相依關係：

    registry/jobs_watchdog.yaml   ← 唯一門檻來源（兩邊都讀它，無人硬編數字）
              |
        jobs_freshness_core.py    ← 唯一判準（classify/evaluate）
              |-- jobs_freshness_watchdog.py     （排程執行 → Slack 告警）
              +-- dashboard/data_jobs_freshness.py（唯讀 API → Web UI 燈號）

## 本檔的硬性約束

1. **零 subprocess、零 spawn 原語**——全檔的外部作用只有 sqlite3 唯讀查詢
   與 `Path.read_text()`（讀 YAML）。dashboard 端點被打開一百次也不會送出
   任何通知、不會觸發任何執行。（dashboard/test_data_jobs_freshness.py 有
   AST 靜態斷言鎖定這件事。）
2. **對 jobs.db 唯讀**：`file:...?mode=ro` URI ＋ `PRAGMA query_only=ON`
   雙保險；全檔無任何 INSERT/UPDATE/DELETE/CREATE。不建檔、不 migrate。
3. **不硬編門檻**：所有數字來自 registry/jobs_watchdog.yaml。
4. **本層 fail-closed**（缺設定／DB 讀不到 → raise WatchdogError）。UI 要的
   是 fail-soft，那層轉換在 dashboard/data_jobs_freshness.py 做——**把
   fail-soft 做進這一層會讓看門狗靜默降級成「沒事」，那正是 2026-08 那場
   31 天靜默的成因**。

五態判準的完整說明見 jobs_freshness_watchdog.py 檔頭與 registry/jobs_watchdog.yaml。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "registry" / "jobs_watchdog.yaml"
DEFAULT_JOBS_DB = ROOT / "hermes" / "jobs.db"

STATE_HEALTHY = "healthy"
STATE_INCONCLUSIVE = "inconclusive"
STATE_TRIGGER_DEAD = "trigger_dead"
STATE_EXECUTOR_DEAD = "executor_dead"
STATE_EXECUTOR_DEGRADED = "executor_degraded"

ALERTING_STATES = (STATE_TRIGGER_DEAD, STATE_EXECUTOR_DEAD, STATE_EXECUTOR_DEGRADED)

STATE_LABEL = {
    STATE_HEALTHY: "健康",
    STATE_INCONCLUSIVE: "進行中（無結論，不告警）",
    STATE_TRIGGER_DEAD: "觸發端死了（連 enqueue 都沒有）",
    STATE_EXECUTOR_DEAD: "執行端死了（有進件、零 completed）",
    STATE_EXECUTOR_DEGRADED: "執行端退化（dead_letter 比例超標）",
}

_DEFAULT_KEYS = ("lookback_hours", "min_expected_enqueued", "stuck_backlog_hours",
                 "dead_letter_ratio_threshold", "min_terminal_sample")


class WatchdogError(RuntimeError):
    """判準層失敗（設定／DB）——fail-closed。看門狗據此 exit 1；UI 據此轉灰燈。"""


# ---------- 設定 ----------

def load_config(path: Path) -> dict:
    """讀 registry/jobs_watchdog.yaml。任何缺漏一律 fail-closed（不套用
    隱含預設值），因為「門檻靜默變成 0」會讓看門狗永遠不叫。"""
    if not path.exists():
        raise WatchdogError(f"設定檔不存在：{path}")
    try:
        import yaml  # lazy：只有真的要讀設定時才需要 pyyaml
    except ImportError as exc:  # pragma: no cover - 環境問題
        raise WatchdogError(f"缺少 pyyaml，無法讀設定：{exc}") from exc
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise WatchdogError(f"設定檔解析失敗（{path}）：{exc}") from exc
    if not isinstance(doc, dict):
        raise WatchdogError(f"設定檔格式錯誤（{path}）：頂層必須是 mapping")

    defaults = doc.get("defaults")
    if not isinstance(defaults, dict):
        raise WatchdogError("設定檔缺少 defaults 區塊")
    missing = [k for k in _DEFAULT_KEYS if k not in defaults]
    if missing:
        raise WatchdogError(f"設定檔 defaults 缺少欄位：{', '.join(missing)}")

    sources = doc.get("sources")
    if not isinstance(sources, list) or not sources:
        raise WatchdogError("設定檔 sources 必須是非空 list")
    for src in sources:
        if not isinstance(src, dict) or not src.get("id"):
            raise WatchdogError(f"sources 項目格式錯誤：{src!r}")
        if "expect_enqueue" not in src:
            raise WatchdogError(f"source {src.get('id')!r} 缺少 expect_enqueue")

    alert = doc.get("alert")
    if not isinstance(alert, dict) or not alert.get("channel"):
        raise WatchdogError("設定檔缺少 alert.channel")
    alert.setdefault("send_cli", "hermes")
    alert.setdefault("message_key_prefix", "agentos-watchdog")
    return doc


def _source_setting(source: dict, defaults: dict, key: str):
    return source[key] if key in source else defaults[key]


# ---------- 唯讀查詢 ----------

def _read_only_conn(path: Path) -> sqlite3.Connection:
    """雙保險唯讀：mode=ro URI（driver 層拒絕寫）＋ PRAGMA query_only。"""
    uri = f"file:{path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def collect_counts(conn: sqlite3.Connection, source: str, cutoff_iso: str,
                   stuck_before_iso: str) -> dict:
    """單一 source 在 window 內的計數。全部是 SELECT。

    - enqueued：created_at >= cutoff
    - completed：status='completed' 且完成時間 >= cutoff（completed_at 缺
      值時退回 updated_at——舊資料相容）
    - dead_letter：status='dead_letter' 且 updated_at >= cutoff
    - stuck：status in (queued, running) 且 created_at < stuck_before
      （超過正常最壞終結時間仍未終結＝worker 沒在消化）
    - last_completed_at：最後一次成功（不受 window 限制，給人看嚴重程度）
    """
    row = conn.execute(
        """
        SELECT
          SUM(CASE WHEN created_at >= :cutoff THEN 1 ELSE 0 END) AS enqueued,
          SUM(CASE WHEN status='completed'
                    AND COALESCE(completed_at, updated_at) >= :cutoff
                   THEN 1 ELSE 0 END) AS completed,
          SUM(CASE WHEN status='dead_letter' AND updated_at >= :cutoff
                   THEN 1 ELSE 0 END) AS dead_letter,
          SUM(CASE WHEN status IN ('queued','running')
                    AND created_at < :stuck THEN 1 ELSE 0 END) AS stuck,
          MAX(CASE WHEN status='completed'
                   THEN COALESCE(completed_at, updated_at) END) AS last_completed_at
        FROM jobs WHERE source = :source
        """,
        {"cutoff": cutoff_iso, "stuck": stuck_before_iso, "source": source},
    ).fetchone()
    return {
        "enqueued": row["enqueued"] or 0,
        "completed": row["completed"] or 0,
        "dead_letter": row["dead_letter"] or 0,
        "stuck": row["stuck"] or 0,
        "last_completed_at": row["last_completed_at"],
    }


# ---------- 五態判準 ----------

def classify(counts: dict, *, expect_enqueue: bool, min_expected_enqueued: int,
             dead_letter_ratio_threshold: float, min_terminal_sample: int) -> tuple[str, str]:
    """回傳 (state, 人話理由)。純函式——五態判準的單一真相，測試直接打這裡；
    Slack 告警與 Web UI 燈號都只經由這一個函式，不各判各的。"""
    enqueued = counts["enqueued"]
    completed = counts["completed"]
    dead = counts["dead_letter"]
    stuck = counts["stuck"]
    terminal = completed + dead

    if enqueued < min_expected_enqueued:
        if expect_enqueue:
            # 觸發端死了：這個 source 本來就該定期進件，卻連 enqueue 都沒有。
            # （只看 completed 的判準會把這種情況跟「執行端死了」混為一談，
            #  而兩者的修法完全不同：一個修 timer/task，一個修執行端。）
            return (STATE_TRIGGER_DEAD,
                    f"window 內 enqueued={enqueued} < 門檻 {min_expected_enqueued}"
                    "——觸發器（timer／Task Scheduler）沒有進件")
        if terminal == 0 and stuck == 0:
            # 事件驅動 source 沒事發生＝正常，不是故障（這是「零 completed
            # 就告警」最主要的誤報來源，必須在這裡擋掉）。
            return (STATE_HEALTHY, "事件驅動 source，window 內無進件亦無殘留——正常")

    if completed == 0:
        if dead > 0:
            return (STATE_EXECUTOR_DEAD,
                    f"window 內 completed=0、dead_letter={dead}"
                    "——有進件但全部失敗，執行端死了")
        if stuck > 0:
            return (STATE_EXECUTOR_DEAD,
                    f"window 內 completed=0，且有 {stuck} 筆 job 卡在 "
                    "queued/running 超過門檻——worker 沒有在消化")
        if enqueued > 0:
            return (STATE_INCONCLUSIVE,
                    f"window 內 enqueued={enqueued}，尚無終結結果、也沒有卡太久的"
                    " job——可能只是還在跑，不告警")
        return (STATE_HEALTHY, "window 內無進件、無殘留")

    if terminal >= min_terminal_sample and dead / terminal >= dead_letter_ratio_threshold:
        return (STATE_EXECUTOR_DEGRADED,
                f"dead_letter 比例 {dead}/{terminal}＝{dead / terminal:.0%} ≥ 門檻 "
                f"{dead_letter_ratio_threshold:.0%}")

    return (STATE_HEALTHY,
            f"window 內 enqueued={enqueued}、completed={completed}、"
            f"dead_letter={dead}")


# ---------- 評估 ----------

def evaluate(jobs_db: Path, config: dict, now: datetime) -> list[dict]:
    """逐 source 評估。jobs.db 不存在／讀不了 → WatchdogError（fail-closed）。"""
    if not jobs_db.exists():
        raise WatchdogError(
            f"jobs.db 不存在：{jobs_db}——看門狗無法判斷任何事，"
            "不假設『沒事』（fail-closed）")
    defaults = config["defaults"]
    findings: list[dict] = []
    try:
        conn = _read_only_conn(jobs_db)
    except sqlite3.Error as exc:
        raise WatchdogError(f"jobs.db 無法唯讀開啟（{jobs_db}）：{exc}") from exc
    try:
        try:
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(jobs)")}
        except sqlite3.Error as exc:
            raise WatchdogError(f"jobs.db 讀取失敗：{exc}") from exc
        if not cols:
            raise WatchdogError(f"jobs.db 沒有 jobs 表（{jobs_db}）——schema 不符預期")
        for src in config["sources"]:
            lookback = float(_source_setting(src, defaults, "lookback_hours"))
            stuck_hours = float(_source_setting(src, defaults, "stuck_backlog_hours"))
            cutoff = (now - timedelta(hours=lookback)).isoformat()
            stuck_before = (now - timedelta(hours=stuck_hours)).isoformat()
            try:
                counts = collect_counts(conn, src["id"], cutoff, stuck_before)
            except sqlite3.Error as exc:
                raise WatchdogError(f"查詢 source={src['id']} 失敗：{exc}") from exc
            state, reason = classify(
                counts,
                expect_enqueue=bool(src["expect_enqueue"]),
                min_expected_enqueued=int(
                    _source_setting(src, defaults, "min_expected_enqueued")),
                dead_letter_ratio_threshold=float(
                    _source_setting(src, defaults, "dead_letter_ratio_threshold")),
                min_terminal_sample=int(
                    _source_setting(src, defaults, "min_terminal_sample")),
            )
            findings.append({
                "source": src["id"], "state": state, "reason": reason,
                "expect_enqueue": bool(src["expect_enqueue"]),
                "lookback_hours": lookback, "counts": counts,
                # description 是 registry 裡的人話說明（UI 用；看門狗訊息不使用）。
                "description": src.get("description"),
            })
    finally:
        conn.close()
    return findings
