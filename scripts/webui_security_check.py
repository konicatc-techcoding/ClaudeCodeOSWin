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

追加(2026-07-24,docs/webui-service-control-proposal.md §1 唯讀燈號;
只追加、不放寬既有 1–9 項的任何判準):
  10. 常駐狀態燈號唯讀       — UI 元件零按鈕/零點擊處理器/僅 GET 取數;
      探測資料層 subprocess 位點僅兩個凍結唯讀查詢常數,無任何
      systemd/wsl 寫入動詞(寫入部分未核准,零程式碼)。
      2026-07-28 追加(只加嚴):systemd 快照資料層 data_systemd_wsl.py
      (/api/systemd-status 與排程表換源修正)同判準鎖定——subprocess
      位點恰為兩處凍結唯讀查詢常數(list-units/list-timers)、守門複用
      data_resident._distro_state(distro 未運作不喚醒)、無寫入動詞

追加(2026-07-24,docs/webui-update-button-proposal.md §3 唯讀升級預檢;
只追加、不放寬既有 1–10 項的任何判準):
  11. 更新升級預檢唯讀       — UI 零執行/升級/同步鈕(唯一互動為讀取型
      RefreshButton、onClick 僅 reload)、僅 GET 取數;資料層 git 讀取限
      凍結唯讀模板集合(subprocess 單一位點、非白名單模板即拒絕),白名單
      子指令集與原始碼皆無任何寫入 git 子指令,WSL 端不喚醒 distro
      (階段二寫入執行未核准,零程式碼)

追加(2026-07-27,docs/webui-service-control-proposal.md v1.1 §2 寫入部分
使用者核准;只追加、不放寬既有 1–11 項的任何判準):
  12. WSL 服務控制寫入(白名單第二群組)— bridge 第二群組白名單常數存在
      且恰為預期集合(單元僅 hermes-worker/hermes-telegram 兩 service、
      動詞僅 start/stop/restart,皆凍結);指令固定模板
      `wsl -d Ubuntu systemctl --user <op> <unit>` 無任意單元名參數化
      (route=枚舉笛卡兒積、lookup 全字串比對、execFile 固定字面);
      白名單外 400+audit;禁區動詞(enable/disable/mask/daemon-reload/
      --terminate)技術上無入口;UI 端枚舉與 bridge 一致、二次確認、
      stop 語意明示、bridge 離線按鈕停用;兩群組互不滲透。

追加(2026-08-04,docs/webui-update-button-proposal.md §9 待拍板項 2 拍板;
只追加、不放寬既有 1–12 項的任何判準——第 2/12 項的端點與 execFile 枚舉
**有意識**擴充 +1 並保持封閉):
  13. 遠端 fetch 寫入(白名單第三群組,第四個寫入例外)— FETCH_COMMANDS
      凍結且恰為拍板四條(Windows upstream/origin + WSL origin/upstream,
      每條 timeout 60 秒);**無 --prune**(純加法)、無 pull/merge/checkout/
      reset 字面;per-remote fail-loud(四條各自回報,一條失敗不中止其餘)
      + 每條 audit;併發 409;UI 寫入面隔離於 UpdateFetch.tsx(零參數 POST、
      防連點、完成後 fresh=1 重查預檢繞過 45 秒快取)、群組互不滲透。

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
RESIDENT_COMPONENT = WEBUI / "src" / "ResidentStatus.tsx"
DATA_RESIDENT = REPO_ROOT / "dashboard" / "data_resident.py"
DATA_SYSTEMD_WSL = REPO_ROOT / "dashboard" / "data_systemd_wsl.py"
UPDATE_COMPONENT = WEBUI / "src" / "views" / "UpdatePrecheck.tsx"
DATA_UPDATE = REPO_ROOT / "dashboard" / "data_update.py"
UPDATE_FETCH_COMPONENT = WEBUI / "src" / "UpdateFetch.tsx"
SERVICE_COMPONENT = WEBUI / "src" / "ServiceControl.tsx"
HERMES_VIEW = WEBUI / "src" / "views" / "Hermes.tsx"

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
    # 2026-08-04 有意識擴充:第三群組〔重新整理遠端資訊〕fetch(使用者拍板,
    # docs/webui-update-button-proposal.md §9 項 2)加入受核准端點枚舉——
    # 這是擴充白名單,不是放寬檢查:枚舉仍封閉,多一條或少一條都 FAIL。
    expected = {
        ("GET", "/health"),
        ("POST", "/api/hermes/dashboard"),
        ("POST", "/api/hermes/dashboard/reload"),
        ("POST", "/api/hermes/dashboard/stop"),
        ("POST", "/api/repo/fetch-remotes"),
    }
    if set(routes) == expected and len(routes) == 5:
        details.append("全字串比對端點恰為五種白名單操作: health / start / reload / stop"
                       " / fetch-remotes(第三群組,2026-08-04 核准;"
                       "第二群組服務控制 route 為枚舉表 lookup,由第 12 項單獨檢查)")
    else:
        ok = False
        details.append(f"全字串比對端點不符五種白名單: {routes}")

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


