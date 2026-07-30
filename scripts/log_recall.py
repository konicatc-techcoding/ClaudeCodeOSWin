#!/usr/bin/env python3
"""scripts/log_recall.py — v0.1（memory-lifecycle 提案 B1，2026-07-30 拍板）

把一次 recall-first 檢索的結果 append 到 logs/recall_log.jsonl（append-only）。
埋點位置：delegation_policy.md 決策程序步驟 1.5——CoS 講出 recall 結果那一行
之後呼叫本 script 記一筆。統計語義是「方向性下限」（prompt 層 best-effort，
漏記可能存在），消費端是 consolidate-memory 的 retention review。

用法：
    <venv-python> scripts/log_recall.py \
        --entry interactive|headless \
        --result hit_skill|hit_memory|miss \
        [--hit-ids "a,b"] [--task-hint "一句話任務分類"] [--log-file PATH]

每行一筆 JSON（JSONL）：
    {"ts": "<UTC ISO 8601>", "entry": "...", "result": "...",
     "hit_ids": [...], "task_hint": "..."}

行為底線：
- append-only：只用 append mode 開檔，絕不改寫既有行；兩側（Windows 前台／
  WSL headless）各自 append，JSONL 併集即全量，天然可合併。
- logs/ 目錄不存在就建立（含中間層）。
- 任何失敗（磁碟／權限／參數）exit 非零，但 stderr 錯誤訊息**只有一行**——
  這個呼叫會被嵌進 CoS 決策程序，不能吵。成功時 stdout 也只回一行 ok。
- 零第三方依賴（純 stdlib）、明確 utf-8，Windows/WSL 同一份程式可跑。
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOG_FILE = ROOT / "logs" / "recall_log.jsonl"

VALID_ENTRIES = ("interactive", "headless")
VALID_RESULTS = ("hit_skill", "hit_memory", "miss")


class _OneLineArgumentParser(argparse.ArgumentParser):
    """參數錯誤時只印一行到 stderr（預設 argparse 會印 usage＋error 兩行）。"""

    def error(self, message):
        print(f"log_recall 參數錯誤：{message}", file=sys.stderr)
        raise SystemExit(2)


def parse_hit_ids(raw: str) -> list:
    """逗號分隔 → list，去除空白項；空字串 → []。"""
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def build_record(entry: str, result: str, hit_ids: list, task_hint: str) -> dict:
    return {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "entry": entry,
        "result": result,
        "hit_ids": hit_ids,
        "task_hint": task_hint,
    }


def append_record(record: dict, log_file: Path) -> None:
    """append 一行 JSON。目錄不存在就建；只用 append mode，絕不覆寫。"""
    log_file.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False)
    with open(log_file, "a", encoding="utf-8", newline="\n") as f:
        f.write(line + "\n")


def main(argv=None) -> int:
    parser = _OneLineArgumentParser(
        prog="log_recall.py",
        description="Append 一筆 recall 結果到 logs/recall_log.jsonl（append-only）",
    )
    parser.add_argument("--entry", required=True, choices=VALID_ENTRIES,
                        help="呼叫入口：interactive（前台）或 headless（背景）")
    parser.add_argument("--result", required=True, choices=VALID_RESULTS,
                        help="recall 結果：hit_skill / hit_memory / miss")
    parser.add_argument("--hit-ids", default="",
                        help="命中的 skill 名／memory 檔名，逗號分隔（可選）")
    parser.add_argument("--task-hint", default="",
                        help="一句話任務分類（可選）")
    parser.add_argument("--log-file", default=str(DEFAULT_LOG_FILE),
                        help=f"log 檔路徑（預設 {DEFAULT_LOG_FILE}）")
    args = parser.parse_args(argv)

    try:
        record = build_record(
            entry=args.entry,
            result=args.result,
            hit_ids=parse_hit_ids(args.hit_ids),
            task_hint=args.task_hint,
        )
        append_record(record, Path(args.log_file))
    except Exception as exc:  # noqa: BLE001 — 單行錯誤是本工具的介面契約
        print(f"log_recall 失敗：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
