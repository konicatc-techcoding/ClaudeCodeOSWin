---
name: hermes-tavily-key-plaintext-todo
description: Windows 側 Hermes gptcoding profile 的 Tavily API key 以明碼寫在 config.yaml 的 mcp_servers.tavily.url 裡，待修
metadata:
  type: project
---

在 Phase 2a Hermes profile 稽核過程中發現：`%LOCALAPPDATA%\hermes\profiles\gptcoding\config.yaml` 的 `mcp_servers.tavily.url` 欄位把 Tavily API key 以明碼形式直接寫在 URL 字串裡，沒有透過環境變數注入。

**Why**：明碼憑證留在設定檔屬於安全衛生問題——config.yaml 可能被備份、同步或不慎提交，導致 key 外洩。同一個 repo 其他 profile 已經在用 `env:` 前綴的憑證慣例（例如 `credential_pool` 裡 `source: env:OPENROUTER_API_KEY` 的模式），tavily 這條應該比照辦理。

**How to apply**：不是這次任務範圍內的緊急項目，屬於「找時間修掉」的待辦。動手時：
1. 先備份原始 `config.yaml`。
2. 把 key 改成從環境變數讀取（例如 `TAVILY_API_KEY`），比照既有 `env:` 前綴慣例。
3. `config.yaml` 只保留 placeholder，不留明碼值。
4. 這屬於 engineering domain 的技術實作，之後排入工作時應分派給 `engineering` subagent，而非由 CoS 自行動手。
