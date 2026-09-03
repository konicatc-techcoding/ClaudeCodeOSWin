#!/usr/bin/env python3
"""dashboard/data_update.py — 「Hermes 更新」唯讀升級預檢的資料層(階段一)。

設計正本:docs/webui-update-button-proposal.md §3(階段一——唯讀升級預檢)。
**只實作唯讀預檢**;階段二寫入執行(ff-only merge/依賴重建/服務重啟)未核准,
本模組零任何寫入入口——subprocess 只有一個位點(_exec),且只執行下方凍結的
**唯讀 git 查詢**模板/建構器,沒有其他 spawn 位點。

## 第一鐵律(提案 §0/§8,不可協商)

**絕不呼叫 `hermes update`、bootstrap installer、或任何寫入/觸網 git 子指令。**
git 讀取一律只用白名單子指令(rev-parse / rev-list / merge-base --is-ancestor /
for-each-ref / describe --tags / log --oneline / status --porcelain /
remote / remote get-url)。**絕對禁止** fetch / pull / merge / reset / checkout /
clone / push / commit / rebase / stash / apply —— 白名單以兩層在程式層強制:
(1) 無參數查詢限 FROZEN_GIT_TEMPLATES 成員;(2) 帶 remote 名的查詢限
REF_TEMPLATE_BUILDERS 建構器,且 remote 名須通過 REMOTE_NAME_RE 嚴格驗證
(不得含 `-` 開頭、`/`、空白 → 無法注入旗標或路徑)。非白名單一律 ValueError。

## 雙基準比較(2026-07-24 防重演落地後的必要修正)

防重演機制上線後 Windows repo 的 remote 結構改變,**只比 origin 會誤報最新**:

- Windows:`origin` = 使用者**私有備份 repo**(本機 main 已 push,兩者相等——
  這個相等就是防重演機制本身,使 `reset --hard origin/main` 成為 no-op);
  `upstream` = 官方 NousResearch。只看 origin 會得到 0/0「已最新」,
  但實際 vs 官方是領先 12 / 落後 16。
- WSL:**remote 結構與 Windows 不同**,且**2026-07-25 re-graft 後又改過一次**
  (見下方「WSL remote 結構已變更」)。故**不假設兩端 remote 同名**,
  一律動態讀取實際 remote 清單(`git remote`),並**依 remote URL 判定角色**
  (而非依名稱),再對每個 remote 各算一組 ahead/behind/ff。

角色(role):

| role     | 判定(URL)                          | 語意 |
|----------|-------------------------------------|------|
| upstream | 含 NousResearch/hermes-agent        | 官方上游——「有沒有新版可吸收」 |
| backup   | 含 hermes-agent-private             | 私有備份/防重演基準——「本機與雲端是否同步」 |
| follow   | 路徑正規化後 == Windows hermes-agent | 應跟隨的權威基準——「本端有沒有跟上 Windows 整合 tip」 |
| peer     | 其他(其他本機路徑/不明 remote)     | 其他基準,僅供參考,不計入整體燈 |

## WSL remote 結構已變更(2026-07-25 re-graft)→ `follow` role(2026-08-03)

2026-07-25 的 WSL side re-graft(docs/wsl-regraft-plan.md,方案 A,已執行完成)
把 WSL `main` 對齊到 Windows 整合 tip `970118870`(兩側樹逐 byte 相同),
並**重排 remote**:

- 舊:`origin` = 官方 NousResearch、`windows-side` = 本機 Windows 路徑
- **新:`origin` = `/mnt/c/Users/razer/AppData/Local/hermes/hermes-agent`
  (本機 Windows 路徑)、`upstream` = 官方 NousResearch**

目的是讓 `hermes update` 的 diverged fallback(`reset --hard origin/main`)
退化成對「Windows 整合 tip」的 no-op(防重演)。

**曾經的燈號盲點(2026-08-03 已修正,提案 §10.1 待辦落地)**:此前本機路徑
URL 既不含 `nousresearch/hermes-agent` 也不含 `hermes-agent-private`,一律判為
`peer`(`counts_toward_overall=False`),導致「WSL 有沒有跟上 Windows 整合 tip」
——WSL 最該被監控的一條——**看得到但不會亮燈**,整體燈完全由 `upstream` 組
(官方)獨力驅動;而兩側對官方落後同樣數量本來就是預期常態。

現在新增 **`follow`** role 補上這個盲點,判定為 `_is_windows_repo_url()`:

- **不是「只要是本機路徑就算」**——那會讓任何本機 remote 被誤升級成權威基準。
  判準是**路徑正規化後與凍結的 `WINDOWS_REPO_PATH` 逐字相等**
  (`_canonical_path_key()`:統一分隔符/大小寫、收斂重複斜線、`/mnt/<碟>/` ↔
  `<碟>:/`、去尾斜線與 `/.git`、`file://` 前綴;網路 URL(`scheme://`、
  scp 形式 `user@host:path`)一律不視為本機路徑)。
- **零 I/O、零額外 subprocess**:純字串比對,不 stat、不查該 repo 內容
  (故不新增任何指令面;白名單不變)。
- **誤判邊界**(刻意選擇的取捨):
  - *偽陰性(安全側)*:自訂 automount 根(非 `/mnt/`)、junction/symlink、
    8.3 短檔名、UNC(`\\\\wsl.localhost\\...`)等**別名路徑**不會被認出 →
    退回 `peer` = 修正前的行為(看得到、不亮燈),不會誤報健康。
  - *偽陽性*:唯有「該路徑確實存在但裡面不是 hermes-agent」才會誤判;
    但那同時代表 Windows target 自身也在探測錯的 repo,是更上游的問題,
    不是本判準能區分的。
  - `LOCALAPPDATA` 未設定 → `WINDOWS_REPO_PATH is None` → **永不**判 `follow`。

`follow` 的燈號語意與 `upstream` 組**相反**(見下方燈號表):落後 = 該同步了
(藍,資訊態);領先/分歧 = **異常**(橙)——WSL 理論上不該有 Windows 沒有的
commit。

**拍板(2026-08-03,選項 b):follow 存在時,同 target 的 upstream 組降為
資訊性**(`_apply_follow_demotion()`)——某 target 存在 `follow` 組,即代表
該端的正確語意是「跟隨者」:對官方落後多少是與 Windows 相同的預期常態,
不該驅動整體燈。此時該 target 的 `upstream` 組 `counts_toward_overall`
改為 False(照常輸出數字/diverge/summary,UI 標「僅供參考」),整體燈由
`follow` 組獨力驅動(WSL 現況:恆為 `overall_driver=follow`)。規則跟著
**remote 拓撲**走,不硬編 target id——若未來拓撲變動(例如 WSL 改指雲端、
不再有 follow remote),upstream 自動回復計入,不留錯的硬編。`backup` 不受
此降級影響:防重演基準在任何 target 都計入(理論上 WSL 不會有,防禦性保留)。
邊角:follow 組存在但 ref 不存在(applicable=False,灰)時**仍然降級**——
「最該監控的一條查不到」本身就該以灰示警,不得讓 upstream 的常態橙蓋過去。

`<remote>/main` ref 不存在(未 fetch 過/無該 remote)→ 該組
applicable=False「不適用/無法查詢」,不噴例外、不計入整體燈。

## 遠端資訊只讀本地已有 refs(提案 §3.1 待拍板項 5 的預設)

所有 ahead/behind/ff 一律相對**本地已有的 `<remote>/main`**計算——
**不執行 fetch**(有網路副作用)。輸出明確標示「遠端資訊可能過期,未 fetch」。

## live 版本字串(提案 §3.1 第一項;2026-07-25 切片 1 補實作)

輸出形如 `v0.19.0 upstream 3910ab28 + local 97011887 (+12 carried commits)`,
四個組成部分與其**零副作用**取得方式:

| 部分 | 來源 | 為何無副作用 |
|------|------|--------------|
| `v0.19.0` | `git show HEAD:pyproject.toml` 的 `[project] version` | `show` 是唯讀子指令,參數是**凍結字面**(`GIT_PYPROJECT`,零參數化);只從 object database 讀 blob,不碰工作區、不觸網、不 spawn 任何非 git process |
| `upstream 3910ab28` | `git merge-base <upstream-remote>/main HEAD`(取前 8 碼) | `merge-base` 本來就在白名單(既有 `--is-ancestor` 用的同一個);純圖論查詢,無寫入 |
| `local 97011887` | 既有的 `rev-parse --short HEAD`(取前 8 碼) | 既有唯讀查詢,無新增 |
| `(+12 carried commits)` | 既有 upstream 組的 `ahead`(`rev-list --count`) | 既有唯讀查詢,無新增 |

**為何不用其他來源**(逐一排除,留紀錄以免日後「優化」成有副作用的版本):

- **不跑 `hermes --version` / 任何 hermes CLI**——那會 spawn 一個真的 Python
  process、載入整包 hermes、可能觸發 banner 的 update 檢查(`hermes_cli/banner.py`
  會寫 `~/.hermes/.update_check`)。**那就是「看狀態」變「動狀態」**,違反 §3.1 唯讀鐵律。
- **不讀 `venv/.../*.dist-info`**——Windows 端可直接讀檔,但 WSL 端得另開一個
  **非 git 的** `wsl -d ... ls` 呼叫,會破壞「subprocess 只有一個位點且只跑 git」
  這條可被靜態驗證的不變式。
- **不讀 `gateway_state.json`**——實測其內容無版本欄位(只有 pid/state/platforms)。

**語意誠實聲明**:版本取自 **HEAD 的 pyproject.toml**。兩端都是 editable install
(`pip install -e`),故 HEAD 的原始碼即生效程式碼;但若 merge 後**未重跑
`pip install -e`**,已安裝的依賴可能落後於此字串——那不在本欄位涵蓋範圍
(工作樹未提交的改動則另由 `dirty` 欄呈現)。`pyproject.toml` 不存在/無法解析時
一律回 None,字串降級成不含 `v…` 的形式,不臆測、不噴例外。

## 兩端並列(提案 §4,處理方式不對稱)

- **Windows**:%LOCALAPPDATA%\\hermes\\hermes-agent,直接 `git -C <repo>`。
  service 欄複用 data_resident._gateway_ready()——自 2026-08-04 起含
  **gateway pid 活性驗證**(事故:gateway 死了一天半仍顯示就緒;活性 helper
  一份共用於常駐燈與本模組,細節見 data_resident.py docstring 專節)。
  expect_custom=True:客製 commit 以 **upstream 組**衡量(相對官方的領先數);
  若該數歸零 → 疑似被 reset 到純上游 → **紅**(2026-07-24 事故偵測型防線,
  提案 §6)。注意**不可**以 origin 組衡量——防重演落地後 origin 相等是健康態。
  expect_rescue=True:rescue ref 遺失 → 紅。
- **WSL**:經 `wsl -d Ubuntu --exec git -C <repo>`。**distro Stopped 不喚醒**
  ——先用 data_resident 既有的 `_distro_state()`(只跑 `wsl --list --verbose`)
  守門;非 Running 直接灰,絕不下任何 `wsl -d` 指令。服務狀態複用
  data_resident 既有探測,不重寫。expect_custom/expect_rescue 皆 False。
  **WSL 端無 live gateway**(gateway 只在 Windows 側),故服務欄看的是
  systemd --user 常駐單元,不是 gateway。整體燈的來源見上方
  「WSL remote 結構已變更 → follow role」——2026-08-03 拍板(選項 b)後由
  `follow` 組(是否跟上 Windows 整合 tip)**獨力驅動**;`upstream` 組照常
  顯示但降為資訊性(follow 存在時不計入,見 `_apply_follow_demotion()`)。

## 燈號(每組一盞 + 整體取較嚴重者)

每組(comparison)依 role 給光與建議:

| light  | upstream 組 | backup 組 | follow 組(語意與 upstream 相反)|
|--------|-------------|-----------|--------------------------------|
| green  | 無落後 = 已吸收官方最新 | 0/0 = 備份同步(防重演基準有效)| 0/0 = 已跟上 Windows 整合 tip |
| blue   | 落後 N 且無客製分歧 = 可 ff-only 前進 | 備份領先本機,可 ff-only 前進 | 落後 N = 該同步了(結構上可 ff-only)|
| orange | 落後 N 且同時領先(帶客製)= diverged,**必須受控 merge、不可自動** | 本機領先備份(客製未進備份)或雙向分歧 | **領先或分歧 = 異常**(本端不該有 Windows 沒有的 commit)|
| gray   | 該 remote 不適用/ref 不存在/查詢失敗 | 同左 | 同左 |

**整體燈** = 目標端層級的紅(工作樹髒／客製遺失／rescue 遺失)優先,否則取
**計入組**(`counts_toward_overall=True`)中**較嚴重者**;並以 `overall_driver`
標示是哪一組造成的,避免「備份健康但官方有新版」被誤讀成壞掉。

哪些組計入是 **per-target** 的(2026-08-03 拍板,選項 b):

- 無 `follow` 組的 target(現況 Windows):upstream 與 backup 計入,同上。
- 有 `follow` 組的 target(現況 WSL):該端語意是「跟隨者」→ `upstream`
  降為資訊性不計入,整體燈由 `follow`(+理論上的 `backup`)驅動——
  WSL 卡片即 follow 三態:已跟上=綠/落後=藍(該同步)/領先或分歧=橙(異常)。
- `peer` 永遠不計入。

同嚴重度時以 `DRIVER_PRIORITY` 決勝(follow > backup > upstream)——WSL 端
因只剩 follow 一組計入而自然無需決勝;Windows 端維持既有行為(backup 先於
upstream)。計入組的摘要**全部併陳**於 advice,不因此漏訊;降級組的資訊仍
完整輸出於 comparisons(看得到,只是不驅動燈)。

燈號短文字(light_text)以共用 LIGHT_TEXT 表為底、`ROLE_LIGHT_TEXT` 做
per-role 覆寫(2026-08-04):follow 的橙態顯示「領先/分歧於 Windows 整合
tip——異常」而非共用表的「帶客製 diverge——需受控 merge」——後者描述的是
對官方的正向 diverge,與 follow 的反向異常語意不符。target 整體 badge 的
短文字跟著 `overall_driver` 的 role 走(同一套覆寫)。

容錯:任何探測失敗一律優雅退化為 gray,不噴例外。快取 45 秒 TTL。
"""
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

