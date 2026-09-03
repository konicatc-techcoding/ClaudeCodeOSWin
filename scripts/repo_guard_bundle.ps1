# repo_guard_bundle.ps1 — 未推送 commit 的離線 bundle 保險（防 Install 鈕 reset --hard）
#
# 問題（STATUS.md §3 結構性弱點 / memory:hermes-agent-repo-work）:
#   Hermes 桌面「Install」鈕會對 hermes-agent repo 執行 `reset --hard origin/main`。
#   main == origin/main 時是 no-op;但只要本機有客製 commit 尚未 push 到私有備份,
#   按下去就會**靜默吃掉**（2026-07-24 已真實發生過一次）。現有防線只有升級預檢
#   （dashboard/data_update.py 的 backup 組 ahead>0 亮橙）——事後偵測,且要人主動去看。
#
# 本 script 做什麼:
#   對每個受保護 repo,算出「不被任何 remote-tracking ref 涵蓋」的本地 commit
#   （branches + tags + stash）。有暴露就打一份**增量 git bundle** 存到 repo 外的
#   固定位置,並用 `git bundle verify` 當場驗證可還原,再寫一份 manifest（含還原指令）。
#   → reset --hard 之後,repo 本身被清掉的 commit 仍能從 bundle 完整取回。
#
# 為什麼是 bundle 而不是自動 push / 攔截 Install:
#   - Install 鈕是 Hermes 桌面程式的行為,我們無法保證攔得到（沒有官方 hook 點）。
#   - 自動 push 需要網路與憑證,且失敗模式（離線、token 過期）會靜默留下暴露窗口。
#   - bundle 完全在我們控制範圍內、離線、零憑證、零觸網,且**對目標 repo 唯讀**
#     （只跑 for-each-ref / rev-parse / rev-list / status --porcelain / bundle
#     create|verify;bundle create 只寫 repo 外的輸出檔）。
#   代價:只做到「可還原」,不是「不會被吃」。故本 script 是止血層,
#   push 到私有備份仍是正解（exit=3 就是要讓排程把暴露狀態叫出來）。
#
# 唯讀鐵律:全文不存在 fetch / pull / push / merge / reset / checkout / commit /
#   stash / gc / prune 等任何會改動目標 repo 的 git 子指令。憑證檔一概不碰
#   （memory:hermes-credential-handling-safety-lessons）;工作樹髒檔只記**檔案數**,
#   不讀內容、不做 diff、不進 bundle（避免把任何內容外流到 repo 外）。
#
# 冪等:同一組暴露 ref（fingerprint 相同）不會重複打 bundle,只沿用既有快照。
# 失敗語意:任一 target 失敗 → 該 target 標 error 並停在原地印原因,其他 target
#   續跑;整體 exit=1。**絕不自動修復、絕不動目標 repo**（故也不需要 rollback）。
# 相容:Windows PowerShell 5.1（無 &&/||、無三元;native 指令一律看 $LASTEXITCODE）。
#
# 用法:
#   powershell -ExecutionPolicy Bypass -File scripts\repo_guard_bundle.ps1
#   開關:-DryRun（只算不寫）、-Json（機器可讀輸出,給 dashboard/排程用）、
#         -Keep <n>（每個 target 保留幾份,預設 10）、
#         -Targets "id=path,id2=path2"（覆寫預設清單;沙箱測試用）
#
# Exit codes:
#   0 = 全部 target 皆無暴露（安全）
#   3 = 有暴露,但已全部成功 bundle 並 verify 通過（已保險,仍該去 push）
#   1 = 有 target 失敗（含 bundle/verify 失敗）——這是唯一需要人介入的碼
param(
    [string]$Targets = "",
    [string]$StoreRoot = (Join-Path $env:LOCALAPPDATA "AgentOS\repo-guard"),
    [int]$Keep = 10,
    [switch]$DryRun,
    [switch]$Json
)

$ErrorActionPreference = "Continue"  # 一律以 $LASTEXITCODE 判定,避開 PS5.1 stderr 重導誤爆

# 允許出現在本 script 的 git 子指令白名單（唯讀 + 只寫 repo 外檔案的 bundle）。
# Invoke-Git 在程式層強制,非白名單一律 throw——防日後「順手」加上寫入指令。
$script:AllowedGitVerbs = @("rev-parse", "for-each-ref", "rev-list", "status", "bundle")
$script:Results = @()
$script:HadError = $false
$script:HadExposure = $false
$script:ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

