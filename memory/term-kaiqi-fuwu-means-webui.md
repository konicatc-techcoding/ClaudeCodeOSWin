---
name: term-kaiqi-fuwu-means-webui
description: 使用者說「開啟服務」時的預設指涉對象——新版 webui（localhost:5173），不是 Hermes 背景服務或舊版 Streamlit dashboard
metadata:
  type: feedback
---

使用者說「開啟服務」時，預設指的是啟動新版 Web UI（[webui/](../webui/README.md)，`.claude/launch.json` 的 `webui` 設定，`npm run local --prefix webui`，對應 `http://localhost:5173/`），不是：
- Hermes 背景常駐服務（WSL2 內的 systemd 服務：hermes-worker/hermes-telegram/hermes-rss 等）
- 舊版 Streamlit dashboard（`localhost:8501`）

**Why**：2026-08-03 對話中「開啟服務」一詞先後被誤認為指 Hermes 背景服務，來回確認後使用者明確拍板：以後這個說法固定指新版 webui。新舊兩套 UI 目前並存（見 [webui-migration-decisions.md](webui-migration-decisions.md)），容易混淆，需要明確的預設值避免每次都要重新問。

**How to apply**：之後看到「開啟服務」，直接視為要求啟動 `webui`（`preview_start` 用 `.claude/launch.json` 裡 name=`webui` 那組設定），不需要再用 AskUserQuestion 確認是哪一個。若使用者想要的其實是 Hermes 背景服務或舊版 dashboard，需改用更明確的說法（例如「開 Hermes 服務」「開 dashboard」）。
