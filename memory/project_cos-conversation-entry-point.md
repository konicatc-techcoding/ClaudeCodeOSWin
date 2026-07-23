---
name: project-cos-conversation-entry-point
description: 使用者已採納的「怎麼跟 CoS 對話」最終定論——對話入口直接用現成互動式 `claude` CLI（不自建 web 對話頁）、語音輸入優先用 Windows Win+H（不自建 STT）；並釐清即時互動找 CLI／非同步外包找 Telegram-headless 的使用情境劃分。此決策推翻先前「Console 對話頁走路 A 薄殼包 headless」方向
metadata:
  type: project
---

來源：2026-07-22 互動式前台 CoS session（使用者提出、經 `planning` 評估後採納）。本則是「怎麼跟 CoS 對話」這個問題的**最終定論**，取代 [[project_agentos-console-phasing]] 先前記錄的「Console 對話頁 MVP 走路 A 薄殼包 headless」方向（該檔已據此校準；細節見文末）。

## 對話入口 = 直接用互動式 `claude` CLI（不自建 web 對話頁）

**結論**：日常跟 CoS 對話，直接開現成的互動式 `claude` CLI，不再自建 web 對話 MVP。

- **理由**：CLI 原生就內建先前自建 MVP 要辛苦重造的一切——token 串流、互動式權限授權（人在迴路、非 headless fail-closed）、多輪有狀態 session、subagent 分派顯示。先前規劃的「web 對話 MVP（路 A 薄殼包 headless）」本質是重造一個**比 CLI 更差的 client**，沒有存在理由。
- **操作前提（重要）**：終端機 cwd 必須是**專案根**（repo root），開 `claude` 才會載入 root `CLAUDE.md`、得到 CoS 分派行為。在別的目錄開 `claude` 只是裸 session，不是 CoS。
- **已更正的事實／限制（2026-07-22 校準）**：互動式 Claude Code / CLI / Desktop 的 session **不會**被 Hermes bridge 自動捕捉。先前曾把「CLI session 會被 Hermes session bridge 捕捉（`session_source` 含 `cli`）」當成加分項記錄，經 `planning` 實地讀程式碼查證為**錯誤**：Hermes bridge（`hermes/bridge_scanner.py` → `HermesSessionAdapter`）**只讀 Hermes 自己的 `state.db`**（`%LOCALAPPDATA%\hermes\state.db`），其中 `sessions.source` 的 `cli`/`tui` 指的是 **HermesAgent（NousResearch Hermes）自己的 CLI/TUI 入口**，**不是 Claude Code 的 CLI/Desktop**；Claude Code 自己的 session 存在 `~/.claude/`（例：`C:\Users\razer\.claude\projects\...`），Hermes bridge 根本不讀那裡。連帶：`claude_native` 執行的 subagent 產出（跑在 Claude Code session 裡）也**不會**進入 Hermes 那條 bridge 記憶管線。**注意這不影響 CLI 當對話入口的核心結論**——那是基於「CLI 原生已有串流／互動授權／多輪／subagent 顯示、不必自建」，與這個（已不成立的）附帶好處無關；此處僅移除一個錯誤的加分敘述。

## 語音輸入 = 優先用 OS 內建 Windows Win+H（不自建 STT）

- **Win+H**（Windows 內建語音打字）對聚焦的文字輸入口述轉文字，近乎零工程。
- **待驗證（pending，使用者自測，非動工項）**：Win+H 能否穩定對終端機 TUI 輸入是版本/環境相關。使用者會自己花約 2 分鐘實測（開終端跑 `claude`、按 Win+H 講一句，看字有沒有進去）。這是使用者自測，不是要排的工。
- **備援**：即使 Win+H 對終端不靈，口述到任何可靠文字框（記事本/筆記）再貼進 CLI，一樣近乎零成本。
- **限制**：語音適合研究類 prompt；打 code 辨識差。實務是「語音組字、手按 Enter 送出」。
- **明確不自建 STT**：對互動式 TUI 灌 stdin 脆弱、ROI 低。

## 使用情境劃分（誠實記錄，避免日後誤用）

- **即時、在場、來回互動的研究／簡單開發 → 用 CLI。**
- **真正 fire-and-forget（丟出去、人離開、完成再通知）→ 用既有 Telegram → headless 背景路，不是 CLI。** CLI 是佔用注意力的即時 session，解不了非同步外包。
- **遠端／瀏覽器／手機丟任務 → 也是 Telegram 守備範圍**，CLI 給不了。
- **結論**：即時互動找 CLI，非同步外包／遠端找 Telegram；兩者合起來才完整覆蓋「把任務丟出去」的需求。

## Roadmap 影響

- **「web 對話 MVP（路 A 薄殼包 headless）」→ 擱置**（被現成 CLI 取代，不再是現行方向）。連帶先前列為其前置關卡的「headless `claude -p --resume` 能否撐 web 多輪對話」spike **不再是待辦**——它是被 CLI 決策取代，不是還要做。
- **「SDK 常駐宿主 ＋ 全套重功能（串流／授權卡片／中途控制／subagent 視覺化／語音）」→ 更往後擱置**，只在「CLI 真的撞到某個它給不了、而使用者確實需要的具體缺口」時才重啟討論。
- **「三區外殼／監控頁（Stage 3 唯讀監控）」→ 不動，照原計畫。** 那是唯讀觀測，跟對話入口是兩條獨立軌，不受本決策影響。

## How to apply

- 需要「跟 CoS 對話」時，預設答案就是「在 repo root 開互動式 `claude` CLI」，不是去找/蓋 web 對話頁。
- 有人再提「做 web 對話 MVP／路 A 薄殼／SDK 宿主」時，先引用本則：現行方向是現成 CLI，路 A 與 SDK 宿主已擱置，除非拿得出「CLI 給不了、且使用者確實需要」的具體缺口。
- 判斷任務該走哪條入口：即時互動→CLI；非同步外包/遠端→Telegram-headless。
- Stage 3 唯讀監控與三區外殼是獨立軌，別因本決策而誤以為整個 dashboard 願景被砍。

## 相關記憶

- [[project_agentos-console-phasing]] — AgentOS Dashboard 願景分期。本則推翻該檔先前的「Console 對話頁 MVP 走路 A」段落；該檔已校準為「對話入口採現成 CLI + Win+H、路 A/SDK 宿主擱置」，Stage 3 唯讀監控定位不變。
- [[project_agent-os-web-ui-timing-deferred]] — 先前對 Web UI 開發時機的擱置討論；本則進一步收斂為「對話入口用現成 CLI、不自建 web 前端」。
- Stage 3 唯讀 dashboard 設計提案（文件，非 memory）：`docs/stage3-dashboard-observability-proposal.md`（唯讀觀測軌，不受本決策影響）。
