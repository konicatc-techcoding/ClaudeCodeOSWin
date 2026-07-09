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


def process_job(job: dict):
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
        msg = f"invoke_cos.sh exit code {proc.returncode}: {proc.stderr[:500]}"
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
