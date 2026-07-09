#!/usr/bin/env python3
"""hermes/adapters/rss.py — v0.1

RSS adapter：完全不自己做排程判斷（跟 cron.py 同一個原則），
「多久檢查一次」交給部署層的排程器（目前是 WSL2 systemd 的 .timer，
見 hermes/systemd/；hermes/launchd/ 是 macOS legacy）。這支腳本本身無狀態，
一次呼叫就是「整批檢查一次所有設定的 feed」：抓取、去重、解析、
對每個沒看過的新項目呼叫 db.enqueue()，結束。

v0.1 範圍刻意只做這些——不做摘要優化、不做內容分析，那些是
prompt_template 請 CoS/intelligence 去做的事，不是這支 adapter 的責任。

單一 feed 抓取/解析失敗只跳過那個 feed，不影響其他 feed。

第一次看到一個新設定的 feed 時，只建立「已讀」基準線、不 enqueue——
否則新增一個 feed 當下就會把它過去所有文章都當成新內容塞進 queue。

用法：
    python3 hermes/adapters/rss.py
"""
import json
import logging
import sys
from pathlib import Path

import feedparser
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import db  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = ROOT / "hermes" / "config" / "rss_feeds.yaml"
STATE_PATH = ROOT / "hermes" / "state" / "rss_seen.json"
LOG_DIR = ROOT / "logs" / "hermes"

MAX_SEEN_PER_FEED = 500

logger = logging.getLogger("hermes.rss")


def setup_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if not logger.handlers:
        formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        file_handler = logging.FileHandler(LOG_DIR / "rss_adapter.log")
        file_handler.setFormatter(formatter)
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        logger.addHandler(stream_handler)
        logger.setLevel(logging.INFO)


def load_config() -> list[dict]:
    if not CONFIG_PATH.exists():
        logger.error(f"找不到設定檔 {CONFIG_PATH}")
        sys.exit(1)
    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    return config.get("feeds", [])


def load_seen() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {}


def save_seen(seen: dict):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(seen), encoding="utf-8")


def entry_guid(entry) -> str:
    return entry.get("id") or entry.get("link") or entry.get("title") or ""


def process_feed(feed_config: dict, seen: dict) -> int:
    """回傳這次新 enqueue 的項目數。"""
    name = feed_config["name"]
    url = feed_config["url"]
    prompt_template = feed_config["prompt_template"]

    is_first_run = name not in seen
    seen_list = seen.get(name, [])
    seen_set = set(seen_list)

    parsed = feedparser.parse(url)
    if parsed.bozo and not parsed.entries:
        raise ValueError(f"feed 解析失敗或格式有誤: {parsed.get('bozo_exception')}")

    new_count = 0
    for entry in parsed.entries:
        guid = entry_guid(entry)
        if not guid or guid in seen_set:
            continue

        if not is_first_run:
            prompt = prompt_template.format(
                title=entry.get("title", "（無標題）"),
                link=entry.get("link", ""),
            )
            db.enqueue(
                source="rss",
                prompt=prompt,
                payload={"feed": name, "guid": guid, "link": entry.get("link")},
            )
            new_count += 1

        seen_set.add(guid)
        seen_list.append(guid)

    seen[name] = seen_list[-MAX_SEEN_PER_FEED:]
    return new_count


def run_once() -> int:
    feeds = load_config()
    seen = load_seen()
    total_new = 0
    for feed_config in feeds:
        name = feed_config.get("name", "?")
        try:
            new_count = process_feed(feed_config, seen)
            if new_count:
                logger.info(f"feed '{name}'：{new_count} 筆新項目 enqueue")
            else:
                logger.info(f"feed '{name}'：沒有新項目")
            total_new += new_count
        except Exception as e:
            logger.warning(f"feed '{name}' 處理失敗，跳過: {e}")
    save_seen(seen)
    return total_new


def main():
    setup_logging()
    db.init_db()
    total_new = run_once()
    logger.info(f"跑完一輪，總共 enqueue {total_new} 筆新項目")


if __name__ == "__main__":
    main()
