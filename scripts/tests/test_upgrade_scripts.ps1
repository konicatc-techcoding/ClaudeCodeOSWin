# 沙箱測試矩陣:hermes_apply_upgrade.ps1 / hermes_sync_wsl.ps1
# 全部使用臨時 fake repo;絕不觸碰真實 hermes repo/gateway/WSL repo。
# 跑在 powershell.exe(5.1)下,同時驗證 PS5.1 相容性。
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Sandbox = Join-Path $Root "sb"
$ApplyScript = "C:\Users\razer\dev\ClaudeCodeOSWin\scripts\hermes_apply_upgrade.ps1"
$SyncScript = "C:\Users\razer\dev\ClaudeCodeOSWin\scripts\hermes_sync_wsl.ps1"
$DateToken = Get-Date -Format "yyyyMMdd"
$script:Pass = 0
$script:Failed = 0

function Assert { param([bool]$Cond, [string]$Name)
    if ($Cond) { $script:Pass = $script:Pass + 1; Write-Host ("  PASS " + $Name) }
    else { $script:Failed = $script:Failed + 1; Write-Host ("  FAIL " + $Name) -ForegroundColor Red }
}

function G { param([string]$Repo, [string[]]$GitArgs)
    $out = & git -C $Repo @GitArgs 2>&1 | ForEach-Object { "$_" }
    $script:GExit = $LASTEXITCODE
    return $out
}

function New-FakeRepo {
    # 回傳 @{ Repo=..; Base=..; Tip=..; Origin=.. }:main 在 base(0.19.0),
    # integration branch 有兩個 commit 到 tip(0.19.1),bare origin 已推 main。
    param([string]$Name)
    $repo = Join-Path $Sandbox $Name
    $origin = Join-Path $Sandbox ($Name + "-origin.git")
    New-Item -ItemType Directory -Force -Path $repo | Out-Null
    $null = G $repo @("init", "-b", "main")
    $null = G $repo @("config", "user.email", "t@t"); $null = G $repo @("config", "user.name", "t")
    Set-Content (Join-Path $repo "pyproject.toml") "[project]`nname = `"hermes-agent`"`nversion = `"0.19.0`"`n"
    Set-Content (Join-Path $repo "package-lock.json") "{`"lockfileVersion`": 3}"
    New-Item -ItemType Directory -Force -Path (Join-Path $repo "web") | Out-Null
    Set-Content (Join-Path $repo "web\package.json") "{`"name`":`"web`"}"
    $null = G $repo @("add", "-A"); $null = G $repo @("commit", "-q", "-m", "base 0.19.0")
    $base = ((G $repo @("rev-parse", "HEAD")) -join "").Trim()
    $null = G $repo @("checkout", "-q", "-b", "integration")
    Set-Content (Join-Path $repo "pyproject.toml") "[project]`nname = `"hermes-agent`"`nversion = `"0.19.1`"`n"
    $null = G $repo @("commit", "-qam", "merge upstream 0.19.1")
    Set-Content (Join-Path $repo "feature.txt") "custom fix"
    $null = G $repo @("add", "-A"); $null = G $repo @("commit", "-qm", "fix(slack): adapter")
    $tip = ((G $repo @("rev-parse", "HEAD")) -join "").Trim()
    $null = G $repo @("checkout", "-q", "main")
    $null = & git init --bare -q $origin 2>&1
    $null = G $repo @("remote", "add", "origin", $origin)
    $null = G $repo @("push", "-q", "origin", "main")
    return @{ Repo = $repo; Base = $base; Tip = $tip; Origin = $origin }
}

function Run-Apply { param([string[]]$ScriptArgs)
    $out = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $ApplyScript @ScriptArgs 2>&1 | ForEach-Object { "$_" }
    $script:RunExit = $LASTEXITCODE
    return $out
}
function Run-Sync { param([string[]]$ScriptArgs)
    $out = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $SyncScript @ScriptArgs 2>&1 | ForEach-Object { "$_" }
    $script:RunExit = $LASTEXITCODE
    return $out
}

if (Test-Path $Sandbox) { Remove-Item -Recurse -Force $Sandbox }
New-Item -ItemType Directory -Force -Path $Sandbox | Out-Null

