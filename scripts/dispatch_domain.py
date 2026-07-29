#!/usr/bin/env python3
"""scripts/dispatch_domain.py — v0.1（Phase 1）

Domain Execution Router：CoS 分類出 owner／category 之後，實際「執行」一個
delegated domain task 的跨平台 adapter。CoS 分類與整合邏輯不在這裡；這個
腳本只做「選通道、明確 argv 執行、回傳穩定 JSON envelope」。

三條可能通道（見 registry/capability_lanes.yaml）：
  - execution=native        → 委派給目前的 Claude session（實際上是把工作
                               轉呼叫 scripts/route_model.py，沿用它既有的
                               via=native 行為：印一句提示、不對外呼叫）
  - execution=route_model   → 呼叫 scripts/route_model.py 解析 capability
                               （2026-07-20 起 registry 裡所有 route 都是
                               via=native；原本這裡會走的 OpenRouter provider
                               已隨三條 openrouter-* lane 一併移除，見
                               registry/model_router.yaml 開頭註記）
  - execution=hermes_profile→ 呼叫 Hermes CLI 的 `-z/--oneshot` one-shot 模式，
                               明確帶 --profile（禁止依賴 sticky active_profile）

用法（<venv-python> = Windows `.venv/Scripts/python.exe`／Linux(WSL) `.venv/bin/python`）：
    <venv-python> scripts/dispatch_domain.py \
        --owner engineering --category code_change \
        --prompt-file <path> --execution-id <id> \
        [--capability complex_coding] [--lane hermes-nemocoding] \
        [--timeout 600] [--hermes-bin <path>] [--log-dir <dir>]

輸出：單一 JSON envelope 印到 stdout（見 build_envelope）。process exit code：
0 = envelope.exit_status 屬於 {success, fallback_success}；其餘一律非零——
呼叫端不需要重新解析 envelope 才能知道「這次執行有沒有問題」。

編碼慣例（呼叫端請注意）：stdout 明確鎖定 UTF-8（見 main()），不受執行環境的
console codepage 影響。用 subprocess 呼叫本腳本時，請用
`encoding="utf-8"`（不要用 `text=True` 讓它退回系統 locale——Windows 中文
環境常見 cp950/cp936，內含繁體中文訊息的 JSON 用 locale 解碼會直接
UnicodeDecodeError）。

範圍界線（Phase 1，見任務交付說明）：
  - 不修改 worker.py／db.py／systemd／timer／人工核准佇列。
  - 不被 CoS／worker.py 呼叫——這是獨立、可單獨測試的 adapter，接線是 Phase 2。
  - 不讀取／輸出 .env、token、API key；不碰 memory/*.md。
  - Hermes lane 的 fake/mock executable 測試見 scripts/test_dispatch_domain.py。
"""
import argparse
import json
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    import yaml
except ImportError:
    sys.exit(
        "需要 PyYAML，請用專案內的 venv 執行，不要用系統 Python。\n"
        "  Windows:     .venv/Scripts/python.exe scripts/dispatch_domain.py ...\n"
        "  Linux(WSL):  .venv/bin/python scripts/dispatch_domain.py ...\n"
        "（建立方式見 scripts/requirements.txt 與 route_model.py 開頭說明。）"
    )

sys.path.insert(0, str(Path(__file__).resolve().parent))
import route_model  # noqa: E402　（重用 resolve_prompt_path 的路徑邊界檢查，不重造）

ROOT = Path(__file__).resolve().parent.parent
AGENTS_PATH = ROOT / "registry" / "agents.yaml"
ROUTER_PATH = ROOT / "registry" / "model_router.yaml"
LANES_PATH = ROOT / "registry" / "capability_lanes.yaml"
ROUTE_MODEL_SCRIPT = ROOT / "scripts" / "route_model.py"
DEFAULT_LOG_DIR = ROOT / "logs" / "dispatch_domain"

DEFAULT_TIMEOUT_SECONDS = 600  # 跟 hermes/worker.py 的 JOB_TIMEOUT_SECONDS 對齊
# Windows CreateProcess 的 command line 總長度上限約 32,767 字元。hermes -z 的
# prompt 是直接進 argv（不像 invoke_cos_triage.sh 那樣是「檔案傳到 wrapper，
# wrapper 內部才組 argv」——這裡是 Python subprocess 直接組出最終命令列），
# 所以要在真的送出前擋住過長 prompt，寧可 fail-visible 也不要送到一半被系統
# 截斷或直接失敗又看不出原因。20000 字元是保守值，留給其他 argv token 空間。
MAX_HERMES_PROMPT_CHARS = 20_000

