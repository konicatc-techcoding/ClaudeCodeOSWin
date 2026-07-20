---
name: hermes-task-category-model-routing-preference
description: 使用者在意的未來功能——依任務類型/範疇設定該用哪個模型/Hermes lane，目前架構只到 domain 層級，未支援
metadata:
  type: project
---

使用者 2026-07-20 在 Phase 2f 開工前明確標記：他很在意「依任務的類型或範疇，規範該用哪個模型」這個功能，希望在適合開發的時機被提醒（不是現在就要做）。

**現況落差**：`registry/agents.yaml` 的 `default_capability` 只能做到 domain 層級（例如整個 `engineering` domain 固定配一個 capability），無法做到「同一個 domain 底下，A 類任務用 Hermes lane、B 類任務用 Claude native」這種更細的區分。`scripts/dispatch_domain.py` 雖然吃 `--category` 參數（對應 `registry/delegation_policy.yaml` 的任務分類），但目前只作為 JSON envelope 的記錄欄位，**不參與 lane 選擇邏輯**——真正決定 lane 的只有 `--capability`/`--lane`。也就是說，「依任務類型自動選模型」這個決策引擎目前完全不存在。

**Why**：這比 [[hermes-agent-repo-work]] 提到的 Phase 2f（讓 subagent 知道有 active 的 Hermes lane 可以呼叫）更進一步——Phase 2f 只解決「工具存在但沒人用」，使用者要的是「訂規則決定什麼時候該用哪個」，屬於下一層的設計工作。

**How to apply**：Phase 2f（讓 subagent 真正呼叫 `dispatch_domain.py`）完工、且四條 Hermes lane 有一段時間的真實使用經驗之後，適合重新提出這個功能討論。屆時建議走 `planning` domain 起草一份設計提案（比照本專案 Stage 2.5/2.6/2.7 的既有慣例），核心問題包括：規則的顆粒度要到多細（domain / category / 單一任務關鍵字）、規則正本放哪個 registry 檔案、要不要跟現有 `delegation_policy.yaml` 的任務分類共用同一套 category 定義。不要在 Phase 2f 或其他不相關任務裡順便夾帶這個功能，範圍會失控。
