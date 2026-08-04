# hermes_sync_wsl.ps1 — WSL 部署複本跟上 Windows 整合 tip(ff-only)
#
# 用途:Windows 側升級(hermes_apply_upgrade.ps1)完成**並 push** 後,把 WSL
#   的 hermes-agent 複本 ff 前進到同一 tip 並重裝 editable。
# 前提:
#   - WSL repo 的 origin = 本機 Windows repo(2026-07-25 re-graft 設計);
#     fetch 為本機路徑讀取,免憑證免網路。
#   - **順序不可反**:Windows 先完成 push(hermes_apply_upgrade.ps1 步驟 8)
#     再跑本 script——「未 push 即失效」弱點見 memory:hermes-agent-repo-work
#     與 docs/hermes-0191-merge-plan.md §8。
#   - WSL venv 目錄是 `venv`(**不是 .venv**——2026-08-04 實跑的坑;
#     計畫文件 §8 原文的 `.venv` 是錯的)。
#
# 行為:fetch origin → 非 ff 即停(印診斷)→ merge --ff-only origin/main →
#   venv/bin/python -m pip install -e ".[messaging]" → hermes --version 與
#   Windows 側核對。fail-loud、冪等(已同步/已同版則跳過)。
# 相容:Windows PowerShell 5.1;wsl 呼叫一律 `--exec` argv 形式
#   (不經 shell,避開 wsl.exe 引號吞噬——2026-08-04 沿用的實證作法)。
#
# 用法:
#   powershell -ExecutionPolicy Bypass -File scripts\hermes_sync_wsl.ps1
#   開關:-DryRun、-SkipInstall、-SkipVersionCompare;
#   -TestLocalRepo <path>(**僅沙箱測試用**:以本機 git 取代 wsl 執行同一套
#   git 邏輯,絕不對真實 WSL repo 動作;此模式自動略過 pip/版本核對)。
param(
    [string]$Distro = "Ubuntu",
    [string]$WslRepoPath = "/home/razer/.hermes/hermes-agent",
    [string]$WindowsRepoPath = (Join-Path $env:LOCALAPPDATA "hermes\hermes-agent"),
    [switch]$DryRun,
    [switch]$SkipInstall,
    [switch]$SkipVersionCompare,
    [string]$TestLocalRepo = ""
)

$ErrorActionPreference = "Continue"  # 以 $LASTEXITCODE 判定,避開 PS5.1 stderr 重導誤爆

function Write-Step { param([string]$Text) Write-Host "`n=== $Text ===" -ForegroundColor Cyan }
function Write-Ok { param([string]$Text) Write-Host "  [ok] $Text" -ForegroundColor Green }
function Write-SkipMsg { param([string]$Text) Write-Host "  [skip] $Text" -ForegroundColor Yellow }
function Write-Info { param([string]$Text) Write-Host "  $Text" }

function Fail {
    param([string]$Reason)
    Write-Host "`n########## WSL 同步中止(fail-loud,不自動處理) ##########" -ForegroundColor Red
    Write-Host "原因:$Reason" -ForegroundColor Red
    Write-Host "診斷(唯讀):"
    $h = Invoke-RepoGit @("rev-parse", "--short=12", "HEAD") -AllowFail
    Write-Host ("  HEAD: " + ($h -join ""))
    $s = Invoke-RepoGit @("status", "--porcelain") -AllowFail
    if ($s) { Write-Host ("  工作樹:`n" + (($s | ForEach-Object { "    " + $_ }) -join "`n")) }
    else { Write-Host "  工作樹:乾淨" }
    Write-Host "  參考:docs/hermes-0191-merge-plan.md §8(WSL ff-only 後續)"
    exit 1
}

function Invoke-RepoGit {
    # 對 WSL repo 執行 git:production 走 `wsl --exec` argv 形式(不經 shell);
    # -TestLocalRepo 時以本機 git 對沙箱 repo 執行同一套邏輯(僅測試用)。
    param([string[]]$GitArgs, [switch]$AllowFail)
    if ($TestLocalRepo) {
        $out = & git -C $TestLocalRepo @GitArgs 2>&1 | ForEach-Object { "$_" }
    } else {
        $out = & wsl.exe -d $Distro --exec git -C $WslRepoPath @GitArgs 2>&1 | ForEach-Object { "$_" }
    }
    $code = $LASTEXITCODE
    $script:LastGitExit = $code
    if (($code -ne 0) -and (-not $AllowFail)) {
        Fail ("git " + ($GitArgs -join " ") + " 失敗(exit=$code):`n" + ($out -join "`n"))
    }
    return $out
}