def check_resident_readonly(component: str, data_resident: str,
                            data_systemd_wsl: str) -> tuple[bool, list[str]]:
    """追加第 10 項:背景常駐狀態燈號(唯讀)——燈號元件與唯讀資料層不得存在
    任何操作入口。寫入部分已於 2026-07-27(v1.1)核准,但操作入口**集中在
    ServiceControl.tsx + bridge 第二群組**(第 12 項檢查);本項判準不變:
    燈號元件與 data_resident.py 維持零寫入面(讀寫分離)。只追加,不影響 1–9 項。

    2026-07-28 追加 (d)(只加嚴,既有 (a)(b)(c) 判準原封不動):systemd 快照
    資料層 data_systemd_wsl.py(/api/systemd-status 與排程表由裸 systemctl
    換源至 WSL 包裹的修正)以同一套判準鎖定 spawn 面封閉。"""
    details: list[str] = []
    ok = True

    # (a) UI 元件:零按鈕、零點擊處理器、取數僅經唯讀 apiGet(GET-only client)
    if "<button" in component or "onClick" in component:
        ok = False
        details.append("ResidentStatus.tsx 出現 button/onClick 操作入口(操作必須集中於 ServiceControl.tsx)")
    else:
        details.append("ResidentStatus.tsx 零 button/零 onClick(無任何操作入口,含 disabled 假按鈕)")
    if "fetch(" in component:
        ok = False
        details.append("ResidentStatus.tsx 出現直連 fetch(取數必須走唯讀 apiGet)")
    elif 'apiGet<ResidentStatusPayload>("/api/resident-status")' in component:
        details.append("取數僅經唯讀 API client(apiGet → GET /api/resident-status,8799)")
    else:
        ok = False
        details.append("未找到預期的唯讀 API 取數呼叫")

    # (b) 資料層:subprocess 位點僅兩個凍結唯讀查詢常數
    call_sites = re.findall(r"subprocess\.run\(\s*(\w+)", data_resident)
    if sorted(set(call_sites)) == ["WSL_LIST_COMMAND", "WSL_SYSTEMCTL_COMMAND"] and len(call_sites) == 2:
        details.append("data_resident.py subprocess 位點恰為兩處凍結常數(wsl --list / systemctl list-units)")
    else:
        ok = False
        details.append(f"data_resident.py subprocess 位點與預期不符: {call_sites}")
    if re.search(r'WSL_LIST_COMMAND\s*=\s*\("wsl\.exe",\s*"--list",\s*"--verbose"\)', data_resident) and \
            '"list-units"' in data_resident:
        details.append("探測指令皆為唯讀查詢形式;第一層 wsl --list 不喚醒 distro")
    else:
        ok = False
        details.append("凍結探測指令常數內容與預期不符")

    # (c) 資料層:無任何 systemd/wsl 寫入動詞字面值
    write_verbs = ['"start"', '"stop"', '"restart"', '"enable"', '"disable"', '"mask"',
                   "--terminate", "--shutdown", "daemon-reload"]
    found = [verb for verb in write_verbs if verb in data_resident]
    if found:
        ok = False
        details.append(f"data_resident.py 出現寫入動詞字面值: {found}")
    else:
        details.append("data_resident.py 無任何 systemd/wsl 寫入動詞字面值")

    # (d) systemd 快照資料層(data_systemd_wsl.py,2026-07-28 只加嚴追加):
    #     subprocess 位點枚舉封閉(恰為兩處凍結唯讀查詢常數)、守門複用
    #     data_resident._distro_state(distro 未運作不喚醒)、無寫入動詞。
    wsl_sites = re.findall(r"subprocess\.run\(\s*(\w+)", data_systemd_wsl)
    if sorted(set(wsl_sites)) == ["WSL_LIST_TIMERS_COMMAND", "WSL_LIST_UNITS_COMMAND"] \
            and len(wsl_sites) == 2:
        details.append("data_systemd_wsl.py subprocess 位點恰為兩處凍結常數"
                       "(list-units / list-timers)")
    else:
        ok = False
        details.append(f"data_systemd_wsl.py subprocess 位點與預期不符: {wsl_sites}")
    if '"list-units"' in data_systemd_wsl and '"list-timers"' in data_systemd_wsl \
            and '"--output=json"' in data_systemd_wsl:
        details.append("data_systemd_wsl.py 探測指令皆為唯讀查詢形式"
                       "(list-units/list-timers,timers 凍結為 --output=json——"
                       "文字版右對齊單空格會切壞解析,2026-07-28 定案)")
    else:
        ok = False
        details.append("data_systemd_wsl.py 凍結探測指令常數內容與預期不符"
                       "(timers 須為 --output=json)")
    if "_distro_state()" in data_systemd_wsl and "避免喚醒" in data_systemd_wsl:
        details.append("data_systemd_wsl.py 守門複用 data_resident._distro_state:"
                       "distro 非 Running 不下任何 wsl -d(不喚醒)")
    else:
        ok = False
        details.append("data_systemd_wsl.py 未找到 distro 不喚醒守門(應複用 _distro_state)")
    wsl_found = [verb for verb in write_verbs if verb in data_systemd_wsl]
    if wsl_found:
        ok = False
        details.append(f"data_systemd_wsl.py 出現寫入動詞字面值: {wsl_found}")
    else:
        details.append("data_systemd_wsl.py 無任何 systemd/wsl 寫入動詞字面值")
    return ok, details