# 相容兩種匯入路徑（1-2 修正）:`dashboard/` 沒有 __init__.py,api.py 是把
# dashboard/ 插進 sys.path 後以 **top-level** 名稱匯入;但從 repo 根做
# `import dashboard.<mod>`（namespace package)時 top-level 名稱不存在 →
# ModuleNotFoundError。故先試相對匯入(有 parent package 時成立),失敗才退回
# top-level。兩條路徑都可用,現行 api.py 啟動方式行為不變。
try:
    from . import data_resident  # 複用:distro 無喚醒守門、WSL 單元探測、Windows gateway 就緒
    from . import redact  # 輸出前掃描共用正本(慣例統一過;本模組無憑證資料,防禦性)
except ImportError:  # 無 parent package(api.py 的 sys.path 匯入方式)
    import data_resident
    import redact

# --- 目標 repo 路徑(唯讀讀取;路徑寫死,無參數化入口)-------------------
_local_app_data = os.environ.get("LOCALAPPDATA")
WINDOWS_REPO_PATH: Path | None = (
    Path(_local_app_data) / "hermes" / "hermes-agent" if _local_app_data else None
)
# WSL 內的 hermes-agent 已知部署路徑(--exec 無 shell,不做 ~ 展開,故用絕對路徑;
# 路徑不符 → git 失敗 → 該端優雅退化為灰,不噴例外)。
WSL_REPO_POSIX = "/home/razer/.hermes/hermes-agent"
WSL_DISTRO = data_resident.WSL_DISTRO  # "Ubuntu"(與燈號同一 distro 判準)