function Invoke-Wsl {
    # 非 git 的 WSL 指令(pip/hermes),同樣 --exec argv 形式。
    param([string[]]$CmdArgs)
    $out = & wsl.exe -d $Distro --exec @CmdArgs 2>&1 | ForEach-Object { "$_" }
    $script:LastWslExit = $LASTEXITCODE
    return $out
}

Write-Host "hermes_sync_wsl.ps1 — WSL ff-only 對齊(規格:docs/hermes-0191-merge-plan.md §8)"
if ($TestLocalRepo) { Write-Host "[TestLocalRepo] 沙箱模式:git 邏輯對 $TestLocalRepo 執行,pip/版本核對自動略過" -ForegroundColor Yellow }
if ($DryRun) { Write-Host "[DryRun] 只印計畫,不執行任何寫入" -ForegroundColor Yellow }

# --- 步驟 1:前置自檢 --------------------------------------------------------
Write-Step "步驟 1/5:前置自檢"
$null = Invoke-RepoGit @("rev-parse", "--git-dir") -AllowFail
if ($script:LastGitExit -ne 0) {
    Fail "無法讀取 repo(distro 未啟動?路徑錯?)——wsl -d $Distro --exec git -C $WslRepoPath"
}
$branch = (Invoke-RepoGit @("rev-parse", "--abbrev-ref", "HEAD")) -join ""
if ($branch -ne "main") { Fail "WSL repo 目前 branch 是 '$branch',不是 main" }
$dirty = Invoke-RepoGit @("status", "--porcelain")
if ($dirty) { Fail ("WSL 工作樹不乾淨(fail-closed):`n" + ($dirty -join "`n")) }
Write-Ok "repo 可達、在 main、工作樹乾淨"
if (-not $TestLocalRepo) {
    Write-Info "提醒:若擔心同步窗口內 timer 觸發,可先暫停 hermes 相關 systemd timer(參考 docs/wsl-regraft-plan.md Phase 2 清單);本 script 不代動服務。"
}

# --- 步驟 2:fetch origin(本機路徑,免憑證免網路)---------------------------
Write-Step "步驟 2/5:git fetch origin"
if ($DryRun) {
    Write-Host "  [DryRun] git fetch origin"
} else {
    $null = Invoke-RepoGit @("fetch", "origin")
    Write-Ok "fetch 完成(origin = 本機 Windows repo)"
}

# --- 步驟 3:ff-only merge ----------------------------------------------------
Write-Step "步驟 3/5:merge --ff-only origin/main"
$headFull = ((Invoke-RepoGit @("rev-parse", "HEAD")) -join "").Trim()
$originMain = ((Invoke-RepoGit @("rev-parse", "origin/main")) -join "").Trim()
if ($headFull -eq $originMain) {
    Write-SkipMsg ("已與 origin/main 同步(" + $headFull.Substring(0, 8) + "),跳過 merge")
} else {
    $null = Invoke-RepoGit @("merge-base", "--is-ancestor", "HEAD", $originMain) -AllowFail
    if ($script:LastGitExit -ne 0) {
        $behind = (Invoke-RepoGit @("rev-list", "--count", "HEAD..origin/main") -AllowFail) -join ""
        $ahead = (Invoke-RepoGit @("rev-list", "--count", "origin/main..HEAD") -AllowFail) -join ""
        Fail ("非 ff:WSL HEAD 領先 origin/main $ahead 個 commit(落後 $behind)。" +
              "WSL 理論上不該有 Windows 沒有的 commit——先人工釐清那些 commit 是什麼(預檢頁 follow 組會顯示橙),不自動處理。")
    }
    if ($DryRun) {
        Write-Host ("  [DryRun] git merge --ff-only origin/main(" + $headFull.Substring(0, 8) + " → " + $originMain.Substring(0, 8) + ")")
    } else {
        $null = Invoke-RepoGit @("merge", "--ff-only", "origin/main")
        $nowHead = ((Invoke-RepoGit @("rev-parse", "HEAD")) -join "").Trim()
        if ($nowHead -ne $originMain) { Fail "merge 後 HEAD 不等於 origin/main——不明狀態,人工檢查" }
        Write-Ok ("已 ff 前進:" + $headFull.Substring(0, 8) + " → " + $originMain.Substring(0, 8))
    }
}

