# Dashboard 資料層與唯讀 API — v0.1(**Streamlit 呈現層已於 2026-08-15 退役**)

> **退役紀錄**:本目錄原本的 Streamlit dashboard(`dashboard/app.py`)於 2026-07-23 標記 deprecated 並進入並行觀察期(見 `docs/webui-migration-proposal.md` §4.3 DoD 第 4 項),觀察期滿後於 **2026-08-15 正式退役**——`app.py`、`test_app.py` 已自 repo 移除,`streamlit` 已自 `scripts/requirements.txt` 移除。新 Web UI(`webui/`)是唯一觀測面:P1 功能對等+P2 Stage 3 三項觀測功能(憑證/Lane 狀態、統一排程健康表+模型漂移旗標、Hermes session 列表,資料層見 `dashboard/data_stage3.py`),全部經唯讀 API `dashboard/api.py`(bind `127.0.0.1:8799`、只有 GET、序列化前過 `redact.py` 憑證掃描;測試 `test_api.py`)曝露。
>
> 本目錄現在只剩**資料層**(`data.py`、`data_stage3.py`、`data_resident.py`、`data_systemd_wsl.py`、`data_update.py`、`redact.py`)與**唯讀 API**(`api.py`)——沒有任何 UI 呈現層。

Localhost-only、read-only 的系統狀態資料層。不提供任何修改/刪除/重跑 job 的操作——這是刻意的範圍限制。

## 啟動

新 Web UI 的啟動方式見 `webui/README.md`;日常一鍵啟動用 `scripts/start_webui_stack.ps1`(或桌面捷徑「AgentOS WebUI」,冪等地帶起 webui launcher 與唯讀 API)。只單獨起唯讀 API:

```bash
# 必須在 repo 根目錄跑
.venv/Scripts/python.exe dashboard/api.py   # http://127.0.0.1:8799
```

## 安全邊界

- **Read-only 是技術上強制的**：`dashboard/data.py` 用 `sqlite3.connect(f"file:{path}?mode=ro", uri=True)` 開 `hermes/jobs.db`，任何寫入嘗試 SQLite 自己會直接拒絕（見 `test_data.py` 的 `test_readonly_connection_rejects_writes`）。
- **不 import `hermes/db.py`**：那裡有 `enqueue()`/`mark_completed()` 等寫入函式，dashboard 完全不碰那個模組，物理上不具備呼叫寫入函式的能力。
- **不顯示密鑰**：`get_adapter_config_status()` 只回報「有沒有設定、設定了幾筆」，`hermes/config/telegram.json` 的 `bot_token` 永遠不會出現在回應中（`test_data.py` 測資料層、`test_api.py` 測 API 回應全文，都有專門測這件事）。
- **localhost-only**：唯讀 API bind 寫死 `127.0.0.1`（`api.py` 的 `API_HOST` 常數，無 host 參數）。

## 內容（經 `api.py` 曝露給 `webui/` 的資料）

- **總覽**：Worker／三個 adapter 的常駐服務狀態（`/api/systemd-status` 自 2026-07-28 起由 `data_systemd_wsl.py` 供應：Windows 側經 `wsl -d` 唯讀查詢且不喚醒 distro；`data.py` 的 `get_systemd_status()` 是裸 `systemctl --user` 查詢、只在 WSL/Linux 內執行時有效，為早期 Streamlit 版所用、現已無呼叫端但函式仍保留；`get_launchd_status()` 是 macOS legacy）、adapter 設定狀態、五種 job 狀態統計、五個 domain 的狀態
- **Jobs**：可篩選的最近 job 列表；以 job id 取完整內容 + 對應的 log
- **成本**：總成本／平均成本／依 source 分組
- **Memory**：inbox 的 pending/processed/failed 數量、正本檔案清單
- **Logs**：選一個 log 檔案看最後 N 行

## 測試

```bash
.venv/Scripts/python.exe dashboard/test_data.py   # 資料層：暫存 db/state，含 read-only 強制測試
.venv/Scripts/python.exe dashboard/test_api.py    # 唯讀 API：GET-only、localhost-only、bot_token 不外洩、redact 掃描
.venv/Scripts/python.exe -m unittest discover -s dashboard -p "test_*.py"   # dashboard 全部測試
```

## 已知限制

- `dashboard/data.py` 的 SQL 查詢跟 `hermes/db.py` 的 schema 各自維護，`db.py` 改 schema 時要記得手動同步這邊的查詢。
- 沒有伺服器端推播/自動刷新，由前端決定重新取數時機（部分探測型資料層自帶短 TTL 快取：`data_systemd_wsl.py`／`data_resident.py` 5 秒、`data_update.py` 45 秒，見各模組 docstring）。