GIT_BIN = "git"
WSL_BIN = "wsl.exe"
GIT_TIMEOUT_SECONDS = 15

CACHE_TTL_SECONDS = 45.0  # 提案 §4 建議 30–60 秒;git 讀取重,短快取避免輪詢放大成本

COMPARE_BRANCH = "main"  # 比較基準分支(各 remote 的 <remote>/main)
SHORT_SHA_LEN = 8        # live 版本字串的 sha 縮寫長度(提案 §3.1 範例即 8 碼)

# remote 名嚴格驗證:字母數字開頭,只允許字母數字與 . _ - ——
# 不得含 `-` 開頭/`/`/空白 → 技術上無法注入旗標、路徑或額外參數。
REMOTE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")

# --- 角色判定(依 remote URL,不依名稱——兩端 remote 命名不同)-------------
OFFICIAL_UPSTREAM_MARKER = "nousresearch/hermes-agent"  # 官方上游
PRIVATE_BACKUP_MARKER = "hermes-agent-private"          # 私有備份/防重演基準

# WSL 對 Windows 磁碟的預設 automount 根。**刻意只認這一個**——自訂 automount
# 根(/etc/wsl.conf 的 root=)不會被認出,結果是退回 peer(偽陰性,安全側)。
WSL_MOUNT_PREFIX = "/mnt/"

ROLE_LABEL = {
    "upstream": "官方上游(有無新版可吸收)",
    "backup": "私有備份/防重演基準(本機與雲端是否同步)",
    "follow": "Windows 整合 tip(本端是否跟上——應跟隨的權威基準)",
    "peer": "其他基準(僅供參考)",
}

# 計入整體燈的角色(單組層級的預設)。peer 之外皆計入;follow 於 2026-08-03
# 加入(提案 §10.1)。**注意**:這只是預設——target 層級還有一道
# `_apply_follow_demotion()`(2026-08-03 拍板,選項 b):同 target 存在 follow
# 組時,upstream 降為資訊性不計入(該端語意是跟隨者,對官方落後是預期常態)。
COUNTED_ROLES = ("upstream", "backup", "follow")

# 同嚴重度時誰被標為 overall_driver(數字大者優先)。follow 最優先——「該跟上
# Windows 整合 tip」是 WSL 最該被看見的一條;backup > upstream 只是**保持**
# 加入 follow 之前的既有行為(Windows 端 origin=backup 先被列出,打平時取它)。
DRIVER_PRIORITY = {"follow": 2, "backup": 1, "upstream": 0}

