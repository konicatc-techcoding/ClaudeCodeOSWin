# Delegation Policy — v0.1

這份文件定義 Chief of Staff（CoS）怎麼決定一個任務「誰做」。目的是把分派邏輯做成明確規則，而不是讓模型當下自由心證——尤其是「這任務很小，我自己做比較快」這種判斷，過去測試中已經證實會被模型的合理直覺蓋過去（見下方「為什麼需要這份政策」）。

規則的機器可讀版本在 [`registry/delegation_policy.yaml`](registry/delegation_policy.yaml)，這份文件是給人看的說明跟決策程序。CoS、未來的 Hermes 前置分類、未來的 Job Queue 都應該查同一份 yaml，不要各自維護一套判斷邏輯。

## 為什麼需要這份政策

v0.1 測試時發現：CoS 收到「檢查一個檔案的語法」這種小任務時，即使 `CLAUDE.md` 明確寫著「不自己動手做 domain 的專業工作」，還是直接用 Bash/Read 自己做掉了，完全沒有透過 `Agent` 工具分派給 `engineering`。原因不是指令不清楚，而是模型會用自己的判斷力覆蓋規則——「這麼小的事，走 subagent 太浪費」是完全合理的直覺，但這正是這套系統要避免依賴的東西：分派與否應該是可預期、可稽核的，不該取決於當下模型覺得任務大不大。

同一輪測試也驗證了另一半：遇到 `planning`（狀態 `planned`，還沒有 subagent）的任務時，CoS 正確地拒絕自己代打、如實告知使用者。這份政策就是把那個「誠實」的行為模式，擴大套用到所有 `active` 領域的任務上——不管任務看起來多小。

## 決策程序

CoS 收到任何任務時，依序執行：

1. **分類（Classify）**：對照 `registry/delegation_policy.yaml` 裡的 `direct_categories` 跟 `delegated_categories`，判斷這個任務屬於哪一類。
1.5. **Recall（動手／分派前先查記憶與 skill）**：任務經第 1 步判定為需要實際執行的任務（即不是 `general_conversation`、`clarification` 這種一開始就由 CoS 直接回覆的類別）後，**在進入第 2 步分派、或 CoS 自己動手之前**，一律先做一次明確檢索，且**必須把結果講出來**，格式固定為一行：

    `Recall: 命中 skill <名稱> ｜ 命中 memory <條目/檔名> ｜ 查無相似`

    （三選一；同時命中 skill 與 memory 就都列出。）檢索範圍是 (a) `.claude/skills/**/SKILL.md` 有沒有可直接執行的程序（Procedural）、(b) `memory/MEMORY.md` 索引與相關 `memory/*.md` 有沒有可複用的既有決策或事實（Semantic）。三種結果的處理：

    - **命中 skill** → 走該 skill。若該 skill 屬於某領域的專業工作，仍照第 3 步分派，不因為「已經找到現成程序」就自己代打。
    - **命中既有決策／事實** → 把它當上下文帶進第 3 步分派（一併交給領域 subagent），或在確認相符後直接複用既有結論、不從頭重新規劃。
    - **查無相似** → 明確記「查無相似」，才進入從頭規劃／分派。

    **相關性確認（防呆，強制）**：命中不等於照抄。CoS 要先確認召回的舊解確實對得上當前任務，並在複用時註明是「直接複用」還是「以舊解為基礎調整」；若召回內容看似相關但脈絡可能已過期（尤其規劃類），標為「相似但需確認」，不得拿舊結論硬套當前任務。

    **為什麼要強制且可稽核**：跟這份政策存在的理由同源（見「為什麼需要這份政策」）——模型會用「這任務我直接從頭想比較快」的合理直覺跳過查記憶。把 recall 做成必講的一行，讓「有沒有查、查到什麼」變成可預期、可稽核，而不是取決於當下模型想不想查。

    **recall 統計落地（2026-07-30 起，memory-lifecycle 提案 B1）**：講出 recall 結果那一行之後，執行 `scripts/log_recall.py` 把這次結果 append 進 `logs/recall_log.jsonl`（`--entry interactive|headless --result hit_skill|hit_memory|miss [--hit-ids ...] [--task-hint ...]`；同時命中 skill 與 memory 時 result 擇主要者、hit-ids 全列）。此統計餵給 retention review（升格/汰選依據，見 `registry/consolidation_policy.yaml` 的 `retention:` 區塊）。log 是 best-effort：script 失敗不阻斷任務，繼續往下走即可，但不得因嫌麻煩而略過呼叫。

    **與 `orientation_read`／`depends_on` 的關係**：輕量 recall（掃 MEMORY.md 索引 + skill 清單）屬於 `orientation_read` 既有範圍——只用來決定「複用什麼、帶什麼上下文去分派」，一樣受 orientation_read 邊界約束：不能拿召回內容直接把領域的實質工作做掉。若命中的內容需要 `knowledge` 去整併／解讀完整記憶、或要在既有規劃上疊加，就照下方「領域間依賴」先分派 `knowledge` 取上下文——recall-first 本質上就是把「先找 knowledge 補脈絡」推廣到所有任務的前置動作。
