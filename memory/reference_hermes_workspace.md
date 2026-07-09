---
name: hermes-workspace-projects
description: Pointers to the user's projects living in the Hermes workspace (outside ClaudeCodeOS) — what exists there and its last observed state
metadata:
  type: reference
---

使用者在 Hermes（NousResearch HermesAgent）的 workspace 裡有自己的專案，與 ClaudeCodeOS 本身無關，但屬於使用者的進行中工作脈絡。本檔案記錄「那邊有什麼」與最後觀察到的狀態；正本資料在 Hermes 側（episodic 層 `state.db`），這裡只是指標與摘要。

## ResearchHelper

- **用途**：研究助理專案——對指定主題搜尋資訊，並把結果上傳到 Notion（主腳本 `search_and_notion.py`，設定檔為 Notion API 的 config template）。
- **最後觀察狀態（2026-06-27，來源：Hermes tui session `20260628_004555_13dd7b`「ResearchHelper 加強建議分析」，模型 nvidia/nemotron-3-super-120b-a12b）**：仍是基本骨架——`search_and_notion.py` 只印出開始/結束訊息，實際的搜尋與 Notion 整合邏輯尚未實作；目錄結構已建立但缺 `prompts/` 目錄；Notion API 設定檔只有 template（佔位值，未填真實 token）。
- **該次分析的結論方向**：需要補上真正的搜尋整合（第一優先），其餘改善建議在 session 原文中（匯入摘錄因 adapter 500 字元截斷未完整保留；需要細節時可經 `HermesSessionAdapter` 回讀該 session）。
