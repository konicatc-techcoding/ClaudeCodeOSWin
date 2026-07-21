---
name: project-agent-os-web-ui-timing-deferred
description: 使用者對本系統（Agent OS）Web UI 該何時開始設計的猶豫，討論後未拍板，擱置待之後依系統實際進度重新討論
metadata:
  type: project
---

來源：2026-07-17 Hermes telegram session `20260717_161047_484d25a5`（「Agent OS Web UI 開發時機」）。

使用者猶豫的問題：本系統（Agent OS）開發過程中，要在開發中就設計 Web UI，還是等開發完之後才開始設計。

討論中提出但**未拍板**的方向性建議：採 **API/Domain-first + UI contract-first + UI thin-slice** 路線——核心能力、資料模型、workflow 與 observability 先做穩，同時儘早做少量真實 Web UI 驗證操作方式，但讓 UI 只依賴穩定的 domain contract（`Web UI → Stable Control Plane API → Domain Model → Runtime/Worker/Queue`），避免直接耦合到底層實作細節，這樣底層日後替換（例如 in-process worker 換 Celery/Temporal、SQLite 換 PostgreSQL、polling 換 SSE/WebSocket）不會強迫 UI 跟著重構。

使用者最後表示「到後面再做又怕開發拖太慢……算了，之後再討論好了」，**沒有做出決定**，把討論延後。討論中提到之後重新討論時可用來判斷投入時機的準則：
- 核心 workflow 是否已有可跑通的 vertical slice
- `Task / Run / Event / Artifact / Approval` 的資料模型是否已初步穩定
- 目前最痛的是 backend 能力不足，還是缺少操作/觀測/除錯介面
- 是否需要 UI 來驗證 human-in-the-loop 流程
- UI 目標是 internal operator console，還是未來的 end-user product

**How to apply**：這不是排定的工作項目，只是留下判斷準則供之後重提時直接引用，節省重新討論的成本。之後 `planning` 或使用者本人若重新評估 Web UI 開發時機，可以直接用上面五個準則起頭，不用假設已有結論——目前這個問題完全開放，沒有拍板方向。