# --- 凍結的唯讀 git 查詢模板(無參數版;subprocess 只允許這組 + 下方建構器)---
GIT_HEAD_SHORT = ("rev-parse", "--short", "HEAD")
GIT_BRANCH = ("rev-parse", "--abbrev-ref", "HEAD")
GIT_DESCRIBE = ("describe", "--tags", "--always")
GIT_PORCELAIN = ("status", "--porcelain")
GIT_REMOTE_LIST = ("remote",)  # 列出 remote 名稱(唯讀)
GIT_REFS = (
    "for-each-ref",
    "--format=%(refname:short) %(objectname:short) %(objecttype)",
    "refs/heads/",
    "refs/tags/",
)
# live 版本字串的套件版本來源:讀 **HEAD 的** pyproject.toml blob。
# `show` 是唯讀子指令,且此處是零參數化的凍結字面——無任何注入面,
# 不碰工作區、不觸網、不 spawn 非 git process(理由詳見模組 docstring)。
GIT_PYPROJECT = ("show", "HEAD:pyproject.toml")

FROZEN_GIT_TEMPLATES = frozenset({
    GIT_HEAD_SHORT, GIT_BRANCH, GIT_DESCRIBE, GIT_PORCELAIN, GIT_REMOTE_LIST, GIT_REFS,
    GIT_PYPROJECT,
})


# --- 帶 remote 名的唯讀查詢建構器(唯一的可變部分是通過 REMOTE_NAME_RE 的名稱)---
def _t_remote_url(remote: str) -> tuple[str, ...]:
    return ("remote", "get-url", remote)


def _t_tip(remote: str) -> tuple[str, ...]:
    return ("rev-parse", "--short", f"{remote}/{COMPARE_BRANCH}")


def _t_behind(remote: str) -> tuple[str, ...]:
    return ("rev-list", "--count", f"HEAD..{remote}/{COMPARE_BRANCH}")


def _t_ahead(remote: str) -> tuple[str, ...]:
    return ("rev-list", "--count", f"{remote}/{COMPARE_BRANCH}..HEAD")


def _t_ancestor(remote: str) -> tuple[str, ...]:
    return ("merge-base", "--is-ancestor", "HEAD", f"{remote}/{COMPARE_BRANCH}")


def _t_diverge_log(remote: str) -> tuple[str, ...]:
    return ("log", "--oneline", "-n", "20", f"{remote}/{COMPARE_BRANCH}..HEAD")


def _t_merge_base(remote: str) -> tuple[str, ...]:
    """本地歷史裡最新的、該 remote 也有的 commit(＝共同祖先)。
    live 版本字串的 `upstream <sha>` 就是這一顆。子指令 `merge-base` 與既有的
    `--is-ancestor` 同一個,白名單無需擴充寫入面;純圖論查詢,零寫入。"""
    return ("merge-base", f"{remote}/{COMPARE_BRANCH}", "HEAD")


REF_TEMPLATE_BUILDERS = (
    _t_remote_url, _t_tip, _t_behind, _t_ahead, _t_ancestor, _t_diverge_log,
    _t_merge_base,
)

# 白名單子指令(提案 §3.1 列舉集)。凍結模板與建構器產出的每一條,args[0] 都在
# 此集合內。刻意不寫任何寫入子指令的字面值(靜態測試鎖定)。
ALLOWED_GIT_SUBCOMMANDS = frozenset({
    "rev-parse", "rev-list", "merge-base", "for-each-ref",
    "describe", "branch", "log", "status", "remote",
    "show",  # 僅用於 GIT_PYPROJECT 這一條凍結字面(讀 HEAD 的 pyproject.toml blob)
})

LIGHT_TEXT = {
    "green": "已是最新",
    "blue": "可 ff-only 前進",
    "orange": "帶客製 diverge——需人工受控 merge",
    "red": "異常——需人工檢查",
    "gray": "無法查詢",
}

# per-role 短文字覆寫(2026-08-04):共用表的橙態語意是「帶客製 diverge 需
# 受控 merge 官方」,但 follow 組的橙是**反向異常**——本端不該有 Windows
# 沒有的 commit(領先/分歧,見 §10.1 拍板語意)。只覆寫語意不貼的格子;
# 其餘沿用共用表(follow 藍=「可 ff-only 前進」本就貼切,綠/灰同)。
ROLE_LIGHT_TEXT = {
    "follow": {"orange": "領先/分歧於 Windows 整合 tip——異常,需人工檢查"},
}


def _light_text_for(role: str | None, light: str) -> str:
    """比較組/target 的短文字:先查 per-role 覆寫,否則共用表。
    target 層以 overall_driver 當 role(driver=target/None 時自然落回共用表)。"""
    return ROLE_LIGHT_TEXT.get(role or "", {}).get(light, LIGHT_TEXT[light])

# 嚴重度排序(整體燈取較嚴重者)
LIGHT_SEVERITY = {"green": 0, "blue": 1, "gray": 2, "orange": 3, "red": 4}

_cache: tuple[float, dict] | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _exec(cmd: tuple[str, ...]) -> tuple[int, str] | None:
    """**唯一的 subprocess.run 位點**。只由 _run_git / _run_git_remote 呼叫,
    兩者都已先做白名單驗證。回傳 (returncode, stdout) 或 None(執行失敗)。"""
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=GIT_TIMEOUT_SECONDS,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    return proc.returncode, (proc.stdout or "")


def _run_git(prefix: tuple[str, ...], args: tuple[str, ...]) -> tuple[int, str] | None:
    """無參數唯讀查詢:**只接受 FROZEN_GIT_TEMPLATES 內的 args**——
    任何未凍結的 args(含任何寫入子指令)在此直接 ValueError 拒絕。"""
    if args not in FROZEN_GIT_TEMPLATES:
        raise ValueError(f"非白名單 git 模板,拒絕執行:{args}")
    return _exec(tuple(prefix) + tuple(args))


def _run_git_remote(prefix: tuple[str, ...], builder, remote: str) -> tuple[int, str] | None:
    """帶 remote 名的唯讀查詢:建構器須在 REF_TEMPLATE_BUILDERS 內,remote 名
    須通過 REMOTE_NAME_RE(無法注入旗標/路徑),產出的子指令須在白名單內。

    注意 merge-base --is-ancestor 以 returncode 傳達結果(0=祖先、1=非祖先),
    兩者都不是錯誤,由呼叫端解讀。"""
    if builder not in REF_TEMPLATE_BUILDERS:
        raise ValueError(f"非白名單 git 建構器,拒絕執行:{builder}")
    if not isinstance(remote, str) or not REMOTE_NAME_RE.match(remote):
        raise ValueError(f"remote 名稱不合法,拒絕執行:{remote!r}")
    args = builder(remote)
    if args[0] not in ALLOWED_GIT_SUBCOMMANDS:
        raise ValueError(f"非白名單 git 子指令,拒絕執行:{args[0]}")
    return _exec(tuple(prefix) + tuple(args))


