來源：手動測試種子（這份內容是真實的專案狀態，不是測試假資料）
內容：v0.1 的五個領域 subagent（intelligence / engineering / automation / knowledge / planning）已全部建立完成，並各自通過 CoS routing test（確認 CoS 會正確分派給對應 subagent_type，不會自己代打）。目前還沒動工的部分：Hermes 常駐程式與 SQLite job queue、knowledge 領域的排程觸發（何時自動跑 consolidate-memory）、headless 模式下的 Bash 權限（WebSearch/WebFetch 已解決）。
