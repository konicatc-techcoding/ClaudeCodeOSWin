---
name: aichain-claude-cli-provider-trial
description: 使用者想試把 AIChain 每日分析從 anthropic_api 改走 claude_cli（Claude Code 訂閱制），動機是「看品質會不會比較好」而非省錢——2026-08-27 那份以省錢為前提、結論「不建議改」的評估因此不適用，需重新設計成品質對照實驗；等目前待處理項目（A11→A12→A8→A6→B1→B2）完成後才規劃
metadata:
  type: project
---

使用者 2026-08-27 明確要求記住的未來規劃。**現在不做**——使用者表示「等目前待處理的項目完成後再規劃」。本檔案記錄意圖、動機（很重要，會改變評估框架）、以及動手前必須知道的技術事實，避免屆時重跑一次查證。

## 核心意圖與動機（動機是關鍵，別搞錯）

想試把 **AIChain 每日分析**的 LLM 呼叫，從 Anthropic API（`00_CONFIG\claude_provider.yaml` 的 `provider: anthropic_api`、`model: claude-sonnet-5`）改走 **`claude_cli`（Claude Code，訂閱制）**。

**動機是「看品質會不會比較好」，不是省錢。**

這一點必須寫清楚，因為它讓既有評估失效：2026-08-27 `engineering` 做過一份完整評估，結論是「不建議改」——但**那份評估是以省錢為前提**（論據是每月只省個位數美元、且會與互動式訂閱額度競爭）。使用者的動機既然是品質，**那份評估的結論不適用**，屆時需要重新設計成**品質對照實驗**，不要拿舊結論當定案回覆使用者。

## 實驗設計方向（CoS 建議，尚未拍板）

用**同一天的 packet 分別跑 `anthropic_api` 與 `claude_cli`，比對輸出品質**，而不是直接換掉 cron 的 provider。

Claude Code 那條路的潛在差異來源是**它有工具**——可以自行讀 packet 原始檔、追查 evidence URL。這既可能**提升**分析品質，也可能**降低 JSON 純度的穩定性**（工具用完後的輸出格式較難約束）。兩個方向都要在對照裡看。

## 技術事實（2026-08-27 唯讀查證，動手前先看這段）

- **`claude_cli` provider 已完整實作**（`AIChainClaude\src\aichain_claude\provider.py:315-385`），不是待填空位。只改 `00_CONFIG\claude_provider.yaml` 三行（`provider` / `command` / `claude_enabled`）即可跑，**0 行 code**。
- **但它與 `anthropic_api` 有三處不對等，直接換會降級**：
  1. **沒有 JSON repair 重試**（`anthropic_api` 有，在 `provider.py:274-296`）——一次驗不過就整條鏈當天失敗。
  2. **不內嵌 packet 內容**——只送 `effective_prompt.md`，裡面只有 packet 的**路徑字串**；`anthropic_api` 會把 103KB packet 全文貼進 prompt。
  3. **沒有嚴格輸出約束那段**——「Return exactly one raw JSON object…」等五條寫在 `_build_anthropic_prompt` 裡，`claude_cli` 路徑不經過它。

### 兩個必踩的坑

- **`--output-format json` 會壞掉**：回傳的是信封 `{"type":"result","result":"..."}`，原樣寫進 `claude_response.json` 會讓 schema 找不到 `analysis_version` 而失敗。**必須用預設的 text**。注意既有先例 `hermes/adapter/invoke_cos.sh` 正是用 json——**不可照抄**。
- **需要 `--add-dir`**：packet 與 provider 工作目錄跨專案，要指向 `DailyAIChainResearchV2` 才讀得到。
- **cron 環境 PATH 與互動式 shell 不同**：`command` 要寫絕對路徑 `C:\Users\razer\.local\bin\claude.exe`。（此類 cron 環境差異與 job 維運慣例另見 [[hermes-cron-store-binding-gateway-alignment]]；AIChain 是 `no_agent=True` 純 script job，不受 [[hermes-cron-model-pin-convention]] 的模型 pin fail-closed 影響、不需要 pin。）

### 成本現況（供日後對照，非決策理由）

- 2026-08 實際帳單 **$3.84**，但該月只跑了 14 天（應為約 26 天）。滿月且 B3-β 上線後 packet 變厚，**預估約 $7.5/月**。屬推論待驗——`provider.py` 未保存 `usage`。
- **另一個成本旋鈕**：`content_enricher` 的 `max_excerpt_chars=1200` 才是真正線性控制輸入成本的參數；`max_tokens: 32000` 只是輸出上限、調它省不到錢。目前**刻意不動**，等跑滿一個月看實際帳單再說。

### 已排除的路線

**路線 B（走 Hermes lane）已排除**：`dispatch_domain.py` 把 prompt 當 argv 傳，完整輸入 121KB 是 Windows argv 上限的 3.7 倍；且可用 lane 都是 OpenAI Codex 非 Claude、schema 通過率未知、該額度 2026-08-05 曾耗盡。

## 前置條件（2026-08-27 當下的「目前待處理項目」，依序）

1. **A11** — 修 `web_search` 的 recency／分類
2. **A12 層級 A** — Tavily 主力化輕量版
3. **A8** — 修 14 條壞掉的官方 feed（**必須排在 A12 之後**，否則會關掉 Tavily 觸發條件、全文覆蓋反而倒退）
4. **A6** — evidence gate
5. **B1** — 切 `claude_analysis.v2` 契約
6. **B2** — 送達驗證與新鮮度看門狗

**B1 與本項有交互作用**：若先切 v2，schema 更嚴格（`thesis_memory_updates` 每筆強制 http(s) URL、且禁止用單一來源證據 strengthen thesis），屆時再換 provider 的 **JSON 通過率風險會更高**。**兩者的先後順序需要另外拍板**，不要預設「照上面清單順序做完 B1 再換」就是定案。

## How to apply（屆時怎麼接手）

- 重提時機：上述六項前置完成後。重提前先確認「動機仍是品質」，若使用者改口成本考量，才回頭適用 2026-08-27 那份省錢評估。
- 相關檔案都在 AIChain 專案側，**不在 ClaudeCodeOS 版控內**——比照 [[feedback_hermes-cron-scripts-no-commit]] 的慣例，改完即生效、沒有 commit 這個步驟。專案位置指標見 [[reference_hermes_workspace]]。
- 這則屬於「依任務類型選模型/lane」這個更大主題的一個具體個案，通則見 [[hermes-task-category-model-routing-preference]]。