# 憑證樣式遮罩——沿用 hermes/bridge_triage_handler.py 的 _CREDENTIAL_PATTERN
# 精神（錯誤訊息要可診斷但不能洩漏憑證），這裡是獨立小型複本，不跨
# hermes/ 套件耦合。
_CREDENTIAL_PATTERN = re.compile(
    r"(sk-ant-[A-Za-z0-9_\-]{8,}|sk-or-[A-Za-z0-9_\-]{8,}|Bearer\s+[A-Za-z0-9._\-]{8,})"
)

_HERMES_PROFILE_NOT_FOUND_RE = re.compile(r"Profile '.*' does not exist")
_HERMES_EMPTY_RESPONSE_RE = re.compile(r"no final response was produced")

EXECUTIONS = {"native", "route_model", "hermes_profile"}


def redact(text: Optional[str]) -> Optional[str]:
    if text is None:
        return None
    return _CREDENTIAL_PATTERN.sub("[REDACTED]", text)


class DispatchError(Exception):
    """Fail-visible 分類錯誤——一律在還沒嘗試對外執行之前就擋下。"""

    def __init__(self, exit_status: str, message: str):
        super().__init__(message)
        self.exit_status = exit_status
        self.message = message


@dataclass
class ExecutionResult:
    exit_status: str          # success | timeout | profile_not_found | hermes_not_found |
                               # empty_output | bad_usage_json | nonzero_exit
    result_text: Optional[str]
    usage: Optional[dict]
    provider: Optional[str]
    model: Optional[str]
    profile: Optional[str]
    detail: Optional[str]
    returncode: Optional[int]


# ---------------------------------------------------------------------------
# Registry 讀取與驗證——任何一步查到跟另一份 registry 對不起來，一律
# DispatchError("registry_error", ...)，絕不猜測性補值繼續跑。
# ---------------------------------------------------------------------------

def load_registries():
    with open(AGENTS_PATH, "r", encoding="utf-8") as f:
        agents_doc = yaml.safe_load(f)
    with open(ROUTER_PATH, "r", encoding="utf-8") as f:
        router_doc = yaml.safe_load(f)
    with open(LANES_PATH, "r", encoding="utf-8") as f:
        lanes_doc = yaml.safe_load(f)
    return agents_doc, router_doc, lanes_doc


def get_agent(agents_doc: dict, owner: str) -> dict:
    for agent in agents_doc.get("agents", []):
        if agent.get("id") == owner:
            if agent.get("status") != "active":
                raise DispatchError(
                    "registry_error",
                    f"owner='{owner}' 的狀態是 '{agent.get('status')}'，不是 active，不可分派執行。",
                )
            return agent
    raise DispatchError("registry_error", f"owner='{owner}' 不存在於 registry/agents.yaml。")


def resolve_capability(agents_doc: dict, router_doc: dict, agent: dict, override: Optional[str]) -> str:
    capability = override or agent.get("default_capability")
    if not capability:
        raise DispatchError(
            "registry_error",
            f"owner='{agent.get('id')}' 沒有 default_capability，且未用 --capability 指定。",
        )
    if capability not in router_doc.get("routes", {}):
        raise DispatchError(
            "registry_error",
            f"capability='{capability}' 不存在於 registry/model_router.yaml 的 routes。",
        )
    return capability


def find_lane_by_id(lanes_doc: dict, lane_id: str) -> Optional[dict]:
    for lane in lanes_doc.get("lanes", []):
        if lane.get("id") == lane_id:
            return lane
    return None


