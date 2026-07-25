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

| role     | 判定(URL)                   | 語意 |
|----------|------------------------------|------|
| upstream | 含 NousResearch/hermes-agent | 官方上游——「有沒有新版可吸收」 |
| backup   | 含 hermes-agent-private      | 私有備份/防重演基準——「本機與雲端是否同步」 |
| peer     | 其他(例如本機路徑 remote)   | 其他基準,僅供參考,不計入整體燈 |

## WSL remote 結構已變更(2026-07-25 re-graft)+ **已知燈號限制**

2026-07-25 的 WSL side re-graft(docs/wsl-regraft-plan.md,方案 A,已執行完成)
把 WSL `main` 對齊到 Windows 整合 tip `970118870`(兩側樹逐 byte 相同),
並**重排 remote**:

- 舊:`origin` = 官方 NousResearch、`windows-side` = 本機 Windows 路徑
- **新:`origin` = `/mnt/c/Users/razer/AppData/Local/hermes/hermes-agent`
  (本機 Windows 路徑)、`upstream` = 官方 NousResearch**

目的是讓 `hermes update` 的 diverged fallback(`reset --hard origin/main`)
退化成對「Windows 整合 tip」的 no-op(防重演)。

**⚠️ 由此產生的已知限制(本模組刻意不修正判定邏輯)**:

WSL 的 `origin` 現在是**本機路徑 URL**,既不含 `nousresearch/hermes-agent`
也不含 `hermes-agent-private`,故 `_role_for_url()` 判為 **`peer`**
→ `counts_toward_overall = False`。

**後果:WSL target 的「整體燈」完全由 `upstream` 組(官方)驅動,
「WSL 有沒有跟上 Windows 整合 tip」這條完全不計入燈號。**
偏偏那才是 WSL 最該被監控的一條——WSL 的正確語意就是「跟隨 Windows」,
對官方落後多少反而是與 Windows 相同的預期常態(re-graft 後兩側同為
領先 12 / 落後 295)。該組資訊仍**完整輸出**在 comparisons 內(role=`peer`,
`counts_toward_overall=False`),只是不參與燈號合成——**看得到,但不會亮燈**。

改善方向(新增一種能辨識「本機路徑且指向 Windows repo」的 role 並計入燈號)
已記在 docs/webui-update-button-proposal.md §10.1,**需獨立評估與核准**;
在那之前**本模組的角色判定與燈號邏輯維持原樣不動**,此處僅作說明。

`<remote>/main` ref 不存在(未 fetch 過/無該 remote)→ 該組
applicable=False「不適用/無法查詢」,不噴例外、不計入整體燈。

## 遠端資訊只讀本地已有 refs(提案 §3.1 待拍板項 5 的預設)

所有 ahead/behind/ff 一律相對**本地已有的 `<remote>/main`**計算——
**不執行 fetch**(有網路副作用)。輸出明確標示「遠端資訊可能過期,未 fetch」。

## 兩端並列(提案 §4,處理方式不對稱)

- **Windows**:%LOCALAPPDATA%\\hermes\\hermes-agent,直接 `git -C <repo>`。
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
  「WSL remote 結構已變更 + 已知燈號限制」——**由 upstream 組獨力驅動**。

## 燈號(每組一盞 + 整體取較嚴重者)

每組(comparison)依 role 給光與建議:

| light  | upstream 組 | backup 組 |
|--------|-------------|-----------|
| green  | 無落後 = 已吸收官方最新 | 0/0 = 備份同步(防重演基準有效)|
| blue   | 落後 N 且無客製分歧 = 可 ff-only 前進 | 備份領先本機,可 ff-only 前進 |
| orange | 落後 N 且同時領先(帶客製)= diverged,**必須受控 merge、不可自動** | 本機領先備份(客製未進備份)或雙向分歧 |
| gray   | 該 remote 不適用/ref 不存在/查詢失敗 | 同左 |

**整體燈** = 目標端層級的紅(工作樹髒／客製遺失／rescue 遺失)優先,否則取
role ∈ {upstream, backup} 兩組中**較嚴重者**;並以 `overall_driver` 標示是
哪一組造成的,避免「備份健康但官方有新版」被誤讀成壞掉。

