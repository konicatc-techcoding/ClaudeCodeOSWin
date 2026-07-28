# Dashboard — v0.1(**deprecated,並行觀察期中**)

> **P2 完成,本 dashboard 標記 deprecated(2026-07-23,見 `docs/webui-migration-proposal.md` §4.3 DoD 第 4 項)**:新 Web UI(`webui/`)已完成 P1 功能對等+P2 Stage 3 三項觀測功能(憑證/Lane 狀態、統一排程健康表+模型漂移旗標、Hermes session 列表,資料層見 `dashboard/data_stage3.py`,經唯讀 API `dashboard/api.py` 曝露)。本 Streamlit dashboard 進入**並行觀察期**:並行一個自然使用週期、期間零維護只讀,觀察期滿後再實際移除。日常使用請改用 `webui/`(啟動方式見 `webui/README.md`)。
>
> **P1 並存紀錄(2026-07-23)**:新 Web UI 經唯讀 API `dashboard/api.py`(bind `127.0.0.1:8799`、只有 GET、序列化前過 `redact.py` 憑證掃描;測試 `test_api.py`)取數,達成與本 dashboard 的功能對等。

Localhost-only、read-only 的系統狀態檢視。不提供任何修改/刪除/重跑 job 的操作——這是刻意的範圍限制。手動啟動，不是常駐服務（沒有裝進 `hermes/systemd/`，也不曾裝進舊環境的 launchd）。

## 啟動

```bash
# Windows（本機目前環境；用 python.exe -m，不要用 Scripts/*.exe wrapper——
# venv 改名過，exe wrapper 內嵌的舊路徑已失效，python.exe -m 不受影響）
.venv/Scripts/python.exe -m streamlit run dashboard/app.py --server.address=localhost
```

瀏覽器打開 `http://localhost:8501`。

## 安全邊界

- **Read-only 是技術上強制的**：`dashboard/data.py` 用 `sqlite3.connect(f"file:{path}?mode=ro", uri=True)` 開 `hermes/jobs.db`，任何寫入嘗試 SQLite 自己會直接拒絕（見 `test_data.py` 的 `test_readonly_connection_rejects_writes`）。
- **不 import `hermes/db.py`**：那裡有 `enqueue()`/`mark_completed()` 等寫入函式，dashboard 完全不碰那個模組，物理上不具備呼叫寫入函式的能力。
- **不顯示密鑰**：`get_adapter_config_status()` 只回報「有沒有設定、設定了幾筆」，`hermes/config/telegram.json` 的 `bot_token` 永遠不會出現在畫面上（`test_data.py`、`test_app.py` 都有專門測這件事）。
- **localhost-only**：啟動指令明確帶 `--server.address=localhost`，不要省略。

## 內容

- **總覽**：Worker／三個 adapter 的常駐服務狀態（本 Streamlit 版用裸 `systemctl --user` 查詢，見 `data.py` 的 `get_systemd_status()`——只在 WSL/Linux 內執行時有效，維持 deprecated 零改動；新 webui 的 `/api/systemd-status` 已於 2026-07-28 換源至 `data_systemd_wsl.py`，Windows 側經 `wsl -d` 查詢且不喚醒 distro。`get_launchd_status()` 是 macOS legacy）、adapter 設定狀態、五種 job 狀態統計、五個 domain 的狀態
- **Jobs**：可篩選的最近 job 列表；貼 job id 看完整內容 + 對應的 log
- **成本**：總成本／平均成本／依 source 分組
- **Memory**：inbox 的 pending/processed/failed 數量、正本檔案清單
- **Logs**：選一個 log 檔案看最後 N 行

## 測試

```bash
.venv/Scripts/python.exe dashboard/test_data.py   # 資料層：暫存 db/state，含 read-only 強制測試
.venv/Scripts/python.exe dashboard/test_app.py    # 用 Streamlit 官方 AppTest API 實際跑整個 app，對著真實專案資料，確認不噴例外、不洩漏密鑰
```

## 已知限制

- `dashboard/data.py` 的 SQL 查詢跟 `hermes/db.py` 的 schema 各自維護，`db.py` 改 schema 時要記得手動同步這邊的查詢。
- 沒有自動刷新，用手動的「🔄 重新整理」按鈕（資料有 5 秒快取）。