def _ok_str(result: tuple[int, str] | None) -> str | None:
    if result is None or result[0] != 0:
        return None
    return result[1].strip()


def _to_count(text: str | None) -> int | None:
    if text is None:
        return None
    try:
        return int(text.split()[0])
    except (ValueError, IndexError):
        return None


def _short8(sha: str | None) -> str | None:
    """統一縮寫長度(提案 §3.1 範例用的就是 8 碼:`upstream 3910ab28 + local 97011887`)。
    純字串切片,不再跑一次 git。"""
    if not sha:
        return None
    token = sha.strip().split()[0] if sha.strip() else ""
    return token[:SHORT_SHA_LEN] or None


def _parse_project_version(text: str | None) -> str | None:
    """從 pyproject.toml 內容取 `[project]` 段的 `version`。

    刻意用**段落感知的逐行掃描**而非 toml 解析器:輸入是外部檔案,逐行掃描
    對畸形內容天然免疫(不會噴例外),且只認 `[project]` 段——避免誤抓
    `[tool.poetry] version` 之類的其他段落。找不到一律 None(不臆測)。"""
    if not text:
        return None
    section = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            continue
        if section != "project":
            continue
        match = re.match(r'^version\s*=\s*["\']([^"\']+)["\']\s*$', line)
        if match:
            return match.group(1).strip() or None
    return None


def _live_version(head_short: str | None, package: str | None,
                  comparisons: list[dict]) -> dict:
    """組出提案 §3.1 的 live 版本字串。任何一段取不到就誠實省略該段,
    不噴例外、不臆測(全都取不到時 text=None)。"""
    upstream = _pick(comparisons, "upstream")
    upstream_base = _short8(upstream.get("merge_base")) if upstream else None
    carried = upstream.get("ahead") if upstream else None
    local = _short8(head_short)

    parts: list[str] = []
    if package:
        parts.append(f"v{package}")
    if upstream_base:
        parts.append(f"upstream {upstream_base}")
    if local:
        parts.append(f"+ local {local}" if parts else f"local {local}")
    text = " ".join(parts) if parts else None
    if text and carried:
        text = f"{text} (+{carried} carried commits)"
    return {
        "package": package,
        "upstream_base": upstream_base,
        "local": local,
        "carried": carried,
        "text": text,
        "source": "HEAD:pyproject.toml + merge-base(唯讀 git;未執行 hermes CLI)",
    }


# 網路型 URL 的兩種形態(皆非本機路徑,直接排除,不可能是 Windows repo):
# 1) `scheme://…`(https/git/ssh/file…)——除 file:// 外一律排除;
# 2) scp 形式 `user@host:path`(git 的簡寫 SSH URL)。
_SCP_LIKE_RE = re.compile(r"^[^/\\]+@[^/\\]+:")
_LEADING_DRIVE_RE = re.compile(r"^/([a-z]:/.*)$")   # file:///C:/x → /c:/x → c:/x
_MOUNT_DRIVE_RE = re.compile(r"^([a-z])(/.*)?$")    # /mnt/c/x 的 "c/x" → c:/x


def _canonical_path_key(raw: str | None) -> str | None:
    """把「本機路徑型」remote URL 正規化成可逐字比對的 key;非本機路徑 → None。

    純字串運算——**不 stat、不 realpath、不觸檔案系統**(故無 I/O、無副作用,
    也不受探測時序影響)。正規化步驟:去 `file://` → 分隔符統一為 `/` →
    收斂重複斜線 → 小寫 → `/mnt/<碟>/…` 換成 `<碟>:/…` → 去尾斜線與 `/.git`。

    刻意**不**解析 symlink/junction/8.3 短檔名/UNC 別名:那需要檔案系統存取,
    而認不出的代價只是退回 peer(偽陰性,安全側),不會誤報健康。"""
    if not raw:
        return None
    text = raw.strip()
    if not text:
        return None
    if "://" in text.lower():
        if not text.lower().startswith("file://"):
            return None  # http(s)/git/ssh/… 網路 URL:不是本機路徑
        text = text[len("file://"):]
    if _SCP_LIKE_RE.match(text):
        return None      # user@host:path 的 SSH 簡寫:不是本機路徑
    key = re.sub(r"/{2,}", "/", text.replace("\\", "/")).lower()
    drive = _LEADING_DRIVE_RE.match(key)
    if drive:
        key = drive.group(1)
    if key.startswith(WSL_MOUNT_PREFIX):
        mount = _MOUNT_DRIVE_RE.match(key[len(WSL_MOUNT_PREFIX):])
        if mount:
            key = f"{mount.group(1)}:{mount.group(2) or '/'}"
    key = key.rstrip("/")
    if key.endswith("/.git"):
        key = key[:-len("/.git")].rstrip("/")
    return key or None


def _is_windows_repo_url(url: str | None) -> bool:
    """該 remote 是否**確實**指向 Windows 側 hermes-agent repo(follow role 判準)。

    **不是「只要是本機路徑就算」**——必須正規化後與凍結的 WINDOWS_REPO_PATH
    (%LOCALAPPDATA%\\hermes\\hermes-agent,本模組唯一的 Windows repo 定義)
    逐字相等;任何其他本機路徑一律留在 peer,不會被誤升級成權威基準。
    LOCALAPPDATA 未設定(WINDOWS_REPO_PATH is None)時**永遠**回 False。
    誤判邊界詳見模組 docstring。"""
    if WINDOWS_REPO_PATH is None:
        return False
    target = _canonical_path_key(str(WINDOWS_REPO_PATH))
    key = _canonical_path_key(url)
    return target is not None and key is not None and key == target


def _role_for_url(url: str | None) -> str:
    """依 remote URL 判定角色(不依名稱——兩端 remote 命名不同)。

    順序固定:官方/私有備份的 URL 標記優先(兩者都不可能是本機路徑),
    再判 follow(指向 Windows hermes-agent 的本機路徑),其餘為 peer。"""
    low = (url or "").lower()
    if PRIVATE_BACKUP_MARKER in low:
        return "backup"
    if OFFICIAL_UPSTREAM_MARKER in low:
        return "upstream"
    if _is_windows_repo_url(url):
        return "follow"
    return "peer"


