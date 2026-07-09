# AI 產業動態 — 2026 年 7 月上旬快照

由 intelligence domain 蒐集整理，供 Chief of Staff／使用者查閱長期脈絡用。原始蒐集日期：2026-07-06。之後若有新一輪蒐集，建議另開檔案或在此檔案追加日期區段，避免覆蓋歷史紀錄。

## 各公司模型/技術現況（截至 2026-07-06）

- **OpenAI**：正在預覽 GPT-5.6 系列（Sol / Terra / Luna 三種尺寸），主打強化 cybersecurity 能力與更強安全棧；新增 max reasoning effort、ultra mode（多 subagent 協作）。目前僅開放給約 20 個信任夥伴，預計 2026-07 內擴大開放。定價：Sol $5/$30、Terra $2.5/$15、Luna $1/$6（每 1M tokens，input/output）。另與 Cerebras 合作，Sol 在該硬體上可達 750 tokens/秒。
- **Anthropic**：2026-06-30 發布 Claude Sonnet 5，2026-07-01 起成為 Free/Pro 預設模型，號稱「最 agentic 的 Sonnet」，效能接近旗艦 Opus 4.8；原生 1M token context window；促銷價 $2/$10（每 Mtok，至 2026-08-31）。同時重新全球部署 Claude Fable 5（先前因出口管制暫停，現已解除）。另發布 Claude Science（科研 workbench，串接 NVIDIA BioNeMo）公開 beta，以及 Claude Apps Gateway（給 Amazon Bedrock / Google Cloud 的自架控制平面，含 SSO、集中式權限、成本歸戶）。並公開了新版「Claude 憲法」（constitutional AI 文件更新）。
- **Google DeepMind**：主力模型為 Gemini 3.5（Flash 版本已廣泛可用，比 Gemini 3.1 Pro 在部分 coding/agentic 測試上更強，輸出速度號稱比其他前沿模型快 4 倍）。產品策略明顯往「日常工作作業系統」靠攏：Daily Brief（整合 Gmail/Calendar 的每日簡報）、Gemini Spark、Gemini Live；另有 Gemini Omni 多模態影片生成模型。
- **Meta**：2026-07-01 宣布成立新雲端事業 Meta Compute，出售自家過剩 AI 運算力，直接與 AWS/Azure/GCP 競爭（消息一出晶片股下跌、Meta 股價站上 600 美元）。模型方面，Llama 系列已由 Meta Superintelligence Labs 於 2026-04 推出的 Muse Spark（內部代號 Avocado）取代，先前歷經三次跳票。
- **xAI**：2026-07-05 Elon Musk 宣布 Grok Imagine（圖像/影片生成）開發完成。Speech-to-Text（支援 25 語言）與 Text-to-Speech API 已 GA。Grok MAU 約 1.17 億（2026-03，來自 SpaceX IPO 文件），但成長自 3 月起已進入平台期，US mobile app 市佔持平。與 Databricks 合作，Grok 進駐 Databricks Agent Bricks。
- **Microsoft**：規劃將 Copilot 消費版與企業版整合為單一 App（預計 2026-08），並推出需額外付費的「AutoPilot」常駐自動化 agent。2026-07-02 宣布成立 Microsoft Frontier Company，投入 25 億美元、6000 名工程／產業專家協助企業導入 AI，首批客戶包含 LSEG、聯合利華、Land O'Lakes、Accenture。Copilot Chat 現已可選用 Claude 作為模型選項。
- **NVIDIA**：Vera Rubin 平台已進入量產（七款新晶片，含 CPU/GPU/NVLink 6/ConnectX-9/BlueField-4/Spectrum-6，以及整合的 Groq 3 LPU），主打 agentic AI 推理；相較 Blackwell 平台號稱推理 token 成本降 10 倍、MoE 訓練所需 GPU 數降 4 倍。2026 下半年起由 AWS、Google Cloud、Microsoft、OCI 及 CoreWeave/Lambda/Nebius/Nscale 等雲端夥伴部署。另與 SK Hynix 簽訂多年期記憶體合作。

## 各公司公開策略／路線圖方向

- **OpenAI（Sam Altman）**：預期 AGI 於 2028 年前後出現，superintelligence 更晚（同樣約 2028 年底前）；2026 年首要任務是企業銷售（enterprise sales），API 業務成長快於 ChatGPT。長期主軸：agents everywhere、memory 作為護城河、devices 作為 context 入口、compute 視為公用事業（utility）；並押注 AI for science（從整理既有知識轉向實際發現新知）。
- **Anthropic（Dario Amodei）**：預測 AGI 於 1-3 年內出現、軟體自動化 2 年內；預期 AI 產業於 2030 年前創造數兆美元營收（2028 年達數千億美元量級）。Anthropic 自身營收從 2023 年初的 0 成長到 2025 年底約 90 億美元，2026 年預估 200-260 億美元。策略聚焦企業市場以維持高利潤、避免過度冒進投資（"responsible scaling"）。公開列出五大風險：自主性偏差（misalignment）、生物濫用、核／輻射威脅、威權集中化（監控/社會控制濫用）、經濟劇烈失衡（大規模失業）。

## 產業重要動態

- **監管**：美國川普政府於 2026-06-02 發布第 14409 號行政命令，強調以創新為主、鬆綁 AI 開發限制，但要求 AI 開發商在對外部夥伴開放新模型前，須提前最多 30 天自願與聯邦政府分享；同時要求國安機關建立 AI 風險評估框架與 AI 網路安全資訊分享中心。白宮先前於 2026-03-20 發布的《國家 AI 政策框架》主張聯邦優先於州法（preemption），但將兒童安全、AI 運算/資料中心基礎建設、州政府採購排除在外。截至 2026-06 底兩週內，美國各州及國會共通過 19 項新 AI 法案，顯示州層級監管仍持續增加、形成多層次合規環境。
- **資金**：2026 上半年全球創投資金創新高達 5100 億美元（超越 2025 全年 4400 億美元），Q2 單季超過 2000 億美元湧入新創。OpenAI 與 Anthropic 兩家合計拿下上半年新創募資的 43%（約 2170 億美元）。OpenAI 完成史上最大單筆私募輪 1220 億美元，投後估值達 8520 億美元。Microsoft 預計 2026 年資本支出約 1900 億美元（年增 61%）；JPMorgan 上修全球 AI 相關資本支出至 2030 年估計達 5.5 兆美元。約 88% 的 AI 新創資金流向美國公司，顯示地域集中度極高。
- **合作案**：Microsoft 與澳洲 Nine Entertainment 簽署首見的新聞媒體內容合作協議（供 Copilot 輸出引用其新聞內容）。California 州政府與 Anthropic 合作，推出州內部 AI 助理 Poppy，已在 67 個部門、2800+ 名員工試行，預計 2026-07 全州鋪開，號稱是美國史上最大規模的政府 AI 部署案（且價格為半價）。NAVER 與 NVIDIA 擴大合作，採用 NVIDIA DSX 平台建置主權 AI 基礎設施（起始 55MW，目標擴展至 GW 級），支援 HyperCLOVA X 模型與 2026 下半年在南韓推出的 AI Agent Platform。

## 使用建議

若之後要延續追蹤，建議下一輪蒐集聚焦：(1) GPT-5.6 是否如期於 7 月公開發布、(2) Gemini 3.5 完整版（非 Flash）動向、(3) Microsoft Copilot App 整合上線後的市場反應、(4) 美國各州 AI 法案是否被聯邦 preemption 排除的後續進展。
