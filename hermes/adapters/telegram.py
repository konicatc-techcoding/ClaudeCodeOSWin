#!/usr/bin/env python3
"""hermes/adapters/telegram.py — v0.1

Telegram polling adapter：用 getUpdates 長輪詢接收訊息，不開 webhook
（不需要公開網址/SSL，適合本機使用）。設計見專案對話紀錄與
hermes/README.md。

只做兩件事：
1. 把授權過的訊息轉成 job，呼叫 hermes/db.py 的 enqueue()——不直接碰
   worker、不直接呼叫 CoS。
2. 把已經 completed、還沒回覆過的 telegram job 結果送回對應的 chat。

用法：
    python3 hermes/adapters/telegram.py            # 常駐模式
    python3 hermes/adapters/telegram.py --once      # 跑一輪就結束（手動測試用）
"""
import argparse
import json
import logging
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import db  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = ROOT / "hermes" / "config" / "telegram.json"
STATE_PATH = ROOT / "hermes" / "state" / "telegram_offset.json"
LOG_DIR = ROOT / "logs" / "hermes"

DELIVERY_POLL_INTERVAL_SECONDS = 5
NETWORK_RETRY_BASE_SECONDS = 5
NETWORK_RETRY_CAP_SECONDS = 60

logger = logging.getLogger("hermes.telegram")


def setup_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if not logger.handlers:
        formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        file_handler = logging.FileHandler(LOG_DIR / "telegram_adapter.log")
        file_handler.setFormatter(formatter)
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        logger.addHandler(stream_handler)
        logger.setLevel(logging.INFO)


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        logger.error(
            f"找不到設定檔 {CONFIG_PATH}。請建立這個檔案，內容範例：\n"
            '{"bot_token": "123456:ABC-DEF...", "allowed_chat_ids": [123456789], '
            '"poll_timeout_seconds": 30}'
        )
        sys.exit(1)
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def load_offset() -> int:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8")).get("offset", 0)
    return 0


def save_offset(offset: int):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps({"offset": offset}), encoding="utf-8")


class TelegramAPIError(Exception):
    def __init__(self, status_code, body):
        self.status_code = status_code
        self.body = body
        super().__init__(f"Telegram API error {status_code}: {body}")


def _call(method: str, token: str, params: dict, timeout: int) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = json.dumps(params).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise TelegramAPIError(e.code, e.read().decode(errors="replace"))


def get_updates(token: str, offset: int, poll_timeout: int) -> list[dict]:
    body = _call(
        "getUpdates", token, {"offset": offset, "timeout": poll_timeout},
        timeout=poll_timeout + 10,
    )
    if not body.get("ok"):
        raise TelegramAPIError(200, body)
    return body.get("result", [])


def send_message(token: str, chat_id: int, text: str):
    _call("sendMessage", token, {"chat_id": chat_id, "text": text}, timeout=30)


def process_update(update: dict, allowed_chat_ids: set) -> str | None:
    """回傳 enqueue 出去的 job id；這則更新不該處理就回傳 None。"""
    message = update.get("message")
    if message is None:
        return None
    chat_id = message.get("chat", {}).get("id")
    text = message.get("text")
    if chat_id is None or not text:
        return None
    if chat_id not in allowed_chat_ids:
        logger.info(f"忽略不在白名單的 chat_id={chat_id}")
        return None
    job_id = db.enqueue(
        source="telegram",
        prompt=text,
        thread_id=f"telegram:{chat_id}",
        payload={
            "chat_id": chat_id,
            "message_id": message.get("message_id"),
            "update_id": update.get("update_id"),
        },
    )
    logger.info(f"enqueue job {job_id} from chat_id={chat_id}")
    return job_id


def poll_once(config: dict, offset: int) -> int:
    """跑一輪 getUpdates，處理完回傳下一輪該用的 offset。"""
    allowed = set(config["allowed_chat_ids"])
    updates = get_updates(
        config["bot_token"], offset, config.get("poll_timeout_seconds", 30)
    )
    for update in updates:
        process_update(update, allowed)
        offset = update["update_id"] + 1
    return offset


def deliver_completed_jobs(config: dict):
    """把 completed 且還沒回覆過的 telegram job 結果送回對應 chat。"""
    for row in db.list_undelivered_completed("telegram"):
        payload = json.loads(row["payload"])
        chat_id = payload.get("chat_id")
        if chat_id is None:
            logger.warning(f"job {row['id']} 的 payload 沒有 chat_id，跳過")
            continue
        try:
            send_message(config["bot_token"], chat_id, row["result"] or "(沒有內容)")
            db.mark_delivered(row["id"])
            logger.info(f"delivered job {row['id']} to chat_id={chat_id}")
        except Exception as e:
            logger.warning(f"job {row['id']} 投遞失敗，下一輪再試: {e}")


def run_once(config: dict) -> int:
    offset = load_offset()
    try:
        offset = poll_once(config, offset)
        save_offset(offset)
    except TelegramAPIError as e:
        logger.warning(f"Telegram API 錯誤: {e}")
    deliver_completed_jobs(config)
    return offset


def run_forever(config: dict):
    offset = load_offset()
    backoff = NETWORK_RETRY_BASE_SECONDS
    last_delivery_check = 0.0

    logger.info("telegram adapter starting")
    while True:
        try:
            offset = poll_once(config, offset)
            save_offset(offset)
            backoff = NETWORK_RETRY_BASE_SECONDS
        except TelegramAPIError as e:
            if e.status_code in (401, 403):
                logger.error(f"認證失敗（{e.status_code}），bot token 可能失效，結束程序: {e.body}")
                sys.exit(1)
            logger.warning(f"Telegram API 錯誤，{backoff}s 後重試: {e}")
            time.sleep(backoff)
            backoff = min(backoff * 2, NETWORK_RETRY_CAP_SECONDS)
        except (urllib.error.URLError, TimeoutError) as e:
            logger.warning(f"網路錯誤，{backoff}s 後重試: {e}")
            time.sleep(backoff)
            backoff = min(backoff * 2, NETWORK_RETRY_CAP_SECONDS)

        now = time.time()
        if now - last_delivery_check >= DELIVERY_POLL_INTERVAL_SECONDS:
            deliver_completed_jobs(config)
            last_delivery_check = now


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="跑一輪就結束（手動測試用）")
    args = parser.parse_args()

    setup_logging()
    db.init_db()
    config = load_config()

    if args.once:
        run_once(config)
        logger.info("--once：跑完一輪")
    else:
        run_forever(config)


if __name__ == "__main__":
    main()