def _classify_comparison(role: str, ahead: int | None, behind: int | None) -> tuple[str, str]:
    """單一比較組的燈與建議。回傳 (light, summary)。"""
    if ahead is None or behind is None:
        return "gray", "無法查詢(該 remote 的 main ref 不存在或查詢失敗)"

    if role == "upstream":
        if behind == 0:
            extra = f",本機另帶 {ahead} 個客製 commit" if ahead else ""
            return "green", f"已吸收官方最新{extra}"
        if ahead > 0:
            return "orange", (
                f"官方有 {behind} 個新 commit,因帶 {ahead} 個客製屬 diverged,"
                f"需受控 merge(不可自動)"
            )
        return "blue", f"官方有 {behind} 個新 commit,無客製分歧,結構上可 ff-only 前進"

    if role == "backup":
        if behind == 0 and ahead == 0:
            return "green", "備份同步 ✓(防重演基準有效)"
        if behind > 0 and ahead > 0:
            return "orange", f"與備份雙向分歧(落後 {behind}／領先 {ahead}),需人工處理"
        if ahead > 0:
            return "orange", f"本機領先備份 {ahead} 個 commit(尚未推送——防重演基準未涵蓋這些客製)"
        return "blue", f"備份領先本機 {behind} 個 commit,結構上可 ff-only 前進"

    if role == "follow":
        # 語意與 upstream 組**相反**:落後 = 該同步(資訊態,藍);
        # 領先/分歧 = 異常(橙)——本端理論上不該有 Windows 沒有的 commit。
        if behind == 0 and ahead == 0:
            return "green", "已跟上 Windows 整合 tip ✓"
        if behind > 0 and ahead > 0:
            return "orange", (
                f"與 Windows 整合 tip 雙向分歧(落後 {behind}／領先 {ahead})"
                f"——本端不該有 Windows 沒有的 commit,需人工檢查"
            )
        if ahead > 0:
            return "orange", (
                f"領先 Windows 整合 tip {ahead} 個 commit"
                f"——本端不該有 Windows 沒有的 commit,需人工檢查"
            )
        return "blue", (
            f"落後 Windows 整合 tip {behind} 個 commit,該同步了"
            f"(無分歧,結構上可 ff-only 前進)"
        )

    # peer:僅供參考
    if behind == 0 and ahead == 0:
        return "green", "與此基準一致"
    if behind > 0 and ahead > 0:
        return "orange", f"與此基準分歧(落後 {behind}／領先 {ahead})"
    if ahead > 0:
        return "green", f"領先此基準 {ahead} 個 commit"
    return "blue", f"落後此基準 {behind} 個 commit"


def _build_comparison(prefix: tuple[str, ...], remote: str) -> dict:
    """對單一 remote 建一組比較(純唯讀;ref 不存在 → applicable=False)。"""
    url = _ok_str(_run_git_remote(prefix, _t_remote_url, remote))
    role = _role_for_url(url)
    ref = f"{remote}/{COMPARE_BRANCH}"
    tip = _ok_str(_run_git_remote(prefix, _t_tip, remote))
    base = {
        "remote": remote,
        "url": url,
        "role": role,
        "role_label": ROLE_LABEL[role],
        "ref": ref,
        "tip": tip,
        "counts_toward_overall": role in COUNTED_ROLES,
    }
    if tip is None:
        return {
            **base, "applicable": False, "behind": None, "ahead": None,
            "can_ff": None, "diverged": None, "diverge_commits": [],
            "merge_base": None,
            "light": "gray", "light_text": _light_text_for(role, "gray"),
            "summary": f"不適用/無法查詢:本地無 {ref} ref(未 fetch 過此 remote)",
        }
    behind = _to_count(_ok_str(_run_git_remote(prefix, _t_behind, remote)))
    ahead = _to_count(_ok_str(_run_git_remote(prefix, _t_ahead, remote)))
    # 共同祖先:live 版本字串的 `upstream <sha>` 來源(見模組 docstring 的版本欄)。
    merge_base = _ok_str(_run_git_remote(prefix, _t_merge_base, remote))
    ancestor = _run_git_remote(prefix, _t_ancestor, remote)
    can_ff = (ancestor[0] == 0) if ancestor is not None else None
    diverge_commits: list[str] = []
    if ahead:
        log = _ok_str(_run_git_remote(prefix, _t_diverge_log, remote)) or ""
        diverge_commits = [ln.strip() for ln in log.splitlines() if ln.strip()]
    light, summary = _classify_comparison(role, ahead, behind)
    return {
        **base, "applicable": True, "behind": behind, "ahead": ahead,
        "can_ff": can_ff, "diverged": (ahead > 0) if ahead is not None else None,
        "diverge_commits": diverge_commits, "merge_base": merge_base,
        "light": light, "light_text": _light_text_for(role, light), "summary": summary,
    }


def _apply_follow_demotion(comparisons: list[dict]) -> None:
    """target 層級的角色權重(2026-08-03 拍板,選項 b):同一 target 存在
    `follow` 組時,`upstream` 組降為資訊性(counts_toward_overall=False)。

    理由:有 follow remote 即代表該端的正確語意是「跟隨者」——對官方落後多少
    是與 Windows 相同的預期常態,不該驅動整體燈;真正該監控的是「有沒有跟上
    Windows 整合 tip」(follow 組)。規則跟著 **remote 拓撲**走,不硬編
    target id:未來拓撲變動(例如 WSL 改指雲端、不再有 follow remote)時
    upstream 自動回復計入。`backup` 不降級(防重演基準在任何 target 都計入);
    follow 組 ref 不存在(applicable=False,灰)時**仍然降級**——「最該監控的
    一條查不到」就該以灰示警,不得讓 upstream 的常態橙蓋過去。

    純資料後處理(in-place 改 counts_toward_overall),零查詢、零副作用。"""
    if any(c.get("role") == "follow" for c in comparisons):
        for c in comparisons:
            if c.get("role") == "upstream":
                c["counts_toward_overall"] = False


