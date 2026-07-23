---
name: wrapup
description: >-
  收工程序:更新 STATUS.md 現況快照並 commit。當使用者說「收工」「今天到這」
  「wrap up」「更新一下 status」或明確要結束本次 session 時使用。
  只在互動式(前台)session 執行;headless 背景任務不得使用本 skill。
---

# /wrapup — 收工程序

目的:讓 STATUS.md 永遠反映「最新一次收工時」的狀態,下個 session 開場 30 秒跟上。

## 步驟

1. **回顧本次 session 實際發生的事**(以事實為準,不靠印象):
   - `git log --oneline` 對照 STATUS.md 目前記載的「上一個 session 做了什麼」,
     找出這次新增的 commit。
   - `git status` 看有哪些未 commit 的變更。
   - 回顧本次對話中拍板的決策、發現的問題、產生的待辦。

2. **更新 [STATUS.md](../../../STATUS.md) 四個區塊**,並改「最後更新」日期:
   - **目前所在階段**:只有階段真的推進/改變時才動。
   - **上一個 session 做了什麼**:整段改寫成「本次 session」的內容
     (具體到 commit hash 與檔案),不累積歷史——被換掉的內容如有長期價值,
     應該已在 roadmap/memory 留痕,不歸 STATUS.md 保管。
   - **卡住/未決的問題**:新增本次冒出的、移除本次已解決的。
   - **下一步**:更新為明確可執行的第一步。

3. **Commit**:把 STATUS.md 連同本次 session 其他未 commit 的變更一起 commit
   (訊息用 `docs:` 或對應語意的 prefix)。若工作樹有不該入版控的暫存檔,先排除。
   使用者呼叫 /wrapup 即視為同意這次 commit;push 仍需另外確認。

4. **回報**:一句話總結 STATUS.md 的變更重點(階段有沒有推進、新增了哪些未決問題、
   下一步是什麼),讓使用者收工前最後掃一眼。

## 邊界

- 不做 memory 整併——那是 `consolidate-memory` 的事;若 `memory/inbox/` 有待整併
  檔案,只在回報時提醒一句,不代做。
- 不 push、不打 tag、不動 STATUS.md 以外的文件內容(其他文件的修改應該在
  session 過程中就完成,/wrapup 只負責快照與收尾 commit)。
- 期中 commit 不需要動 STATUS.md;只有 /wrapup 這次收尾 commit 帶上它。
