#!/usr/bin/env python3
"""hermes/worker.py — v0.1

Hermes 的 job queue worker：從 hermes/jobs.db 撈 queued job，透過
hermes/adapter/invoke_cos.sh 呼叫 Chief of Staff，依結果更新狀態。
設計見 hermes/DESIGN.md。

用法：
    python3 hermes/worker.py            # 常駐模式，持續 poll（給常駐部署層用，目前是 WSL2 systemd）
    python3 hermes/worker.py --once     # 跑一輪：處理目前所有 eligible 的 queued job，然後結束（手動測試用）
"""
import argparse
import json
import logging
import os
import signal
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bridge_triage_handler  # noqa: E402
import db  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
INVOKE_COS = ROOT / "hermes" / "adapter" / "invoke_cos.sh"
LOG_DIR = ROOT / "logs" / "hermes"

POLL_INTERVAL_SECONDS = 5
JOB_TIMEOUT_SECONDS = 600
MAX_CONCURRENT_JOBS = 1  # v0.1：先跑 1，等穩定後再調到 2-3（見 hermes/DESIGN.md）
STALE_AFTER_SECONDS = 600

WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"

worker_logger = logging.getLogger("hermes.worker")


def setup_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if not worker_logger.handlers:
        formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        file_handler = logging.FileHandler(LOG_DIR / "worker.log")
        file_handler.setFormatter(formatter)
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(formatter)
        worker_logger.addHandler(file_handler)
        worker_logger.addHandler(stream_handler)
        worker_logger.setLevel(logging.INFO)


def write_job_log(job_id: str, lines: list[str]):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOG_DIR / f"{job_id}.log", "a", encoding="utf-8") as f:
        for line in lines:
            f.write(line.rstrip("\n") + "\n")


ERROR_MESSAGE_MAX_CHARS = 500       # error_message 欄位（DB）總長上限
ERROR_RESULT_MAX_CHARS = 300        # 失敗 JSON 的 result 文字截斷長度
ERROR_RAW_STDOUT_MAX_CHARS = 200    # 無法解析成 JSON 時的裸截斷長度


def build_failure_message(returncode: int, stdout: str, stderr: str) -> str:
    """組出「有診斷價值」的 error_message（F2）。

    背景：`claude -p --output-format json` 把錯誤寫在 **stdout 的 JSON 裡**
    （例如 `{"is_error": true, "subtype": "error_during_execution",
    "result": "Not logged in · Please run /login"}`），stderr 常常是空的。
    原本只取 stderr → error_message 永遠是 `... exit code 1: `，是
    2026-08~09 全線 dead_letter「28 天查不出原因」的直接放大器。

    敏感內容取捨（**刻意的設計限制**）：stdout 是 CoS 的完整輸出，可能
    含使用者資料，而 error_message 會落進 jobs.db（並被 dashboard／
    webui 讀取顯示）。所以這裡只做三件事，且**只在失敗路徑**（呼叫端
    僅在 returncode != 0 時使用本函式）：
      1. stderr 有東西就優先用 stderr（診斷價值高、幾乎不含使用者資料）。
      2. stderr 為空才看 stdout，且**優先取結構化錯誤欄位**
         （subtype／is_error）＋僅在該 payload 自稱是錯誤時才附上
         `result` 文字（成功 payload 的 result＝CoS 給使用者的完整答案，
         正是最不該入庫的東西——那種情況只記 metadata）。
      3. `result` 仍截斷至 300 字元；連 JSON 都解析不出來時才裸截斷
         stdout，且只留 200 字元（無結構＝無法判斷內容性質，取最少量）。
    """
    stderr_text = (stderr or "").strip()
    if stderr_text:
        detail = stderr_text[:ERROR_MESSAGE_MAX_CHARS]
    else:
        detail = _stdout_failure_detail(stdout)
    return f"invoke_cos.sh exit code {returncode}: {detail}"[:ERROR_MESSAGE_MAX_CHARS]


def _stdout_failure_detail(stdout: str) -> str:
    stdout_text = (stdout or "").strip()
    if not stdout_text:
        return "(stdout 與 stderr 皆為空)"
    try:
        payload = json.loads(stdout_text)
    except (json.JSONDecodeError, ValueError):
        return f"(stderr 空；stdout 非 JSON) {stdout_text[:ERROR_RAW_STDOUT_MAX_CHARS]}"
    if not isinstance(payload, dict):
        return f"(stderr 空；stdout JSON 非物件) {stdout_text[:ERROR_RAW_STDOUT_MAX_CHARS]}"
    subtype = payload.get("subtype")
    is_error = payload.get("is_error")
    detail = f"(stderr 空；取自 stdout JSON) subtype={subtype} is_error={is_error}"
    looks_like_error = bool(is_error) or (subtype is not None and subtype != "success")
    result = payload.get("result")
    if looks_like_error and isinstance(result, str) and result.strip():
        detail += f" result={result.strip()[:ERROR_RESULT_MAX_CHARS]}"
    elif not looks_like_error:
        # payload 自稱成功卻 exit != 0：矛盾本身就是線索，但 result 是
        # 給使用者的完整答案，不入庫（見上方敏感內容取捨）。
        detail += " result=（payload 自稱成功，內容不入庫）"
    return detail


