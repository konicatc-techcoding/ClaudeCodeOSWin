"""
Hermes 技能同步橋接——定期比對 ~/.hermes/skills/ 底下所有 SKILL.md
的內容雜湊，把「新增或變更的技能」變成一筆 job，交給既有的 job queue 處理。

同機器，直接讀檔案系統，不透過 Hermes API server。

流程跟 rss.py／cron.py 一致：偵測到事件 → enqueue() → 之後由
worker.py 派給 headless CoS，由 CoS 自己決定要不要寫進
memory/inbox/、記錄什麼內容。本檔案不直接寫 inbox，也不判斷
「該不該採納」。

已對照實際 hermes/db.py 確認：
- enqueue(source, prompt, payload=None, ...)：prompt 是必填的自然語言
  文字（會變成 headless CoS 的 claude -p 輸入），payload 是額外的
  結構化 JSON 欄位，這裡拿來放 added/changed 的技能清單。
- source 欄位只是 TEXT NOT NULL，沒有 CHECK constraint，"hermes"
  可以直接當合法值用，不需要改 schema。

【仍需確認的假設】
- import 方式已對照 rss.py／cron.py／telegram.py 確認並修正為一致寫法：
  `sys.path.insert(0, str(Path(__file__).resolve().parent.parent))` 後
  `import db`，呼叫時一律用 `db.enqueue(...)`／`db.init_db()`。
- 觸發方式假設用 cron（跟 daily-memory-check 同模式）定期執行
  `python hermes_bridge.py`。
- 第一次執行只建立基線快照、不產生 job，避免把 Hermes 現有的所有
  技能都當成「新變更」灌進 job queue。
"""
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import db  # noqa: E402

HERMES_SKILLS_DIR = Path.home() / ".hermes" / "skills"
SNAPSHOT_PATH = Path(__file__).resolve().parent.parent / "state" / "hermes_skills_snapshot.json"


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _scan_skills() -> dict:
    """回傳 {技能相對路徑: 內容 hash}，例如
    {"research/arxiv": "abc123...", "devops/docker": "def456..."}
    """
    result = {}
    for skill_md in HERMES_SKILLS_DIR.rglob("SKILL.md"):
        rel = skill_md.parent.relative_to(HERMES_SKILLS_DIR)
        result[str(rel)] = _hash_file(skill_md)
    return result


def _load_snapshot() -> dict:
    if SNAPSHOT_PATH.exists():
        return json.loads(SNAPSHOT_PATH.read_text())
    return {}


def _save_snapshot(skills: dict) -> None:
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_PATH.write_text(json.dumps(skills, indent=2, ensure_ascii=False))


def sync() -> None:
    db.init_db()  # 冪等，跟 db.py 自己的 CLI 一樣每次執行前確保 table 存在

    current = _scan_skills()
    previous = _load_snapshot()

    if not previous:
        _save_snapshot(current)
        print(f"首次執行，已建立基線快照（{len(current)} 個技能），不產生 job。")
        return

    added = [name for name in current if name not in previous]
    changed = [
        name
        for name in current
        if name in previous and current[name] != previous[name]
    ]

    if added or changed:
        lines = []
        if added:
            lines.append(f"新增技能：{', '.join(added)}")
        if changed:
            lines.append(f"更新技能：{', '.join(changed)}")
        prompt_text = "Hermes 技能自主學習偵測到變更：\n" + "\n".join(lines)

        job_id = db.enqueue(
            source="hermes",
            prompt=prompt_text,
            payload={"added": added, "changed": changed},
        )
        print(f"已 enqueue job {job_id}：\n{prompt_text}")
    else:
        print("Hermes 技能清單無變更。")

    _save_snapshot(current)


if __name__ == "__main__":
    sync()
