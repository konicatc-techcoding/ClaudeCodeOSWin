---
name: hermes-cron-store-binding-gateway-alignment
description: Hermes 原生 cron job 落在哪個 store，由「建立當下該行程解析到的 HERMES_HOME」動態決定（context override > HERMES_HOME env > 平台預設 global root）。前台 hermes CLI 會套 sticky active profile，把 home 綁到該 profile；背景真正在跑的是 root/default gateway（use_cron_store(root)）。若在沒有 gateway 在跑的 active profile 底下 create，job 靜默永不觸發。守則：job 所在 store 與有 daemon 在 tick 的 store 必須同一個 home
metadata:
  type: reference
---

Hermes（NousResearch HermesAgent，Windows 側原始碼在 `%LOCALAPPDATA%\hermes\hermes-agent`，見 [[reference_hermes_workspace]]）原生 cron 的 **store 歸屬與 gateway 對齊**機制，經原始碼＋實測雙重查證。這則講「job 寫進哪個 store、誰在 tick 它」；與 [[hermes-cron-model-pin-convention]] 講的「模型 pin/花費保護 fail-closed」是不同層次的主題。

## ✅ CoS/前台建立 cron job 前的檢查清單（先看這個）

在 CoS/前台情境要新建「會被實際執行」的 Hermes cron job 前，逐項確認：

1. **先切到 default profile**：`hermes profile use default`，再 `hermes cron create`。**不要**在某個 named profile（如 `financialresearch`）的 sticky active 狀態下直接建。
   - 理由：只有 `default`（= global root）底下有 running 的 gateway/ticker 在掃 store。named profile 的 gateway 在 multiplexer 下**沒有自己的 ticker**；在它底下建的 job 會寫進該 profile 的 store，沒有任何 ticker 會去 tick 它 → **保證孤兒、永不觸發、且不會報錯**（就是下方 (a) 風險）。
2. agent job 記得同時照 [[hermes-cron-model-pin-convention]] `pin provider+model`（避免換全域模型後 fail-closed）。
3. **建立後驗證**：`hermes cron status` 顯示 gateway running，且 `hermes cron list` 看得到該 job → 代表 store 與有 daemon 的 home 對齊、會觸發，才算完成。

> **事實校準（別把因果記錯）**：這份清單防的是「在非 default profile 建 cron → job 永不觸發」的 **(a) 風險**。**2026-07-22 實際壞掉的 AI news job（`9a65cc2347c8`）不是這個問題造成的**——它一直都在 global root store，故障根因是「unpinned agent job 撞上全域模型 `gpt-5.6-terra → gpt-5.6-sol` 漂移，被花費保護 fail-closed」（見 [[hermes-cron-model-pin-convention]]）。兩者是不同層次的問題，不要在記憶裡把今天的故障歸因成 profile-store 問題。

## 機制（為什麼會發生）

cron job 落在哪個 store，由「**建立當下那個行程解析到的 `HERMES_HOME`**」決定——是動態解析，不是固定 global root，也不是查詢時的 profile 回頭改變 job 歸屬。

- 解析優先序（`cron/jobs.py` `_current_cron_store` 118–140 → `hermes_constants.py` `get_hermes_home` 55–110）：
  1. context override（`use_cron_store`）
  2. `HERMES_HOME` 環境變數
  3. 平台預設 global root
- 重要陷阱：`active_profile` 檔案本身**不會**把 home 導向 profile，只印一行警告；但**前台 `hermes` CLI 啟動時會套用 sticky active profile**，把 home 綁到該 profile。所以前台建立/查詢時，實際 home = sticky active profile 的路徑（而非 global root、也非 shell 顯式設的 `HERMES_HOME`——見守則 3 的實測）。
- 背景真正在跑的是 root/default gateway，它用 `use_cron_store(root)` 明確路由到 global root store（`web_server.py` 10835–10851、`gateway.py` 7024–7033 收斂到 default root）。

## 兩種影響，風險等級差很多（務必區分）

- **(a) 建立階段 = 實質「job 靜默永不觸發」風險（高危）。** 若在一個「沒有 gateway 在跑」的 active profile 底下 `hermes cron create`，job 會寫進那個 profile 的空 store，沒有任何 daemon 去 tick → 永遠不觸發。**這台目前正是高危配置**：前台 sticky profile 是 `financialresearch`，而它的 gateway 是 **stopped**（`hermes cron status` 自己會警告 "Gateway is not running — cron jobs will NOT fire"）。此刻直接 `hermes cron create` 就會踩到。
- **(b) 查詢/管理既有 job = 純視圖問題，job 照跑（無害）。** 晨報（已 pin）、garmin、alpha 都在 global root store、由 root gateway 正常執行（heartbeat 新鮮、next_run 持續推進）。前台 `hermes cron list` 顯示 "No scheduled jobs" 只是因為 CLI 綁在 `financialresearch` 空 store，**不是修復失敗、不影響執行**。

## 正確操作守則（避免踩坑）

核心原則：**「job 所在的 store」與「有 daemon 在 tick 的 store」必須是同一個 home。**

1. 要建立「會被實際執行」的 cron job：先 `hermes profile use <root gateway 對應的 profile>` 把 sticky profile 對齊到 live gateway 的 home，再 `hermes cron create`；或透過 root gateway 服務介面（如 Telegram `/cron`，目前這批 job 就是這樣建立的）建立，天然落在 root store。
2. 建立後一定驗證：`hermes cron status` 要顯示 "Gateway is running" 且 `hermes cron list` 看得到該 job，才代表 store 與 daemon 對齊、會觸發。
3. 要用 CLI 查/管理現有 root store 的 job：同樣要把 CLI 綁到 root home（`hermes profile use` 對齊）。**實測即使 shell 顯式設 `HERMES_HOME=root`，CLI 仍被 sticky profile 覆蓋而顯示空**——所以正解是切 profile，或（如 [[hermes-cron-model-pin-convention]] 那次 pin）走底層 `use_cron_store(root)` 直接對 root store 操作、繞過 CLI 綁定。

## 證據路徑（供日後查閱）

- `%LOCALAPPDATA%\hermes\hermes-agent\cron\jobs.py`（`_current_cron_store`/`use_cron_store` 118–140、`load_jobs` 動態解析 830）
- `%LOCALAPPDATA%\hermes\hermes-agent\hermes_constants.py`（`get_hermes_home` 解析序 55–110）
- `%LOCALAPPDATA%\hermes\hermes-agent\hermes_cli\web_server.py`（gateway cron 路由 10835–10851）、`hermes_cli\gateway.py`（收斂 default root 7024–7033）

## 關聯

- [[hermes-cron-model-pin-convention]] — 同為 Hermes 原生 cron 的維運陷阱，但談的是「換全域模型後 unpinned agent job fail-closed」的模型 pin/花費保護行為，與本則的 store 歸屬/gateway 對齊是不同機制。
- [[hermes-profile-sticky-vs-ephemeral]] — profile 切換的正確姿勢（`-p` per-command 不黏 vs `profile use` 持久 sticky）。本則守則 1「建 cron 前先 `hermes profile use default`」背後的通用原理；臨時用別的 profile 一律 `-p`，就不會不小心黏在無 gateway 的 profile 而踩 (a) 風險。
- [[reference_hermes_workspace]] — Hermes workspace 與原始碼位置。