def process_job(job: dict):
    # Stage 2.5c（提案 §7.5）：source-specific execution routing——triage
    # source 走專屬入口點（invoke_cos_triage.sh）、triage 專屬 timeout、
    # 絕不 resume；其餘 source 走下方既有路徑，逐字不動。這是 job queue
    # 內部「用哪個呼叫入口執行這個 job」的執行路由，不是 Stage 2.6 的
    # domain dispatch（提案 §0 澄清）。
    if job["source"] == bridge_triage_handler.TRIAGE_SOURCE:
        bridge_triage_handler.process_triage_job(job)
        return

    job_id = job["id"]
    thread_id = job["thread_id"]
    prompt = job["prompt"]

    session_id = db.get_resumable_session(thread_id)

    log_lines = [
        f"=== job {job_id} (attempt {job['attempts']}/{job['max_attempts']}) ===",
        f"source={job['source']} thread_id={thread_id} session_id={session_id}",
        f"prompt: {prompt}",
    ]

    cmd = [str(INVOKE_COS), prompt]
    if session_id:
        cmd.append(session_id)

    try:
        proc = subprocess.run(
            cmd, cwd=ROOT, capture_output=True, text=True, timeout=JOB_TIMEOUT_SECONDS
        )
    except subprocess.TimeoutExpired:
        msg = f"逾時（超過 {JOB_TIMEOUT_SECONDS} 秒），已終止"
        log_lines.append(f"FAILED: {msg}")
        write_job_log(job_id, log_lines)
        db.mark_failed(job_id, msg)
        worker_logger.warning(f"job {job_id} timed out")
        return
    except OSError as e:
        msg = f"無法執行 invoke_cos.sh: {e}"
        log_lines.append(f"FAILED: {msg}")
        write_job_log(job_id, log_lines)
        db.mark_failed(job_id, msg)
        worker_logger.warning(f"job {job_id} failed to launch: {e}")
        return

    log_lines.append(f"exit_code={proc.returncode}")
    log_lines.append(f"stdout: {proc.stdout[:4000]}")
    if proc.stderr:
        log_lines.append(f"stderr: {proc.stderr[:2000]}")

    if proc.returncode != 0:
        # F2：stderr 為空時 fallback 到 stdout 的結構化錯誤欄位
        # （claude -p --output-format json 把錯誤寫在 stdout）。
        msg = build_failure_message(proc.returncode, proc.stdout, proc.stderr)
        log_lines.append(f"FAILED: {msg}")
        write_job_log(job_id, log_lines)
        db.mark_failed(job_id, msg)
        worker_logger.warning(f"job {job_id} failed: exit {proc.returncode}")
        return

    try:
        result_json = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        msg = f"無法解析 invoke_cos.sh 的輸出為 JSON: {e}"
        log_lines.append(f"FAILED: {msg}")
        write_job_log(job_id, log_lines)
        db.mark_failed(job_id, msg)
        worker_logger.warning(f"job {job_id} failed: bad json")
        return

    cost = result_json.get("total_cost_usd")

    # 注意（F2 診斷附註）：本分支在「登入失效」這類故障中是**到不了的**
    # ——`claude -p` 該情況下 exit code 非 0，上面的 returncode 分支先攔截。
    # 但它不是普遍意義上的死碼：只要出現「exit code 0 但 payload 自稱失敗
    # ／subtype != success」的組合（例如未來版本改變 exit code 語意），這裡
    # 仍是唯一的攔截點。故保留不動（刪除＝行為變更）。
    if result_json.get("is_error") or result_json.get("subtype") != "success":
        msg = f"CoS 回報失敗: subtype={result_json.get('subtype')} is_error={result_json.get('is_error')}"
        log_lines.append(f"FAILED: {msg} (cost_usd={cost})")
        write_job_log(job_id, log_lines)
        db.mark_failed(job_id, msg, cost_usd=cost)
        worker_logger.warning(f"job {job_id} failed: {msg}")
        return

    result_text = result_json.get("result", "")
    new_session_id = result_json.get("session_id")

    log_lines.append(f"COMPLETED. session_id={new_session_id} cost_usd={cost}")
    log_lines.append(f"result: {result_text}")
    write_job_log(job_id, log_lines)

    db.mark_completed(job_id, result_text, cost_usd=cost)
    db.upsert_session(thread_id, new_session_id)

    worker_logger.info(f"job {job_id} completed (cost=${cost})")


def run_once() -> int:
    """跑一輪：reaper + 處理目前所有 eligible 的 queued job，回傳處理了幾筆。"""
    reaped = db.reap_stale_jobs(STALE_AFTER_SECONDS)
    if reaped:
        worker_logger.info(f"reaper: requeued/dead-lettered {reaped} stale job(s)")

    processed = 0
    futures = []
    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_JOBS) as pool:
        while True:
            job = db.claim_next_job(WORKER_ID)
            if job is None:
                break
            worker_logger.info(f"claimed job {job['id']} (source={job['source']})")
            futures.append(pool.submit(process_job, job))
            processed += 1
            # v0.1 併發=1，這裡簡化成等最早提交的那個做完再繼續撈；
            # 之後把 MAX_CONCURRENT_JOBS 調高時，可以改用
            # concurrent.futures.wait(..., return_when=FIRST_COMPLETED) 提升排程效率。
            if len(futures) >= MAX_CONCURRENT_JOBS:
                futures.pop(0).result()
        for f in futures:
            f.result()
    return processed


def run_forever():
    signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(0))
    signal.signal(signal.SIGINT, lambda signum, frame: sys.exit(0))
    worker_logger.info(f"hermes worker starting ({WORKER_ID}), max_concurrent={MAX_CONCURRENT_JOBS}")
    try:
        while True:
            n = run_once()
            if n == 0:
                time.sleep(POLL_INTERVAL_SECONDS)
    finally:
        worker_logger.info("hermes worker stopped")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="跑一輪就結束，不常駐（手動測試用）")
    args = parser.parse_args()

    db.init_db()
    setup_logging()

    if args.once:
        n = run_once()
        worker_logger.info(f"--once：處理了 {n} 筆 job")
    else:
        run_forever()


if __name__ == "__main__":
    main()
