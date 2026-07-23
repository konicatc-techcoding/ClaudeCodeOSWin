---
name: hermes-cron-model-pin-convention
description: Hermes 原生 cron 的 agent job 每次觸發跟隨全域 default 模型；換全域模型後未 pin 的 agent job 會 fail-closed（RuntimeError、不發 inference）。慣例：agent job 建立時一律 pin provider+model，換全域模型後盤點補 pin
metadata:
  type: reference
---

Hermes（NousResearch HermesAgent，Windows 側原始碼在 `%LOCALAPPDATA%\hermes\hermes-agent`，見 [[reference_hermes_workspace]]）原生 cron 的模型綁定行為，經原始碼查證後的機制與維運慣例。2026-07-22 因一次全域換模型導致晨報 job 停擺而確立。

## 機制（為什麼會發生）

- Hermes 原生 cron 的「unpinned agent job」設計上**每次觸發都跟隨當下的全域 default 模型**（`config.yaml` 的 `model.default`），不是鎖在建立當時的模型。
- job 的 `model_snapshot` **不是綁定**，而是一條 drift tripwire（絆索）：它只記錄「建立當下全域是什麼」，觸發時拿來比對「現在的全域跟當初是否不同」。
- 當「有 snapshot ＋ 未 pin ＋ 現值與 snapshot 不同」三條件同時成立，Hermes 花費保護（issue #44585）會 **fail-closed：raise RuntimeError、完全不發任何 inference 呼叫**。這是**硬行為，不可設定關閉、無法降級成 warn**——`cron/scheduler.py`（3206–3260）沒有任何 config 閘門可以繞過。
- 真正免疫全域模型變更的是「pin」（job 的 `provider`/`model` 兩個欄位皆非空），**不是 snapshot**。純 script job（`no_agent=True`，例如 garmin/aichain）不做 inference，不受此 guard 影響、不需要 pin。

## 慣例（以後怎麼避免）

1. 所有會呼叫模型（agent）的重要 cron job，**建立時一律帶 `provider` + `model` 兩軸一起 pin**：`cronjob action=create ... provider=... model=...`。只給 `model` 不給 `provider`，provider 軸仍算未 pin，一樣會 fail-closed。
2. pin 的代價：pinned job 不會自動採用未來的新模型，遷移時要主動 `cronjob action=update` 逐一更新。
3. 維護一份 pinned job 清單（job_id → provider/model），方便換模型時盤點。
4. **每次換全域模型（改 `config.yaml` 的 `model.default`，或被 auto-raise 改動）後，順手 `cronjob action=list` 盤點還有哪些 agent job 的 model/provider 是 null（＝下次觸發會 fail-closed），當場 pin 回舊值或 pin 到新值。**

## 這次事件（案例）

- 2026-07-21 使用者更換全域模型 `gpt-5.6-terra → gpt-5.6-sol`（`.codex_gpt55_autoraise_notice: gpt-5.6-sol:50:85`）。
- 晨報 job `daily-github-ai-models-applications`（id `9a65cc2347c8`，每天 09:00 發 GitHub AI 晨報到 Slack #ai-news）為 unpinned，隔天 09:00 起 fail-closed 報錯、未發 Slack。
- 2026-07-22 修復：pin 回 `gpt-5.6-terra`（理由：該 job 只做搜尋、不做分析，沿用舊模型即可）。

## 相關原始碼證據路徑（供日後查閱）

- drift guard：`%LOCALAPPDATA%\hermes\hermes-agent\cron\scheduler.py`（3206–3260）
- create/pin 參數與 snapshot 計算：`%LOCALAPPDATA%\hermes\hermes-agent\cron\jobs.py`（`create_job` 1039–1057、snapshot 936–1026）
- cronjob 工具 create 參數：`%LOCALAPPDATA%\hermes\hermes-agent\tools\cronjob_tools.py`（736–746）

## 關聯

- 這是「換全域模型」這個動作的一個維運副作用；與使用者在意的「依任務類型/範疇選模型 lane」需求（[[hermes-task-category-model-routing-preference]]）不同層次——那個講的是 CoS/domain 側的 lane 決策引擎，這個講的是 Hermes 原生 cron 的花費保護行為。
