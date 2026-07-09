# Stage 1 Checkpoint — Pre-Bridge Foundation（完成宣告）

日期：2026-07-09　狀態：**checkpoint 定稿**
彙整：planning domain；驗證證據來源：engineering 同日全面掃描
對應 roadmap：[hermes-integration-roadmap.md](hermes-integration-roadmap.md) Stage 1

---

## 1. Checkpoint 宣告與範圍說明（誠實記錄）

**Stage 1 於 2026-07-09 標記完成，定名「Pre-Bridge Foundation」。**

範圍與原規劃不同，必須明說：roadmap 原本的 Stage 1 是「Knowledge 手動匯入路徑」，
三項核心 DoD 中——

- **DoD 3（session 匯入政策）已定稿**：[memory-taxonomy.md](memory-taxonomy.md) +
  [`registry/consolidation_policy.yaml`](../registry/consolidation_policy.yaml)。
- **DoD 1／2（用一個真實 Hermes session 實走 `to-inbox` → `consolidate-memory` → 正本，
  含 idempotent 驗證）未執行**——政策已定稿但流程沒有實走過，**不標成完成**，
  改列為 **Stage 2 開工前的必要前置（gate）**，見第 5 節與 roadmap Stage 2 更新。

實際完成的是「bridge 開工前的全部地基」：adapter、記憶政策、schema 定義、部署同步、
平台修復與環境事實澄清。這個 checkpoint 宣告的就是這組地基，故定名 Pre-Bridge Foundation。

---

## 2. 交付清單（時序）

| # | 交付 | 產出物 |
|---|------|--------|
| 1 | **HermesSessionAdapter**（read-only importer，20 tests） | `hermes/session_adapter/`；[README](../hermes/session_adapter/README.md) |
| 2 | **Stage 0.5 部分項**：default gateway 排程修復；profile 診斷（結論：不刪 profile 的非破壞建議）；gateway 未啟動根因查明（非效能問題） | 排查結論已入 [Stage 0 報告](hermes-shared-storage-bootstrap.md)與本 checkpoint；殘項見第 5 節 |
| 3 | **Dashboard Jobs 修復**（jobs.db 缺檔容錯）＋確立架構事實：**job pipeline 原生 Windows 跑不動**（worker 依賴 POSIX script + systemd，現行部署在 WSL 側——設計現況，非缺陷） | `dashboard/`；環境事實記錄於本 checkpoint 第 5 節與 roadmap Stage 2 |
| 4 | **venv 整併**（單一 Windows `.venv`）＋ mac→Windows 路徑全案轉換 | repo 全域；WSL 側另有 Linux 原生 `.venv`（不同步，見 sync plan） |
| 5 | **Memory taxonomy 三層定義 + N-gate consolidation 政策**；`daily-memory-check` prompt 已接上 N-gate | [memory-taxonomy.md](memory-taxonomy.md)、`registry/consolidation_policy.yaml`、`hermes/config/cron_jobs.yaml` |
| 6 | **Stage 1 收斂**：部署敘述修正（launchd 降級 legacy，目標環境 Windows/WSL2）；Capability Lanes schema；Bridge State schema（`claudecodeos.bridge_state.v1`，格式定稿、實作待 Stage 2） | [capability-lanes.md](capability-lanes.md)、`registry/capability_lanes.yaml`、[memory-bridge-state.md](memory-bridge-state.md)、`registry/bridge_state_schema.yaml` |
| 7 | **Stage 1.4：部署同步方案 + 首次同步完成**；Telegram bot 邊界定案（`telegram.json` 不同步；ClaudeCodeOS bot＝控制入口、Hermes profile bots＝對話入口，本 repo 不管理） | [deployment-sync-plan.md](deployment-sync-plan.md)、`scripts/sync_to_wsl.sh`；同步前備份見第 6 節 |

---

## 3. 驗證證據（2026-07-09 engineering 掃描）

### 3.1 測試：**123 tests／0 失敗／1 skipped**

| 套件 | 數量 | | 套件 | 數量 |
|---|---|---|---|---|
| db | 20 | | route_model | 12 |
| cron | 5 | | capability_lanes | 12 |
| rss | 9 | | bridge_state | 7 |
| telegram | 12 | | dashboard_data | 20 |
| session_adapter | 20 | | dashboard_app | 6（1 skip） |

### 3.2 設定與部署一致性

| 檢查 | 結果 |
|---|---|
| registry yaml 可解析 | **6/6**（agents／model_router／delegation_policy／consolidation_policy／capability_lanes／bridge_state_schema） |
| `cron.load_jobs()` | 正常 |
| WSL 部署側 systemd units | **5/5 active** |
| WSL 側 `cron_jobs.yaml` | 已含 N-gate prompt |
| WSL 側三個新 registry yaml | 已同步存在 |
| 同步一致性 | **0 檔案差異**（僅 3 筆目錄 timestamp 殘差，無內容意義） |

---

## 4. 基準數據（Stage 2 對照用）