function Write-Step { param([string]$Text) if (-not $Json) { Write-Host "`n=== $Text ===" -ForegroundColor Cyan } }
function Write-Ok { param([string]$Text) if (-not $Json) { Write-Host "  [ok] $Text" -ForegroundColor Green } }
function Write-Warn2 { param([string]$Text) if (-not $Json) { Write-Host "  [warn] $Text" -ForegroundColor Yellow } }
function Write-Err2 { param([string]$Text) if (-not $Json) { Write-Host "  [ERROR] $Text" -ForegroundColor Red } }
function Write-Info { param([string]$Text) if (-not $Json) { Write-Host "  $Text" } }

function Invoke-Git {
    # 唯一 git 位點。$GitArgs[0] 必須在白名單內,否則直接 throw。
    param([string]$RepoPath, [string[]]$GitArgs, [switch]$AllowFail)
    if ($script:AllowedGitVerbs -notcontains $GitArgs[0]) {
        throw "非白名單 git 子指令: $($GitArgs[0])（本 script 僅允許唯讀操作）"
    }
    # @(...) 不可省:PS5.1 會把單行輸出降級成 [string],之後 $out[0] 取到的是 [char]
    # （2026-09-03 沙箱實測踩到:.Trim() 在 char 上不存在 → 整個 target 誤判 error）。
    $out = @(& git -C $RepoPath @GitArgs 2>&1 | ForEach-Object { "$_" })
    $script:LastGitExit = $LASTEXITCODE
    if (($script:LastGitExit -ne 0) -and (-not $AllowFail)) {
        throw ("git " + ($GitArgs -join " ") + " 失敗(exit=" + $script:LastGitExit + "):`n" + ($out -join "`n"))
    }
    # 一律讓 return 自然 unroll;**每個呼叫端都必須用 @() 包住**（見下方註解),
    # 否則 PS5.1 會把單行輸出降級成 [string],$out[0] 取到 [char]（2026-09-03 實測踩到）。
    return $out
}

function Get-Fingerprint {
    param([string[]]$Lines)
    $joined = ($Lines | Sort-Object) -join "`n"
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = $sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($joined))
    } finally {
        $sha.Dispose()
    }
    return (($bytes | ForEach-Object { $_.ToString("x2") }) -join "")
}

function Resolve-TargetList {
    param([string]$Spec)
    $list = @()
    if ($Spec) {
        foreach ($item in ($Spec -split ",")) {
            $trimmed = $item.Trim()
            if (-not $trimmed) { continue }
            $idx = $trimmed.IndexOf("=")
            if ($idx -lt 1) { throw "Targets 格式錯誤(應為 id=path): $trimmed" }
            $list += [pscustomobject]@{
                id   = $trimmed.Substring(0, $idx).Trim()
                path = $trimmed.Substring($idx + 1).Trim()
            }
        }
        return $list
    }
    # 預設 target。hermes-agent 是唯一受 Install 鈕威脅的;ClaudeCodeOSWin 納入是因為
    # 2026-09-03 實測它累積 7 個未 push commit 擱置一週多沒人發現——同一種人為紀律失效。
    $repoRoot = Split-Path -Parent $script:ScriptDir
    $list += [pscustomobject]@{ id = "hermes-agent"; path = (Join-Path $env:LOCALAPPDATA "hermes\hermes-agent") }
    $list += [pscustomobject]@{ id = "ClaudeCodeOSWin"; path = $repoRoot }
    return $list
}