def validate_lane(lane: dict, router_doc: dict, owner: str) -> None:
    """跟 scripts/test_capability_lanes.py 同一組一致性規則，在 runtime 也
    當場查一次——registry 漂移不能只靠獨立測試事後抓到，執行當下就要擋。
    """
    lane_id = lane.get("id", "<missing id>")
    execution = lane.get("execution")
    if execution not in EXECUTIONS:
        raise DispatchError("registry_error", f"lane '{lane_id}' 的 execution='{execution}' 不合法。")

    if owner not in (lane.get("allowed_agents") or []):
        raise DispatchError(
            "registry_error",
            f"owner='{owner}' 不在 lane '{lane_id}' 的 allowed_agents 內。",
        )

    routes = router_doc.get("routes", {})
    route = routes.get(lane.get("capability"))
    if route is None:
        raise DispatchError(
            "registry_error",
            f"lane '{lane_id}' 的 capability='{lane.get('capability')}' 在 model_router.yaml 不存在。",
        )

    if execution == "route_model":
        if route.get("via") != "openrouter":
            raise DispatchError(
                "registry_error",
                f"lane '{lane_id}'：execution=route_model 但 model_router.yaml 對應 route 的 via 不是 openrouter。",
            )
        if lane.get("model") != route.get("openrouter_model"):
            raise DispatchError(
                "registry_error",
                f"lane '{lane_id}' 的 model 跟 model_router.yaml 的 openrouter_model 不同步。",
            )
    elif execution == "native":
        if route.get("via") != "native":
            raise DispatchError(
                "registry_error",
                f"lane '{lane_id}'：execution=native 但 model_router.yaml 對應 route 的 via 不是 native。",
            )
        if lane.get("model") is not None:
            raise DispatchError("registry_error", f"lane '{lane_id}'：native lane 的 model 應為 null。")
    elif execution == "hermes_profile":
        if not lane.get("hermes_profile"):
            raise DispatchError(
                "registry_error",
                f"lane '{lane_id}'：execution=hermes_profile 但沒有填 hermes_profile 欄位。",
            )
        if lane.get("model") is not None:
            raise DispatchError(
                "registry_error",
                f"lane '{lane_id}'：hermes_profile lane 不應重複記載 model，應為 null。",
            )


def select_lane(lanes_doc: dict, router_doc: dict, owner: str, capability: str,
                 lane_override: Optional[str]) -> dict:
    if lane_override:
        lane = find_lane_by_id(lanes_doc, lane_override)
        if lane is None:
            raise DispatchError("registry_error", f"--lane='{lane_override}' 不存在於 capability_lanes.yaml。")
        if lane.get("capability") != capability:
            raise DispatchError(
                "registry_error",
                f"--lane='{lane_override}' 的 capability='{lane.get('capability')}' 跟已解析的 "
                f"capability='{capability}' 不一致。",
            )
        validate_lane(lane, router_doc, owner)
        return lane

    # 沒有明確指定 lane：只在 status=active 的候選裡挑（自動選路徑刻意保守，
    # 不會自動選到 status=reference 的 lane——那些通道存在、Router 也能執行，
    # 但要操作者透過 --lane 明確 opt-in，見完工回報「lane status 判斷」一節的
    # 理由）。截至 Phase 2d（2026-07-20），registry 裡所有 hermes_profile lane
    # 都已轉 active，因此也會被這條自動選路徑納入候選；哪個 lane 實際被選中
    # 仍取決於 registry/capability_lanes.yaml 裡符合 capability／owner 的 lane
    # 清單順序（見該檔案開頭的說明）。
    candidates = [
        lane for lane in lanes_doc.get("lanes", [])
        if lane.get("capability") == capability
        and lane.get("status") == "active"
        and owner in (lane.get("allowed_agents") or [])
    ]
    if not candidates:
        raise DispatchError(
            "registry_error",
            f"owner='{owner}' capability='{capability}' 沒有任何 status=active 的 lane 可自動選用；"
            f"如需使用 status=reference 的 lane（例如 Hermes profile），請用 --lane 明確指定。",
        )
    lane = candidates[0]
    validate_lane(lane, router_doc, owner)
    return lane


# ---------------------------------------------------------------------------
# 執行
# ---------------------------------------------------------------------------

def run_subprocess(argv, cwd, timeout) -> subprocess.CompletedProcess:
    # 明確 argv list，shell=False（預設）——不得改成 shell=True。
    # 明確指定 encoding="utf-8"，不要用 text=True 依賴系統 locale：Windows
    # 中文環境（例如這台機器的 cp950）下，子行程（hermes -z）stdout/stderr
    # 若含繁體中文內容，用系統預設 codepage 解碼會直接 UnicodeDecodeError
    # 把子行程崩潰（本檔開頭「編碼慣例」註記講的是呼叫端怎麼呼叫這支腳本，
    # 這裡是同一條原則套用在這支腳本自己怎麼呼叫它的子行程）。errors="replace"
    # 是最後防線：就算真的混進非法位元組，也要 fail-visible 地把可讀部分
    # 傳回來，而不是整個 subprocess 呼叫直接拋例外、把已完成的執行結果弄丟。
    return subprocess.run(
        argv, cwd=cwd, capture_output=True, encoding="utf-8", errors="replace",
        timeout=timeout, shell=False,
    )