def check_update_precheck_readonly(component: str, data_update: str) -> tuple[bool, list[str]]:
    """追加第 11 項:Hermes 更新——唯讀升級預檢(階段一)。階段二寫入(執行/升級/
    同步)未核准——UI 不得出現任何執行入口,資料層 git 讀取限凍結唯讀模板,無任何
    寫入/觸網 git 子指令。只追加,不放寬既有 1–10 項的任何判準。
    正本 docs/webui-update-button-proposal.md §3/§8。

    2026-07-25(切片 1,live 版本字串)**只加嚴不放寬**,新增 (g)(h) 兩組判準:
    (g) 版本來源必須是凍結字面 `git show HEAD:pyproject.toml`,且 `show` 在原始碼
        僅出現該一次(杜絕日後改成可參數化用法);
    (h) 除唯一的 subprocess.run 外不得存在任何其他 spawn 原語——鎖死「版本改用
        跑 hermes CLI 取得」這條會產生副作用的捷徑。
    原 (g) WSL 不喚醒判準原封不動改編號為 (i)。"""
    details: list[str] = []
    ok = True

    # (a) UI:取數僅經唯讀 apiGet;無直連 fetch。2026-08-04 起允許的取數
    #     URL 恰為兩個字面:一般讀取與 fetch 鈕完成後的 fresh=1(繞過 45 秒
    #     快取拿新 refs)——仍是 GET-only 唯讀端點,無其他參數化入口。
    if "fetch(" in component:
        ok = False
        details.append("UpdatePrecheck.tsx 出現直連 fetch(取數必須走唯讀 apiGet;"
                       "fetch 鈕的寫入面必須隔離在 UpdateFetch.tsx)")
    elif re.search(
        r'apiGet<UpdatePrecheckPayload>\(\s*useFresh\s*\?\s*"/api/update-precheck\?fresh=1"'
        r'\s*:\s*"/api/update-precheck"\s*\)', component,
    ):
        details.append("取數僅經唯讀 API client(apiGet → GET /api/update-precheck"
                       ",可帶 fresh=1 繞過快取;URL 恰兩個字面,無參數化入口)")
    else:
        ok = False
        details.append("未找到預期的唯讀 API 取數呼叫(應為 useFresh 二選一的兩個凍結 URL 字面)")

    # (b) UI:零執行入口——view 內不自訂 <button(僅共用 RefreshButton),
    #     且唯一 onClick 是 reload(讀取型重跑預檢),非任何執行/升級/同步動作
    if "<button" in component:
        ok = False
        details.append("UpdatePrecheck.tsx 自訂了 <button(階段二執行鈕未核准;唯讀先行)")
    else:
        details.append("UpdatePrecheck.tsx 無自訂 <button(唯一互動為共用 RefreshButton 讀取鈕)")
    onclicks = re.findall(r"onClick=\{[^}]*\}", component)
    bad_onclicks = [oc for oc in onclicks if oc != "onClick={reload}"]
    if bad_onclicks:
        ok = False
        details.append(f"UpdatePrecheck.tsx onClick 非讀取型 reload: {bad_onclicks}")
    else:
        details.append(f"onClick 僅 reload(重跑唯讀預檢),共 {len(onclicks)} 處,無執行/升級/同步處理器")

    # (c) UI:寫入面字面不得出現(hermes update/ff-only merge/reset/fetch/pull)
    ui_forbidden = ["hermes update", "--ff-only", '"reset"', '"merge"', '"fetch"', '"pull"']
    ui_found = [f for f in ui_forbidden if f in component]
    if ui_found:
        ok = False
        details.append(f"UpdatePrecheck.tsx 出現寫入面字面: {ui_found}")
    else:
        details.append("UpdatePrecheck.tsx 無 hermes update/ff-only/reset/merge/fetch/pull 字面")

    # (d) 資料層:subprocess.run 只有一個位點(在 _exec 內)
    call_sites = re.findall(r"subprocess\.run\(", data_update)
    if len(call_sites) == 1:
        details.append("data_update.py subprocess.run 位點恰為一處(_exec 唯一 spawn 點)")
    else:
        ok = False
        details.append(f"data_update.py subprocess.run 位點與預期不符: {len(call_sites)} 處")

    # (e) 資料層:git 讀取的兩層白名單強制——
    #     (e1) 無參數查詢限凍結模板集合;(e2) 帶 remote 名的查詢限凍結建構器,
    #     且 remote 名須過嚴格 regex(擋 `-` 開頭旗標、`/` 路徑、空白等注入)。
    if "FROZEN_GIT_TEMPLATES" in data_update and re.search(
        r"if args not in FROZEN_GIT_TEMPLATES", data_update
    ):
        details.append("無參數 git 查詢以 FROZEN_GIT_TEMPLATES 成員檢查強制(非白名單 → ValueError)")
    else:
        ok = False
        details.append("未找到 FROZEN_GIT_TEMPLATES 的凍結模板強制檢查")
    if re.search(r"if builder not in REF_TEMPLATE_BUILDERS", data_update):
        details.append("帶 remote 名的 git 查詢限 REF_TEMPLATE_BUILDERS 凍結建構器")
    else:
        ok = False
        details.append("未找到 REF_TEMPLATE_BUILDERS 的建構器白名單檢查")
    if re.search(r'REMOTE_NAME_RE\s*=\s*re\.compile\(r"\^\[A-Za-z0-9\]\[A-Za-z0-9\._-\]', data_update) \
            and "REMOTE_NAME_RE.match(remote)" in data_update:
        details.append("remote 名以嚴格 regex 驗證(不得含 - 開頭/斜線/空白 → 無法注入旗標或路徑)")
    else:
        ok = False
        details.append("remote 名的嚴格 regex 驗證與預期不符")
    if re.search(r"if args\[0\] not in ALLOWED_GIT_SUBCOMMANDS", data_update):
        details.append("建構器產出的子指令再過一次 ALLOWED_GIT_SUBCOMMANDS 白名單(縱深防禦)")
    else:
        ok = False
        details.append("未找到建構器產出子指令的白名單覆核")

    # (f) 資料層:白名單子指令集不含任何寫入子指令;原始碼無寫入 git 命令字面
    if re.search(r"ALLOWED_GIT_SUBCOMMANDS\s*=\s*frozenset", data_update):
        details.append("ALLOWED_GIT_SUBCOMMANDS 白名單子指令集存在(rev-parse/rev-list/merge-base/… 唯讀)")
    else:
        ok = False
        details.append("未找到 ALLOWED_GIT_SUBCOMMANDS 白名單集")
    # 寫入 git 子指令的「命令字面」(quoted token)——"merge-base" 是唯讀模板,
    # 其字面為 '"merge-base"',不含 '"merge"';故此掃描不誤傷。
    git_write_literals = ['"fetch"', '"pull"', '"merge"', '"reset"', '"checkout"',
                          '"clone"', '"push"', '"commit"', '"rebase"', '"stash"', '"apply"']
    git_found = [lit for lit in git_write_literals if lit in data_update]
    if git_found:
        ok = False
        details.append(f"data_update.py 出現寫入 git 命令字面: {git_found}")
    else:
        details.append("data_update.py 無任何寫入 git 命令字面(fetch/pull/merge/reset/checkout/…)")

    # (g) 資料層:live 版本字串的來源必須是凍結字面(2026-07-25 切片 1 加嚴)。
    #     版本**不得**靠跑 hermes CLI 取得——那會 spawn process 並可能寫
    #     ~/.hermes/.update_check(「看狀態」變「動狀態」)。故唯一允許的來源是
    #     讀 HEAD 的 pyproject.toml blob,且該指令是零參數化的凍結字面。
    if re.search(r'GIT_PYPROJECT\s*=\s*\(\s*"show",\s*"HEAD:pyproject\.toml"\s*\)', data_update):
        details.append("live 版本來源為凍結字面 git show HEAD:pyproject.toml(零參數化,無注入面)")
    else:
        ok = False
        details.append("未找到凍結的版本來源字面(應為 GIT_PYPROJECT = (\"show\", \"HEAD:pyproject.toml\"))")
    # `show` 只准出現在兩處:那條凍結字面,以及 ALLOWED_GIT_SUBCOMMANDS 的成員宣告。
    # 多於兩處 = 有人另外拼了一條 show 指令;且不得以 f-string 參數化。
    show_uses = re.findall(r'"show"', data_update)
    parameterised_show = re.search(r'f"[^"\n]*show', data_update)
    if len(show_uses) == 2 and not parameterised_show:
        details.append("子指令 show 僅出現於凍結字面與白名單宣告兩處,且無 f-string 參數化用法")
    else:
        ok = False
        details.append(
            f"子指令 show 的用法與預期不符(出現 {len(show_uses)} 處,"
            f"參數化={bool(parameterised_show)};只允許凍結字面+白名單宣告)")

    # (h) 資料層:除唯一的 subprocess.run 外,不得存在任何其他 spawn 原語
    spawn_primitives = ["Popen", "os.system", "os.popen", "subprocess.call",
                        "check_output", "os.exec", "os.spawn", "shutil.which"]
    spawn_found = [p for p in spawn_primitives if p in data_update]
    if spawn_found:
        ok = False
        details.append(f"data_update.py 出現非 git 的 spawn 原語: {spawn_found}")
    else:
        details.append("data_update.py 無任何其他 spawn 原語(Popen/os.system/check_output/…)")

    # (i) 資料層:WSL 端不喚醒——複用 resident 的 _distro_state 守門,distro 非 Running 不下 wsl -d
    if "_distro_state()" in data_update and "避免喚醒" in data_update:
        details.append("WSL 端沿用 resident _distro_state() 守門:distro 非 Running 不下任何 wsl -d(不喚醒)")
    else:
        ok = False
        details.append("未找到 WSL distro 不喚醒守門(應複用 data_resident._distro_state)")
    return ok, details


