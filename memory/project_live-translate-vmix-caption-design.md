---
name: project-live-translate-vmix-caption-design
description: 即時翻譯字幕系統（外部音訊→Gemini Live Translate→網頁字幕→vMix）的基本設計拍板——目前只是設計討論，尚未開工
metadata:
  type: project
---

來源：Hermes gptcoding profile session `20260716_231927_a64d3f38`（WSL 側 `~/.hermes/state.db`，Telegram 入口，model=gpt-5.6-sol），對話時間 2026-07-30 13:39–13:44（session 起於 07-16 後 resume）。完整設計檔存於 Hermes 側：`C:\Users\razer\.hermes\plans\2026-07-30_134140-live-translate-vmix-basic-design.md`。

**狀態：設計討論，尚未開工**——這是一次性的架構設計對話產出，不是進行中專案，沒有排程、沒有 repo、沒有待辦。

## 需求一句話

從麥克風／Audio Interface 收音，經 Gemini Live Translate 即時翻譯成繁體中文，顯示在可調樣式的字幕網頁上，並同步送進 vMix（可控行數、行寬）。

## 資料流

```text
麥克風/Audio Interface → Audio Capture（裝置/聲道選擇、音量表）
→ Audio Converter（16 kHz / Mono / PCM16 / 100 ms chunk）
→ Gemini 3.5 Live Translate → Transcript Assembler（partial/final 合併、去重、斷句）
→ Caption Formatter（行數、行寬、標點換行）
→ 分流兩路：WebSocket → 字幕網頁 ｜ vMix Adapter → vMix API
```

## 關鍵技術選型

- 翻譯模型：`gemini-3.5-live-translate-preview`（Preview 模型，名稱必須放設定檔，不硬編碼），目標語言 `zh-Hant`
- 後端：Python 3.11 + FastAPI + WebSocket，`google-genai` SDK、`sounddevice`/PortAudio、`httpx` 呼叫 vMix HTTP API
- 前端：React + TypeScript，雙頁面——`/control`（控制設定＋即時預覽）、`/overlay`（純字幕透明頁，無控制元件）
- Windows 音訊第一版只支援 WASAPI/WDM；若裝置只走 ASIO 要另做 ASIO Adapter，不能假設 PortAudio 一定支援

## 最關鍵決策

**字幕以 vMix Browser Input 為主要輸出（把 `/overlay` 頁加進 vMix），GT Title + SetText API 作為相容備援。** 理由：Browser Input 由 CSS 完全掌控字型/顏色/背景/描邊/陰影/行數/行寬/動畫，網頁預覽與 vMix 顯示一致；GT Title API 只適合更新文字，不適合任意改視覺屬性（樣式應由 `.gtzip` 模板預定義）。此時 vMix API 只負責 Overlay 顯示/隱藏、狀態檢查、Input 切換。

## 最有價值的工程細節

1. **Bounded queue 丟舊音訊**：audio callback 不直接呼叫 Gemini API，而是寫進有限容量 queue（callback → bounded queue → Gemini sender）；網路變慢時丟棄最舊音訊，不讓字幕延遲累積。
2. **行寬用 Unicode display width，不用字串長度**：中文全形約 2 columns、英數約 1 column，中英混排時直接數字元會導致每行視覺寬度不一致。
3. **字幕文字取 `output_audio_transcription`**（翻譯後中文轉錄），`input_audio_transcription` 是原文轉錄；LiveConnectConfig 帶 `translation_config(target_language_code="zh-Hant", echo_target_language=True)`。
4. **vMix 更新節流與去重**：每秒約 5–10 次上限，文字沒變不重複呼叫 API；GT Title 欄位名通常要 `.Text` 結尾，中文/`&`/換行必須正確 URL encode，成功回 HTTP 200、錯誤通常回 500，啟動時先讀 `/api/` XML 確認 Input 與欄位存在。
5. **音訊統一格式**：不論裝置原生 44.1/48 kHz、stereo/多聲道，一律轉 16 kHz / Mono / signed PCM16 little-endian、每 100 ms 一個 chunk（Gemini Live 官方要求）。
6. 字幕格式化順序：優先在 `，。！？；：` 後換行 → 再按最大行寬切 → 超過最大行數只留最後 N 行；partial 可即時顯示，final 才寫入歷史。

## 六階段開發序

1. 音訊擷取（裝置列舉/選擇、音量表、轉 16 kHz mono PCM16、寫測試 WAV 驗證格式）
2. Gemini CLI 翻譯（串流到 Gemini、CLI 顯示原文＋繁中，先不做網頁與 vMix）
3. 字幕格式器（partial/final 合併、去重、標點斷句、行數行寬，中英文＋emoji 單元測試）
4. 字幕網頁（FastAPI WebSocket、控制頁、透明字幕頁、即時樣式）
5. vMix 整合（健康檢查、Input discovery、SetText、Browser Input、節流與錯誤隔離、真實 preset 驗證）
6. 直播穩定性（Windows 打包、長時間運行、網路/裝置/Gemini session/vMix 各種中斷恢復）

**How to apply**：使用者若提到要開工做即時翻譯字幕／vMix 字幕系統，先引用本檔與 Hermes 側完整設計檔，不用重新設計；分派給 `engineering` 時把「Browser Input 為主、GT Title 為備」與上述工程細節寫進任務脈絡。在使用者明確表態開工前，這不是待辦。
