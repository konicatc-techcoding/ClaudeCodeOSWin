# WSL 側 hermes-agent 受控 re-graft 執行計畫（v1）

日期：2026-07-25　狀態：**✅ 已執行完成（2026-07-25，方案 A，Phase 0–7 全過）**
調查執行：`engineering` domain（**純唯讀**，未做任何寫入操作）
執行者：主 session（真實基礎設施操作依既有工作慣例不下放給 subagent）

> **執行結果摘要**：實際耗時約 12 分鐘，**Telegram bot 停機 0 分鐘**；WSL 由
> `c12c64f9e` 對齊到 `970118870`，`git diff --stat origin/main` 零輸出（兩側樹
> 逐 byte 相同）。過程中發現計畫**兩處與實際行為不符**並已就地修正（§4 Phase 5
> 的 `branch.main.remote`、`hermes config set` 的型別問題）——完整紀錄見
> **[§10 執行紀錄](#10-執行紀錄2026-07-25)**。以下 §1–§9 保留為執行前的原始
> 計畫內容（事實表為 re-graft **前**的狀態），僅在 §4 Phase 5 就地加註更正。

**目的**：把 WSL 側 `/home/razer/.hermes/hermes-agent`（停在 2026-07-17 的舊客製
merge `c12c64f9e`，落後官方 160）收拾乾淨，對齊到 Windows 側已驗證的整合 tip
`970118870`，使**兩側行為一致**，並補上 WSL 側缺席的防重演措施。

依賴文件：

- [webui-update-button-proposal.md](webui-update-button-proposal.md) §2（人工受控
  流程十步）、§4（兩端 target 表）、§6（防重演）
- `dashboard/data_update.py`（階段一唯讀預檢；本計畫的觸發來源）
- `memory/hermes-agent-repo-work.md`（2026-07-24 事故與受控修復紀錄、受控升級慣例）
- `memory/hermes-gateway-init-slow.md`（gateway 狀態檔約 3.5 分鐘才寫）

---

## 0. 調查方法與唯讀聲明

本計畫的所有事實來自 2026-07-25 的唯讀查詢：`wsl -d Ubuntu --exec bash -lc "…"`
下的 `git rev-parse / log / status --porcelain / stash list / branch -vv /
for-each-ref / remote -v / rev-list --count / merge-base --is-ancestor /
cat-file -t / check-ignore / clean -nd（dry-run）`、`systemctl --user list-units /
list-timers / is-enabled`、`ps`、`ls / stat / grep`。

**未執行**任何 `fetch / pull / merge / reset / checkout / stash / clean（非
dry-run）/ pip / systemctl start|stop`，未改動任何檔案、ref、index 或工作樹。

**唯一需揭露的例外**：在 **Windows** repo 上跑過一次
`git merge-tree --write-tree --name-only c12c64f9e 970118870`（衝突模擬）。
該指令會在 Windows repo 的 object store 產生**不可達的 loose object**，
**不改任何 ref／branch／index／工作樹**，會被日後 gc 自動回收。WSL 側零寫入。

---

## 1. 現況盤點（事實表，附實際指令輸出）

### 1.1 兩側 git 座標

| 項目 | Windows（live） | WSL |
|---|---|---|
| repo | `C:\Users\razer\AppData\Local\hermes\hermes-agent` | `/home/razer/.hermes/hermes-agent` |
| HEAD | `970118870167091e07730543f897cd2b363c9dd1` | `c12c64f9e94da03190039db0352f3e190edb61c4` |
| branch | `main` | `main`（`branch.main.remote=origin`） |
| `git describe --tags --always` | `rescue/pre-remerge-20260724-14-g970118870` | `pre-upstream-merge-20260717-653-gc12c64f9e9` |
| 工作樹 | **乾淨**（`## main...origin/main`，無其他行） | **髒**：`?? .install_method` |
| stash | —（未查，非目標） | **空**（`git stash list` 無輸出） |
| 本地 branch | `main` / `integration/v0.19.0-custom` / `rescue/pre-updater-merge-20260724` / 2 個 feat/* | **只有 `main`** |
| remotes | `origin` = 私有備份 `konicatc-techcoding/hermes-agent-private`；`upstream` = 官方 NousResearch | `origin` = **官方 NousResearch**；`windows-side` = `/mnt/c/Users/razer/AppData/Local/hermes/hermes-agent` |
| vs 官方 | 領先 12 / 落後 16（`upstream/main` = `46c7a4076`, 2026-07-23） | 領先 11 / 落後 160（快取的 `origin/main` = `c48d53413a`, 2026-07-18） |
| live 版本字串 | v0.19.0（整合 tip `970118870`） | `Hermes Agent v0.18.2 (2026.7.7.2) · upstream c48d5341 · local c12c64f9 (+11 carried commits)` |

WSL `branch -vv` 原始輸出：

```
* main c12c64f9e9 [origin/main: ahead 11, behind 160] Merge origin/main into main (multiplexer lifecycle + Slack hardening preserved)
```

WSL remote 的 refs 最後更新時間為 **2026-07-18 14:54–14:56**（`.git/FETCH_HEAD`
mtime `2026-07-18 14:56:59`），亦即 **`windows-side/main` 這個 ref 是過期的，
仍指向 `c12c64f9e9`**——不是事故後的 `970118870`。

> **關鍵推論**：今天若直接跑 2026-07-18 那道
> `git merge --ff-only windows-side/main`，它會是 **no-op**（ref 沒動過），
> 不會有任何效果。必須先 `git fetch windows-side` 才看得到 `970118870`。

### 1.2 WSL「領先 11 個 commit」到底是什麼

`git log --oneline origin/main..HEAD`（WSL）：

```
c12c64f9e9 Merge origin/main into main (multiplexer lifecycle + Slack hardening preserved)
03bb983e31 Add outbound-only Slack send path (delivery hardening Phase 3.5)
e03e6d94ba Wire Slack idempotency into CLI and cron delivery (delivery hardening Phase 3)
7554806259 Add per-profile Slack outbound channel allowlist (delivery hardening Phase 2)
6dddf1f675 Add Slack send ledger + retry policy (delivery hardening Phase 1)
57206d3546 Align docs and CLI help with multiplexer gateway lifecycle ownership
cd39955c4e Add hermes gateway doctor/cleanup for per-profile gateway artifacts
514e5b485b Surface multiplexer ownership in the dashboard frontend
698de0ed9d Own channel-apply restarts under the multiplexer (409, coalescer, conflict status)
af9e6c9ad5 Make compute_effective_enabled the single source of truth for platform enablement
f8de8bee2f Add static multiplexer ownership guard for named-profile gateway lifecycle
```

**這 11 個不是 WSL 獨有的東西**——它們就是 2026-07-18 從 Windows 側
fast-forward 過去的同一批客製 commit（multiplexer ownership ×6 ＋ Slack
delivery hardening ×4 ＋ 1 個舊 merge）。決定性證據（在 Windows repo 上算）：

```
$ git rev-list --count HEAD..c12c64f9e94da03190039db0352f3e190edb61c4
1
$ git log --oneline HEAD..c12c64f9e94da03190039db0352f3e190edb61c4
c12c64f9e Merge origin/main into main (multiplexer lifecycle + Slack hardening preserved)

$ git merge-base --is-ancestor 03bb983e3 970118870…   # 客製 tip
rc=0            ← 是祖先
$ git merge-base --is-ancestor c48d53413a 970118870…  # WSL 快取的上游 tip
rc=0            ← 是祖先
$ git merge-base --is-ancestor c12c64f9e 970118870…
rc=1            ← 不是祖先（＝不能 ff-only，任務前提確認）
```

也就是說：**WSL 的 1597↔1 不對稱關係中，「WSL 有而 Windows 沒有」的 commit
只有 1 個，就是舊 merge commit `c12c64f9e` 本身**；它的兩個 parent
（客製 tip `03bb983e3`、上游 `7cb2d2cd4`）都已在 `970118870` 的祖先集合內。

再確認 WSL 沒有藏在其他 ref 底下的獨有 commit：

```
$ git rev-list --count --all --not main origin/main    # WSL
0
```

＝ **WSL 所有 ref（含 tag）可達的 commit，全部落在 `main` 或 `origin/main` 的
歷史裡，零獨有。** WSL 獨有的 tag `pre-1b-upgrade-20260718` → `05cbddc012`，
Windows 側 `git cat-file -t 05cbddc012` = `commit`（物件存在），亦非獨有內容。

### 1.3 工作樹髒的確切內容

```
$ git status --porcelain            # WSL
?? .install_method
$ git clean -nd                     # dry-run
Would remove .install_method
$ stat -c '%s bytes mtime=%y' .install_method
4 bytes  mtime=2026-07-07 14:24:43
$ cat .install_method
git
```

**結論：唯一的髒點是 Hermes bootstrap installer 自己產生的 4-byte runtime
artifact，內容 `git`，不是使用者工作。** 而且：

```
$ git check-ignore -v .install_method           # WSL（c12c64f9e 的 .gitignore）
（無輸出，rc=1）  ← 未被忽略，所以顯示為 ??
$ git show 970118870…:.gitignore | grep -n install_method
156:/.install_method                            ← Windows tip 已把它加入忽略
```

→ **對齊到 `970118870` 之後，這個檔案會自動變成 gitignored，髒的狀態不藥而癒。**
`git reset --hard` 不刪未追蹤檔，所以檔案本身也會原樣保留（installer 需要它）。
**本計畫全程不跑 `git clean`。**

### 1.4 WSL 執行環境

| 項目 | 事實 |
|---|---|
| venv | `/home/razer/.hermes/hermes-agent/venv`（**在 repo 內**），uv 0.11.27 建立，CPython 3.11.15 |
| 安裝形式 | **editable**：`site-packages/__editable__.hermes_agent-0.18.2.pth` ＋ `hermes_agent-0.18.2.dist-info` |
| venv 是否會被 reset 波及 | **不會**：`git check-ignore -v venv` → `.gitignore:2:/venv/`，兩側 tip 皆忽略（`970118870:.gitignore` 有 `/venv/`、`/venv.old/`、`.venv/`） |
| venv 內有 pip | **有**（`venv/bin/pip`、`pip3`、`pip3.11`），可直接 `./venv/bin/python -m pip install -e …` |
| CLI 進入點 | `/home/razer/.local/bin/hermes` → `exec "/home/razer/.hermes/hermes-agent/venv/bin/hermes" "$@"`（unset PYTHONPATH/PYTHONHOME） |
| `HERMES_HOME` | `/home/razer/.hermes` |
| `config.yaml` | **獨立實體檔**（`-rw------- 7041 bytes`，非 symlink），有 3 份 `.bak` |
| `.env` | **獨立實體檔**（`-rw------- 23467 bytes`，非 symlink），有 1 份 `.bak` |
| `state.db` | **仍是 symlink** → `/mnt/c/Users/razer/AppData/Local/hermes/state.db`（追蹤後大小 48 bytes、mtime 2026-07-07，實質未使用） |
| 其他 symlink | `memories` / `sessions` / `skills` → `/mnt/c/.../hermes/*` |
| WSL 本地 DB | `slack_send_ledger.db`（12288 bytes，mtime 2026-07-20，**WSL 獨有實體檔，不在 repo 內**） |
| 磁碟 | `/` 948G 可用；`.git` 634M、`venv` 332M |

WSL `config.yaml` 的 Slack 客製設定確實在生效狀態：

```
119:  outbound_allowed_channels:
120-    - C0BHE9NFW0P
121-    - C0BGX4AKPNV
122-    - C0BJUTB056C
123-    - C0BJV0HQJ8Y
124-    - C0BJ0HRM87L
125-    - C0BHG2195BL
```

客製 CLI 表面存在（`hermes send --help`，WSL 現況 baseline）：

```
usage: hermes send [-h] [-t TARGET] [-f PATH] [-s LINE] [--message-key KEY]
  --message-key KEY     Explicit idempotency key for this send. Reruns with
  --force-resend        Bypass message-key deduplication and send again
```

### 1.5 WSL 服務拓撲——**Telegram bot 其實不依賴 hermes-agent**

`ps -eo pid,etime,cmd`（WSL，只有這兩個常駐）：

```
310  1-04:16  /home/razer/dev/ClaudeCodeOSWin/.venv/bin/python3 …/hermes/adapters/telegram.py
311  1-04:16  /home/razer/dev/ClaudeCodeOSWin/.venv/bin/python3 …/hermes/worker.py
```

`systemctl --user`：`hermes-telegram.service` / `hermes-worker.service` = active
running；`hermes-rss` / `hermes-bridge` / `hermes-cron-daily-memory-check` =
inactive（timer 觸發的 one-shot）。

**三個 bridge 相關 timer 是 masked**（unit 檔 symlink 指向 `/dev/null`）：

```
$ systemctl --user is-enabled hermes-bridge-notifier.timer hermes-bridge-pipeline.timer hermes-bridge-scanner.timer
masked
masked
masked
```

作用中的 timer（`list-timers --all`）：`hermes-rss.timer`（**每 30 分鐘**）、
`hermes-bridge.timer`（每日 08:10）、`hermes-cron-daily-memory-check.timer`
（每日 08:00）。

**關鍵**：這些 unit 的 `ExecStart` 全部指向
`~/dev/ClaudeCodeOSWin/.venv/bin/python3` 跑 **ClaudeCodeOSWin 自己的**
`hermes/adapters/*.py`、`hermes/worker.py`，**不是** hermes-agent 套件。
逐檔 grep（`rss.py` / `hermes_bridge.py` / `cron.py` / `telegram.py` /
`worker.py`）確認**沒有任何一個會呼叫 `hermes` CLI 或 import hermes-agent**；
`worker.py` 的 `subprocess` 只用來跑 `hermes/adapter/invoke_cos.sh`（Claude CLI）。

全 repo 中唯一會呼叫 `hermes send` 的是 `hermes/bridge_notifier.py`
（`DEFAULT_SEND_CLI = "hermes"`，用 `-t slack:<channel> --message-key`），
而**它的 timer 是 masked，不會自動觸發**。

**WSL 內沒有任何 hermes gateway process 在跑**（`ps` 無 gateway）——
live gateway 只在 Windows 側。

> **停機窗口的真相**：使用者的 Telegram bot（`adapters/telegram.py`）與本次
> re-graft **零耦合**，**不需要停**。真正需要靜默的只有「會呼叫 `hermes` CLI
> 的路徑」，而它目前只有一條且已 masked。詳見 §5。

### 1.6 防重演狀態落差（WSL 側尚未收拾）

| 防線 | Windows | WSL |
|---|---|---|
| `origin` 指向 | 私有備份（`reset --hard origin/main` ≈ no-op） | **官方 NousResearch ← 危險** |
| `updates.pre_update_backup` | `true` | **`false` ← 落差** |
| `hermes --version` 的更新提示 | — | `Update available: 160 commits behind — run 'hermes update'` |

WSL 現況等於**還踩在 2026-07-24 事故的同一顆地雷上**：只要在 WSL 跑
`hermes update`，diverged fallback 的 `git reset --hard origin/main` 就會把
11 個客製 commit 一次抹掉。本計畫把這條一併補起來（§4 Phase 5）。

### 1.7 唯讀衝突模擬（若走方案 B 會遇到什麼）

在 Windows repo 執行（不寫工作樹）：

```
$ git merge-tree --write-tree --name-only c12c64f9e 970118870…
rc=1
015be60f88f7c3d00a10b5daf871a7704697cc5a
hermes_cli/web_server.py
tests/hermes_cli/test_web_server_channel_apply_multiplex.py

Auto-merging gateway/config.py
Auto-merging hermes_cli/web_server.py
CONFLICT (content): Merge conflict in hermes_cli/web_server.py
Auto-merging tests/hermes_cli/test_web_server_channel_apply_multiplex.py
CONFLICT (content): Merge conflict in tests/hermes_cli/test_web_server_channel_apply_multiplex.py
```

**2 個衝突檔**。但這 2 個衝突的本質是「同一組客製 × 兩個不同上游基準的**兩次
獨立解法**互撞」，不是「WSL 有新東西要保留」——見 §2 的判準。

### 1.8 客製內容在 `970118870` 內的完整性（對齊不會少東西）

10 個非 merge 客製 commit 觸及的**全部 46 個檔案**，逐一
`git cat-file -e 970118870…:<path>` → **全部存在，零 MISSING**。關鍵標記：

```
$ git grep -l outbound_allowed_channels 970118870… -- '*.py'
plugins/platforms/slack/adapter.py
tests/gateway/test_slack_outbound_allowlist.py
$ git grep -l outbound_allowed_channels c12c64f9e -- '*.py'
plugins/platforms/slack/adapter.py        ← 位置完全相同
tests/gateway/test_slack_outbound_allowlist.py

$ git show 970118870…:hermes_cli/send_cmd.py | grep -c -- '--message-key'
3
$ git cat-file -e 970118870…:hermes_cli/gateway_doctor.py     → OK
$ git cat-file -e 970118870…:hermes_cli/gateway_ownership.py  → OK
$ git cat-file -e 970118870…:tools/slack_send_ledger.py       → OK
```

---

## 2. 方案比較與推薦

### 判準

1. **不能遺失 WSL 獨有內容**——§1.2 已證明**獨有內容為零**，此判準對所有方案
   都自動滿足（只要保留 rescue ref）。
2. **兩側行為一致**是本次的目標，不只是副產品。
3. 不製造新的分歧歷史（分歧＝下一次事故的燃料）。
4. 可 rollback。

### 方案 A — 直接對齊到 `970118870`（`fetch windows-side` ＋ `reset --hard`）　**★ 推薦**

- **會失去什麼**：只有 merge commit `c12c64f9e` 這一個 commit 物件會離開
  `main` 的歷史（內容不失，因為它的兩個 parent 都已在 `970118870` 祖先中，
  而它自己的衝突解法已被 Windows 側**經過 6 檔人工解衝突 ＋ 574 tests 全綠**
  的新解法取代）。且我們會用 rescue tag 把它釘住，物件不會被 gc。
- **優點**：
  - 結果與 Windows 側 **byte-for-byte 同一棵樹**，「兩側行為一致」是**定義上
    成立**，不是「應該差不多」。
  - 零衝突需要判斷——沒有任何一行程式碼需要人做取捨。
  - 歷史線性化：WSL `main` 從此就是 Windows `main`，未來同步永遠可 ff。
  - 與 §1.3 的髒點自癒、§1.6 的防重演 re-point 天然搭配。
- **缺點**：`reset --hard` 是破壞性指令（需 rescue ref 兜底、需明確核准）；
  「WSL 曾經有過自己的 merge」這件事只留在 rescue tag 裡。
- **風險**：低（前提：執行前的 gate 檢查 §4 Phase 3 步驟 3-4 通過）。

### 方案 B — 受控 merge（`git merge windows-side/main` 解衝突）

- 需要人解 **2 個衝突檔**（§1.7）：`hermes_cli/web_server.py`、
  `tests/hermes_cli/test_web_server_channel_apply_multiplex.py`。
- **致命缺點**：merge 完成後 WSL 會得到一個 **Windows 側沒有的 merge commit**，
  且該 commit 的樹**幾乎不可能與 `970118870` 完全相同**（衝突解法由 WSL 這次
  的人決定）。結果是：
  - 「兩側行為一致」從**可證明**退化成**靠人相信**；
  - WSL 永久領先 Windows ≥1 commit → 下次同步又不能 ff → **把今天的問題
    複製到未來每一次**；
  - 需要在 WSL 側重跑完整 574 tests 才敢說沒事（沙箱、時間成本）。
- 唯一的存在理由是「WSL 有獨有內容要保」——**§1.2 已證明沒有**。→ **駁回**。

### 方案 C1 — cherry-pick WSL 獨有 commit 再對齊

- 可 cherry-pick 的獨有 commit 數 = **0**。此方案退化成方案 A ＋多餘步驟。→ 駁回。

### 方案 C2 — rebase WSL `main` 到 `970118870`

- 要 rebase 的 commit 只有一個 merge commit（`--rebase-merges` 才處理得了），
  rebase 一個「其內容已完全被目標包含」的 merge，結果不是空 commit 就是
  衝突。純粹的自找麻煩。→ 駁回。

### 方案 D — 重新 clone

- 需重抓 634M `.git`（或從 `/mnt/c` copy）、venv 要重建（本來就要）、
  會丟掉 WSL 現有 tag／reflog（＝丟掉 rollback 錨）、`~/.local/bin/hermes` 的
  絕對路徑要重指。相對方案 A **成本更高、風險更高、收益為零**。→ 駁回。

### 推薦

> **採方案 A**：`git fetch windows-side` → gate 檢查 → `git reset --hard 970118870`
> → 重建 editable install → 防重演 re-point（`origin`↔`upstream` 換名）→ 驗證。
>
> 決定性理由：**WSL 側零獨有內容**（`git rev-list --count --all --not main
> origin/main` = 0，且 `HEAD..c12c64f9e` 只有 merge commit 自己），所以
> 「保留 WSL 歷史」沒有任何要保護的實質；而方案 A 是唯一能讓兩側樹**完全相同**
> 、且讓未來同步永遠是 ff 的做法。

---

## 3. rescue ref / 備份錨點

比照 Windows 側慣例（`rescue/pre-remerge-20260724`、
`rescue/pre-updater-merge-20260724`），本次建 **1 個 tag ＋ 1 個 branch**
（雙保險：tag 防誤刪、branch 讓 `git branch` 一眼看得到）：

| 錨點 | 型別 | 指向 | 位置 |
|---|---|---|---|
| `rescue/pre-regraft-20260725` | **tag**（annotated） | `c12c64f9e` | WSL repo |
| `rescue/pre-regraft-20260725` | **branch** | `c12c64f9e` | WSL repo |

> tag 與 branch 同名在 git 中允許（不同 namespace），但為避免 `git checkout
> rescue/pre-regraft-20260725` 的歧義警告，**rollback 一律用完整 ref 路徑**
> `refs/tags/rescue/pre-regraft-20260725`（見 §7）。

檔案層備份（repo 外，成本近零，值得做）：

| 檔案 | 備份檔名 |
|---|---|
| `~/.hermes/config.yaml` | `config.yaml.bak.pre-regraft-20260725` |
| `~/.hermes/.env` | `.env.bak.pre-regraft-20260725` |
| `~/.hermes/slack_send_ledger.db` | `slack_send_ledger.db.bak.pre-regraft-20260725` |

**不需要備份**：`venv/`（會重建）、`.install_method`（`reset --hard` 不刪未追蹤檔）、
`state.db`（symlink 指向 Windows，本流程不碰）。

---

## 4. 逐步執行序列

**符號**：🟢 唯讀／可安全重跑　🟡 有寫入但可逆　🔴 **破壞性**　⛔ **gate：不過就停**

所有指令都以 WSL 內為執行環境。建議先開一個常駐 shell 避免每步 `wsl.exe` 開銷：

```powershell
wsl -d Ubuntu
```

以下指令假設已在該 shell 內、且已 `cd ~/.hermes/hermes-agent`。
（若要逐條從 Windows 下，前綴 `wsl -d Ubuntu --exec bash -lc "cd ~/.hermes/hermes-agent && …"`。）

---

### Phase 0 — 執行前唯讀複驗（🟢，約 1 分鐘）

確認自本調查以來狀態沒漂移。**任一項與預期不符 → 停，回頭重新調查。**

```bash
cd ~/.hermes/hermes-agent
git rev-parse HEAD                 # 預期 c12c64f9e94da03190039db0352f3e190edb61c4
git status --porcelain             # 預期只有一行：?? .install_method
git stash list                     # 預期空
git for-each-ref refs/heads        # 預期只有 refs/heads/main
git rev-list --count --all --not main origin/main   # 預期 0（零獨有 commit）
```

```powershell
# Windows 側（另開視窗）：確認整合 tip 沒被動過
git -C "$env:LOCALAPPDATA\hermes\hermes-agent" rev-parse HEAD
# 預期 970118870167091e07730543f897cd2b363c9dd1
git -C "$env:LOCALAPPDATA\hermes\hermes-agent" status --porcelain
# 預期空（乾淨）
```

---

### Phase 1 — 建 rescue 錨點（🟡，約 1 分鐘）

```bash
cd ~/.hermes/hermes-agent
git tag -a rescue/pre-regraft-20260725 c12c64f9e \
  -m "WSL pre-regraft state (2026-07-17 custom merge, upstream c48d5341, +11 carried)"
git branch rescue/pre-regraft-20260725 c12c64f9e

# 驗證錨點成立（🟢）
git rev-parse refs/tags/rescue/pre-regraft-20260725^{commit}   # 預期 c12c64f9e94…
git rev-parse refs/heads/rescue/pre-regraft-20260725           # 預期 c12c64f9e94…
```

檔案層備份：

```bash
cp -p ~/.hermes/config.yaml           ~/.hermes/config.yaml.bak.pre-regraft-20260725
cp -p ~/.hermes/.env                  ~/.hermes/.env.bak.pre-regraft-20260725
cp -p ~/.hermes/slack_send_ledger.db  ~/.hermes/slack_send_ledger.db.bak.pre-regraft-20260725
ls -la ~/.hermes/*.pre-regraft-20260725
```

---

### Phase 2 — 靜默窗口開始（🟡，約 1 分鐘）

**只停 timer，不停 Telegram bot／worker**（§1.5：兩者與 hermes-agent 零耦合）。

```bash
systemctl --user stop hermes-rss.timer hermes-bridge.timer hermes-cron-daily-memory-check.timer
systemctl --user list-timers --all --no-pager      # 確認上述三個已不在排程中
```

同時**人為約束**：窗口內不手動執行任何 `hermes …` 指令、不手動跑
`python3 hermes/bridge_notifier.py notify`。

> 若使用者仍偏好最保守：可額外
> `systemctl --user stop hermes-telegram.service hermes-worker.service`，
> Telegram bot 將中斷 ~20 分鐘。**本計畫不建議**——沒有技術必要。

---

### Phase 3 — fetch ＋ gate ＋ 對齊（🟢→⛔→🔴，約 3–8 分鐘）

```bash
cd ~/.hermes/hermes-agent

# 3-1（🟡，只寫 .git，不動工作樹）從本機路徑 remote 取回 Windows 整合歷史
git fetch windows-side
```

> 若 fetch 報 `detected dubious ownership in repository at '/mnt/c/...'`：
> 執行 `git config --global --add safe.directory /mnt/c/Users/razer/AppData/Local/hermes/hermes-agent`
> 後重試（這是 /mnt/c 跨檔案系統的已知情況，非異常）。
> 從 9p 掛載讀 ~1600 commit 可能需要數分鐘，屬正常。

```bash
# 3-2（🟢）確認取回的 tip 正確
git rev-parse windows-side/main
# 必須 = 970118870167091e07730543f897cd2b363c9dd1
```

```bash
# 3-3 ⛔ GATE A：確認 WSL 相對 Windows tip 的獨有 commit 只有那個 merge
git rev-list --count windows-side/main..HEAD          # 必須 = 1
git log --oneline windows-side/main..HEAD             # 必須只有 c12c64f9e9 那一行
```

**若 GATE A 回傳 > 1 → 立即停止**，代表有本調查未涵蓋的新 commit，需重新評估
（此時 rescue 錨已建立，尚未做任何破壞性動作，可安全中止）。

```bash
# 3-4 ⛔ GATE B：確認客製確實被目標 tip 涵蓋
git merge-base --is-ancestor 03bb983e31 windows-side/main; echo "custom_tip_ancestor=$?"   # 必須 0
git merge-base --is-ancestor c12c64f9e  windows-side/main; echo "old_merge_ancestor=$?"    # 預期 1（非祖先＝不能 ff，符合預期）
```

```bash
# 3-5 🔴 破壞性：對齊到 Windows 整合 tip
git reset --hard 970118870167091e07730543f897cd2b363c9dd1
```

```bash
# 3-6（🟢）驗證對齊結果
git rev-parse HEAD                 # 必須 = 970118870167091e07730543f897cd2b363c9dd1
git status --porcelain             # 必須為空（.install_method 已被新 .gitignore 忽略）
git diff --stat windows-side/main  # 必須無輸出（與 Windows 樹完全相同）
ls -la .install_method             # 檔案仍在（reset --hard 不刪未追蹤檔）
ls -d venv                         # venv 仍在（/venv/ 被 gitignore）
```

**⚠️ 全程禁止 `git clean`（任何形式）**——`venv/`（332M）與 `.install_method`
都是被忽略／未追蹤的必要檔案。

---

### Phase 4 — 重建 editable 安裝（🟡，約 3–10 分鐘，取決於網路）

```bash
cd ~/.hermes/hermes-agent
./venv/bin/python -m pip install -e ".[messaging]"
```

> 與 2026-07-24 Windows 側修復所用的 `pip install -e ".[messaging]"` 同一道，
> 只是明確指定 venv 的 python 以免用錯直譯器。
> 若 pip 過慢，等價的 uv 版本（可選）：
> `~/.hermes/bin/uv pip install --python ./venv/bin/python -e ".[messaging]"`

```bash
# 驗證版本已由 0.18.2 換到 0.19.0（🟢）
ls venv/lib/python3.11/site-packages/ | grep -iE 'hermes_agent|__editable__'
# 預期出現 hermes_agent-0.19.0.dist-info 與 __editable__.hermes_agent-0.19.0.pth
```

---

### Phase 5 — 防重演 re-point（🟡，約 1 分鐘）

目標：讓 WSL 的 `hermes update` / installer 的 `reset --hard origin/main`
**變成對「Windows 整合 tip」的 no-op**，而不是對官方純上游的毀滅性重置。

```bash
cd ~/.hermes/hermes-agent
git remote rename origin upstream          # 官方 NousResearch → upstream（與 Windows 命名一致）
git remote rename windows-side origin      # 本機 Windows repo → origin（reset 目標 = 整合 tip）

# 驗證（🟢）
git remote -v
# 預期：
#   origin    /mnt/c/Users/razer/AppData/Local/hermes/hermes-agent (fetch/push)
#   upstream  https://github.com/NousResearch/hermes-agent.git (fetch/push)
git rev-parse origin/main                  # 必須 = 970118870…（＝ HEAD，reset 為 no-op）
git config --local branch.main.remote      # ⚠️ 見下方更正：實際會是 upstream，不是 origin
git status -sb                             # 預期 ## main...origin/main（無 ahead/behind）
```

> **⚠️ 執行時更正（2026-07-25 實測）——計畫原文寫「預期 `origin`（remote rename
> 會自動更新）」是錯的。**
> `git remote rename origin upstream` **會把 branch tracking 一併帶走**：
> rename 後 `branch.main.remote` 變成 `upstream`（不是 `origin`），
> `branch.main.merge` 也跟著留在舊 remote 下。
> **後果**：若不修，「跟隨 Windows 整合 tip」的 fail-safe 會失效——任何走
> tracking 的 `reset --hard @{upstream}` / `hermes update` 會把目標算成
> **官方純上游的舊快取**，正好是本計畫要拆的那顆地雷。
> **修法（Phase 5 必跑，不是可選）**：
>
> ```bash
> git config --local branch.main.remote origin
> git config --local branch.main.merge refs/heads/main
> # 驗證
> git config --local branch.main.remote   # 必須 = origin
> git status -sb                          # 必須 = ## main...origin/main（無 ahead/behind）
> ```

補上 `pre_update_backup` 落差（Windows 為 `true`，WSL 目前 `false`）：

```bash
# 用 hermes 自己的設定介面（優先）
hermes config set updates.pre_update_backup true
# 若無此子指令，改為手動編輯 ~/.hermes/config.yaml 第 133 行：
#   pre_update_backup: false   →   pre_update_backup: true
grep -n -A3 '^updates:' ~/.hermes/config.yaml    # 驗證：預期 pre_update_backup: true
```

> **⚠️ 執行時更正（2026-07-25 實測）**：`hermes config set
> updates.pre_update_backup true` 會把值寫成**字串 `'true'`**，而不是 YAML
> boolean `true`。Windows 側是 boolean，兩側型別不一致有被誤判的風險，
> 執行時已手動改回 boolean。**驗證時要看的是有沒有引號**：
> `pre_update_backup: true`（✅）vs `pre_update_backup: 'true'`（❌）。

> **設計說明（為何 WSL 的 `origin` 指本機路徑而非私有 GitHub repo）**：
> Windows 側用私有備份 repo 是因為它本身就是客製的源頭、且有 GitHub 憑證。
> WSL 側的正確語意是「跟隨 Windows 整合 tip」——用本機路徑 remote 既免憑證、
> 免網路，又讓「reset --hard origin/main」的最壞情況正好等於我們想要的狀態。
> 這比指向私有 GitHub repo（WSL 可能無憑證 → fetch 失敗 → update 半途而廢）
> 更 fail-safe。

---

### Phase 6 — 驗證清單（🟢，約 3–5 分鐘）

比照 2026-07-24 Windows 側修復所跑的那套。**逐條記錄實際輸出。**

| # | 項目 | 指令 | 通過判準 |
|---|---|---|---|
| V1 | git 座標 | `git rev-parse HEAD; git status --porcelain; git status -sb` | HEAD = `970118870…`；porcelain 空；`## main...origin/main` 無 ahead/behind |
| V2 | 與 Windows 樹一致 | `git diff --stat origin/main` | **無輸出** |
| V3 | 版本字串 | `hermes --version` | 含 `v0.19.0`、`upstream 3910ab28`（或 Windows 側同值）、`local 97011887`；**不再出現** `160 commits behind` |
| V4 | 客製模組 import | `./venv/bin/python -c "import tools.slack_send_ledger, hermes_cli.gateway_doctor, hermes_cli.gateway_ownership; print('custom modules OK')"` | 印出 `custom modules OK`，零 traceback |
| V5 | allowlist 程式碼在位 | `git grep -l outbound_allowed_channels -- '*.py'` | 列出 `plugins/platforms/slack/adapter.py` 與 `tests/gateway/test_slack_outbound_allowlist.py` |
| V6 | allowlist 設定生效 | `grep -n -A7 'outbound_allowed_channels' ~/.hermes/config.yaml` | 6 個頻道 ID 完整（`C0BHE9NFW0P` … `C0BHG2195BL`） |
| V7 | `--message-key` 支援 | `hermes send --help \| grep -- '--message-key'` | 出現 `--message-key KEY` 與 `--force-resend` |
| V8 | gateway doctor | `hermes gateway doctor` | 指令存在且正常回報（WSL 無 live gateway，**回報「無執行中 gateway／無殘留 artifact」即為通過**；重點是 multiplexer 客製子指令仍在） |
| V9 | **Slack allowlist 負面測試（fail-closed）** | `hermes send -t slack:C0NOTALLOWED000 --message-key regraft-neg-20260725 "negative test"` | **必須被拒絕**（非零 exit ／明確 allowlist 拒絕訊息）；**且該訊息未送出**。這是最關鍵一條——通過代表 Phase 2 的客製硬化仍活著 |
| V10 | ledger 冪等（正面，可選） | 對**允許清單內**的頻道以同一 `--message-key` 送兩次；第二次應 no-op | 第二次不重複投遞（會實際發一則訊息到 Slack，執行前徵得使用者同意並選定測試頻道） |
| V11 | notifier 端到端（可選） | `cd ~/dev/ClaudeCodeOSWin && python3 hermes/bridge_notifier.py notify --dry-run`（若支援）或 `… log` | 不噴錯 |
| V12 | 防重演確認 | `git remote -v; git rev-parse origin/main; grep -n pre_update_backup ~/.hermes/config.yaml` | `origin` = 本機 Windows 路徑且 `origin/main` = HEAD；`pre_update_backup: true` |
| V13 | rescue 錨仍在 | `git for-each-ref \| grep rescue` | `refs/tags/rescue/pre-regraft-20260725` 與 `refs/heads/rescue/pre-regraft-20260725` 皆 = `c12c64f9e…` |
| V14 | Telegram bot 存活 | `systemctl --user is-active hermes-telegram.service hermes-worker.service`；並在 Telegram 實際對 bot 送一則訊息 | 兩者 `active`；bot 有回應 |
| V15 | 唯讀預檢重跑 | 從 Windows 跑 `.venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'dashboard'); import data_update, json; print(json.dumps(data_update.get_update_precheck(), ensure_ascii=False, indent=2))"` | WSL target 不再是紅（工作樹已乾淨）；WSL 對 upstream 的 behind 從 160 降到 16（與 Windows 相同）；`peer`/`origin` 組顯示與 Windows 一致 |

> **V8 的 3.5 分鐘注意事項**：WSL 目前**沒有** live gateway，所以本次流程
> **不涉及** gateway 重啟，也就沒有 3.5 分鐘暖機窗口。若使用者另行決定在
> WSL 起一個 gateway 來做更完整驗證，則從 `hermes gateway start` 起算
> **至少等 3.5 分鐘**才可讀狀態檔判定成敗，期間「未寫狀態檔」**不等於**失敗
> （見 `memory/hermes-gateway-init-slow.md`）。

---

### Phase 7 — 恢復排程（🟡，約 1 分鐘）

```bash
systemctl --user start hermes-rss.timer hermes-bridge.timer hermes-cron-daily-memory-check.timer
systemctl --user list-timers --all --no-pager
# 確認三個 timer 回到排程；hermes-rss.timer 應在 30 分鐘內有 NEXT
```

**不要**去動那三個 masked 的 bridge-notifier/pipeline/scanner timer——
它們是刻意 masked 的既有狀態，本流程不改變。

---

## 5. 停機窗口

| 項目 | 內容 |
|---|---|
| **必須停的服務** | **無。** Telegram bot（`adapters/telegram.py`）與 worker 跑在 ClaudeCodeOSWin 自己的 `.venv`，不 import hermes-agent、不呼叫 `hermes` CLI（§1.5 逐檔驗證）。WSL 內也沒有 hermes gateway process。 |
| **需暫停的排程** | `hermes-rss.timer`（每 30 分）、`hermes-bridge.timer`（每日 08:10）、`hermes-cron-daily-memory-check.timer`（每日 08:00）——純屬保守（三者亦不用 hermes CLI），避免窗口內有任何 job 讀到半更新狀態 |
| **需人為靜默** | 窗口內不手動跑 `hermes …`、不手動跑 `bridge_notifier.py notify`（唯一會用 `hermes send` 的路徑，其 timer 已 masked） |
| **Telegram bot 停機時間** | **0 分鐘**（不停） |
| **總窗口預估** | **15–25 分鐘**：Phase 0–2 約 3 分；Phase 3 `fetch`（/mnt/c 9p 讀 ~1600 commit）3–8 分 ＋ `reset --hard`（2026 檔）＜1 分；Phase 4 `pip install -e` 3–10 分；Phase 6 驗證 3–5 分；Phase 7 1 分。**保守抓 30 分鐘**。 |
| **建議時機** | 避開 08:00／08:10 的每日 timer；`hermes-rss.timer` 每 30 分一次，開始前先看 `list-timers` 的 `NEXT`，挑剛跑完的時點起手 |
| **gateway 3.5 分鐘暖機** | **本流程不適用**（WSL 無 live gateway）。僅在使用者另行 `hermes gateway start` 時才需計入，且期間不得判定 not running |

---

## 6. 驗證清單（速查版）

依序：V1 git 座標 → V2 與 Windows 樹零差異 → V3 版本字串 → V4 客製模組 import
→ V5/V6 allowlist（程式碼＋設定）→ V7 `--message-key` → V8 `gateway doctor`
→ **V9 allowlist 負面測試 fail-closed（最關鍵）** → V10 ledger 冪等（可選、會實際送訊息）
→ V11 notifier（可選）→ V12 防重演 → V13 rescue 錨 → V14 Telegram bot 回應
→ V15 唯讀預檢重跑。完整判準見 §4 Phase 6 表。

**任一條不過 → 不繼續往下 → 進入 §7 rollback 評估**（不自動 rollback）。

---

## 7. Rollback 程序（明示指令，**不自動執行**）

觸發條件：Phase 6 任一驗證項不過，且無法在 15 分鐘內就地修好。

```bash
cd ~/.hermes/hermes-agent

# R1 🔴 破壞性：回到 re-graft 前的 tip
git reset --hard refs/tags/rescue/pre-regraft-20260725

# R2 還原 remote 命名（Phase 5 的反向操作）
git remote rename origin windows-side
git remote rename upstream origin

# R3 重建舊版 editable 安裝
./venv/bin/python -m pip install -e ".[messaging]"

# R4 還原設定檔（僅在 Phase 5 改過且要完全回到原狀時）
cp -p ~/.hermes/config.yaml.bak.pre-regraft-20260725 ~/.hermes/config.yaml

# R5 恢復排程
systemctl --user start hermes-rss.timer hermes-bridge.timer hermes-cron-daily-memory-check.timer

# R6 驗證回復成功（🟢）
git rev-parse HEAD                 # 必須 = c12c64f9e94da03190039db0352f3e190edb61c4
git status --porcelain             # 預期 ?? .install_method（回到原本的髒狀態，正常）
git remote -v                      # origin = NousResearch、windows-side = /mnt/c/...
hermes --version                   # 應回到 v0.18.2 … local c12c64f9
hermes send --help | grep -- '--message-key'   # 客製仍在
```

**注意**：R1 之後 `.install_method` 會重新變成未追蹤（舊 `.gitignore` 沒有那條），
這是**預期行為**，不是錯誤。

**rollback 不會影響**：`~/.hermes/slack_send_ledger.db`（未動）、
`state.db` symlink（未動）、Windows 側任何東西（全程唯讀被 fetch）。

---

## 8. 風險表

| # | 風險 | 機率 | 影響 | 緩解／偵測 |
|---|---|---|---|---|
| R-1 | `reset --hard` 丟掉 WSL 獨有內容 | **極低** | 高 | §1.2 已用 3 條 rev-list/merge-base 證明獨有 commit = 0；Phase 3 GATE A（`count windows-side/main..HEAD` 必須 = 1）在破壞前再擋一次；rescue tag＋branch 雙錨 |
| R-2 | `git clean` 誤刪 `venv/`（332M）或 `.install_method` | 低 | 中 | **計畫全程明文禁止 `git clean`**；`reset --hard` 不刪未追蹤檔；Phase 3-6 明確驗證 `venv` 與 `.install_method` 仍在 |
| R-3 | `git fetch windows-side` 因 /mnt/c 擁有權被拒（`dubious ownership`） | 中 | 低 | Phase 3-1 已附 `safe.directory` 修法；此步僅寫 `.git`，失敗可無痛重試 |
| R-4 | `pip install -e ".[messaging]"` 中途失敗 → 半安裝狀態 | 中 | 中 | 此時 git 樹已是 0.19.0 但 dist-info 可能仍 0.18.2；重跑同一條指令即可（冪等）；仍失敗則走 §7 rollback（R3 會把 0.18.2 裝回去） |
| R-5 | 窗口內有 job 呼叫到半更新的 hermes | 低 | 中 | 唯一呼叫路徑 `bridge_notifier` 的 timer 已 masked；Phase 2 再停三個作用中 timer；人為約束不手動跑 `hermes` |
| R-6 | **WSL 側 `hermes update` 再次毀客製**（現況地雷，尚未拆） | **中（現況）** | 高 | Phase 5 把 `origin` 指向 Windows 整合 tip → `reset --hard origin/main` 變 no-op；並補 `pre_update_backup: true`。**這是本次流程的附加價值，不是附帶損害** |
| R-7 | Phase 5 換名後 `hermes update` 從本機路徑「更新」，導致 WSL 永遠追不到官方新版 | 中 | 低 | 這是**刻意的設計**：WSL 的正確語意就是跟隨 Windows。要吸收官方新版時，流程是「Windows 先受控 merge → WSL `git fetch origin && git merge --ff-only origin/main`」，永遠可 ff |
| R-8 | 共享 `state.db`（symlink 到 Windows）因版本差異出問題 | 低 | 中 | 該檔 48 bytes、mtime 停在 2026-07-07，實質未使用；且 Windows 已在 0.19.0，本次是**消除**版本落差而非製造。全程不碰此檔 |
| R-9 | V9 負面測試若寫錯頻道格式，變成真的送出訊息 | 低 | 中 | 使用明顯不存在且不在 allowlist 的 ID（如 `C0NOTALLOWED000`）；預期在 allowlist 檢查就被擋，不會觸網 |
| R-10 | Windows 側在窗口內被人動到（HEAD 漂移） | 低 | 中 | Phase 0 複驗 Windows HEAD；Phase 3-2 再確認 `windows-side/main` = `970118870…`；窗口內請勿在 Windows 側做 git 操作 |
| R-11 | Telegram bot 被誤停造成生產中斷 | 低 | 中 | 本計畫**明文不停** telegram/worker；§5 已說明技術上無必要 |
| R-12 | rescue tag 與 branch 同名造成 checkout 歧義 | 低 | 低 | §7 rollback 一律用 `refs/tags/…` 完整路徑 |

---

## 9. 執行後應更新的文件

- `memory/hermes-agent-repo-work.md`：補記 2026-07-25 WSL re-graft（方案 A、
  rescue ref `rescue/pre-regraft-20260725` = `c12c64f9e`、WSL 防重演 re-point
  完成、兩側 tip 一致 = `970118870`）。
- `docs/webui-update-button-proposal.md` §4 target 表：WSL 側註記「remote 結構
  已改為 `origin` = 本機 Windows 路徑、`upstream` = 官方」，以及「WSL 無 live
  gateway，重啟項不適用」。
- `dashboard/data_update.py` docstring 的雙基準說明：WSL 端 remote 角色判定
  結果會改變（`origin` 由 `upstream` role 變成 `peer` role，因 URL 是本機路徑）
  ——**這會讓 WSL target 的「整體燈」失去 upstream 組**（見下）。

> **⚠️ 已知副作用（需在執行前讓使用者知情）**：Phase 5 換名後，WSL 端
> `_role_for_url()` 對 `origin`（本機路徑）判為 `peer`、對 `upstream`
> （NousResearch URL）判為 `upstream`。由於 `upstream/main` 這個本地 ref
> 在換名後會存在（原 `origin/main` 被 rename 帶過去 = `c48d53413a`，
> 且不會自動更新），預檢會顯示 WSL「落後官方 160」直到有人跑
> `git fetch upstream`。這不是壞掉，但**預檢畫面會不好看**。
> 建議在 Phase 5 之後追加一步（可選、僅觸網不改工作樹）：
> `git fetch upstream --prune`，讓 `upstream/main` 追到 `46c7a4076`，
> 預檢即顯示與 Windows 相同的「落後 16」。
>
> （執行後補註：這一步**實際有做**，兩側都跑了 `git fetch upstream --prune`；
> 但因官方在此期間又前進，實際數字不是「落後 16」而是**兩側同為
> 領先 12 / 落後 295**，官方 tip = `760112adb6`。詳見 §10。）

---

## 10. 執行紀錄（2026-07-25）

**狀態：✅ 完成。** 依方案 A 執行，Phase 0–7 全部走完，由主 session 親自操作。

### 10.1 結果總覽

| 項目 | 結果 |
|---|---|
| 實際耗時 | **約 12 分鐘**（計畫預估 15–25 分，保守抓 30 分） |
| **Telegram bot 停機** | **0 分鐘**（§1.5 的判斷成立——完全不需要停） |
| WSL HEAD | `c12c64f9e` → **`970118870`**（與 Windows 整合 tip 相同） |
| 兩側樹一致性 | `git diff --stat origin/main` **零輸出** → 兩側樹**逐 byte 相同** |
| 工作樹 | 髒 → **乾淨**（`.install_method` 被新 `.gitignore` 吸收，**檔案本身保留**，如 §1.3 預測） |
| editable install | 0.18.2 → **0.19.0** |
| remote 結構 | `origin` = `/mnt/c/Users/razer/AppData/Local/hermes/hermes-agent`；`upstream` = 官方 NousResearch |
| `updates.pre_update_backup` | `false` → **boolean `true`**（與 Windows 一致） |
| 驗證 | **V1–V15 全數通過**（V10 與 V14 後半於 2026-07-25 補測完成，見 §10.4） |

**rescue 錨（已建立，rollback 仍可用）**：

| 錨點 | 型別 | 指向 |
|---|---|---|
| `rescue/pre-regraft-20260725` | **tag**（annotated，tag object `3def71e96b`） | `c12c64f9e` |
| `rescue/pre-regraft-20260725` | **branch** | `c12c64f9e` |

**檔案層備份（已建立）**：`~/.hermes/` 下的
`config.yaml.bak.pre-regraft-20260725`、`.env.bak.pre-regraft-20260725`、
`slack_send_ledger.db.bak.pre-regraft-20260725`。

**最關鍵的一條**：**V9 Slack allowlist 負面測試 fail-closed 通過**——送往非
白名單頻道被**明確拒絕 ＋ exit 1 ＋ 訊息未送出**。這證明 re-graft 後客製硬化
仍然活著，是本次整個流程真正要換到的保證。

### 10.2 計畫未預料、執行時修正的兩個偏差（**照著跑的人務必先看這節**）

#### 偏差 1 — `git remote rename origin upstream` 會把 branch tracking 一併帶走

- **計畫原文（§4 Phase 5）**：`git config --local branch.main.remote` → 「預期
  `origin`（remote rename 會自動更新）」。**這句是錯的。**
- **實際**：rename 後 `branch.main.remote` 變成 **`upstream`**。git 的 rename
  會把 tracking 設定跟著舊 remote 名一起搬過去，而不是跟著「origin 這個名字」留下。
- **為什麼要緊**：本計畫 Phase 5 的整個目的，是讓「走 tracking 的 reset」
  （`hermes update` 的 diverged fallback、installer 的 update 路徑）指向
  **Windows 整合 tip**。若 tracking 還指著 `upstream`，那條 fail-safe 就失效，
  reset 目標會變成**官方純上游的舊快取**——等於這次流程最重要的防重演沒生效。
- **修法（已執行，已回寫進 §4 Phase 5）**：

  ```bash
  git config --local branch.main.remote origin
  git config --local branch.main.merge refs/heads/main
  ```

#### 偏差 2 — `hermes config set` 會把 boolean 寫成字串

- `hermes config set updates.pre_update_backup true` 寫進 `config.yaml` 的是
  **字串 `'true'`**，不是 YAML boolean `true`。
- Windows 側是 boolean，為避免兩側型別不一致造成日後判讀差異，**已手動改為
  boolean**。
- 驗證時請看有沒有引號：`pre_update_backup: true`（✅）／
  `pre_update_backup: 'true'`（❌）。

### 10.3 其他執行細節（下次會再遇到的坑）

- **WSL git 沒有 committer identity**：建 annotated tag 會失敗。處理方式是用
  一次性 env 變數 `GIT_COMMITTER_NAME` / `GIT_COMMITTER_EMAIL` / `GIT_AUTHOR_*`
  提供身分，**不寫進任何設定檔**（不汙染 WSL 的 git 全域／本地設定）。
- **從 Windows 用 `wsl.exe` 下指令時，引號會被吞掉**：含空格、`|`、`"` 的指令
  會被拆錯參數。實務做法是**避免內嵌引號**，或用「PowerShell 雙引號外層 ＋
  bash 單引號內層」。這是本次最花時間的摩擦點之一。
- **`git fetch windows-side` 的輸出證實了 §1 的關鍵推論**：

  ```
  c12c64f9e9...9701188701  main  (forced update)
  ```

  → 該 ref 原本確實停在 `c12c64f9e9`（過期），**若不先 fetch，2026-07-18 那道
  `merge --ff-only windows-side/main` 今天會是 no-op**。
- **官方基準已刷新**：兩側都跑過 `git fetch upstream --prune`，官方 tip 現為
  **`760112adb6`**；**兩側對官方皆為領先 12 / 落後 295**，且領先的 12 個
  commit **逐條相同**。計畫 §4 Phase 6 V15 寫的「落後從 160 降到 16」是以
  2026-07-23 的官方快照為準，實際數字以此節為準——**重點不是絕對數字，而是
  兩側完全相同**。

### 10.4 補測紀錄（2026-07-25，驗證清單至此全數關閉）

**V10 ledger 冪等（正面）——2026-07-25 補測，通過。**
使用者選定測試頻道 `C0BHZC2EG84`（先例頻道，在 allowlist 內），
以同一 `--message-key regraft-v10-20260725` 連送兩次相同內容：

| 次序 | CLI 輸出 | exit |
|---|---|---|
| 第 1 次 | `sent` | 0 |
| 第 2 次 | `already sent (deduplicated, ts=1784963993.073579)` | 0 |

`~/.hermes/slack_send_ledger.db` 的 `sends` 表由 **5 列增為 6 列**（只增一列），
該列 `state='success'`、`attempts=1`——第二次呼叫**沒有**產生新的投遞嘗試，
Slack 頻道端只出現一則訊息。至此 ledger 的去重路徑在 re-graft 後確認完好，
與 V9（allowlist fail-closed）合起來涵蓋客製硬化的兩條主要路徑。

**V14 後半 Telegram bot 回應——2026-07-25 由使用者實測，通過。**
使用者在 Telegram 對 bot 送訊息並收到回應。前半（`systemctl --user is-active
hermes-telegram.service hermes-worker.service` 皆 `active`）在執行當日即已通過，
且本流程全程未停這兩個 unit。複查時另確認：worker 正常承接背景 CoS 任務
（`invoke_cos.sh` + `claude -p` 執行中），Telegram adapter 跑的是 ClaudeCodeOSWin
自己的 `.venv`、與 hermes-agent 無耦合——與 §2 調查結論一致。

至此 §9 驗證清單 V1–V15 全數關閉，本次 re-graft 無遺留驗證項。

### 10.5 rollback 狀態

§7 的 rollback 程序**仍然完整可用**：rescue tag／branch 皆在、三份檔案備份皆在。
若日後需回退，注意 R2 的 remote 還原之外，**還要一併修正 tracking**——原 §7
未涵蓋這點。理由同偏差 1：R2 的 `git remote rename origin windows-side` 會把
tracking 帶成 `windows-side`，但 re-graft **前**的原始狀態是
`branch.main.remote=origin`（指官方 NousResearch）。故 R2 之後要補：

```bash
git config --local branch.main.remote origin
git config --local branch.main.merge refs/heads/main
git status -sb          # 應回到 ## main...origin/main [ahead 11, behind 160] 之類
```