def check_service_control(bridge: str, component: str, hermes_view: str) -> tuple[bool, list[str]]:
    """追加第 12 項:WSL systemd 服務控制(寫入,白名單第二群組)——
    2026-07-27 使用者核准(docs/webui-service-control-proposal.md v1.1 §2.2/
    §2.3 選項 a:併入 bridge 8787)。只追加、不放寬既有 1–11 項的任何判準。
    邊界模型:PID ownership 不適用(單元由 systemd 管理),以「具名白名單
    窮舉」替代(提案 §2.1 對照表)——本項鎖定枚舉封閉與模板不可參數化。"""
    details: list[str] = []
    ok = True

    # (a) 第二群組白名單常數存在且恰為預期集合(凍結;與第一群組獨立分列)
    if re.search(
        r'SERVICE_UNIT_WHITELIST\s*=\s*Object\.freeze\(\[\s*"hermes-worker\.service",\s*"hermes-telegram\.service"\s*\]\)',
        bridge,
    ):
        details.append("單元枚舉凍結且恰為 hermes-worker.service / hermes-telegram.service(不含 timer)")
    else:
        ok = False
        details.append("SERVICE_UNIT_WHITELIST 常數不存在或與預期集合不符")
    if re.search(r'SERVICE_OP_WHITELIST\s*=\s*Object\.freeze\(\[\s*"start",\s*"stop",\s*"restart"\s*\]\)', bridge):
        details.append("動詞枚舉凍結且恰為 start/stop/restart(枚舉封閉)")
    else:
        ok = False
        details.append("SERVICE_OP_WHITELIST 常數不存在或與預期集合不符")

    # (b) 指令固定模板凍結:wsl.exe -d Ubuntu systemctl --user + <op> + <unit>
    if re.search(
        r'SERVICE_COMMAND\s*=\s*Object\.freeze\(\{\s*bin:\s*"wsl\.exe",\s*'
        r'args:\s*Object\.freeze\(\[\s*"-d",\s*"Ubuntu",\s*"systemctl",\s*"--user"\s*\]\)',
        bridge,
    ):
        details.append("指令模板凍結: wsl.exe -d Ubuntu systemctl --user <op> <unit>")
    else:
        ok = False
        details.append("SERVICE_COMMAND 凍結模板不存在或內容被改動")

    # (c) 無任意單元名參數化:route=兩枚舉笛卡兒積、lookup 全字串比對、
    #     execFile 為固定字面且全檔位點封閉(taskkill+服務控制共兩處)
    if re.search(r"for \(const unit of SERVICE_UNIT_WHITELIST\)\s*\{\s*for \(const op of SERVICE_OP_WHITELIST\)", bridge):
        details.append("route 表由兩個白名單的笛卡兒積建成(2×3=6 條),無其他來源")
    else:
        ok = False
        details.append("SERVICE_ROUTES 建表方式與預期不符(必須是兩枚舉的笛卡兒積)")
    if "SERVICE_ROUTES.get(request.url)" in bridge:
        details.append("route lookup 為 Map 全字串嚴格比對(不解析 URL、不抽取參數)")
    else:
        ok = False
        details.append("未找到 SERVICE_ROUTES 全字串 lookup")
    if "execFile(serviceCommand.bin, [...serviceCommand.args, op, unit]" in bridge:
        details.append("服務 execFile 呼叫=「凍結模板 + 枚舉 op/unit」固定字面,不可參數化")
    else:
        ok = False
        details.append("服務 execFile 呼叫形式與預期固定字面不符")
    # 2026-08-04 有意識擴充:第三群組 fetch(核准的第四寫入例外)新增一個
    # execFile 位點——枚舉由 2 → 3,仍封閉(多一處即 FAIL);該位點的指令
    # 凍結性由第 13 項單獨鎖定。
    exec_calls = len(re.findall(r"execFile\(", bridge))
    if exec_calls == 3:
        details.append("bridge 全檔 execFile 位點恰為三處(taskkill + 服務控制 + fetch-remotes),spawn 面封閉")
    else:
        ok = False
        details.append(f"execFile 位點 {exec_calls} 處,與預期(3)不符")

    # (d) 白名單外一律 400 + audit(前綴命中但非枚舉成員)
    if re.search(r"if \(!route\) \{\s*auditService\(", bridge) and "白名單外路徑" in bridge and re.search(
        r"auditService\([\s\S]{0,200}?sendJson\(response,\s*400", bridge
    ):
        details.append("前綴命中但非枚舉成員 → 400 + audit(白名單外一律拒絕並留痕)")
    else:
        ok = False
        details.append("未找到白名單外 400+audit 攔截")

    # (e) 禁區動詞(提案 §0.2)技術上無入口——剝離 // 註解後掃描
    code_only = re.sub(r"(?m)(?<!:)//.*$", "", bridge)
    forbidden = ['"enable"', '"disable"', '"mask"', "daemon-reload", "--terminate"]
    found = [f for f in forbidden if f in code_only]
    if found:
        ok = False
        details.append(f"bridge 程式碼出現禁區動詞: {found}")
    else:
        details.append("bridge 程式碼無 enable/disable/mask/daemon-reload/--terminate(動詞枚舉封閉)")

    # (f) audit:auditService 涵蓋成功/失敗/重複操作拒絕/白名單外拒絕,
    #     每筆含時間、動詞、單元、結果/exit code
    audit_calls = len(re.findall(r"auditService\(", bridge))
    if audit_calls >= 5 and re.search(r"service:\$\{op\} \| unit=\$\{unit\}", bridge) and "exit=" in bridge:
        details.append(f"auditService 共 {audit_calls} 處(成功/失敗/拒絕全路徑),格式含動詞/單元/exit code")
    else:
        ok = False
        details.append(f"auditService 呼叫僅 {audit_calls} 處或格式不含動詞/單元/exit code")

    # (g) UI 端(ServiceControl.tsx):枚舉一致、請求面封閉、二次確認、
    #     stop 語意明示、bridge 離線按鈕停用(非假按鈕)
    unit_literals = set(re.findall(r"hermes-[a-z-]+\.service", component))
    if unit_literals == {"hermes-worker.service", "hermes-telegram.service"}:
        details.append("UI 單元字面恰為白名單兩成員,無白名單外單元名")
    else:
        ok = False
        details.append(f"UI 單元字面與白名單不符: {sorted(unit_literals)}")
    if '"http://127.0.0.1:8787"' in component and (
        "`${BRIDGE_URL}${SERVICE_ROUTE_PREFIX}${action.unit}/${action.op}`" in component
    ):
        details.append("UI 操作 URL 僅由凍結枚舉值組出(bridge 8787 + /api/service/ 模板)")
    else:
        ok = False
        details.append("UI 操作 URL 組成與預期不符")
    if "setPending({ unit, op })" in component and "execute(pending)" in component:
        details.append("二次確認:第一次點擊僅進入待確認狀態,execute 只由確認鈕觸發")
    else:
        ok = False
        details.append("未找到二次確認流程")
    if "停止後不自動恢復" in component and "依 enable 狀態" in component:
        details.append("stop 真實語意(不自動恢復,至下次登入/distro 重啟由 systemd 拉起)明示於按鈕旁")
    else:
        ok = False
        details.append("未找到 stop 語意明示文字(v1.1 §2.4 定案)")
    if "disabled={disabled}" in component and "未運行,按鈕已停用" in component:
        details.append("bridge 離線 → 按鈕明確停用+原因說明(非假按鈕)")
    else:
        ok = False
        details.append("未找到 bridge 離線停用處理")
    if "等待狀態收斂" in component:
        details.append("操作後顯示收斂等待(不樂觀更新)")
    else:
        ok = False
        details.append("未找到「不樂觀更新」的收斂等待提示")

    # (h) 兩群組互不滲透:服務控制元件不打第一群組端點;Hermes view 不打第二群組
    if "/api/hermes/" not in component:
        details.append("ServiceControl.tsx 零引用第一群組端點(/api/hermes/*)")
    else:
        ok = False
        details.append("ServiceControl.tsx 引用了第一群組端點(群組滲透)")
    if "/api/service/" not in hermes_view:
        details.append("Hermes.tsx 零引用第二群組端點(/api/service/*)")
    else:
        ok = False
        details.append("Hermes.tsx 引用了第二群組端點(群組滲透)")
    return ok, details