def execute_native_or_openrouter(lane: dict, capability: str, prompt_path: Path,
                                  timeout: int) -> ExecutionResult:
    argv = [sys.executable, str(ROUTE_MODEL_SCRIPT), capability, str(prompt_path)]
    try:
        proc = run_subprocess(argv, cwd=ROOT, timeout=timeout)
    except subprocess.TimeoutExpired:
        return ExecutionResult(
            exit_status="timeout",
            result_text=None, usage=None,
            provider=lane.get("provider"), model=lane.get("model"), profile=None,
            detail=f"逾時（超過 {timeout} 秒），已終止", returncode=None,
        )

    stdout = (proc.stdout or "").strip()
    stderr = redact((proc.stderr or "").strip())

    if proc.returncode != 0:
        return ExecutionResult(
            exit_status="nonzero_exit",
            result_text=None, usage=None,
            provider=lane.get("provider"), model=lane.get("model"), profile=None,
            detail=stderr or f"route_model.py 結束碼 {proc.returncode}，無 stderr 內容",
            returncode=proc.returncode,
        )

    if not stdout:
        return ExecutionResult(
            exit_status="empty_output",
            result_text=None, usage=None,
            provider=lane.get("provider"), model=lane.get("model"), profile=None,
            detail="route_model.py 結束碼 0 但 stdout 是空的", returncode=proc.returncode,
        )

    return ExecutionResult(
        exit_status="success",
        result_text=stdout, usage=None,
        provider=lane.get("provider"), model=lane.get("model"), profile=None,
        detail=None, returncode=proc.returncode,
    )


def resolve_hermes_argv_prefix(explicit: Optional[str]) -> list:
    """回傳呼叫 hermes 的 argv 前綴（list）。

    --hermes-bin 預設情況下是單一可執行檔路徑（例如指到真正的 hermes.exe，
    PATH 裡找得到就直接用 'hermes'）；也支援帶多個 token 的命令字串（例如測試
    時用 '"<python.exe>" "<fake_hermes.py>"' 這種「直譯器＋腳本」形式）——
    用 shlex.split(posix=False) 解析，Windows 路徑的反斜線不會被誤判成跳脫字元。
    這裡不呼叫 shell，只是把字串拆成 argv 前綴的 token 清單，實際執行仍是
    subprocess.run(argv_list, shell=False)。

    PATH 找不到時的 fallback：headless／非 login shell 呼叫（例如 WSL 側
    systemd 或 `claude -p` 背景任務的 subprocess）常常沒有載入 login shell 的
    PATH，而 WSL 上的 hermes CLI 裝在 ~/.local/bin/（login shell 才會加進
    PATH）。所以 PATH 查無時，明確檢查這一個已知安裝位置——存在就用它；
    仍然不存在才報錯，且錯誤訊息要指出「查過哪裡」跟「多半是 PATH 問題」，
    讓呼叫端能分辨「沒安裝」與「PATH 沒帶到」兩種情況（誠實優先，不做
    更多魔法搜尋）。
    """
    raw = explicit or shutil.which("hermes")
    if not raw:
        fallback = Path.home() / ".local" / "bin" / "hermes"
        if fallback.is_file():
            # 直接回傳單一路徑 token，不經 shlex——路徑含空白也不會被拆壞。
            return [str(fallback)]
        raise DispatchError(
            "hermes_not_found",
            "在 PATH 中找不到 hermes 執行檔，已知安裝位置 "
            f"{fallback} 也不存在（未帶 --hermes-bin）。若 hermes 其實已安裝，"
            "多半是非 login shell 的 PATH 沒帶到安裝目錄——請確認 PATH，"
            "或用 --hermes-bin 明確指定路徑。",
        )
    # shlex.split(posix=False) 用來斷 token（不吃掉反斜線，Windows 路徑安全），
    # 但 posix=False 模式下配對引號會原樣保留在 token 裡，要自己剝掉。
    tokens = shlex.split(raw, posix=False)
    prefix = []
    for tok in tokens:
        if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in ('"', "'"):
            tok = tok[1:-1]
        prefix.append(tok)
    if not prefix:
        raise DispatchError("hermes_not_found", f"--hermes-bin 解析後是空的：{raw!r}")
    return prefix


