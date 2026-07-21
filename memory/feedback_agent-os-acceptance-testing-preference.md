---
name: feedback-agent-os-acceptance-testing-preference
description: 使用者對本系統（Agent OS）功能驗收方式的固定偏好——先跑單筆完整鏈路確認正常，再做批次測試
metadata:
  type: feedback
---

來源：2026-07-17 Hermes telegram session `20260717_152215_088d96af`（「Agent OS 驗收測試偏好」）。

使用者明確表達的工作偏好（非單一任務指示，是長期適用的驗收習慣）：**之後做 Agent OS（本系統）的功能驗收時，偏好先跑單筆完整鏈路，確認 scanner、importer 和 enqueue 都正常後，再進行批次測試。**

**How to apply**：之後 `engineering`／`automation` 等領域驗收類似管線功能（例如 bridge scanner/importer、enqueue 流程）時，應先安排一次「單筆冒煙測試」，確認關鍵鏈路正常後才轉批次/大量測試，不要一開始就跑批次驗收。
