---
name: hermes-profile-sticky-vs-ephemeral
description: Hermes profile 操作的正確姿勢——一次性/臨時用某 profile 一律 `hermes -p <name> <cmd>`（per-command、跑完即還原、不寫 active_profile），不要用 `hermes profile use <name>`（持久 sticky、無 TTL/無自動還原、除非再 profile use 否則永遠不變）。整個 shell 臨時用某 profile 則 `export HERMES_HOME=...\profiles\<name>`（限該 shell）；只有真的要長期改預設才 `profile use`；被黏住用 `hermes profile use default` 清回 root。事實依據經原始碼＋實測雙重查證。
metadata:
  type: reference
---

Hermes（NousResearch HermesAgent，Windows 側原始碼在 `%LOCALAPPDATA%\hermes\hermes-agent`，見 [[reference_hermes_workspace]]）profile 切換的**正確操作姿勢**，經原始碼＋實測雙重查證。與 [[hermes-cron-store-binding-gateway-alignment]] 高度相關：那則講「cron job 落哪個 store、誰在 tick」；本則講「切 profile 的 sticky 陷阱與正確姿勢」，是前者「建 cron 前先切 default」守則背後的通用原理，且適用範圍不限於 cron。

## ✅ 核心守則

**一次性/臨時用某個 profile 跑指令，改用 `hermes -p <name> <cmd>`，不要用 `hermes profile use <name>`——後者會 sticky 黏住。**

## 最佳實踐對照表（平常待在 default、只偶爾臨時用別的 profile）

| 情境 | 正確做法 | 是否 sticky |
|------|----------|-------------|
| 一次性用別的 profile 跑指令 | `hermes -p <name> <cmd>`（＝`--profile <name>`）| 否，跑完自動回 default |
| 整個 shell 都要用某 profile | `export HERMES_HOME=...\profiles\<name>` | 限該 shell，不寫 active_profile |
| 真的要長期改預設 | `hermes profile use <name>` | 是，持久 sticky |
| 被黏住要清回 default | `hermes profile use default` | 解除 sticky（unlink active_profile，回 root）|

## 事實依據（別把因果記錯）

- **`hermes profile use <name>` = 持久 sticky。** 把 profile 寫進 `active_profile` 檔，除非再次 `profile use` 否則永遠不變。**沒有任何 TTL / auto-reset / 關閉自動還原機制**——唯一寫入者是 `set_active_profile()`（`profiles.py:1807-1829`），無到期欄位。
- **`hermes -p <name> <cmd>` / `--profile <name>` = per-command。** 只把該 profile 解析成 `HERMES_HOME` 設在**這一個行程**，跑完就沒了，**完全不寫 `active_profile`、不 sticky**（`main.py` `_apply_profile_override`，`-p` 不呼叫 `set_active_profile`；實測連跑多次 `-p <name> ...` 後 active_profile 仍是原值）。
- **`HERMES_PROFILE` 環境變數不是 home 解析用的**——home 解析吃 `HERMES_HOME`。臨時整個 shell 用某 profile 才 `export HERMES_HOME=...\profiles\<name>`（限該 shell）。**沒有 `profile use --no-sticky` 這種變體。**
- **清回 default**：`hermes profile use default`（unlink active_profile 檔、回 root）。

## 延伸應用：改某 profile 的模型

- 非互動、只改 model：`hermes -p <name> config set model.default <model>`。這是**持久變更**（寫進該 profile 的 `config.yaml`）；`-p` 只讓執行的 session 不黏 sticky，**不會**讓「改的模型」臨時失效。
- 要換 provider（連 base_url／憑證一起）：改用互動式 `hermes -p <name> model`。

## 事件背景（為什麼要記這條）

2026-07-22：使用者因先前打了 `hermes profile use financialresearch` 而被 sticky 黏在 `financialresearch`（其 gateway 在 multiplexer 下是 **stopped**），造成前台 `hermes cron list` 看不到實際在 root 跑的 job、且若在該 profile 下新建 cron 會踩「孤兒永不觸發」風險（見 [[hermes-cron-store-binding-gateway-alignment]] 的 (a) 風險）。已用 `hermes profile use default` 清回 default。這條心法就是為了避免重蹈：臨時用別的 profile 一律 `-p`，就不會不小心把自己黏住。

## 關聯

- [[hermes-cron-store-binding-gateway-alignment]] — cron job 的 store 歸屬/gateway 對齊；「建 cron 前先切 default」守則背後的通用原理就是本則的 sticky 陷阱。sticky 黏在無 gateway 的 profile 正是那則 (a) 風險的觸發前提。
- [[reference_hermes_workspace]] — Hermes workspace 與原始碼位置。