def _parse_rescue_refs(refs_text: str | None) -> list[dict]:
    """從 for-each-ref 輸出過濾出 rescue/* refs(分支或 tag 皆算)。"""
    rescue: list[dict] = []
    if not refs_text:
        return rescue
    for line in refs_text.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        if "rescue" not in parts[0]:
            continue
        rescue.append({
            "name": parts[0],
            "object": parts[1],
            "type": parts[2] if len(parts) >= 3 else "unknown",
        })
    return rescue


def _facts_from_prefix(prefix: tuple[str, ...], repo_display: str) -> dict:
    """對一個 git repo(以呼叫前綴表示)跑整組唯讀查詢,回傳原始事實 dict。"""
    head_short = _ok_str(_run_git(prefix, GIT_HEAD_SHORT))
    if head_short is None:
        return {"queryable": False, "repo": repo_display,
                "reasons": ["無法讀取 HEAD(repo 不存在、git 不可用,或非 git 工作區)"]}

    branch = _ok_str(_run_git(prefix, GIT_BRANCH))
    describe = _ok_str(_run_git(prefix, GIT_DESCRIBE))
    porcelain = _run_git(prefix, GIT_PORCELAIN)
    dirty = bool(porcelain[1].strip()) if (porcelain is not None and porcelain[0] == 0) else None
    rescue = _parse_rescue_refs(_ok_str(_run_git(prefix, GIT_REFS)))

    # 動態讀取實際 remote 清單(不假設兩端 remote 結構相同)
    remotes_text = _ok_str(_run_git(prefix, GIT_REMOTE_LIST)) or ""
    remotes = [r.strip() for r in remotes_text.splitlines() if r.strip()]
    comparisons = [
        _build_comparison(prefix, r) for r in remotes if REMOTE_NAME_RE.match(r)
    ]
    # target 層級的角色權重:有 follow 組 → upstream 降為資訊性(選項 b)
    _apply_follow_demotion(comparisons)

    # live 版本字串(提案 §3.1 第一項):套件版本讀 HEAD 的 pyproject.toml blob,
    # 與 upstream 組的共同祖先/領先數組成。**零副作用**——不跑 hermes CLI、
    # 不讀 venv metadata、不新增任何非 git 的 spawn(理由見模組 docstring)。
    package_version = _parse_project_version(_ok_str(_run_git(prefix, GIT_PYPROJECT)))

    return {
        "queryable": True,
        "repo": repo_display,
        "head": {"short": head_short, "describe": describe, "branch": branch},
        "dirty": dirty,
        "rescue_refs": rescue,
        "rescue_count": len(rescue),
        "remotes": remotes,
        "comparisons": comparisons,
        "live_version": _live_version(head_short, package_version, comparisons),
    }


def _pick(comparisons: list[dict], role: str) -> dict | None:
    for c in comparisons:
        if c.get("role") == role:
            return c
    return None


def _classify_target(facts: dict, expect_custom: bool, expect_rescue: bool
                     ) -> tuple[str, str, list[str], str | None]:
    """目標端整體燈。回傳 (light, advice, blocking_reasons, overall_driver)。

    紅(目標端層級)優先:工作樹髒／客製遺失(以 upstream 組衡量)／rescue 遺失;
    否則取 role ∈ COUNTED_ROLES({upstream, backup, follow})各組中較嚴重者。"""
    if not facts.get("queryable"):
        return "gray", "無法查詢此端 repo 狀態", facts.get("reasons", []), None

    comparisons: list[dict] = facts.get("comparisons") or []
    upstream = _pick(comparisons, "upstream")
    reasons: list[str] = []

    if facts.get("dirty"):
        reasons.append("工作樹有未提交變更(status --porcelain 非空)")
    if expect_rescue and facts.get("rescue_count", 0) == 0:
        reasons.append("預期存在的 rescue ref 全部遺失(rollback 錨不見——疑似歷史被改寫)")
    # 客製遺失偵測必須以「官方上游」為基準——防重演落地後 origin(私有備份)
    # 相等是健康態,不能拿來判斷客製有沒有被 reset 掉。
    if expect_custom and upstream is not None and upstream.get("applicable") \
            and upstream.get("ahead") == 0:
        reasons.append("預期帶客製 commit 卻與官方上游零分歧"
                       "(疑似被 reset 到純上游,見 2026-07-24 事故)")
    if reasons:
        return "red", "偵測到需人工檢查的狀態,勿自動操作", reasons, "target"

    counted = [c for c in comparisons if c.get("counts_toward_overall")]
    if not counted:
        return "gray", "無可用的比較基準(找不到官方上游、備份或 Windows 整合 tip remote)", \
            ["repo 未設定可辨識的 upstream/backup/follow remote"], None

    # 較嚴重者主導;同嚴重度以 DRIVER_PRIORITY 決勝(follow > backup > upstream),
    # 不倚賴 `git remote` 的列出順序——避免主導組的指認變成偶然。
    worst = max(counted, key=lambda c: (LIGHT_SEVERITY.get(c["light"], 0),
                                        DRIVER_PRIORITY.get(c["role"], -1)))
    # 建議文字:各組語意併陳,避免「備份健康但官方有新版」被誤讀成壞掉
    order = {"follow": 0, "backup": 1, "upstream": 2}
    parts = [c["summary"] for c in sorted(counted, key=lambda c: order.get(c["role"], 9))]
    return worst["light"], "；".join(parts), reasons, worst["role"]


def _end_payload(base: dict, facts: dict, expect_custom: bool, expect_rescue: bool,
                 service: dict | None) -> dict:
    light, advice, reasons, driver = _classify_target(facts, expect_custom, expect_rescue)
    return {
        **base,
        "expect_custom": expect_custom,
        "expect_rescue": expect_rescue,
        "light": light,
        # 整體 badge 的短文字跟著主導組的 role 語意走(WSL 由 follow 驅動的橙
        # 顯示「領先/分歧於 Windows tip」而非共用表的「帶客製 diverge」);
        # driver 為 target(紅)/None 時自然落回共用表。
        "light_text": _light_text_for(driver, light),
        "advice": advice,
        "blocking_reasons": reasons,
        "overall_driver": driver,
        "service": service,
        **facts,
    }