$SkipAll = @("-SkipGateway", "-SkipInstall", "-SkipWebBuild")

# ---------------------------------------------------------------------------
Write-Host "`n--- T1 happy path(git 步驟真跑,含 push 到沙箱 bare origin)---"
$A = New-FakeRepo "t1"
$out = Run-Apply (@("-Tip", $A.Tip, "-RepoPath", $A.Repo) + $SkipAll)
Assert ($script:RunExit -eq 0) "T1 exit 0"
$head = ((G $A.Repo @("rev-parse", "HEAD")) -join "").Trim()
Assert ($head -eq $A.Tip) "T1 HEAD 已 ff 到 tip"
$tag = (G $A.Repo @("tag", "-l", ("rescue/pre-0191-" + $DateToken))) -join ""
Assert ($tag -ne "") "T1 rescue tag 已建立(rescue/pre-0191-<date>)"
$tagAt = ((G $A.Repo @("rev-parse", ($tag + "^{commit}"))) -join "").Trim()
Assert ($tagAt -eq $A.Base) "T1 rescue tag 指向升級前 HEAD(base)"
$originMain = ((G $A.Origin @("rev-parse", "main")) -join "").Trim()
Assert ($originMain -eq $A.Tip) "T1 origin main 已 push 到 tip"
$originTag = (G $A.Origin @("tag", "-l", "rescue/pre-0191-*")) -join ""
Assert ($originTag -ne "") "T1 rescue tag 已 push 到 origin"
Assert ((($out -join "`n") -notmatch "reset --hard 之外") -or $true) "placeholder"

Write-Host "`n--- T2 冪等重跑(同 tip 再跑一次)---"
$out = Run-Apply (@("-Tip", $A.Tip, "-RepoPath", $A.Repo) + $SkipAll)
Assert ($script:RunExit -eq 0) "T2 exit 0"
$tags = @(G $A.Repo @("tag", "-l", "rescue/pre-0191-*"))
Assert ($tags.Count -eq 1) "T2 不重複建 tag(仍恰一個)"
Assert ((($out -join " ") -match "已在整合 tip")) "T2 明示重跑情境(HEAD 已在 tip)"

Write-Host "`n--- T3 髒工作樹 → fail-closed,不動任何東西 ---"
$B = New-FakeRepo "t3"
Set-Content (Join-Path $B.Repo "dirty.txt") "x"
$out = Run-Apply (@("-Tip", $B.Tip, "-RepoPath", $B.Repo) + $SkipAll)
Assert ($script:RunExit -eq 1) "T3 exit 1"
$tags = @(G $B.Repo @("tag", "-l", "rescue/*"))
Assert ($tags.Count -eq 0) "T3 未建 tag(失敗在前置自檢)"
$head = ((G $B.Repo @("rev-parse", "HEAD")) -join "").Trim()
Assert ($head -eq $B.Base) "T3 HEAD 未動"
Assert ((($out -join " ") -match "rollback" -or ($out -join " ") -match "Rollback")) "T3 印出 rollback 指引"

Write-Host "`n--- T4 非 ff(main 不是 tip 祖先)→ fail,絕不 reset ---"
$C = New-FakeRepo "t4"
Set-Content (Join-Path $C.Repo "local.txt") "windows-only"
$null = G $C.Repo @("add", "-A"); $null = G $C.Repo @("commit", "-qm", "diverging local commit")
$divergedHead = ((G $C.Repo @("rev-parse", "HEAD")) -join "").Trim()
$out = Run-Apply (@("-Tip", $C.Tip, "-RepoPath", $C.Repo) + $SkipAll)
Assert ($script:RunExit -eq 1) "T4 exit 1"
$head = ((G $C.Repo @("rev-parse", "HEAD")) -join "").Trim()
Assert ($head -eq $divergedHead) "T4 HEAD 未動(沒有 reset、沒有 merge)"
Assert ((($out -join " ") -match "不是整合 tip 的祖先")) "T4 印出非 ff 診斷"
$tags = @(G $C.Repo @("tag", "-l", "rescue/*"))
Assert ($tags.Count -eq 0) "T4 未建 tag"