2. **查 Owner**：
   - 屬於 `direct_categories` → CoS 自己處理。
   - 屬於 `delegated_categories` → owner 是對應的 domain。
3. **分派（若 owner 不是 CoS）**：
   - 若 owner 在 `registry/agents.yaml` 的狀態是 `active`：一律用 `Agent` 工具分派，**不管任務看起來多小**。任務規模不是判斷依據。
   - 若 owner 的狀態是 `planned`：誠實告知使用者這個領域還沒有 subagent，不自己代打、不假裝分派成功。
4. **整合**：分派出去的任務，結果回來後由 CoS 整合、回覆。

### 領域間依賴（`depends_on`）

`registry/agents.yaml` 裡某些領域標了 `depends_on`（目前是 `planning` 依賴 `knowledge`）。分派前若任務明顯需要既有脈絡（歷史決策、既有規劃、過去累積的記憶），先分派給依賴領域取得上下文，再把上下文一併交給目標領域處理，最後才整合回覆。不是每次都要兩階段——只有任務明顯依賴既有記憶時才需要；如果目標領域自己判斷「上下文不足」，CoS 應該回頭去補，而不是讓它憑空生成結論。

## CoS 可以自己處理的範圍（`direct_categories`）

只有這五種，其餘一律走分類程序：

- **meta_system_question**：關於這個系統本身的問題（有哪些領域可用、狀態如何、架構是什麼）
- **clarification**：向使用者釐清需求，還沒進入實際執行
- **synthesis**：整合／摘要已經由 subagent 回傳的結果
- **orientation_read**：為了「分類」而讀取 `registry/` 或 `memory/` 的中繼資料——**這個讀取只能用來決定要分派給誰，不能拿讀到的內容直接完成使用者要的實質工作**。這條是專門用來堵住「先讀了檔案、發現很簡單、就順手做完」這條路徑。
- **general_conversation**：跟系統五個領域完全無關的一般對話（閒聊、建議、非任務性問題），CoS 可以用一般助理身份直接回覆。v0.1 integration test（見 [INTEGRATION_TEST.md](INTEGRATION_TEST.md) 測試 7）發現 CoS 遇到這種情況時本來就會直接回答，但政策沒寫清楚——這條是把已經發生的行為明文化，不是新行為。若對話後續轉成明確的領域任務，要重新分類。

## 沒有涵蓋到的任務

如果一個任務分類不出來、又明顯不屬於上面四種直接處理的範圍，不要用「感覺很小」當理由自己動手。應該：

- 向使用者釐清任務性質，或
- 挑 `delegated_categories` 裡最接近的分類分派，並在回覆中註明「這是最接近的分類，如果不準確請告訴我」。

## 給未來元件的說明

這份 policy 刻意設計成 CoS、Hermes、Job Queue 可以共用同一套判斷：

- **CoS**（本版本已生效）：每次任務都走上面的決策程序。
- **Hermes**（規劃中）：目前 Hermes 收到觸發事件後統一交給 CoS 決策，不自己判斷。如果之後想在 Hermes 端做輕量前置分類（例如先幫任務貼標籤，加速 CoS 判斷），應該查同一份 `registry/delegation_policy.yaml`，不要另外維護一套規則。
- **Job Queue**（規劃中）：job 的 owner 欄位可以直接沿用這份分類結果，方便之後做路由、優先順序、或監控「哪個領域的 job 最多」。
- **Model Router**（`registry/model_router.yaml`）：這是不同的軸——delegation policy 決定「哪個領域負責」，model router 決定「該領域內部呼叫哪個模型」。兩者不互相取代：一個任務先被分派給 `engineering`，`engineering` subagent 內部才會視情況呼叫 `scripts/route_model.py <capability>`，或（任務明顯不適合預設能力時）改用 `scripts/dispatch_domain.py` 選路到 Hermes lane（目前 `engineering` 的 `default_capability` 是 `claude_native`；2026-07-20 起 OpenRouter provider 相關路徑已全部移除，見 ARCHITECTURE.md 第 5／5.1 節）。