容錯:任何探測失敗一律優雅退化為 gray,不噴例外。快取 45 秒 TTL。
"""
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import data_resident  # 複用:distro 無喚醒守門、WSL 單元探測、Windows gateway 就緒
import redact  # 輸出前掃描共用正本(慣例統一過;本模組無憑證資料,防禦性)

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

# remote 名嚴格驗證:字母數字開頭,只允許字母數字與 . _ - ——
# 不得含 `-` 開頭/`/`/空白 → 技術上無法注入旗標、路徑或額外參數。
REMOTE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")

# --- 角色判定(依 remote URL,不依名稱——兩端 remote 命名不同)-------------
OFFICIAL_UPSTREAM_MARKER = "nousresearch/hermes-agent"  # 官方上游
PRIVATE_BACKUP_MARKER = "hermes-agent-private"          # 私有備份/防重演基準

ROLE_LABEL = {
    "upstream": "官方上游(有無新版可吸收)",
    "backup": "私有備份/防重演基準(本機與雲端是否同步)",
    "peer": "其他基準(僅供參考)",
}

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

FROZEN_GIT_TEMPLATES = frozenset({
    GIT_HEAD_SHORT, GIT_BRANCH, GIT_DESCRIBE, GIT_PORCELAIN, GIT_REMOTE_LIST, GIT_REFS,
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


REF_TEMPLATE_BUILDERS = (
    _t_remote_url, _t_tip, _t_behind, _t_ahead, _t_ancestor, _t_diverge_log,
)

# 白名單子指令(提案 §3.1 列舉集)。凍結模板與建構器產出的每一條,args[0] 都在
# 此集合內。刻意不寫任何寫入子指令的字面值(靜態測試鎖定)。
ALLOWED_GIT_SUBCOMMANDS = frozenset({
    "rev-parse", "rev-list", "merge-base", "for-each-ref",
    "describe", "branch", "log", "status", "remote",
})

LIGHT_TEXT = {
    "green": "已是最新",
    "blue": "可 ff-only 前進",
    "orange": "帶客製 diverge——需人工受控 merge",
    "red": "異常——需人工檢查",
    "gray": "無法查詢",
}

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


def _role_for_url(url: str | None) -> str:
    """依 remote URL 判定角色(不依名稱——兩端 remote 命名不同)。"""
    low = (url or "").lower()
    if PRIVATE_BACKUP_MARKER in low:
        return "backup"
    if OFFICIAL_UPSTREAM_MARKER in low:
        return "upstream"
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
        "counts_toward_overall": role in ("upstream", "backup"),
    }
    if tip is None:
        return {
            **base, "applicable": False, "behind": None, "ahead": None,
            "can_ff": None, "diverged": None, "diverge_commits": [],
            "light": "gray", "light_text": LIGHT_TEXT["gray"],
            "summary": f"不適用/無法查詢:本地無 {ref} ref(未 fetch 過此 remote)",
        }
    behind = _to_count(_ok_str(_run_git_remote(prefix, _t_behind, remote)))
    ahead = _to_count(_ok_str(_run_git_remote(prefix, _t_ahead, remote)))
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
        "diverge_commits": diverge_commits,
        "light": light, "light_text": LIGHT_TEXT[light], "summary": summary,
    }


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

    return {
        "queryable": True,
        "repo": repo_display,
        "head": {"short": head_short, "describe": describe, "branch": branch},
        "dirty": dirty,
        "rescue_refs": rescue,
        "rescue_count": len(rescue),
        "remotes": remotes,
        "comparisons": comparisons,
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
    否則取 role ∈ {upstream, backup} 兩組中較嚴重者。"""
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
        return "gray", "無可用的比較基準(找不到官方上游或備份 remote)", \
            ["repo 未設定可辨識的 upstream/backup remote"], None

    worst = max(counted, key=lambda c: LIGHT_SEVERITY.get(c["light"], 0))
    # 建議文字:兩組語意併陳,避免「備份健康但官方有新版」被誤讀成壞掉
    order = {"backup": 0, "upstream": 1}
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
        "light_text": LIGHT_TEXT[light],
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
        gateway = data_resident._gateway_ready()
    except Exception:
        gateway = None
    service = {
        "kind": "gateway_state.json",
        "detail": gateway.get("detail") if gateway else "無法讀取",
        "ready": gateway.get("ready") if gateway else None,
        "state": gateway.get("state") if gateway else None,
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
        "stage": "唯讀升級預檢(階段一);寫入/執行功能未核准,無任何執行鈕",
        "targets": [_probe_windows(), _probe_wsl()],
    }


def _gray_target(target_id: str, label: str) -> dict:
    return {"id": target_id, "label": label, "light": "gray",
            "light_text": LIGHT_TEXT["gray"], "advice": "無法查詢", "queryable": False,
            "blocking_reasons": ["預檢發生未預期錯誤"], "service": None,
            "comparisons": [], "overall_driver": None}


def get_update_precheck() -> dict:
    """對外唯一入口:45 秒 TTL 快取 + 全域容錯(任何未預期例外 → 兩端灰)。"""
    global _cache
    now = time.monotonic()
    if _cache is not None and now - _cache[0] < CACHE_TTL_SECONDS:
        return _cache[1]
    try:
        payload = _probe()
    except Exception:
        payload = {
            "checked_at": _now_iso(),
            "remote_note": "遠端資訊只讀本地已有 refs(<remote>/main),可能過期"
                           "——階段一預檢未執行 fetch",
            "stage": "唯讀升級預檢(階段一);寫入/執行功能未核准,無任何執行鈕",
            "targets": [
                _gray_target("windows", "Windows Hermes (live gateway)"),
                _gray_target("wsl", f"WSL Hermes ({WSL_DISTRO})"),
            ],
        }
    payload = redact.scan_structure(payload)
    _cache = (now, payload)
    return payload
