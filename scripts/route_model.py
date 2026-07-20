#!/usr/bin/env python3
"""scripts/route_model.py — v0.1

Model Router 的 script adapter。

用法:
    scripts/route_model.py <capability> <prompt-file>
    cat prompt.txt | scripts/route_model.py <capability> -

讀取 registry/model_router.yaml，把 capability 解析成實際模型：
- via=native：代表這個能力該由目前的 Claude session 直接處理，不對外呼叫

2026-07-20：原本還有 via=openrouter 分支（呼叫 OpenRouter Chat Completions
API），但 OPENROUTER_API_KEY 自系統建成以來從未真正設定過，這條路徑實務上
從未打通。使用者拍板全部移除 OpenRouter provider 相關路徑後，
registry/model_router.yaml 已不再有任何 via=openrouter 的 route，這裡的
呼叫邏輯（call_openrouter 與相關 import）也一併移除，不留死代碼。

<prompt-file> 必須在專案目錄內（不能是 `-` 以外、指向專案外的路徑），
防止被誘導讀取本機任意檔案（如 SSH key、.env）。

這是刻意精簡的版本：沒有重試、沒有 streaming、沒有 MCP 包裝。
之後如果重新需要對外呼叫模型（例如再次引入某個 provider），再視當時需求
設計、加回對應分支。
"""
import sys
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
    都能讀取本機任意檔案（例如 SSH key、.env）並把內容外流。
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


def main():
    if len(sys.argv) != 3:
        sys.exit("用法: route_model.py <capability> <prompt-file|->")

    capability, prompt_source = sys.argv[1], sys.argv[2]
    # prompt 內容目前只有 via=native 分支需要（印出提示，不對外呼叫），但仍先
    # 讀取／驗證路徑邊界——保留這一步是刻意的：日後若重新加回某個對外 provider
    # 分支，prompt 邊界檢查已經在這裡做好，不必重新設計。
    _ = (
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

    sys.exit(
        f"錯誤：capability='{capability}' 解析出的 route via='{route.get('via')}' "
        "不是 native，但目前 route_model.py 沒有任何對外 provider 的呼叫邏輯"
        "（OpenRouter 路徑已於 2026-07-20 移除）。這代表 registry/model_router.yaml "
        "設定跟程式碼能力對不上，請檢查該 route 設定，不要假設有 fallback。"
    )


if __name__ == "__main__":
    main()