def execute_hermes_profile(lane: dict, prompt_text: str, execution_id: str, timeout: int,
                            hermes_argv_prefix: list, log_dir: Path) -> ExecutionResult:
    profile = lane["hermes_profile"]

    if len(prompt_text) > MAX_HERMES_PROMPT_CHARS:
        return ExecutionResult(
            exit_status="prompt_too_long",
            result_text=None, usage=None,
            provider=lane.get("provider"), model=None, profile=profile,
            detail=(
                f"prompt 長度 {len(prompt_text)} 字元超過上限 {MAX_HERMES_PROMPT_CHARS}"
                "（hermes -z 的 prompt 直接進 argv，避免碰到平台命令列長度限制）。"
            ),
            returncode=None,
        )

    # 中性 cwd：全新空的 mktemp 目錄，避免 hermes 從 cwd 自動載入本 repo 的
    # AGENTS.md（Hermes CLI 說明文件明講：「Tools, memory, rules, and
    # AGENTS.md in the CWD are loaded as normal」）——沿用
    # hermes/adapter/invoke_cos_triage.sh 已驗證過的同一個技術手段，不重新
    # 發明機制，也不是只在 prompt 裡口頭要求不要遞迴。
    neutral_dir = tempfile.mkdtemp(prefix="dispatch_domain_")
    try:
        if (Path(neutral_dir) / "AGENTS.md").exists():
            return ExecutionResult(
                exit_status="isolation_error",
                result_text=None, usage=None,
                provider=lane.get("provider"), model=None, profile=profile,
                detail=f"中性暫存目錄內出現 AGENTS.md（不應發生），fail closed: {neutral_dir}",
                returncode=None,
            )

        wrapped_prompt = (
            f"PROJECT_ROOT = {ROOT}\n"
            "（本次執行已切換到全新的中性暫存目錄，避免自動載入專案根目錄的 AGENTS.md；"
            "如需讀寫專案檔案，請一律使用上述絕對路徑，不要假設目前工作目錄就是專案根目錄。）\n"
            "---\n"
            f"{prompt_text}"
        )

        usage_file = Path(log_dir) / f"{execution_id}.usage.json"
        usage_file.parent.mkdir(parents=True, exist_ok=True)
        if usage_file.exists():
            usage_file.unlink()

        argv = list(hermes_argv_prefix) + [
            "--profile", profile,
            "-z", wrapped_prompt,
            "--usage-file", str(usage_file),
        ]

        try:
            proc = run_subprocess(argv, cwd=neutral_dir, timeout=timeout)
        except subprocess.TimeoutExpired:
            return ExecutionResult(
                exit_status="timeout",
                result_text=None, usage=None,
                provider=lane.get("provider"), model=None, profile=profile,
                detail=f"逾時（超過 {timeout} 秒），已終止", returncode=None,
            )

        stdout = (proc.stdout or "").strip()
        stderr_raw = (proc.stderr or "").strip()
        stderr = redact(stderr_raw)

        usage: Optional[dict] = None
        usage_error: Optional[str] = None
        if usage_file.exists():
            try:
                usage = json.loads(usage_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                usage_error = f"usage-file 內容不是合法 JSON：{e}"
        # 注意：profile 不存在時，hermes 在 argparse 之前就 exit——usage-file
        # 根本不會被寫入，這是預期行為（不是損壞），不算 bad_usage_json。

        if proc.returncode != 0:
            if _HERMES_PROFILE_NOT_FOUND_RE.search(stderr_raw):
                exit_status = "profile_not_found"
            elif _HERMES_EMPTY_RESPONSE_RE.search(stderr_raw):
                exit_status = "empty_output"
            else:
                exit_status = "nonzero_exit"
            return ExecutionResult(
                exit_status=exit_status,
                result_text=None, usage=usage,
                provider=lane.get("provider"),
                model=(usage or {}).get("model"),
                profile=profile,
                detail=stderr or f"hermes 結束碼 {proc.returncode}，無 stderr 內容",
                returncode=proc.returncode,
            )

        if not stdout:
            return ExecutionResult(
                exit_status="empty_output",
                result_text=None, usage=usage,
                provider=lane.get("provider"), model=(usage or {}).get("model"), profile=profile,
                detail="hermes 結束碼 0 但 stdout 是空的（不符合 -z 的既定行為，仍 fail-visible 處理）",
                returncode=proc.returncode,
            )

        if usage_error is not None:
            return ExecutionResult(
                exit_status="bad_usage_json",
                result_text=stdout, usage=None,
                provider=lane.get("provider"), model=None, profile=profile,
                detail=usage_error, returncode=proc.returncode,
            )

        return ExecutionResult(
            exit_status="success",
            result_text=stdout, usage=usage,
            provider=lane.get("provider"), model=(usage or {}).get("model"), profile=profile,
            detail=None, returncode=proc.returncode,
        )
    finally:
        shutil.rmtree(neutral_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# 執行單一 lane（含記錄），供 dispatch() 對主 lane／fallback lane 各呼叫一次
# ---------------------------------------------------------------------------

def execute_lane(lane: dict, capability: str, prompt_path: Path, prompt_text: str,
                  execution_id: str, timeout: int, hermes_bin_override: Optional[str],
                  log_dir: Path) -> ExecutionResult:
    execution = lane["execution"]
    if execution in ("native", "route_model"):
        return execute_native_or_openrouter(lane, capability, prompt_path, timeout)
    if execution == "hermes_profile":
        hermes_argv_prefix = resolve_hermes_argv_prefix(hermes_bin_override)
        return execute_hermes_profile(
            lane, prompt_text, execution_id, timeout, hermes_argv_prefix, log_dir
        )
    raise DispatchError("registry_error", f"lane '{lane.get('id')}' 的 execution='{execution}' 不支援執行。")


def write_log(execution_id: str, lines: list, log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    with open(log_dir / f"{execution_id}.log", "a", encoding="utf-8") as f:
        for line in lines:
            f.write(line.rstrip("\n") + "\n")


def build_envelope(execution_id: str, owner: str, category: str, capability: str,
                    lane_id: Optional[str], result: Optional[ExecutionResult],
                    fallback_reason: Optional[str]) -> dict:
    if result is None:
        return {
            "execution_id": execution_id,
            "owner": owner,
            "category": category,
            "capability": capability,
            "lane": lane_id,
            "profile": None,
            "provider": None,
            "model": None,
            "result": None,
            "usage": None,
            "fallback_reason": fallback_reason,
            "exit_status": "registry_error",
        }
    return {
        "execution_id": execution_id,
        "owner": owner,
        "category": category,
        "capability": capability,
        "lane": lane_id,
        "profile": result.profile,
        "provider": result.provider,
        "model": result.model,
        "result": result.result_text,
        "usage": result.usage,
        "fallback_reason": fallback_reason,
        "exit_status": result.exit_status,
        "detail": result.detail,
    }


def dispatch(args) -> tuple:
    """回傳 (envelope: dict, process_exit_code: int)。"""
    log_dir = Path(args.log_dir) if args.log_dir else DEFAULT_LOG_DIR
    log_lines = [
        f"=== dispatch {args.execution_id} ===",
        f"owner={args.owner} category={args.category} capability_override={args.capability} "
        f"lane_override={args.lane}",
    ]

    try:
        agents_doc, router_doc, lanes_doc = load_registries()
        agent = get_agent(agents_doc, args.owner)
        capability = resolve_capability(agents_doc, router_doc, agent, args.capability)
        lane = select_lane(lanes_doc, router_doc, args.owner, capability, args.lane)

        try:
            prompt_path = route_model.resolve_prompt_path(args.prompt_file)
        except SystemExit as e:
            raise DispatchError("prompt_path_error", str(e.code))
        prompt_text = prompt_path.read_text(encoding="utf-8")
    except DispatchError as e:
        log_lines.append(f"FAILED before execution: {e.exit_status}: {e.message}")
        write_log(args.execution_id, log_lines, log_dir)
        envelope = build_envelope(
            args.execution_id, args.owner, args.category,
            args.capability or "<unresolved>", args.lane, None, None,
        )
        envelope["exit_status"] = e.exit_status
        envelope["detail"] = e.message
        return envelope, 1

    log_lines.append(f"resolved capability={capability} lane={lane['id']} execution={lane['execution']}")

    result = execute_lane(
        lane, capability, prompt_path, prompt_text, args.execution_id,
        args.timeout, args.hermes_bin, log_dir,
    )
    log_lines.append(f"primary lane result: exit_status={result.exit_status} returncode={result.returncode}")
    if result.detail:
        log_lines.append(f"detail: {result.detail}")

    fallback_reason = None
    final_lane_id = lane["id"]

    if result.exit_status not in ("success",):
        fallback_lane_id = lane.get("fallback_lane")
        if fallback_lane_id:
            fallback_lane = find_lane_by_id(lanes_doc, fallback_lane_id)
            if fallback_lane is None:
                log_lines.append(f"fallback_lane='{fallback_lane_id}' 不存在，無法 fallback（registry 不一致）")
            else:
                try:
                    validate_lane(fallback_lane, router_doc, args.owner)
                    fallback_result = execute_lane(
                        fallback_lane, fallback_lane["capability"], prompt_path, prompt_text,
                        args.execution_id, args.timeout, args.hermes_bin, log_dir,
                    )
                    fallback_reason = (
                        f"primary lane '{lane['id']}' 失敗（{result.exit_status}: {result.detail}），"
                        f"依 registry 的 fallback_lane 改用 '{fallback_lane_id}'"
                    )
                    log_lines.append(
                        f"fallback lane result: exit_status={fallback_result.exit_status} "
                        f"returncode={fallback_result.returncode}"
                    )
                    final_lane_id = fallback_lane_id
                    if fallback_result.exit_status == "success":
                        fallback_result.exit_status = "fallback_success"
                    else:
                        fallback_result.exit_status = "fallback_failed"
                    result = fallback_result
                except DispatchError as e:
                    log_lines.append(f"fallback lane 驗證失敗：{e.exit_status}: {e.message}")

    write_log(args.execution_id, log_lines, log_dir)

    envelope = build_envelope(
        args.execution_id, args.owner, args.category, capability, final_lane_id,
        result, fallback_reason,
    )
    process_exit_code = 0 if result.exit_status in ("success", "fallback_success") else 1
    return envelope, process_exit_code


def main():
    parser = argparse.ArgumentParser(description="Domain Execution Router（Phase 1）")
    parser.add_argument("--owner", required=True, help="registry/agents.yaml 的 domain id")
    parser.add_argument("--category", required=True, help="delegation_policy 分類結果（原樣記錄，不重新驗證）")
    parser.add_argument("--prompt-file", required=True, help="prompt 檔案路徑，必須在專案目錄內")
    parser.add_argument("--execution-id", required=True, help="這次執行的唯一識別，用於 log／usage 檔命名")
    parser.add_argument("--capability", default=None, help="覆蓋 owner 的 default_capability")
    parser.add_argument("--lane", default=None, help="明確指定 capability_lanes.yaml 的 lane id"
                                                       "（含 status=reference 的 lane，例如 Hermes profile）。"
                                                       "例如搭配 --capability complex_coding 使用 "
                                                       "--lane hermes-gptcoding，明確指定走 Hermes 的 "
                                                       "gptcoding profile：<venv-python> "
                                                       "scripts/dispatch_domain.py --owner engineering "
                                                       "--category code_change --capability complex_coding "
                                                       "--lane hermes-gptcoding --prompt-file <path> "
                                                       "--execution-id <id>")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--hermes-bin", default=None,
                         help="覆蓋 hermes 呼叫命令（預設從 PATH 找 'hermes'）；"
                              "可以是單一執行檔路徑，也可以是帶多個 token 的命令字串"
                              "（例如測試時用 '\"<python>\" \"<script.py>\"'）")
    parser.add_argument("--log-dir", default=None, help="覆蓋 log／usage 檔輸出目錄（測試用；預設 logs/dispatch_domain）")
    args = parser.parse_args()

    envelope, exit_code = dispatch(args)
    # 明確把 stdout 鎖定 UTF-8——Windows 主控台預設編碼常常是本地 codepage
    # （例如這台機器是 cp950），print() 內含繁體中文的 JSON 若不鎖編碼，
    # 寫出來的位元組會是 cp950，之後任何用 UTF-8 讀（包含幾乎所有 JSON
    # parser 的預設假設）都會亂碼——這是「穩定 JSON envelope」承諾的一部分，
    # 不能讓輸出結果隨執行環境的 console codepage 漂移。
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(json.dumps(envelope, ensure_ascii=False, indent=2))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