- **state.db**：**62 sessions／3,156 messages**（`adapter --snapshot` 讀取）。
  來源分佈：tui 46／cli 9／telegram 5／cron 2。
- **觀察註記（待 Stage 2 確認，不影響 checkpoint 判定）**：message 總數（3,156）**低於**
  Stage 0 基準（2026-07-07：59 sessions／3,753 messages），session 數則增加。最可能解釋是
  Hermes 端的 compaction／清理（schema 本有 `compacted` 欄位），但未實證。Stage 2 bridge
  以 `event_id`（session + message rowid）去重，訊息數波動不影響正確性；仍建議 Stage 2
  開工時複查一次，確認不是讀錯 profile db（sticky profile 風險，見 Stage 0 報告）。

---

## 5. 未完項與去向（如實記錄，均不計入本 checkpoint 完成範圍）

| 未完項 | 去向 |
|---|---|
| **原 Stage 1 DoD 1／2**：真實 session 實走 `to-inbox` → `consolidate-memory` → 正本＋idempotent 驗證（政策已定稿、流程未實走） | **Stage 2 開工前的必要前置（gate）**——roadmap Stage 2 依賴節已同步更新 |
| Stage 0.5 殘項：financialresearch gateway 自啟修復 | Stage 0.5 清單保留，建議 Stage 2 前清完（engineering） |
| Stage 0.5 殘項：codereviewer profile 去留 | 同上（使用者決定） |
| Stage 0.5 殘項：三個殭屍 Startup vbs 清理 | 同上（automation） |
| Stage 0.5 殘項：sticky profile 緩解習慣（自動化一律 `--profile default`） | 同上；亦已列 roadmap 風險表 |
| Stage 2 前置決策：bridge 跑哪一側（Windows / WSL） | Stage 2 設計問題 1——「job pipeline 原生 Windows 跑不動」這條事實直接影響此決策（jobs.db 與 worker 都在 WSL 側） |
| Stage 2 前置決策：bridge state 載體（schema 已定稿，存哪裡未定） | Stage 2 設計問題 2 |
| Stage 2 前置決策：`model_router.yaml` TODO 佔位值確認 | Stage 2 開工前確認（engineering） |
| 已知環境事實：job pipeline 原生 Windows 跑不動（worker 靠 POSIX script + systemd，現行部署 WSL 側） | **設計現況、非缺陷**——記錄於此與 roadmap Stage 2，作為 bridge 側別決策的輸入 |

---

## 6. Rollback 索引（各交付物的 rollback 散在各自文件，此處只做索引）

| 交付物 | Rollback 方式 | 出處 |
|---|---|---|
| Stage 0 symlink 共用儲存 | `scripts/hermes_stage0_bootstrap.sh --rollback`（已實測 round-trip）；db 內容還原用 `backup_stage0/state.db.stage0.bak`（sha256 見報告） | [hermes-shared-storage-bootstrap.md](hermes-shared-storage-bootstrap.md) Rollback 節 |
| 首次 WSL 同步（Stage 1.4） | WSL 側：解開 apply 前自動備份 **`/home/razer/backups/ClaudeCodeOSWin-wsl-pre-sync-20260709T182843.tar.gz`** → daemon-reload → 重啟服務。Windows 側：同步唯一寫入是 inbox 新增檔，刪除即還原 | [deployment-sync-plan.md](deployment-sync-plan.md) §8 |
| HermesSessionAdapter | 純新增模組（`hermes/session_adapter/`），刪除目錄即還原；來源資料經 sha256 前後比對證明零寫入 | [adapter README](../hermes/session_adapter/README.md) |
| 三個新 registry yaml ＋ 三份 docs（taxonomy／lanes／bridge state） | 純新增檔案，刪除即還原；均不被 `route_model.py` 等既有執行邏輯讀取（lanes 明確聲明執行邏輯零變更） | 各文件開頭聲明 |
| `daily-memory-check` N-gate prompt | `hermes/config/cron_jobs.yaml` 的修改；還原 = 改回舊版一行 prompt（舊版仍存在於 WSL pre-sync tarball 內） | [deployment-sync-plan.md](deployment-sync-plan.md) §1.2 |
| Dashboard Jobs 修復／venv 整併／路徑轉換 | **無獨立 rollback 載體**——repo 尚未 git 版控（sync plan §1.1），程式層回退目前只能靠 WSL pre-sync tarball（僅涵蓋部署側） | 風險註記見下 |

**風險註記**：Windows 開發正本沒有版本控制，是目前 rollback 能力的最大缺口。
`git init`（Windows 側）已列為 sync plan 的 v0.2 升級路徑，建議在 Stage 2 動工前完成——
bridge 是會長期演化的元件，不該在無版控狀態下開發。

---

## 7. Checkpoint 之後

下一步依 [hermes-integration-roadmap.md](hermes-integration-roadmap.md)（2026-07-09 更新版）：
先完成 Stage 2 的必要前置（原 Stage 1 DoD 1／2 實走 + 前置決策三項 + Stage 0.5 殘項），
再開工 Stage 2 session bridge。
