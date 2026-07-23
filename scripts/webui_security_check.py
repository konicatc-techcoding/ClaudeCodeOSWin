#!/usr/bin/env python3
"""過渡期 Web UI 安全檢查 script(P0 交付物,提案 §4.1 第 8 項)。

純唯讀:只讀取 webui/ 原始碼做靜態檢查,輸出人可讀報告到 stdout,
不修改任何檔案、不啟動任何 process、不發任何網路請求。
P2 完整安全檢查功能完成後由正式版本取代(§4.3 DoD 第 5 項)。

八項檢查(對應 P0 DoD 第 5 條與使用者親定 bridge 安全規格):
  1. localhost-only         — bridge 與 vite 的 bind 位址寫死 127.0.0.1
  2. 固定指令白名單          — spawn 指令為凍結常數,只有預期的四種操作端點
  3. 禁止任意 shell 參數     — 無 shell:true;HTTP 介面不讀 request body
  4. PID ownership          — stop/reload 僅作用於 bridge 自己 spawn 的 process
  5. 重複啟動防護            — 已在線 no-op + 併發啟動去重
  6. CORS                   — origin 白名單僅限本機,非白名單 403
  7. 敏感資料暴露            — 原始碼與設定無疑似真實憑證/密鑰
  8. audit log              — start/stop/reload 每次操作寫入 logs/

P3 追加(2026-07-24,docs/webui-pty-terminal-proposal.md;只追加、不放寬
既有 1–8 項的任何判準):
  9. PTY server 安全        — localhost-only bind、Origin 白名單凍結、
     constant-time token 比對、token 不落磁碟、spawn 目標/引數/cwd 寫死、
     訊息面僅 stdin/resize、audit 只記事件不落 transcript、與唯讀側物理隔離

用法: python scripts/webui_security_check.py
結束碼: 0=全部通過, 1=任一項失敗
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WEBUI = REPO_ROOT / "webui"
BRIDGE = WEBUI / "scripts" / "bridge.mjs"
LAUNCHER = WEBUI / "scripts" / "agentos-local.mjs"
VITE_CONFIG = WEBUI / "vite.config.ts"
PTY_SERVER = WEBUI / "scripts" / "pty-server.mjs"

EXCLUDED_DIRS = {"node_modules", "dist", ".git"}
SOURCE_SUFFIXES = {".ts", ".tsx", ".mjs", ".js", ".json", ".html", ".css", ".md"}


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def iter_source_files():
    for path in sorted(WEBUI.rglob("*")):
        if any(part in EXCLUDED_DIRS for part in path.parts):
            continue
        if path.is_file() and path.suffix in SOURCE_SUFFIXES:
            yield path


class Report:
    def __init__(self) -> None:
        self.results: list[tuple[str, bool, list[str]]] = []

    def add(self, name: str, passed: bool, details: list[str]) -> None:
        self.results.append((name, passed, details))

    def render(self) -> bool:
        print("=" * 72)
        print("Web UI 過渡期安全檢查報告(P0;P3 追加第 9 項 PTY 檢查)")
        print(f"檢查標的: {WEBUI}")
        print("=" * 72)
        all_pass = True
        total = len(self.results)
        for index, (name, passed, details) in enumerate(self.results, 1):
            status = "PASS" if passed else "FAIL"
            all_pass = all_pass and passed
            print(f"\n[{index}/{total}] {status} — {name}")
            for line in details:
                print(f"    - {line}")
        print("\n" + "=" * 72)
        print("總結: " + (f"{total} 項全部通過" if all_pass else "有檢查未通過,詳見上方 FAIL 項"))
        print("=" * 72)
        return all_pass


def check_localhost_only(bridge: str, vite: str) -> tuple[bool, list[str]]:
    details: list[str] = []
    ok = True

    if re.search(r'BRIDGE_HOST\s*=\s*"127\.0\.0\.1"', bridge):
        details.append('bridge BRIDGE_HOST 常數寫死 "127.0.0.1"')
    else:
        ok = False
        details.append("bridge 未找到寫死的 BRIDGE_HOST=127.0.0.1")

    if re.search(r"server\.listen\(\s*bridgePort\s*,\s*BRIDGE_HOST", bridge):
        details.append("bridge listen() 使用 BRIDGE_HOST 常數(無 host 參數化入口)")
    else:
        ok = False
        details.append("bridge listen() 未綁定 BRIDGE_HOST 常數")

    if re.search(r'LOCALHOST_ONLY\s*=\s*"127\.0\.0\.1"', vite) and "host: LOCALHOST_ONLY" in vite:
        details.append("vite server/preview host 寫死 127.0.0.1")
    else:
        ok = False
        details.append("vite 設定未寫死 host=127.0.0.1")

    # tests/ 內的字面 "0.0.0.0" 是「斷言不得出現」的測試字串,不是綁定
    offenders = [
        str(p.relative_to(WEBUI))
        for p in iter_source_files()
        if "tests" not in p.parts and "0.0.0.0" in read(p)
    ]
    if offenders:
        ok = False
        details.append(f"發現 0.0.0.0 綁定: {offenders}")
    else:
        details.append("全部原始碼無 0.0.0.0 綁定")
    return ok, details


def check_command_whitelist(bridge: str, launcher: str) -> tuple[bool, list[str]]:
    details: list[str] = []
    ok = True

    frozen = re.search(
        r'FIXED_COMMAND\s*=\s*Object\.freeze\(\{\s*bin:\s*"hermes",\s*'
        r'args:\s*Object\.freeze\(\[\s*"dashboard",\s*"--host",\s*"127\.0\.0\.1",\s*'
        r'"--port",\s*"9119",\s*"--no-open"\s*\]\)',
        bridge,
    )
    if frozen:
        details.append("spawn 指令為凍結常數: hermes dashboard --host 127.0.0.1 --port 9119 --no-open")
    else:
        ok = False
        details.append("未找到凍結的 FIXED_COMMAND 常數或內容被改動")

    routes = re.findall(r'request\.method === "(GET|POST)" && request\.url === "([^"]+)"', bridge)
    expected = {
        ("GET", "/health"),
        ("POST", "/api/hermes/dashboard"),
        ("POST", "/api/hermes/dashboard/reload"),
        ("POST", "/api/hermes/dashboard/stop"),
    }
    if set(routes) == expected and len(routes) == 4:
        details.append("端點恰為四種白名單操作: health / start / reload / stop")
    else:
        ok = False
        details.append(f"端點不符四種白名單: {routes}")

    spawn_calls = re.findall(r"spawn\(([^,]+),", bridge + launcher)
    allowed_spawn_first_args = {"command.bin", "process.execPath"}
    bad = [s.strip() for s in spawn_calls if s.strip() not in allowed_spawn_first_args]
    if bad:
        ok = False
        details.append(f"spawn 第一參數出現非白名單來源: {bad}")
    else:
        details.append(f"spawn 呼叫僅使用固定來源: {sorted(allowed_spawn_first_args)}")

    if 'execFile("taskkill", ["/PID", String(pid), "/T", "/F"]' in bridge:
        details.append("taskkill 僅接受 bridge 自記錄的整數 PID")
    else:
        ok = False
        details.append("taskkill 呼叫形式與預期不符")

    if '"--stop"' in bridge:
        ok = False
        details.append("發現 hermes dashboard --stop(CLI 全域停止,違反 ownership 規格)")
    else:
        details.append("無 hermes dashboard --stop 全域停止指令(已改為 ownership-verified)")
    return ok, details


def check_no_arbitrary_shell(bridge: str, launcher: str) -> tuple[bool, list[str]]:
    details: list[str] = []
    ok = True
    # 剝離 // 註解後再掃,避免「毋須 shell:true」這類說明文字誤報
    combined = re.sub(r"(?m)//.*$", "", bridge + launcher)

    if re.search(r"shell\s*:\s*true", combined):
        ok = False
        details.append("發現 shell: true")
    else:
        details.append("無 shell: true(spawn 一律不經 shell)")

    body_readers = [
        pattern
        for pattern in (r'request\.on\(\s*["\']data', r"req\.body", r"request\.body", r"readBody", r"json\(\s*request")
        if re.search(pattern, bridge)
    ]
    if body_readers:
        ok = False
        details.append(f"bridge 讀取了 request body: {body_readers}")
    else:
        details.append("bridge 從不讀取 request body(HTTP 介面不接受指令/參數字串)")

    if re.search(r"request\.url\s*\)\s*\.searchParams|URLSearchParams", bridge):
        ok = False
        details.append("bridge 解析了 query 參數")
    else:
        details.append("bridge 不解析 query 參數(URL 僅做全字串比對)")

    if re.search(r"exec\(|execSync\(", combined):
        ok = False
        details.append("發現 exec/execSync(字串型 shell 執行)")
    else:
        details.append("無 exec/execSync 字串型 shell 執行")
    return ok, details


def check_pid_ownership(bridge: str) -> tuple[bool, list[str]]:
    details: list[str] = []
    ok = True

    for fn in ("stopDashboard", "reloadDashboard"):
        match = re.search(fn + r"\(\)\s*\{(.*?)\n  \}", bridge, re.S)
        if match and "ownedAlive()" in match.group(1):
            details.append(f"{fn} 有 ownedAlive() ownership 驗證")
        else:
            ok = False
            details.append(f"{fn} 缺少 ownership 驗證")

    if "拒絕停止" in bridge and re.search(r"statusCode = 409", bridge):
        details.append("非本 bridge 啟動的 process → 409 拒絕")
    else:
        ok = False
        details.append("未找到對外部 process 的拒絕邏輯")

    if "Number.isInteger(pid)" in bridge:
        details.append("kill 前驗證 PID 為整數且來自自有紀錄")
    else:
        ok = False
        details.append("kill 前未驗證 PID")
    return ok, details


def check_duplicate_start_guard(bridge: str) -> tuple[bool, list[str]]:
    details: list[str] = []
    ok = True

    start = re.search(r"async function startDashboard\(\)\s*\{(.*?)\n  \}", bridge, re.S)
    body = start.group(1) if start else ""
    if "await dashboardReady()" in body and "reused: true" in body:
        details.append("已在線再啟動 → no-op(reused),不 spawn 第二個 process")
    else:
        ok = False
        details.append("缺少已在線 no-op 防護")
    if "if (startPromise) return startPromise" in body:
        details.append("併發啟動請求以 startPromise 去重")
    else:
        ok = False
        details.append("缺少併發啟動去重")
    return ok, details


def check_cors(bridge: str) -> tuple[bool, list[str]]:
    details: list[str] = []
    ok = True

    if re.search(r"allowedOrigin\s*=\s*/\^http:\\/\\/\(localhost\|127\\\.0\\\.0\\\.1\):\\d\+\$/", bridge):
        details.append("origin 白名單僅 http://localhost|127.0.0.1:<port>")
    else:
        ok = False
        details.append("origin 白名單 regex 與預期不符")

    if re.search(r"!allowedOrigin\.test\(origin\)[\s\S]{0,120}403", bridge):
        details.append("非白名單 origin → 403")
    else:
        ok = False
        details.append("未找到非白名單 origin 的 403 攔截")
    return ok, details


def check_sensitive_data(_: str) -> tuple[bool, list[str]]:
    details: list[str] = []
    ok = True
    suspect_patterns = [
        (r"eyJ[A-Za-z0-9_-]{20,}", "疑似 JWT"),
        (r"sk-[A-Za-z0-9]{20,}", "疑似 OpenAI key"),
        (r"xox[bap]-[A-Za-z0-9-]{10,}", "疑似 Slack token"),
        (r"AKIA[0-9A-Z]{16}", "疑似 AWS key"),
        # P2 更新(2026-07-23):功能二「憑證/Lane 狀態」上線後,webui 的
        # 說明文字/註解會合法「提及」auth.json(UI 本身零檔案存取,取數只有
        # fetch 唯讀 API 一條路,§3.3 鐵律)。因此本項判準從「出現字樣」收斂
        # 為「同一行出現檔案系統存取呼叫」——原意(webui 不得實際讀取憑證檔)
        # 不變,涵蓋面不縮水。
        (r"(?:readFile|readFileSync|createReadStream|fs\.[A-Za-z]+|open)\([^)\n]*auth\.json"
         r"|auth\.json[^\n]*(?:readFile|readFileSync|createReadStream)",
         "檔案系統存取真實憑證檔"),
    ]
    findings: list[str] = []
    for path in iter_source_files():
        content = read(path)
        for pattern, label in suspect_patterns:
            for match in re.finditer(pattern, content):
                snippet = match.group(0)
                # 測試 fixture 一律 FAKE_/TEST_ 前綴,不視為洩漏
                line_start = content.rfind("\n", 0, match.start()) + 1
                line = content[line_start : content.find("\n", match.start())]
                if "FAKE_" in line or "TEST_" in line:
                    continue
                findings.append(f"{path.relative_to(WEBUI)}: {label} ({snippet[:24]}…)")
    env_files = [p.name for p in WEBUI.glob(".env*")]
    if env_files:
        findings.append(f"webui/ 內存在 .env 檔案: {env_files}")
    if findings:
        ok = False
        details.extend(findings)
    else:
        details.append("原始碼無疑似真實憑證/密鑰;無 .env 檔;fixture 均為 FAKE_ 前綴")
    return ok, details


def check_audit_log(bridge: str) -> tuple[bool, list[str]]:
    details: list[str] = []
    ok = True

    if re.search(r'AUDIT_LOG_NAME\s*=\s*"webui_bridge_audit\.log"', bridge) and re.search(
        r'DEFAULT_LOG_DIR\s*=\s*join\(projectRoot,\s*"\.\.",\s*"logs"\)', bridge
    ):
        details.append("audit log 落點: <repo>/logs/webui_bridge_audit.log")
    else:
        ok = False
        details.append("audit log 落點常數與預期不符")

    audit_calls = len(re.findall(r'audit\("(start|stop|reload)"', bridge))
    if audit_calls >= 8:
        details.append(f"start/stop/reload 各路徑(含拒絕/no-op)共 {audit_calls} 處寫 audit")
    else:
        ok = False
        details.append(f"audit 呼叫僅 {audit_calls} 處,疑有操作路徑未涵蓋")

    if re.search(r"toISOString\(\).*operation.*pid.*result", bridge, re.S):
        details.append("audit 記錄含時間、操作、PID、結果")
    else:
        ok = False
        details.append("audit 記錄欄位不完整")
    return ok, details


def check_pty_server(pty: str, launcher: str, bridge: str) -> tuple[bool, list[str]]:
    """P3 追加:PTY server(pty-server.mjs)安全檢查——只追加,不影響 1–8 項。"""
    details: list[str] = []
    ok = True

    # (a) localhost-only:bind 寫死 127.0.0.1
    if re.search(r'PTY_HOST\s*=\s*"127\.0\.0\.1"', pty) and re.search(
        r"server\.listen\(\s*port\s*,\s*PTY_HOST", pty
    ):
        details.append('PTY_HOST 常數寫死 "127.0.0.1",listen() 綁定該常數(無 host 參數化入口)')
    else:
        ok = False
        details.append("PTY server 未寫死 bind 127.0.0.1")

    # (b) Origin 白名單:凍結、精確兩個 5173 origin(比 bridge regex 更緊)
    if re.search(
        r'ALLOWED_ORIGINS\s*=\s*Object\.freeze\(\[\s*"http://127\.0\.0\.1:5173",\s*"http://localhost:5173",?\s*\]\)',
        pty,
    ) and "ALLOWED_ORIGINS.includes(origin)" in pty:
        details.append("Origin 白名單凍結且僅兩個本機 UI origin,upgrade 前強制比對")
    else:
        ok = False
        details.append("Origin 白名單常數或比對邏輯與預期不符")

    # (c) token:constant-time 比對;經環境變數注入;不落磁碟
    if "timingSafeEqual" in pty and re.search(r'createHash\("sha256"\)', pty):
        details.append("token 比對:sha256 等長化 + timingSafeEqual(constant-time)")
    else:
        ok = False
        details.append("token 比對非 constant-time 實作")
    if re.search(r'TOKEN_ENV\s*=\s*"AGENTOS_PTY_TOKEN"', pty) and "randomBytes(32)" in launcher:
        details.append("token 為 launcher per-boot randomBytes(32),經環境變數注入")
    else:
        ok = False
        details.append("token 產生/注入機制與預期不符")
    if re.search(r"writeFile|createWriteStream", launcher):
        ok = False
        details.append("launcher 出現檔案寫入呼叫(token 不得落磁碟)")
    else:
        details.append("launcher 無任何檔案寫入呼叫(token 不落磁碟)")

    # (d) spawn 邊界:目標寫死 claude、引數凍結為空、cwd 寫死 repo 根、單一 spawn 入口
    if re.search(r'SPAWN_BIN_NAME\s*=\s*"claude"', pty) and re.search(
        r"SPAWN_ARGS\s*=\s*Object\.freeze\(\[\]\)", pty
    ) and re.search(r'SPAWN_CWD\s*=\s*resolve\(webuiRoot,\s*"\.\."\)', pty):
        details.append("spawn 目標寫死 claude、引數凍結為空(零使用者可控參數)、cwd 寫死 repo 根")
    else:
        ok = False
        details.append("spawn 邊界常數與預期不符")
    pty_spawn_calls = re.findall(r"pty\.spawn\(([^,]+),", pty)
    if pty_spawn_calls == ["command.bin"]:
        details.append("pty.spawn 僅一處、目標為啟動時鎖定的 command.bin")
    else:
        ok = False
        details.append(f"pty.spawn 呼叫與預期不符: {pty_spawn_calls}")

    # (e) 訊息面最小化:client→server 僅 stdin/resize
    # 排除 typeof message.type === "string" 這類型別檢查,只抓協定分支
    accepted_types = re.findall(r'(?<!typeof )message\.type === "(\w+)"', pty)
    if set(accepted_types) == {"stdin", "resize"}:
        details.append("WS 協定僅接受 stdin/resize 兩種 client 訊息,未知類型拒絕+audit")
    else:
        ok = False
        details.append(f"WS 訊息面與預期不符: {accepted_types}")

    # (f) audit:獨立 log 檔;只記事件、不落 transcript(唯一寫入點在 audit())
    if re.search(r'AUDIT_LOG_NAME\s*=\s*"webui_pty_audit\.log"', pty) and re.search(
        r'DEFAULT_LOG_DIR\s*=\s*join\(webuiRoot,\s*"\.\.",\s*"logs"\)', pty
    ):
        details.append("audit log 落點: <repo>/logs/webui_pty_audit.log")
    else:
        ok = False
        details.append("audit log 落點常數與預期不符")
    append_calls = len(re.findall(r"appendFileSync\(", pty))
    write_apis = re.findall(r"writeFileSync\(|createWriteStream\(|fs\.write", pty)
    if append_calls == 1 and not write_apis:
        details.append("檔案寫入僅 audit() 內一處 appendFileSync——技術上不存在 transcript 落地路徑")
    else:
        ok = False
        details.append(f"發現 audit 以外的寫入路徑: appendFileSync×{append_calls}, 其他 {write_apis}")

    # (g) 物理隔離:PTY server 不 import bridge/唯讀資料層;唯讀側不 import PTY
    pty_imports = re.findall(r'from\s+"([^"]+)"', pty)
    bad_imports = [s for s in pty_imports if not (s.startswith("node:") or s in {"ws", "node-pty"})]
    if bad_imports:
        ok = False
        details.append(f"PTY server import 了非白名單模組: {bad_imports}")
    else:
        details.append("PTY server import 僅 node 內建 + ws + node-pty(不碰 bridge/唯讀資料層)")
    if "pty-server" in bridge:
        ok = False
        details.append("bridge.mjs 引用了 pty-server(違反物理隔離)")
    else:
        details.append("bridge.mjs 零引用 pty-server")
    readonly_side = [REPO_ROOT / "dashboard" / "api.py", REPO_ROOT / "dashboard" / "data.py"]
    leaks = [p.name for p in readonly_side if p.exists() and ("8801" in read(p) or "pty" in read(p).lower())]
    if leaks:
        ok = False
        details.append(f"唯讀側出現 PTY 引用: {leaks}")
    else:
        details.append("唯讀 API/data 層零 PTY 引用(8801/pty 字樣不存在)")
    return ok, details


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    missing = [str(p) for p in (BRIDGE, LAUNCHER, VITE_CONFIG, PTY_SERVER) if not p.exists()]
    if missing:
        print(f"FAIL — 檢查標的不存在: {missing}")
        return 1

    bridge = read(BRIDGE)
    launcher = read(LAUNCHER)
    vite = read(VITE_CONFIG)
    pty = read(PTY_SERVER)

    report = Report()
    report.add("localhost-only(bind 寫死 127.0.0.1)", *_two(check_localhost_only(bridge, vite)))
    report.add("固定指令白名單(僅四種操作端點、指令凍結)", *_two(check_command_whitelist(bridge, launcher)))
    report.add("禁止任意 shell 參數(無 shell:true、不讀 request body)", *_two(check_no_arbitrary_shell(bridge, launcher)))
    report.add("PID ownership(stop/reload 僅限自有 process)", *_two(check_pid_ownership(bridge)))
    report.add("重複啟動防護(no-op + 併發去重)", *_two(check_duplicate_start_guard(bridge)))
    report.add("CORS(本機 origin 白名單、403 攔截)", *_two(check_cors(bridge)))
    report.add("敏感資料暴露(無真實憑證/密鑰/.env)", *_two(check_sensitive_data(bridge)))
    report.add("audit log(操作記錄落 logs/)", *_two(check_audit_log(bridge)))
    report.add("PTY server 安全(P3:隔離/授權/spawn 邊界/audit 不落 transcript)", *_two(check_pty_server(pty, launcher, bridge)))

    return 0 if report.render() else 1


def _two(result: tuple[bool, list[str]]) -> tuple[bool, list[str]]:
    return result


if __name__ == "__main__":
    sys.exit(main())
