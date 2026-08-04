# /hermes-upgrade — HermesAgent 受控升級程序

```yaml
name: hermes-upgrade
description: >-
  把 hermes-agent(Windows/WSL 兩側)升級到官方新版的完整受控程序。
  當使用者說「升級 hermes」「更新 HermesAgent」「吸收官方新版」或在
  「Hermes 更新」頁看到官方落後數想升級時使用。
  只在互動式(前台)session 執行;headless 背景任務不得使用本 skill。
```

## 前提認知(執行前必讀)

- **repo 永久 diverged**:本機 main 帶客製 commit(Slack 硬化、multiplexer 等),
  升級=受控 merge,**永遠不存在一鍵更新**。絕不碰 `hermes update`、絕不碰
  Hermes Desktop 的 Install 鈕、程序中絕不出現 `git reset`(07-24 事故教訓)。
- 權威參考:auto-memory `hermes-agent-repo-work`(慣例+踩坑清單)、最近一次
  merge 計畫文件(現為 [docs/hermes-0191-merge-plan.md](../../../docs/hermes-0191-merge-plan.md),
  含 runbook/rollback/測試洩漏 `--ignore` 清單)。
- 順序鐵律:**Windows 先受控 merge 並 push 私有備份,WSL 才 ff 跟上**,不可反。

## 程序(五步,兩個核准 gate)

### 步驟 0:看現況
按「Hermes 更新」頁的〔重新整理遠端資訊(fetch)〕鈕(或等使用者按),
確認官方落後數與目標版本點(tag)。使用者指定版本就鎖那個 tag,
沒指定則與使用者確認要不要直上最新 release tag。

### 步驟 1:分派 engineering 隔離 worktree merge(不碰 live)
依 delegation policy 分派 `engineering`,prompt 要點:
- repo `%LOCALAPPDATA%\hermes\hermes-agent`,隔離 worktree 開
  `integration/v<版本>-custom`(基於 live main tip),merge 目標 tag。
- 解衝突原則「客製與上游都要活」;已知熱區與既往取捨見 memory 與上次計畫文件。
- 沙箱全套測試(`HERMES_HOME` 沙箱化;**先 `--ignore` 計畫文件 §11 列的三組
  洩漏測試**:dashboard unified launch / update-flow / pty)。
- 產出 `docs/hermes-<版本>-merge-plan.md`(仿現有格式)+ **同步更新
  `scripts/hermes_extra_pins.txt`**(live venv 額外承載、extras 不涵蓋的 pin)。
- 絕不碰 live main、不重啟 gateway、不 push;worktree 留存。

### 【Gate 1】使用者核准 merge 結果
整合 tip hash、衝突解法、測試結果、拍板事項——使用者點頭才進下一步。

### 步驟 2:Windows live 切換(一條指令)
先向使用者明示停機窗口(gateway 停到狀態檔更新,預估 <10 分鐘,其中
~3.5 分鐘是 gateway 慢啟動)並取得核准(【Gate 2】),然後執行:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\hermes_apply_upgrade.ps1 -Tip <整合tip>
```

script 冪等、fail-loud:前置自檢(乾淨樹+ff 保證)→ rescue tag → 停 gateway
→ ff → pip+pins → web build(package-lock 噪音自動 restore)→ 重啟+S7 全套
驗證(含 Slack 負面/dedup)→ push main+tag。失敗會停在原地印 rollback 指令,
修好後**直接重跑同一指令**。首次可先 `-DryRun` 看八步計畫。

### 步驟 3:WSL 跟上(一條指令)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\hermes_sync_wsl.ps1
```

非 ff 會自己停(那代表兩側漂移,回頭查,不硬推)。

### 步驟 4:對帳與收尾
- 開「Hermes 更新」頁:WSL follow 組**綠**、兩側 live 版本字串一致、
  backup 組綠(已 push)。Windows 對官方 orange 是升級後預期常態。
- 留意下一輪 cron 排程送達正常。
- 更新 auto-memory `hermes-agent-repo-work`(新 tip、rescue tag、新踩坑);
  STATUS.md 留給 /wrapup。

## 邊界

- 兩個 Gate 不可省:merge 結果核准、live 停機核准——live gateway 是使用者的
  生產服務。
- script 測試回歸在 `scripts/tests/test_upgrade_scripts.ps1`(沙箱 repo,
  不碰真實環境),改動兩支 script 後必須重跑。
- 本 skill 不處理「要不要升級」的決策,只處理「怎麼安全地升級」。
