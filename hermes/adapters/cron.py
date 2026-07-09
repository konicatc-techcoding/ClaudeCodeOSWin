#!/usr/bin/env python3
"""hermes/adapters/cron.py — v0.1

Cron adapter：完全不自己做排程判斷，「多久跑一次」交給部署層的排程器
——目前是 WSL2 systemd 的 .timer（見 hermes/systemd/；hermes/launchd/
是 macOS legacy）。這支腳本本身無狀態，一次呼叫只做一件事：查
hermes/config/cron_jobs.yaml、enqueue 一次、結束——跟 hermes/db.py 的
其他使用者一樣，不直接碰 worker、不直接呼叫 CoS。

用法：
    python3 hermes/adapters/cron.py --job-name daily-memory-check
"""
import argparse
import logging
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import db  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = ROOT / "hermes" / "config" / "cron_jobs.yaml"
LOG_DIR = ROOT / "logs" / "hermes"

logger = logging.getLogger("hermes.cron")


def setup_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if not logger.handlers:
        formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        file_handler = logging.FileHandler(LOG_DIR / "cron_adapter.log")
        file_handler.setFormatter(formatter)
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        logger.addHandler(stream_handler)
        logger.setLevel(logging.INFO)


def load_jobs() -> dict:
    if not CONFIG_PATH.exists():
        logger.error(f"找不到設定檔 {CONFIG_PATH}")
        sys.exit(1)
    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    return {job["name"]: job for job in config.get("jobs", [])}


def enqueue_job(job_name: str) -> str:
    """查設定、enqueue 一次，回傳 job id。找不到設定就丟例外，不在這裡處理結束程序。"""
    jobs = load_jobs()
    job_config = jobs.get(job_name)
    if job_config is None:
        raise KeyError(f"設定檔裡找不到 job-name={job_name}")
    return db.enqueue(
        source="cron",
        prompt=job_config["prompt"],
        payload={"job_name": job_name},
        max_attempts=job_config.get("max_attempts", 3),
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-name", required=True)
    args = parser.parse_args()

    setup_logging()
    db.init_db()

    try:
        job_id = enqueue_job(args.job_name)
    except KeyError as e:
        logger.error(str(e))
        sys.exit(1)

    logger.info(f"enqueue job {job_id} for cron job '{args.job_name}'")


if __name__ == "__main__":
    main()