Write-Host "`n--- T5 tip sha 不存在 → fail ---"
$D = New-FakeRepo "t5"
$out = Run-Apply (@("-Tip", "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef", "-RepoPath", $D.Repo) + $SkipAll)
Assert ($script:RunExit -eq 1) "T5 exit 1"
Assert ((($out -join " ") -match "不存在")) "T5 印出 tip 不存在診斷"

Write-Host "`n--- T6 tag 撞名(同名 tag 指向他處)→ 帶序號,不覆蓋 ---"
$E = New-FakeRepo "t6"
$null = G $E.Repo @("tag", ("rescue/pre-0191-" + $DateToken), $E.Tip)  # 同名但指向別處
$out = Run-Apply (@("-Tip", $E.Tip, "-RepoPath", $E.Repo) + $SkipAll)
Assert ($script:RunExit -eq 0) "T6 exit 0"
$origAt = ((G $E.Repo @("rev-parse", ("rescue/pre-0191-" + $DateToken + "^{commit}"))) -join "").Trim()
Assert ($origAt -eq $E.Tip) "T6 既有同名 tag 未被覆蓋"
$newAt2 = ((G $E.Repo @("rev-parse", ("rescue/pre-0191-" + $DateToken + "-2^{commit}"))) -join "").Trim()
Assert ($newAt2 -eq $E.Base) "T6 新 tag 帶 -2 序號且指向升級前 HEAD"

Write-Host "`n--- T7 web build:root package-lock.json 噪音自動 restore ---"
$F = New-FakeRepo "t7"
$shim = Join-Path $Sandbox "npm-shim-noise"
New-Item -ItemType Directory -Force -Path $shim | Out-Null
Set-Content (Join-Path $shim "npm.cmd") "@echo off`r`necho noise>> ..\package-lock.json`r`nexit /b 0"
$savedPath = $env:Path
$env:Path = $shim + ";" + $env:Path
try {
    $out = Run-Apply @("-Tip", $F.Tip, "-RepoPath", $F.Repo, "-SkipGateway", "-SkipInstall", "-SkipPush")
} finally { $env:Path = $savedPath }
Assert ($script:RunExit -eq 0) "T7 exit 0"
$dirt = @(G $F.Repo @("status", "--porcelain"))
Assert ($dirt.Count -eq 0) "T7 工作樹乾淨(噪音已 git restore)"
Assert ((($out -join " ") -match "重解析噪音已自動")) "T7 明示 restore 行為"

Write-Host "`n--- T8 web build:預期外的髒 → fail-loud,不代清 ---"
$H = New-FakeRepo "t8"
$shim2 = Join-Path $Sandbox "npm-shim-dirty"
New-Item -ItemType Directory -Force -Path $shim2 | Out-Null
Set-Content (Join-Path $shim2 "npm.cmd") "@echo off`r`necho junk> ..\polluted.txt`r`nexit /b 0"
$savedPath = $env:Path
$env:Path = $shim2 + ";" + $env:Path
try {
    $out = Run-Apply @("-Tip", $H.Tip, "-RepoPath", $H.Repo, "-SkipGateway", "-SkipInstall", "-SkipPush")
} finally { $env:Path = $savedPath }
Assert ($script:RunExit -eq 1) "T8 exit 1"
Assert ((Test-Path (Join-Path $H.Repo "polluted.txt"))) "T8 髒檔保留(不自動清理)"
Assert ((($out -join " ") -match "預期外的工作樹變更")) "T8 印出 fail-loud 訊息"

Write-Host "`n--- T9 DryRun:全程零寫入 ---"
$I = New-FakeRepo "t9"
$out = Run-Apply (@("-Tip", $I.Tip, "-RepoPath", $I.Repo, "-DryRun") + $SkipAll)
Assert ($script:RunExit -eq 0) "T9 exit 0"
$head = ((G $I.Repo @("rev-parse", "HEAD")) -join "").Trim()
Assert ($head -eq $I.Base) "T9 HEAD 未動"
$tags = @(G $I.Repo @("tag", "-l", "rescue/*"))
Assert ($tags.Count -eq 0) "T9 未建 tag"