# --- 步驟 4:pip editable 重裝(venv,不是 .venv)-----------------------------
Write-Step "步驟 4/5:pip install -e(WSL venv)"
if ($TestLocalRepo -or $SkipInstall) {
    Write-SkipMsg "沙箱模式/-SkipInstall:略過 pip"
} elseif ($DryRun) {
    Write-Host "  [DryRun] wsl --exec $WslRepoPath/venv/bin/python -m pip install -e `"$WslRepoPath[messaging]`""
} else {
    # 目標版本:repo HEAD 的 pyproject(經同一 runner 讀,零額外假設)
    $targetVersion = $null
    $py = Invoke-RepoGit @("show", "HEAD:pyproject.toml") -AllowFail
    if ($script:LastGitExit -eq 0) {
        $section = ""
        foreach ($line in $py) {
            $t = $line.Trim()
            if ($t -match '^\[(.+)\]$') { $section = $Matches[1].Trim(); continue }
            if ($section -ne "project") { continue }
            if ($t -match '^version\s*=\s*["'']([^"'']+)["'']') { $targetVersion = $Matches[1]; break }
        }
    }
    $installed = $null
    $showOut = Invoke-Wsl @(($WslRepoPath + "/venv/bin/python"), "-m", "pip", "show", "hermes-agent")
    if ($script:LastWslExit -eq 0) {
        foreach ($line in $showOut) {
            if ($line -match "^Version:\s*(.+)$") { $installed = $Matches[1].Trim() }
        }
    }
    if ($targetVersion -and ($installed -eq $targetVersion)) {
        Write-SkipMsg "WSL editable 已是 $installed(目標同版),跳過重裝"
    } else {
        Write-Info ("WSL dist-info:" + $installed + " → 目標:" + $targetVersion)
        # 注意:venv/bin(**不是 .venv**,2026-08-04 的坑);-e 用絕對路徑
        # + [messaging] 單一 argv 元素,--exec 不經 shell 無引號問題。
        $out = Invoke-Wsl @(($WslRepoPath + "/venv/bin/python"), "-m", "pip", "install", "-e", ($WslRepoPath + "[messaging]"))
        if ($script:LastWslExit -ne 0) {
            Fail ("WSL pip install -e 失敗(exit=$script:LastWslExit),末段輸出:`n" + (($out | Select-Object -Last 25) -join "`n"))
        }
        Write-Ok "WSL editable 重裝完成"
    }
}

# --- 步驟 5:版本核對(兩側 hermes --version 一致)----------------------------
Write-Step "步驟 5/5:hermes --version 兩側核對"
if ($TestLocalRepo -or $SkipVersionCompare) {
    Write-SkipMsg "沙箱模式/-SkipVersionCompare:略過版本核對"
} elseif ($DryRun) {
    Write-Host "  [DryRun] 比對 wsl hermes --version vs $WindowsRepoPath\venv\Scripts\hermes.exe --version"
} else {
    $wslVer = (Invoke-Wsl @(($WslRepoPath + "/venv/bin/hermes"), "--version")) -join " "
    if ($script:LastWslExit -ne 0) { Fail "WSL hermes --version 執行失敗:$wslVer" }
    Write-Info ("WSL     :" + $wslVer)
    $winHermes = Join-Path $WindowsRepoPath "venv\Scripts\hermes.exe"
    if (-not (Test-Path $winHermes)) {
        Fail "找不到 Windows 側 $winHermes,無法核對(可用 -SkipVersionCompare 略過,但請先想清楚)"
    }
    $winVer = (& $winHermes --version 2>&1 | ForEach-Object { "$_" }) -join " "
    if ($LASTEXITCODE -ne 0) { Fail "Windows hermes --version 執行失敗:$winVer" }
    Write-Info ("Windows :" + $winVer)
    # 比對「版本號 + local sha」兩個 token(banner 其餘字樣兩側可能有差異)
    $pattern = "v\d+\.\d+\.\d+"
    $wslV = [regex]::Match($wslVer, $pattern).Value
    $winV = [regex]::Match($winVer, $pattern).Value
    $shaPattern = "local\s+([0-9a-f]+)"
    $wslSha = [regex]::Match($wslVer, $shaPattern).Groups[1].Value
    $winSha = [regex]::Match($winVer, $shaPattern).Groups[1].Value
    if ((-not $wslV) -or ($wslV -ne $winV)) {
        Fail "版本號不一致:WSL=$wslV vs Windows=$winV"
    }
    if ($wslSha -and $winSha -and ($wslSha -ne $winSha)) {
        Fail "local sha 不一致:WSL=$wslSha vs Windows=$winSha(有一側沒跟到 tip?)"
    }
    Write-Ok ("兩側一致:" + $wslV + "(local " + $wslSha + ")")
}

Write-Host "`n=== 完成 ===" -ForegroundColor Green
Write-Host "升級預檢頁(Hermes 更新)的 WSL follow 組此時應為綠(已跟上 Windows 整合 tip)。"
exit 0