def _probe_windows() -> dict:
    """Windows 端:直接 `git -C <repo>`;服務狀態複用 data_resident gateway 就緒。"""
    base = {"id": "windows", "label": "Windows Hermes (live gateway)",
            "runner": "git -C <windows-repo>"}
    if WINDOWS_REPO_PATH is None:
        return _end_payload(base, {"queryable": False, "repo": "(LOCALAPPDATA 未設定)",
                                   "reasons": ["LOCALAPPDATA 未設定,無法定位 Windows hermes-agent"]},
                            expect_custom=True, expect_rescue=True, service=None)
    prefix = (GIT_BIN, "-C", str(WINDOWS_REPO_PATH))
    facts = _facts_from_prefix(prefix, str(WINDOWS_REPO_PATH))
    try:
        # 含 pid 活性驗證(2026-08-04 事故修正):與常駐燈共用 data_resident 的
        # 同一份 _gateway_ready()/check_gateway_pid_liveness(),不各寫一份。
        # 狀態檔宣稱 running 但 pid 已死/被重用 → ready=False、dead=True,
        # detail 誠實寫明(此前只讀檔案內容,gateway 死了一天半仍顯示就緒)。
        gateway = data_resident._gateway_ready()
    except Exception:
        gateway = None
    service = {
        "kind": "gateway_state.json",
        "detail": gateway.get("detail") if gateway else "無法讀取",
        "ready": gateway.get("ready") if gateway else None,
        "state": gateway.get("state") if gateway else None,
        "dead": gateway.get("dead") if gateway else None,
        "pid": gateway.get("pid") if gateway else None,
        "pid_alive": gateway.get("pid_alive") if gateway else None,
    }
    return _end_payload(base, facts, expect_custom=True, expect_rescue=True, service=service)


def _probe_wsl() -> dict:
    """WSL 端:distro 未 Running 絕不下 `wsl -d` 指令(不喚醒);複用 resident 守門。"""
    base = {"id": "wsl", "label": f"WSL Hermes ({WSL_DISTRO})",
            "runner": f"wsl -d {WSL_DISTRO} --exec git -C <wsl-repo>"}
    try:
        running, distro_detail = data_resident._distro_state()
    except Exception:
        running, distro_detail = None, "distro 狀態查詢失敗"
    if running is None:
        return _end_payload(base, {"queryable": False, "repo": WSL_REPO_POSIX,
                                   "reasons": [f"WSL 無法查詢:{distro_detail}"]},
                            expect_custom=False, expect_rescue=False,
                            service={"kind": "systemd --user", "detail": "未探測(WSL 無法查詢)"})
    if not running:
        return _end_payload(base, {"queryable": False, "repo": WSL_REPO_POSIX,
                                   "reasons": [f"WSL distro {WSL_DISTRO} 未運作"
                                               f"(未下任何 wsl -d 指令,避免喚醒)"]},
                            expect_custom=False, expect_rescue=False,
                            service={"kind": "systemd --user",
                                     "detail": f"未探測(distro {WSL_DISTRO} 未運作,避免喚醒)"})

    prefix = (WSL_BIN, "-d", WSL_DISTRO, "--exec", GIT_BIN, "-C", WSL_REPO_POSIX)
    facts = _facts_from_prefix(prefix, WSL_REPO_POSIX)
    try:
        units = data_resident._probe_units()
    except Exception:
        units = None
    if units is None:
        service = {"kind": "systemd --user", "detail": "systemctl --user 查詢失敗", "units": None}
    else:
        active = [u for u in data_resident.RESIDENT_UNITS
                  if (units.get(u) or {}).get("state") == "active"]
        service = {"kind": "systemd --user",
                   "detail": f"常駐單元 active:{len(active)}/{len(data_resident.RESIDENT_UNITS)}",
                   "units": units,
                   "resident_units": list(data_resident.RESIDENT_UNITS)}
    return _end_payload(base, facts, expect_custom=False, expect_rescue=False, service=service)


def _probe() -> dict:
    """兩端並列預檢(未快取;get_update_precheck() 才是對外入口)。"""
    return {
        "checked_at": _now_iso(),
        "remote_note": "遠端資訊只讀本地已有 refs(<remote>/main),可能過期"
                       "——階段一預檢未執行 fetch",
        "stage": "唯讀升級預檢(階段一);升級/合併/同步執行未核准,無升級執行鈕;"
                 "遠端 fetch(bridge 第三群組)為唯一寫入例外,僅更新 refs",
        "targets": [_probe_windows(), _probe_wsl()],
    }


def _gray_target(target_id: str, label: str) -> dict:
    return {"id": target_id, "label": label, "light": "gray",
            "light_text": LIGHT_TEXT["gray"], "advice": "無法查詢", "queryable": False,
            "blocking_reasons": ["預檢發生未預期錯誤"], "service": None,
            "comparisons": [], "overall_driver": None}


def get_update_precheck(force: bool = False) -> dict:
    """對外唯一入口:45 秒 TTL 快取 + 全域容錯(任何未預期例外 → 兩端灰)。

    force=True(api.py 的 `?fresh=1`,2026-08-04 隨〔重新整理遠端資訊〕fetch
    按鈕引入):**繞過 TTL 立即重新探測**,並以新結果覆寫快取(之後的一般
    請求也拿到新資料)。用途:bridge 跑完四條 fetch 後,UI 重查預檢必須拿到
    fetch 後的 refs——否則 45 秒內只會看到舊快取(2026-08-04 實測撞到)。
    重新探測本身仍是純唯讀 git 查詢,force 不引入任何寫入面。"""
    global _cache
    now = time.monotonic()
    if not force and _cache is not None and now - _cache[0] < CACHE_TTL_SECONDS:
        return _cache[1]
    try:
        payload = _probe()
    except Exception:
        payload = {
            "checked_at": _now_iso(),
            "remote_note": "遠端資訊只讀本地已有 refs(<remote>/main),可能過期"
                           "——階段一預檢未執行 fetch",
            "stage": "唯讀升級預檢(階段一);升級/合併/同步執行未核准,無升級執行鈕;"
                 "遠端 fetch(bridge 第三群組)為唯一寫入例外,僅更新 refs",
            "targets": [
                _gray_target("windows", "Windows Hermes (live gateway)"),
                _gray_target("wsl", f"WSL Hermes ({WSL_DISTRO})"),
            ],
        }
    payload = redact.scan_structure(payload)
    _cache = (now, payload)
    return payload