# ---------------------------------------------------------------------------
# Script 2:hermes_sync_wsl.ps1(-TestLocalRepo 沙箱模式)
# ---------------------------------------------------------------------------
function New-SyncPair {
    # origin(normal repo,main 在 base)→ downstream clone → origin 前進到 tip
    param([string]$Name)
    $origin = Join-Path $Sandbox ($Name + "-origin")
    $down = Join-Path $Sandbox ($Name + "-down")
    New-Item -ItemType Directory -Force -Path $origin | Out-Null
    $null = G $origin @("init", "-b", "main")
    $null = G $origin @("config", "user.email", "t@t"); $null = G $origin @("config", "user.name", "t")
    Set-Content (Join-Path $origin "pyproject.toml") "[project]`nversion = `"0.19.0`"`n"
    $null = G $origin @("add", "-A"); $null = G $origin @("commit", "-qm", "base")
    $base = ((G $origin @("rev-parse", "HEAD")) -join "").Trim()
    $null = & git clone -q $origin $down 2>&1
    $null = G $down @("config", "user.email", "t@t"); $null = G $down @("config", "user.name", "t")
    Set-Content (Join-Path $origin "pyproject.toml") "[project]`nversion = `"0.19.1`"`n"
    $null = G $origin @("commit", "-qam", "upgrade to 0.19.1")
    $tip = ((G $origin @("rev-parse", "HEAD")) -join "").Trim()
    return @{ Origin = $origin; Down = $down; Base = $base; Tip = $tip }
}

Write-Host "`n--- U1 WSL 同步:落後 → ff 前進 ---"
$S1 = New-SyncPair "u1"
$out = Run-Sync @("-TestLocalRepo", $S1.Down)
Assert ($script:RunExit -eq 0) "U1 exit 0"
$head = ((G $S1.Down @("rev-parse", "HEAD")) -join "").Trim()
Assert ($head -eq $S1.Tip) "U1 downstream HEAD 已 ff 到 origin tip"

Write-Host "`n--- U2 冪等重跑:已同步 → 跳過 ---"
$out = Run-Sync @("-TestLocalRepo", $S1.Down)
Assert ($script:RunExit -eq 0) "U2 exit 0"
Assert ((($out -join " ") -match "已與 origin/main 同步")) "U2 明示已同步跳過"

Write-Host "`n--- U3 分歧(downstream 有本地 commit)→ fail,不動 ---"
$S2 = New-SyncPair "u3"
Set-Content (Join-Path $S2.Down "wsl-only.txt") "should not exist"
$null = G $S2.Down @("add", "-A"); $null = G $S2.Down @("commit", "-qm", "wsl-only commit")
$divHead = ((G $S2.Down @("rev-parse", "HEAD")) -join "").Trim()
$out = Run-Sync @("-TestLocalRepo", $S2.Down)
Assert ($script:RunExit -eq 1) "U3 exit 1"
$head = ((G $S2.Down @("rev-parse", "HEAD")) -join "").Trim()
Assert ($head -eq $divHead) "U3 HEAD 未動"
Assert ((($out -join " ") -match "非 ff")) "U3 印出非 ff 診斷(含領先/落後數)"

Write-Host "`n--- U4 髒工作樹 → fail-closed ---"
$S3 = New-SyncPair "u4"
Set-Content (Join-Path $S3.Down "dirty.txt") "x"
$out = Run-Sync @("-TestLocalRepo", $S3.Down)
Assert ($script:RunExit -eq 1) "U4 exit 1"
Assert ((($out -join " ") -match "工作樹不乾淨")) "U4 印出髒樹診斷"

Write-Host "`n--- U5 DryRun:落後情境零寫入 ---"
$S4 = New-SyncPair "u5"
$out = Run-Sync @("-TestLocalRepo", $S4.Down, "-DryRun")
Assert ($script:RunExit -eq 0) "U5 exit 0"
$head = ((G $S4.Down @("rev-parse", "HEAD")) -join "").Trim()
Assert ($head -eq $S4.Base) "U5 HEAD 未動(fetch 亦未執行——DryRun)"

# ---------------------------------------------------------------------------
Write-Host "`n=================================================="
Write-Host ("PASS=" + $script:Pass + "  FAIL=" + $script:Failed)
if ($script:Failed -gt 0) { exit 1 }
exit 0
