---
name: feedback-hermes-cron-scripts-no-commit
description: HermesAgent（Windows 側）建立/維護的 cron 任務——包含 job 定義與其實際執行的 script——都活在 ClaudeCodeOS 專案版控之外，改完即生效，沒有 commit 這個步驟；不要假設它是 git repo、不要嘗試 commit
metadata:
  type: feedback
---

來源：使用者 2026-07-22 明確表述的通用工作慣例（跨所有 Hermes cron 任務適用，不只 GarminCoach）。

## 原則

**HermesAgent（Windows 側）建立的 cron 任務，都不需要、也沒有對象可以 commit。** 包含「使用 named profile 開發/測試、再丟到 default cron 排程」這整套工作流程產生的 cron 任務。

## Why（為什麼沒有 commit 這一步）

- cron 的 **job 定義**存在 Hermes 自管的 `jobs.json`（`%LOCALAPPDATA%\hermes\cron\jobs.json`），屬 Hermes 內部狀態，本來就不進 ClaudeCodeOS 專案版控。
- cron 實際執行的 **script**（例如 GarminCoach，位於 `C:\Users\razer\Documents\HermesWorkspace\GarminCoach`）**不是 git repo**（實測該目錄與其父層都沒有 `.git`）；是 Python 原地執行、改檔即生效、無 build 步驟。
- 這些東西都活在 ClaudeCodeOS 專案版控之外，所以「修好 Hermes cron 的 script」這類任務，改完就生效，**沒有 commit 這個步驟**。

## How to apply（怎麼套用）

- 之後遇到「修 Hermes 側 cron script / 調整 Hermes cron 任務」的工作，**不要假設它是 git repo、不要嘗試 commit**。改完檔案（已測試通過）即視為完成。
- **事件教訓**：2026-07-22 曾誤把 GarminCoach 當成獨立 git repo 分派 commit，engineering 執行時發現 `not a git repository` 才停手更正。就是這條原則要避免重蹈的情況。
- **例外/提醒（非待辦）**：「不 commit」也代表這些 script 目前無任何版本歷史/備份；若使用者未來想要版控/備份 HermesWorkspace 是另一個獨立決定，非預設。

## 關聯

- [[hermes-cron-store-binding-gateway-alignment]] — 「named profile 開發、再丟 default cron 排程」這段的既有守則（cron 要建在 default(=root) 才會被 tick）。本則講的是那套工作流程產生的 cron 任務「不 commit」。
- [[reference_hermes_workspace]] — Hermes workspace（含 cron script 如 GarminCoach 的所在位置 `C:\Users\razer\Documents\HermesWorkspace`）與 Hermes 側專案脈絡。
