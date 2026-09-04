#!/usr/bin/env python3
"""dashboard/api.py — P1+P2 唯讀 API(提案 docs/webui-migration-proposal.md §2.2 選項 A)。

把既有 dashboard/data.py 的唯讀函式群包成 localhost-only 的 HTTP JSON API,
給 webui/(純 Vite + React SPA)取數。P2 起額外曝露 dashboard/data_stage3.py
的 Stage 3 三項唯讀函式(capability-lanes/credential-status/schedule-table/
hermes/sessions)。三鐵律的技術強制(提案 §3.1–§3.4):

- **localhost-only(§3.1)**:bind 寫死 `API_HOST = "127.0.0.1"`——
  `create_server()` 沒有 host 參數,CLI 也沒有 host 選項;不是「預設
  localhost 但可改」,是沒有改的入口。CORS 只對本機 origin 白名單回
  `Access-Control-Allow-Origin`;非白名單 origin 一律 403。
- **技術強制 read-only(§3.2)**:HTTP 層全域攔截——只有 `do_GET` 存在,
  其他任何 method(POST/PUT/DELETE/PATCH/OPTIONS/…)經 `__getattr__`
  一律回 405,不是逐 endpoint 記得擋。模組層只 import stdlib 與
  dashboard/data.py、dashboard/data_stage3.py、dashboard/redact.py,
  **不 import** hermes/db.py、
  hermes/bridge_dispatch.py、cron.jobs 等任何含寫入函式的模組
  (test_api.py 有 import guard 測試以 AST 白名單鎖定)。SQLite 層沿用
  data.py 的 mode=ro。
- **第三道憑證掃描(§3.4)**:每個回應在 JSON 序列化前統一過
  redact.scan_structure()(共用實作,見 redact.py)——P1 endpoints
  理論上不含憑證資料,防線先立起來給 P2 用。

框架選型:stdlib http.server(**零新依賴**)。理由:API 面只有 10 個
簡單 GET endpoint,stdlib 已足;不引入 FastAPI/Flask 可讓 import guard
白名單最小、供應鏈零成長(venv 裡的 starlette/uvicorn 是別的東西帶進來
的,不拿來用,避免多一層框架 import 面)。

啟動(Windows):
    .venv/Scripts/python.exe dashboard/api.py          # http://127.0.0.1:8799
    .venv/Scripts/python.exe dashboard/api.py --port N # 只有 port 可調,host 不可
"""
import argparse
import json
import re
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import data  # noqa: E402
import data_resident  # noqa: E402(背景常駐狀態燈號,唯讀三層探測,見 data_resident.py)
import data_stage3  # noqa: E402(P2:Stage 3 三項唯讀函式,見 data_stage3.py)
import data_jobs_freshness  # noqa: E402(jobs 管線新鮮度燈號:即時唯讀評估,判準與 Slack 看門狗共用,見 data_jobs_freshness.py)
import data_repo_guard  # noqa: E402(未推送 commit 離線保險快照的唯讀狀態,見 data_repo_guard.py)
import data_systemd_wsl  # noqa: E402(Windows 側經 WSL 的 systemd 唯讀快照,見 data_systemd_wsl.py)
import data_update  # noqa: E402(Hermes 更新唯讀升級預檢——階段一,見 data_update.py)
import redact  # noqa: E402

API_HOST = "127.0.0.1"  # 鐵律:寫死,無參數化入口(提案 §3.1)
API_PORT = 8799

# CORS 白名單:僅本機 dev origin(沿用範本 bridge 已示範的 origin 檢查模式)
ALLOWED_ORIGIN_RE = re.compile(r"^http://(localhost|127\.0\.0\.1):\d+$")

# 路徑參數驗證:log 檔名/job id 用嚴格白名單 regex,杜絕 path traversal。
_LOG_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.log$")
_JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SOURCE_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _query_int(query: dict, name: str, default: int, lo: int, hi: int) -> int:
    raw = query.get(name, [None])[0]
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ApiError(400, f"{name} 必須是整數")
    if not lo <= value <= hi:
        raise ApiError(400, f"{name} 必須介於 {lo} 與 {hi} 之間")
    return value


