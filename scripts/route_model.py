#!/usr/bin/env python3
"""scripts/route_model.py — v0.1

Model Router 的 script adapter。

用法:
    scripts/route_model.py <capability> <prompt-file>
    cat prompt.txt | scripts/route_model.py <capability> -

讀取 registry/model_router.yaml，把 capability 解析成實際模型：
- via=openrouter：呼叫 OpenRouter Chat Completions API 並印出結果
- via=native：代表這個能力該由目前的 Claude session 直接處理，不對外呼叫

需要環境變數 OPENROUTER_API_KEY（僅 via=openrouter 時）。
<prompt-file> 必須在專案目錄內（不能是 `-` 以外、指向專案外的路徑），
防止被誘導讀取本機任意檔案（如 SSH key、.env）。

這是刻意精簡的版本：沒有重試、沒有 streaming、沒有 MCP 包裝。
之後如果用量變多、需要 streaming 或結構化 tool call，再升級成 MCP server。
"""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit(
        "需要 PyYAML，請用專案內的 venv 執行，不要用系統 Python。\n"
        "Windows 建立方式：\n"
        "  py -3.11 -m venv .venv && .venv/Scripts/python.exe -m pip install -r scripts/requirements.txt\n"
        "然後用 .venv/Scripts/python.exe scripts/route_model.py 執行。\n"
        "（macOS/Linux 對應：python3 -m venv .venv && .venv/bin/pip install -r scripts/requirements.txt，"
        "執行用 .venv/bin/python3。）"
    )

ROOT = Path(__file__).resolve().parent.parent
ROUTER_CONFIG = ROOT / "registry" / "model_router.yaml"


def load_config() -> dict:
    with open(ROUTER_CONFIG, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def resolve_route(config: dict, capability: str) -> dict:
    routes = config.get("routes", {})
    if capability in routes:
        return routes[capability]
    return {"model": config.get("default", "claude"), "via": "native"}


def resolve_prompt_path(prompt_source: str) -> Path:
    """把使用者傳入的 prompt 檔案路徑限制在專案目錄內。

    沒有邊界檢查的話，任何能影響 prompt_source 這個參數的上游輸入
    都能讀取本機任意檔案（例如 SSH key、.env）再送去 OpenRouter API。
    """
    path = Path(prompt_source).resolve()
    try:
        path.relative_to(ROOT)
    except ValueError:
        sys.exit(
            f"錯誤：prompt 檔案必須在專案目錄內（{ROOT}），"
            f"拒絕讀取專案外路徑：{path}"
        )
    if not path.is_file():
        sys.exit(f"錯誤：找不到 prompt 檔案：{path}")
    return path


def call_openrouter(route: dict, prompt: str) -> str:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        sys.exit("錯誤：未設定 OPENROUTER_API_KEY 環境變數")

    model_slug = route.get("openrouter_model")
    if not model_slug:
        sys.exit(f"錯誤：route 缺少 openrouter_model 欄位: {route}")

    payload = json.dumps({
        "model": model_slug,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        sys.exit(f"OpenRouter 呼叫失敗 ({e.code}): {e.read().decode()}")

    return body["choices"][0]["message"]["content"]


def main():
    if len(sys.argv) != 3:
        sys.exit("用法: route_model.py <capability> <prompt-file|->")

    capability, prompt_source = sys.argv[1], sys.argv[2]
    prompt = (
        sys.stdin.read()
        if prompt_source == "-"
        else resolve_prompt_path(prompt_source).read_text(encoding="utf-8")
    )

    config = load_config()
    route = resolve_route(config, capability)

    if route.get("via") == "native":
        print(
            f"[route_model] capability='{capability}' 對應 via=native，"
            f"代表這個任務應該由目前的 Claude session 直接處理，不需要外部呼叫。"
        )
        return

    print(call_openrouter(route, prompt))


if __name__ == "__main__":
    main()