function Invoke-Target {
    param([string]$Id, [string]$Path)

    $res = [pscustomobject]@{
        id = $Id; path = $Path; status = "unknown"; exposedCommits = 0
        exposedRefs = @(); dirtyFiles = 0; bundle = $null; fingerprint = $null
        skippedReason = $null; error = $null
    }

    Write-Step "target: $Id"
    if (-not (Test-Path -LiteralPath $Path)) {
        $res.status = "error"; $res.error = "路徑不存在: $Path"
        Write-Err2 $res.error
        return $res
    }
    $inside = @(Invoke-Git -RepoPath $Path -GitArgs @("rev-parse", "--is-inside-work-tree") -AllowFail)
    if (($script:LastGitExit -ne 0) -or (($inside -join "") -notmatch "true")) {
        $res.status = "error"; $res.error = "不是 git work tree: $Path"
        Write-Err2 $res.error
        return $res
    }

    # remote 是暴露判定的基準。零 remote → 所有 commit 都算暴露,bundle 會等於整包 repo
    # （hermes 的 .git 有 528MB）,那不是本 script 的用途 → 明確跳過,不硬打。
    $remoteRefs = @(Invoke-Git -RepoPath $Path -GitArgs @("for-each-ref", "--format=%(refname)", "refs/remotes") | Where-Object { $_ })
    if ($remoteRefs.Count -eq 0) {
        $res.status = "skipped"; $res.skippedReason = "此 repo 無任何 remote-tracking ref,無法計算增量基準"
        Write-Warn2 $res.skippedReason
        return $res
    }

    # 納入保護的本地 ref:branches + tags + stash（stash 不被 reset --hard 吃掉,
    # 但同屬「只存在本機」的成果,順手一起保全,成本近乎零）。
    $revArgs = @("--branches", "--tags")
    $hasStash = $false
    Invoke-Git -RepoPath $Path -GitArgs @("rev-parse", "--verify", "--quiet", "refs/stash") -AllowFail | Out-Null
    if ($script:LastGitExit -eq 0) { $hasStash = $true; $revArgs += "refs/stash" }

    $countOut = @(Invoke-Git -RepoPath $Path -GitArgs (@("rev-list", "--count") + $revArgs + @("--not", "--remotes")))
    $exposed = 0
    if ($countOut -and $countOut[0]) { $exposed = [int]($countOut[0].Trim()) }
    $res.exposedCommits = $exposed

    $dirty = @(Invoke-Git -RepoPath $Path -GitArgs @("status", "--porcelain") | Where-Object { $_ })
    $res.dirtyFiles = $dirty.Count   # 只記數量:內容不讀、不進 bundle（憑證/敏感內容外流風險）
    if ($dirty.Count -gt 0) {
        Write-Warn2 "工作樹有 $($dirty.Count) 個未提交變更——**bundle 不涵蓋未提交內容**,reset --hard 仍會吃掉,請自行 commit 或另行保存"
    }

    if ($exposed -eq 0) {
        $res.status = "clean"
        Write-Ok "無暴露:所有本地 branch/tag/stash 的 commit 都已被 remote-tracking ref 涵蓋"
        return $res
    }

    $script:HadExposure = $true
    # 列出暴露的 ref（哪些 branch/tag 帶了 remote 沒有的 commit）
    $localRefs = @(Invoke-Git -RepoPath $Path -GitArgs @("for-each-ref", "--format=%(refname) %(objectname)", "refs/heads", "refs/tags") | Where-Object { $_ })
    if ($hasStash) {
        $stashSha = @(Invoke-Git -RepoPath $Path -GitArgs @("rev-parse", "refs/stash"))[0].Trim()
        $localRefs += ("refs/stash " + $stashSha)
    }
    $exposedRefs = @()
    foreach ($line in $localRefs) {
        $parts = $line -split " "
        $n = @(Invoke-Git -RepoPath $Path -GitArgs @("rev-list", "--count", $parts[1], "--not", "--remotes"))
        $c = 0
        if ($n -and $n[0]) { $c = [int]($n[0].Trim()) }
        if ($c -gt 0) { $exposedRefs += ("{0} (+{1})" -f $parts[0], $c) }
    }
    $res.exposedRefs = $exposedRefs
    Write-Warn2 "暴露 $exposed 個 commit,分布於: $($exposedRefs -join ', ')"

    $fp = Get-Fingerprint -Lines $localRefs
    $res.fingerprint = $fp

    $targetDir = Join-Path $StoreRoot $Id
    $latestPath = Join-Path $targetDir "_latest.json"
    if (Test-Path -LiteralPath $latestPath) {
        $prev = Get-Content -LiteralPath $latestPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($prev.fingerprint -eq $fp) {
            $bundleStill = $false
            if ($prev.bundle) { $bundleStill = Test-Path -LiteralPath $prev.bundle }
            if ($bundleStill) {
                $res.status = "already-bundled"; $res.bundle = $prev.bundle
                Write-Ok "ref 狀態與上次快照相同（fingerprint $($fp.Substring(0,12))…）,沿用既有 bundle: $($prev.bundle)"
                return $res
            }
            Write-Warn2 "fingerprint 相同但 bundle 檔已不在,重打一份"
        }
    }

    if ($DryRun) {
        $res.status = "dry-run"
        Write-Info "[dry-run] 會在 $targetDir 產生新 bundle（涵蓋上列 ref）"
        return $res
    }

    if (-not (Test-Path -LiteralPath $targetDir)) {
        New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
    }
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $bundlePath = Join-Path $targetDir ("{0}-{1}.bundle" -f $Id, $stamp)

    Invoke-Git -RepoPath $Path -GitArgs (@("bundle", "create", $bundlePath) + $revArgs + @("--not", "--remotes")) | Out-Null
    if (-not (Test-Path -LiteralPath $bundlePath)) {
        $res.status = "error"; $res.error = "bundle create 宣稱成功但檔案不存在: $bundlePath"
        Write-Err2 $res.error
        return $res
    }
    # 當場 verify:確認 bundle 自洽且 prerequisite 在本 repo 內存在 → 真的還得回來。
    $verify = @(Invoke-Git -RepoPath $Path -GitArgs @("bundle", "verify", $bundlePath) -AllowFail)
    if ($script:LastGitExit -ne 0) {
        $res.status = "error"
        $res.error = "bundle verify 失敗,保險不成立（bundle 留在 $bundlePath 供人工檢視）:`n" + ($verify -join "`n")
        Write-Err2 $res.error
        return $res
    }

    $size = (Get-Item -LiteralPath $bundlePath).Length
    $res.status = "bundled"; $res.bundle = $bundlePath
    Write-Ok ("bundle 已建立並 verify 通過: {0} ({1:N0} bytes)" -f $bundlePath, $size)

    $manifest = [pscustomobject]@{
        id = $Id; repoPath = $Path; createdAt = (Get-Date).ToString("s")
        fingerprint = $fp; bundle = $bundlePath; bundleBytes = $size
        exposedCommits = $exposed; exposedRefs = @($exposedRefs)
        dirtyFiles = $res.dirtyFiles; localRefs = @($localRefs)
        restore = @(
            "# 1) 確認 bundle 內容與前提:",
            "git -C `"$Path`" bundle verify `"$bundlePath`"",
            "# 2) 把失聯的 branch 取回成 rescued/*（不動現有 branch）:",
            "git -C `"$Path`" fetch `"$bundlePath`" `"refs/heads/*:refs/heads/rescued/*`"",
            "# 3) stash 若在 bundle 內:",
            "git -C `"$Path`" fetch `"$bundlePath`" `"refs/stash:refs/heads/rescued-stash`"",
            "# 4) 用 git log rescued/<branch> 檢視,再自行 merge/cherry-pick 回 main。"
        )
    }
    # 一律寫「UTF-8 無 BOM」:PS5.1 的 Set-Content -Encoding UTF8 會加 BOM,
    # 而 Python 的 json.load 會直接炸（2026-09-03 沙箱實測踩到）——manifest 要能被
    # dashboard/排程用 Python 讀,故改用 .NET 明確指定不含 BOM。
    $manifestJson = $manifest | ConvertTo-Json -Depth 5
    $noBom = New-Object System.Text.UTF8Encoding($false)
    $manifestPath = [System.IO.Path]::ChangeExtension($bundlePath, ".json")
    [System.IO.File]::WriteAllText($manifestPath, $manifestJson, $noBom)
    [System.IO.File]::WriteAllText($latestPath, $manifestJson, $noBom)

    # prune:只刪自己產生的 *.bundle 與同名 .json,保留最新 $Keep 份。
    $all = @(Get-ChildItem -LiteralPath $targetDir -Filter "*.bundle" -File | Sort-Object Name -Descending)
    if ($all.Count -gt $Keep) {
        foreach ($old in $all[$Keep..($all.Count - 1)]) {
            Remove-Item -LiteralPath $old.FullName -Force
            $oldJson = [System.IO.Path]::ChangeExtension($old.FullName, ".json")
            if (Test-Path -LiteralPath $oldJson) { Remove-Item -LiteralPath $oldJson -Force }
            Write-Info "prune 舊快照: $($old.Name)"
        }
    }
    return $res
}