class ReadOnlyAPIHandler(BaseHTTPRequestHandler):
    server_version = "AgentOSReadOnlyAPI/0.1"
    protocol_version = "HTTP/1.1"

    # --- §3.2 全域 method 攔截:只有 do_GET 存在,其他 method 一律 405 ---
    # BaseHTTPRequestHandler 以 getattr(self, "do_" + command) 派發;
    # __getattr__ 攔下所有未定義的 do_* → 405(不是逐 endpoint 記得擋,
    # 也不是列舉「已知的」寫入 method——任何非 GET 都進不來)。
    def __getattr__(self, name: str):
        if name.startswith("do_"):
            return self._reject_non_get
        raise AttributeError(name)

    def _reject_non_get(self) -> None:
        # 不讀 request body;直接關閉連線避免 keep-alive 殘留未讀資料。
        self.close_connection = True
        self._send_json(
            405,
            {"error": "read-only API:只允許 GET"},
            extra_headers={"Allow": "GET"},
        )

    # --- 回應統一出口:§3.4 第三道掃描在這裡,序列化前統一執行 ---
    def _send_json(self, status: int, payload, extra_headers: dict | None = None) -> None:
        body = json.dumps(redact.scan_structure(payload), ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        origin = self.headers.get("Origin")
        if origin and ALLOWED_ORIGIN_RE.match(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    # --- GET:先做 CORS origin 檢查(§3.1),再路由 ---
    def do_GET(self) -> None:  # noqa: N802(BaseHTTPRequestHandler 命名慣例)
        origin = self.headers.get("Origin")
        if origin is not None and not ALLOWED_ORIGIN_RE.match(origin):
            self.close_connection = True
            self._send_json(403, {"error": "origin 不在 localhost 白名單"})
            return
        try:
            status, payload = self._route()
        except ApiError as exc:
            self._send_json(exc.status, {"error": exc.message})
            return
        except Exception:
            # 不回傳例外細節(可能含路徑等內部資訊)
            self._send_json(500, {"error": "internal error"})
            return
        self._send_json(status, payload)

    def _route(self) -> tuple[int, object]:
        parsed = urllib.parse.urlsplit(self.path)
        path = urllib.parse.unquote(parsed.path)
        query = urllib.parse.parse_qs(parsed.query)

        # endpoint 一對一對應 data.py 既有函式(提案 §4.2 第 2 項)
        if path == "/api/health":
            # jobs_db_source（2026-09-04）:Windows 側讀的是 WSL 推來的快照而非
            # runtime db,故健康檢查一併回「資料從哪來、有多舊」——**不能讓
            # 呼叫端以為 jobs_db_exists=True 就等於即時資料**。判定住在
            # data_jobs_snapshot.py(經 data.jobs_source(),api.py 不新增 import,
            # 白名單維持不變)。
            return 200, {"ok": True, "readonly": True,
                         "jobs_db_exists": data.jobs_db_exists(),
                         "jobs_db_source": data.jobs_source()}
        if path == "/api/status-counts":
            return 200, data.get_status_counts()
        if path == "/api/jobs":
            limit = _query_int(query, "limit", 50, 1, 500)
            status = query.get("status", [None])[0]
            if status is not None and status not in data.JOB_STATUSES:
                raise ApiError(400, "status 不在允許清單")
            source = query.get("source", [None])[0]
            if source is not None and not _SOURCE_RE.match(source):
                raise ApiError(400, "source 格式不正確")
            return 200, data.get_recent_jobs(limit=limit, status=status, source=source)
        if path.startswith("/api/jobs/"):
            job_id = path[len("/api/jobs/"):]
            if not _JOB_ID_RE.match(job_id):
                raise ApiError(400, "job id 格式不正確")
            job = data.get_job(job_id)
            if job is None:
                raise ApiError(404, "找不到這個 job id")
            return 200, job
        if path == "/api/jobs-freshness":
            # jobs 管線新鮮度燈號(2026-09-04,F5 看門狗的觀測面對應物)。
            # **即時計算**:直接對 jobs.db 唯讀查詢,判準與門檻與
            # scripts/jobs_freshness_watchdog.py 共用同一個 core 模組與同一份
            # registry/jobs_watchdog.yaml——UI 與告警不會各判各的。
            # 資料層零 subprocess(送 Slack 那一半留在 watchdog,本路徑不 import),
            # **不會送任何告警、不寫任何東西、不觸發任何 job**;fail-soft 轉灰燈。
            #
            # 為什麼另開端點而不附掛(對照 repo_guard 附掛在 update-precheck):
            # repo_guard 與升級預檢講的是同一件事的兩層,放一起才看得懂;新鮮度
            # 則沒有語意上的宿主——/api/status-counts 的契約是
            # `Record<string, number>`(全時段累計),塞進巢狀物件會破壞型別與
            # 其等值測試;/api/health 講的是「API 活著嗎」,與「管線活著嗎」混為
            # 一談正是這次事故的認知錯誤。故獨立端點。第 11 項「URL 恰兩個字面」
            # 的靜態鎖定是針對 UpdatePrecheck.tsx,本改動未觸及該檔。
            return 200, data_jobs_freshness.get_jobs_freshness()
        if path == "/api/cost-summary":
            return 200, data.get_cost_summary()
        if path == "/api/systemd-status":
            # 2026-07-28:改用 Windows 側經 WSL 的唯讀快照(data_systemd_wsl:
            # 分層守門絕不喚醒 distro、三分支誠實狀態 ok/wsl_down/unavailable、
            # 5 秒 TTL 快取)。data.get_systemd_status() 的裸 systemctl 只在
            # WSL/Linux 內有效,原為 Streamlit app.py 所用(2026-08-15 退役),
            # 函式原樣保留、此處不呼叫。
            return 200, data_systemd_wsl.get_wsl_systemd_snapshot()
        if path == "/api/memory/inbox-counts":
            return 200, data.get_memory_inbox_counts()
        if path == "/api/memory/files":
            return 200, data.get_memory_files()
        if path == "/api/domains":
            return 200, data.get_domain_status()
        if path == "/api/adapter-config":
            return 200, data.get_adapter_config_status()
        # --- P2:Stage 3 三項觀測功能(data_stage3.py,全部唯讀)---
        if path == "/api/capability-lanes":
            return 200, data_stage3.get_capability_lane_status()
        if path == "/api/credential-status":
            # 白名單+輸出前掃描在 data_stage3 內完成;_send_json 的第三道
            # 掃描(§3.4)仍照常套用——縱深防禦,不互相取代。
            return 200, data_stage3.get_hermes_credential_status()
        if path == "/api/schedule-table":
            return 200, data_stage3.get_cron_schedule_table()
        if path == "/api/hermes/sessions":
            limit = _query_int(query, "limit", 200, 1, 1000)
            source = query.get("source", [None])[0]
            if source is not None and not _SOURCE_RE.match(source):
                raise ApiError(400, "source 格式不正確")
            return 200, data_stage3.get_hermes_sessions(source=source, limit=limit)
        # --- 背景常駐狀態燈號(docs/webui-service-control-proposal.md §1,唯讀)---
        if path == "/api/resident-status":
            # 三層探測(data_resident.py):distro 未運作即止步,絕不執行會
            # 喚醒 distro 的指令;失敗優雅退化為 gray;資料層帶 5 秒快取。
            return 200, data_resident.get_resident_status()
        # --- Hermes 更新唯讀升級預檢(docs/webui-update-button-proposal.md §3,階段一)---
        if path == "/api/update-precheck":
            # 純唯讀 git 讀取(白名單子指令);兩端並列(Windows/WSL)。WSL 端
            # distro Stopped 不喚醒(複用 data_resident 守門);失敗優雅退化為灰。
            # 45 秒 TTL 快取(git 讀取較重)。**零寫入、零 fetch、無執行入口。**
            # `fresh=1`(2026-08-04):繞過 TTL 立即重探並覆寫快取——供
            # 〔重新整理遠端資訊〕fetch 按鈕完成後取新 refs 用;重探本身
            # 仍是純唯讀查詢,本端點維持 GET-only 零寫入。
            fresh = query.get("fresh", ["0"])[0] == "1"
            payload = data_update.get_update_precheck(force=fresh)
            # 未推送 commit 的離線保險快照(批次 1 止血)——掛在同一個 payload
            # 底下,而**不是**另開端點:兩者講的是同一件事的兩層(現在有沒有
            # 未 push vs 萬一被吃掉救不救得回來),放一起才看得懂差別;也因此
            # UpdatePrecheck.tsx 不需要新增取數 URL(維持第 11 項的「URL 恰
            # 兩個字面」靜態鎖定不被放寬)。
            # data_update 的快取物件是共用參照 → **淺拷貝再加欄位**,不就地
            # 改寫快取。資料層零 subprocess、只讀 _latest.json,不觸發 guard
            # 執行,故本端點仍是純唯讀。
            return 200, {**payload, "repo_guard": data_repo_guard.get_repo_guard_status()}
        if path.startswith("/api/logs/"):
            name = path[len("/api/logs/"):]
            if ".." in name or not _LOG_NAME_RE.match(name):
                raise ApiError(400, "log 檔名格式不正確")
            lines = _query_int(query, "lines", 200, 1, 1000)
            return 200, {"name": name, "content": data.tail_log(name, lines=lines)}
        raise ApiError(404, "not found")

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        # 安靜化預設的 per-request stderr log;錯誤仍由 http.server 內建處理。
        pass


def create_server(port: int = API_PORT) -> ThreadingHTTPServer:
    """建立 server。**沒有 host 參數**——bind 位址寫死 API_HOST(127.0.0.1)。"""
    return ThreadingHTTPServer((API_HOST, port), ReadOnlyAPIHandler)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="AgentOS dashboard 唯讀 API(localhost-only)")
    parser.add_argument("--port", type=int, default=API_PORT, help=f"預設 {API_PORT};host 不可調")
    args = parser.parse_args(argv)
    server = create_server(args.port)
    print(f"read-only API: http://{API_HOST}:{args.port}(Ctrl+C 停止)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