def check_fetch_remotes(bridge: str, fetch_component: str, precheck_component: str) -> tuple[bool, list[str]]:
    """追加第 13 項:〔重新整理遠端資訊〕git fetch(寫入,bridge 白名單第三群組)
    ——2026-08-04 使用者拍板(docs/webui-update-button-proposal.md §9 待拍板項 2,
    第四個寫入例外)。只追加、不放寬既有 1–12 項判準(第 2/12 項的端點與
    execFile 枚舉已**有意識**擴充並保持封閉)。"""
    details: list[str] = []
    ok = True

    # (a) 指令集凍結且恰為拍板的四條(順序、repo、remote 全部字面比對;
    #     一顆鈕零參數——bin/args 無任何插值入口,僅 repo 路徑常數)
    if re.search(
        r'FETCH_COMMANDS\s*=\s*Object\.freeze\(\[', bridge,
    ) and all(m in bridge for m in [
        '"windows:upstream"', '"windows:origin"', '"wsl:origin"', '"wsl:upstream"',
        'args: Object.freeze(["-C", WINDOWS_HERMES_REPO, "fetch", "upstream"])',
        'args: Object.freeze(["-C", WINDOWS_HERMES_REPO, "fetch", "origin"])',
        'args: Object.freeze(["-d", "Ubuntu", "--exec", "git", "-C", WSL_HERMES_REPO, "fetch", "origin"])',
        'args: Object.freeze(["-d", "Ubuntu", "--exec", "git", "-C", WSL_HERMES_REPO, "fetch", "upstream"])',
    ]):
        details.append("FETCH_COMMANDS 凍結且恰為拍板四條(Win upstream/origin + WSL origin/upstream),零參數化")
    else:
        ok = False
        details.append("FETCH_COMMANDS 不存在或與拍板四條不符")

    # (b) 禁止事項:--prune 與 pull/merge/checkout/reset 的指令字面不得出現
    #     (剝離 // 註解後掃;fetch 為純加法,絕不刪 refs、絕不碰工作樹)
    code_only = re.sub(r"(?m)(?<!:)//.*$", "", bridge)
    forbidden = ["--prune", '"pull"', '"merge"', '"checkout"', '"reset"',
                 '"rebase"', '"clean"', "--force", "--mirror", "--tags"]
    found = [f for f in forbidden if f in code_only]
    if found:
        ok = False
        details.append(f"bridge 出現 fetch 禁區字面: {found}")
    else:
        details.append("bridge 無 --prune/pull/merge/checkout/reset/--force 等禁區字面(純加法 fetch)")

    # (c) 執行位點:execFile 為「凍結指令 + 展開 args」固定字面;每條 timeout
    if "execFile(command.bin, [...command.args], { timeout: fetchTimeoutMs }" in bridge:
        details.append("fetch execFile 呼叫=凍結指令固定字面 + 每條 60 秒 timeout,不可參數化")
    else:
        ok = False
        details.append("fetch execFile 呼叫形式與預期固定字面不符")
    if re.search(r"FETCH_TIMEOUT_MS\s*=\s*60000", bridge):
        details.append("每條 fetch timeout 凍結為 60 秒(拍板值)")
    else:
        ok = False
        details.append("FETCH_TIMEOUT_MS 與拍板值(60000)不符")

    # (d) per-remote fail-loud:逐條收集結果、失敗不中止其餘;每條 audit
    if "results.push({ id: command.id, label: command.label, ...outcome })" in bridge \
            and "results.every((r) => r.ok)" in bridge:
        details.append("per-remote fail-loud:四條各自回報 id/exitCode/錯誤,一條失敗不中止其餘")
    else:
        ok = False
        details.append("未找到 per-remote 結果收集(fail-loud)實作")
    audit_calls = len(re.findall(r"auditFetch\(", bridge))
    if audit_calls >= 2 and "fetch:${step}" in bridge:
        details.append(f"auditFetch 共 {audit_calls} 處(每條成敗 + 併發拒絕),沿用同一份 audit log")
    else:
        ok = False
        details.append(f"auditFetch 呼叫僅 {audit_calls} 處或格式不符")

    # (e) 併發防護:進行中再按 → 409 + audit
    if "fetchInFlight" in bridge and re.search(
        r"if \(fetchInFlight\) \{\s*auditFetch\([\s\S]{0,200}?statusCode = 409", bridge
    ):
        details.append("併發防護:一輪 fetch 進行中再觸發 → 409 + audit")
    else:
        ok = False
        details.append("未找到 fetch 併發防護(409+audit)")

    # (f) UI 寫入面隔離:fetch 鈕獨立於 UpdateFetch.tsx;URL 僅由凍結字面組出;
    #     防連點;完成後 onCompleted(讓預檢 fresh 重查)
    if '"http://127.0.0.1:8787"' in fetch_component \
            and '"/api/repo/fetch-remotes"' in fetch_component \
            and "`${BRIDGE_URL}${FETCH_REMOTES_ROUTE}`" in fetch_component:
        details.append("UpdateFetch.tsx 操作 URL 僅由凍結字面組出(bridge 8787 + fetch-remotes route)")
    else:
        ok = False
        details.append("UpdateFetch.tsx 操作 URL 組成與預期不符")
    if 'method: "POST"' in fetch_component and "body:" not in fetch_component:
        details.append("UpdateFetch.tsx POST 不帶 body(bridge 端本就不讀 body,雙重保證零參數)")
    else:
        ok = False
        details.append("UpdateFetch.tsx 請求帶了 body 或形式不符(必須零參數)")
    if "disabled={busy}" in fetch_component and "if (busy) return" in fetch_component:
        details.append("執行中防連點:busy 期間按鈕停用 + handler 提前返回(雙保險)")
    else:
        ok = False
        details.append("未找到防連點實作")
    if "onCompleted()" in fetch_component and "freshNext.current = true" in precheck_component:
        details.append("完成後經 onCompleted 讓預檢以 fresh=1 重查(繞過 45 秒快取)")
    else:
        ok = False
        details.append("未找到 fetch 完成後的 fresh 重查串接")
    # 群組互不滲透:fetch 元件不打其他 bridge 群組端點
    if "/api/hermes/" not in fetch_component and "/api/service/" not in fetch_component:
        details.append("UpdateFetch.tsx 零引用第一/第二群組端點(群組互不滲透)")
    else:
        ok = False
        details.append("UpdateFetch.tsx 引用了其他群組端點(群組滲透)")
    return ok, details


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    missing = [str(p) for p in (BRIDGE, LAUNCHER, VITE_CONFIG, PTY_SERVER,
                                RESIDENT_COMPONENT, DATA_RESIDENT, DATA_SYSTEMD_WSL,
                                UPDATE_COMPONENT, DATA_UPDATE, UPDATE_FETCH_COMPONENT,
                                SERVICE_COMPONENT, HERMES_VIEW) if not p.exists()]
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
    report.add(
        "常駐狀態燈號唯讀(零操作入口、探測指令凍結、無寫入動詞;含 systemd 快照層)",
        *_two(check_resident_readonly(read(RESIDENT_COMPONENT), read(DATA_RESIDENT),
                                      read(DATA_SYSTEMD_WSL))),
    )
    report.add(
        "Hermes 更新升級預檢唯讀(階段一:零執行鈕、git 唯讀模板凍結、無寫入子指令)",
        *_two(check_update_precheck_readonly(read(UPDATE_COMPONENT), read(DATA_UPDATE))),
    )
    report.add(
        "WSL 服務控制寫入(v1.1 第二群組:枚舉封閉、模板凍結、400+audit、UI 邊界)",
        *_two(check_service_control(bridge, read(SERVICE_COMPONENT), read(HERMES_VIEW))),
    )
    report.add(
        "遠端 fetch 寫入(2026-08-04 第三群組:四條凍結指令、無 --prune、"
        "per-remote fail-loud、fresh 重查)",
        *_two(check_fetch_remotes(bridge, read(UPDATE_FETCH_COMPONENT), read(UPDATE_COMPONENT))),
    )

    return 0 if report.render() else 1


def _two(result: tuple[bool, list[str]]) -> tuple[bool, list[str]]:
    return result


if __name__ == "__main__":
    sys.exit(main())