# ---- main ----
$targetList = Resolve-TargetList -Spec $Targets
foreach ($t in $targetList) {
    try {
        $r = Invoke-Target -Id $t.id -Path $t.path
    } catch {
        $r = [pscustomobject]@{
            id = $t.id; path = $t.path; status = "error"; exposedCommits = 0
            exposedRefs = @(); dirtyFiles = 0; bundle = $null; fingerprint = $null
            skippedReason = $null; error = "$_"
        }
        Write-Err2 "$_"
    }
    if ($r.status -eq "error") { $script:HadError = $true }
    $script:Results += $r
}

$exit = 0
if ($script:HadExposure) { $exit = 3 }
if ($script:HadError) { $exit = 1 }

if ($Json) {
    [pscustomobject]@{
        generatedAt = (Get-Date).ToString("s")
        storeRoot   = $StoreRoot
        exitCode    = $exit
        targets     = $script:Results
    } | ConvertTo-Json -Depth 6
} else {
    Write-Step "總結"
    foreach ($r in $script:Results) {
        Write-Info ("{0,-18} {1,-16} exposed={2} dirty={3} {4}" -f $r.id, $r.status, $r.exposedCommits, $r.dirtyFiles, $r.bundle)
    }
    if ($exit -eq 3) {
        Write-Warn2 "有未推送 commit 已被 bundle 保全（可還原）。**這不是終點**:請盡快 push 到私有備份,讓 Install 鈕的 reset --hard 退化成 no-op。"
    }
    if ($exit -eq 1) {
        Write-Err2 "有 target 失敗——上方已印出原因。本 script 對目標 repo 唯讀,無需 rollback;修正後直接重跑同一道指令即可（冪等）。"
    }
    Write-Info "exit=$exit"
}
exit $exit
