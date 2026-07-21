---
name: project-telegram-session-lifecycle-management-idea
description: 使用者提出但明確擱置的構想——Hermes Telegram session 累積後要不要建立生命週期管理機制；目前刻意不建排程、不掃描、不歸檔
metadata:
  type: project
---

來源：2026-07-17 兩個 Hermes telegram session（`20260717_152215_088d96af`「Agent OS 驗收測試偏好」後半段起頭、`20260717_154149_7c80579b`「Telegram Session 生命週期管理」正式討論）。

使用者提出構想：Hermes 的 Telegram session 會持續累積，之後或許需要一套機制找出長時間未使用的 session 並做後續處理。討論後定調為 **Session Lifecycle Management**，而非單純「定期刪除未使用 session」，建議流程為 `Discover → Classify → Notify/Archive → Grace period → Prune → Audit`，並提出三層狀態的雛形（Active：近 30 天有使用，不處理；Stale：31–90 天未使用，標記並產出報告但不刪除；更久的層級討論在匯入摘錄中被截斷，未完整保留）。

**目前明確拍板：暫不執行，只停在構想階段**——使用者當場喊停：「不要真的建立排程，也不要直接執行整理，因為我還沒有確定判斷閒置 session 的標準」，並在同一次討論最後進一步收斂為「保留想法，但不列入正式待辦」。也就是說：**不建立 cron、不做 session scan、不 archive、不 prune，是否要正式立案完全由使用者之後人工判斷**，不是本系統目前的待辦事項。

若之後使用者主動決定要推進，討論中已產出可直接沿用的判定維度，之後可以由此起草一份簡短的 Session Retention Policy：
- 時間（最後活動時間）——只能當初步候選條件，不能單獨當唯一判準
- 任務狀態（是否含 TODO、pending decision、未完成交付）
- 內容價值（是否有架構決策、設定、debug 結論、研究摘要）
- 關聯性（是否被 cron、handoff、外部資料或其他 session 引用）
- 可恢復性（刪除 vs 標記/歸檔的可逆程度，匯入摘錄此欄未完整擷取）

**How to apply**：這不是待辦，是「使用者主動表態前不要碰」的構想紀錄。之後如果使用者提到要處理 Telegram session 堆積問題，先引用這裡已經想過的判定維度，不用重新從零討論；在使用者明確拍板前，不要自行建立任何相關排程或執行任何 scan/archive/prune。
