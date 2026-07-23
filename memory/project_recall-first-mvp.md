---
name: project-recall-first-mvp
description: 2026-07-23 落地的 recall-first MVP 決策記錄——CoS 動手/分派前先主動檢索既有記憶與 skill（讀側），對應既有 consolidation 寫側。含決策程序步驟 1.5、MEMORY.md 改 recall-友善索引、taxonomy §7 讀側對應、consolidation skill_promotion.min_recall_reuse=3；檢索層 MVP 只做 markdown+索引，keyword top-k 腳本與 FTS5 押後、embeddings 不做
metadata:
  type: project
---

來源：2026-07-23 互動式前台 CoS session（草稿由 `planning` 定案、使用者拍板）。本則記錄 **recall-first MVP** 的落地決策——recall-first 本身受益於自己的決策被記錄下來，之後遇到「要不要做 recall 的第 N 版／要不要上腳本」時可直接引用本則。

## 決策內容（2026-07-23 落地）

recall-first 是既有 consolidation 寫側的**讀側對應**：CoS 收到任務、動手或分派之前，先主動檢索既有記憶與 skill，有相似就複用、沒有才從頭來。兩者共用同一批 artifact，**不新增任何寫入路徑**（recall 只讀）。本次落地的四項具體改動：

1. **決策程序加步驟 1.5**（`delegation_policy.md` +「決策程序」步驟 1.5；機器可讀參數在 `registry/delegation_policy.yaml` 的 `recall` 區塊）：強制在動手/分派前講出 `Recall:` 一行（檢索了什麼、命中什麼、複用或從頭），並帶相關性防呆（避免硬套不相關的記憶）。可稽核。
2. **MEMORY.md 改 recall-友善索引**：每條索引補「關鍵字 + 一句『這條回答什麼問題／什麼時候該想到它』」，讓 recall 一眼能判斷該不該點進去。
3. **taxonomy §7 讀側對應**（`docs/memory-taxonomy.md` §7、§7.1）：明文寫下 recall-first 是寫側的鏡像、檢索分層排序（Procedural > Semantic > Episodic，MVP 只查前兩層）、以及 recall 複用達 N 次升級成 skill 的判準。
4. **consolidation `skill_promotion.min_recall_reuse: 3`**（`registry/consolidation_policy.yaml`）：某解法/程序被 recall 召回並複用達 3 次，`knowledge` 在 consolidation 回報中列為 SKILL.md 候補（升級不自動發生，人在迴路，skill 實作分派 `engineering`）。

## 檢索層範疇（MVP 邊界，重要）

- **MVP 只做 (a)**：直接讀 markdown 正本（`memory/*.md`、Procedural artifact）+ `MEMORY.md` recall-友善索引，靠人/CoS 讀索引判斷相關性。無腳本、無自動計數器。
- **押後（非 MVP 範圍）**：keyword top-k 檢索腳本＝Phase 2；SQLite FTS5 全文檢索＝Phase 3。
- **明確不做**：embeddings / 向量檢索——不在本路線圖內。
- 複用次數在 MVP 階段由 `knowledge` 在 consolidation 時人工盤點（「這解法最近被當答案端出幾次」），未來若上 recall 腳本再談自動計數。

## How to apply

- 有人再提「recall 要不要上腳本 / FTS5 / embeddings」時，先引用本則：MVP 只做 markdown+索引，keyword top-k 是 Phase 2、FTS5 是 Phase 3、embeddings 不做——除非拿得出「純讀 markdown+索引真的不夠用」的具體缺口。
- consolidation 時：`consolidate-memory` 的「索引同步」步驟要維護 recall-友善格式（每條帶關鍵字 + 一句「回答什麼問題」）；並人工盤點有無解法被複用達 `min_recall_reuse`（3）次，達標就列 SKILL.md 候補回報 CoS。
- 決策程序上：動手/分派前照步驟 1.5 講出 `Recall:` 一行，別跳過。

## 相關記憶

- [[project_v0_1_status.md]] — v0.1 領域狀態；recall-first MVP 是 v0.1 期間對記憶讀側的補強。
- taxonomy §7／§7.1（文件，非 memory）：`docs/memory-taxonomy.md` — recall-first 讀側對應與 skill 升級判準的完整理由。
