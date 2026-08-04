# hermes_apply_upgrade.ps1 — Hermes live 升級「機械尾段」固化 script(Windows 側)
#
# 用途:把「整合 tip 已備妥」之後的 live 切換全程(rescue tag → 停 gateway →
#       ff merge → 重裝 → web build → 重啟 → S7 驗證 → push)固化成一支
#       fail-closed、冪等、可中斷重跑的 script。
#
# 前提(不由本 script 產生,執行前必須已成立):
#   - 整合 tip 來自 engineering domain 的隔離 worktree 受控 merge,且已經
#     使用者核准(範例:v0.19.1 的 integration/v0.19.1-custom → aa65ff286)。
#   - 本 script 只做 ff 前進:main 必須是 <Tip> 的祖先,否則前置自檢即失敗。
#     **全文不存在 reset**——結構上不可能吃掉客製 commit。
#
# 規格正本:docs/hermes-0191-merge-plan.md §5(runbook)/§6(rollback)/§7(push),
#   含 2026-08-04 首跑實證的坑:root package-lock.json 重解析噪音(自動
#   git restore)、gateway 狀態檔 ~3.5 分鐘才寫(驗證等待)、extra pins
#   (starlette/python-multipart/mcp)版本特定不可硬編(讀 hermes_extra_pins.txt)。
#
# 失敗語意:任一步失敗 → 停在原地,印出當前狀態與 rollback 指令
#   (引用計畫文件 §6),**絕不自動回滾**。
# 冪等:中途失敗修好後重跑,已完成步驟正確跳過(tag 已在/已在 tip/pip 已裝/
#   gateway 已停或已在跑等)。
# 相容:Windows PowerShell 5.1(無 &&/||、無三元運算子;native 指令一律以
#   $LASTEXITCODE 判定,不倚賴 ErrorActionPreference)。
#
# 用法:
#   powershell -ExecutionPolicy Bypass -File scripts\hermes_apply_upgrade.ps1 -Tip <sha>
#   常用開關:-DryRun(全程只印不動)、-SkipSlackTests(略過 S7 兩項 Slack 實測,
#   預設執行)、-SkipGateway/-SkipInstall/-SkipWebBuild/-SkipPush(分段重跑/
#   沙箱測試用;production 正常跑不帶)。
param(
    [Parameter(Mandatory = $true)]
    [string]$Tip,
    [string]$RepoPath = (Join-Path $env:LOCALAPPDATA "hermes\hermes-agent"),
    [string]$ExtraPinsFile = "",  # 空 = <script 所在目錄>\hermes_extra_pins.txt(PS5.1 的 $PSScriptRoot 在 param 階段為空,只能在本文內解析)
    [string]$SlackTestChannel = "C0BHZC2EG84",
    [string]$SlackNegativeChannel = "C0NOTALLOWED000",
    [int]$GatewayReadySeconds = 300,
    [switch]$DryRun,
    [switch]$SkipSlackTests,
    [switch]$SkipGateway,
    [switch]$SkipInstall,
    [switch]$SkipWebBuild,
    [switch]$SkipPush
)

$ErrorActionPreference = "Continue"  # 一律以 $LASTEXITCODE 判定,避免 PS5.1 stderr 重導誤爆
if (-not $ExtraPinsFile) {
    $ExtraPinsFile = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "hermes_extra_pins.txt"
}
$script:RescueTag = $null
$script:TipFull = $null
$script:PlanDoc = "docs/hermes-0191-merge-plan.md"

function Write-Step { param([string]$Text) Write-Host "`n=== $Text ===" -ForegroundColor Cyan }
function Write-Ok { param([string]$Text) Write-Host "  [ok] $Text" -ForegroundColor Green }
function Write-SkipMsg { param([string]$Text) Write-Host "  [skip] $Text" -ForegroundColor Yellow }
function Write-Info { param([string]$Text) Write-Host "  $Text" }

function Invoke-Git {
    # 唯一 git 位點:回傳輸出字串陣列;非零 exit 由呼叫端決定(-AllowFail)。
    param([string[]]$GitArgs, [switch]$AllowFail)
    $out = & git -C $RepoPath @GitArgs 2>&1 | ForEach-Object { "$_" }
    $code = $LASTEXITCODE
    if (($code -ne 0) -and (-not $AllowFail)) {
        Fail ("git " + ($GitArgs -join " ") + " 失敗(exit=$code):`n" + ($out -join "`n"))
    }
    $script:LastGitExit = $code
    return $out
}

function Get-GatewayStateSummary {
    $statePath = Join-Path (Split-Path $RepoPath -Parent) "gateway_state.json"
    if (-not (Test-Path $statePath)) { return "gateway_state.json 不存在($statePath)" }
    try {
        $raw = Get-Content $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
        $alive = $false
        if ($raw.pid) { $alive = ($null -ne (Get-Process -Id $raw.pid -ErrorAction SilentlyContinue)) }
        return ("state=" + $raw.gateway_state + " pid=" + $raw.pid + " pid存活=" + $alive + " updated_at=" + $raw.updated_at)
    } catch { return "gateway_state.json 無法解析" }
}

function Fail {
    param([string]$Reason)
    Write-Host "`n########## 升級中止(fail-loud,絕不自動回滾) ##########" -ForegroundColor Red
    Write-Host "原因:$Reason" -ForegroundColor Red
    Write-Host "`n--- 當前狀態 ---"
    $head = & git -C $RepoPath rev-parse --short=12 HEAD 2>&1 | ForEach-Object { "$_" }
    Write-Host ("HEAD: " + ($head -join ""))
    $st = & git -C $RepoPath status --porcelain 2>&1 | ForEach-Object { "$_" }
    if ($st) { Write-Host ("工作樹:`n" + ($st -join "`n")) } else { Write-Host "工作樹:乾淨" }
    Write-Host ("gateway:" + (Get-GatewayStateSummary))
    if ($script:RescueTag) { Write-Host ("rescue tag:" + $script:RescueTag) }
    Write-Host "`n--- Rollback(人工判斷後執行;正本 $script:PlanDoc §6)---"
    $tagRef = $script:RescueTag
    if (-not $tagRef) { $tagRef = "<rescue-tag(本次尚未建立,見 git tag -l 'rescue*')>" }
    Write-Host "  venv\Scripts\hermes.exe gateway stop"
    Write-Host "  git reset --hard $tagRef      # rollback 才用 reset;本 script 本身絕不執行"
    Write-Host "  venv\Scripts\python.exe -m pip install -e `".[messaging]`""
    Write-Host "  cd web; npm install; npm run build; cd .."
    Write-Host "  venv\Scripts\hermes.exe gateway start"
    Write-Host "#######################################################"
    exit 1
}

function Get-TipProjectVersion {
    # 從 <tip> 的 pyproject.toml 讀 [project] version(段落感知,不裝 toml 解析器)
    $lines = Invoke-Git @("show", ($script:TipFull + ":pyproject.toml")) -AllowFail
    if ($script:LastGitExit -ne 0) { return $null }
    $section = ""
    foreach ($line in $lines) {
        $t = $line.Trim()
        if ($t -match '^\[(.+)\]$') { $section = $Matches[1].Trim(); continue }
        if ($section -ne "project") { continue }
        if ($t -match '^version\s*=\s*["'']([^"'']+)["'']') { return $Matches[1] }
    }
    return $null
}

function Get-GatewayRunProcesses {
    # 唯讀盤點:command line 含 hermes + gateway + run 的 python/hermes 進程
    $procs = @()
    try {
        $all = Get-CimInstance Win32_Process -ErrorAction Stop
        foreach ($p in $all) {
            $cl = $p.CommandLine
            if (-not $cl) { continue }
            if (($cl -match "gateway") -and ($cl -match "\brun\b") -and ($cl -match "hermes")) {
                $procs += $p
            }
        }
    } catch {
        Write-Info "(無法枚舉進程:$($_.Exception.Message)——以 gateway_state.json 為準)"
    }
    return $procs
}

function Invoke-Native {
    # 非 git 的 native 指令(hermes/pip/npm):回傳輸出,exit code 存 $script:LastNativeExit
    param([string]$Exe, [string[]]$NativeArgs, [string]$Cwd)
    if ($Cwd) { Push-Location $Cwd }
    try {
        $out = & $Exe @NativeArgs 2>&1 | ForEach-Object { "$_" }
        $script:LastNativeExit = $LASTEXITCODE
    } finally {
        if ($Cwd) { Pop-Location }
    }
    return $out
}

# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

Write-Host "hermes_apply_upgrade.ps1 — live 升級機械尾段(規格:$script:PlanDoc §5-§7)"
Write-Host ("RepoPath=" + $RepoPath)
Write-Host ("Tip=" + $Tip)
if ($DryRun) { Write-Host "[DryRun] 全程只印計畫,不執行任何寫入" -ForegroundColor Yellow }

# --- 步驟 1:前置自檢(fail-closed,全過才動手)------------------------------
Write-Step "步驟 1/8:前置自檢"
if (-not (Test-Path $RepoPath)) { Fail "RepoPath 不存在:$RepoPath" }
$null = Invoke-Git @("rev-parse", "--git-dir")
$branch = (Invoke-Git @("rev-parse", "--abbrev-ref", "HEAD")) -join ""
if ($branch -ne "main") { Fail "目前 branch 是 '$branch',不是 main——live 切換只在 main 上執行" }

$resolved = Invoke-Git @("rev-parse", "--verify", "--quiet", ($Tip + "^{commit}")) -AllowFail
if ($script:LastGitExit -ne 0) {
    Fail "整合 tip '$Tip' 不存在於本 repo——確認 engineering worktree 的整合 branch 已在同一 object database(worktree 共用 .git 即可見)"
}
$script:TipFull = ($resolved -join "").Trim()
$tipShort = $script:TipFull.Substring(0, 8)

$dirty = Invoke-Git @("status", "--porcelain")
if ($dirty) { Fail ("工作樹不乾淨,先處理再跑(fail-closed):`n" + ($dirty -join "`n")) }

$headFull = ((Invoke-Git @("rev-parse", "HEAD")) -join "").Trim()
$alreadyAtTip = ($headFull -eq $script:TipFull)
if ($alreadyAtTip) {
    Write-Ok "HEAD 已在整合 tip $tipShort(重跑情境)——merge/tag 步驟將跳過"
} else {
    $null = Invoke-Git @("merge-base", "--is-ancestor", "HEAD", $script:TipFull) -AllowFail
    if ($script:LastGitExit -ne 0) {
        $behind = (Invoke-Git @("rev-list", "--count", ("HEAD.." + $script:TipFull)) -AllowFail) -join ""
        $ahead = (Invoke-Git @("rev-list", "--count", ($script:TipFull + "..HEAD")) -AllowFail) -join ""
        Fail ("main 不是整合 tip 的祖先(非 ff 前進;HEAD 領先 $ahead / 落後 $behind)。" +
              "本 script 結構上只做 ff,不會 reset——請回 engineering 重新整合。")
    }
    Write-Ok "ff 前進條件成立:main 是 $tipShort 的祖先"
}

$expectedVersion = Get-TipProjectVersion
if ($expectedVersion) { Write-Ok "目標版本(tip pyproject):$expectedVersion" }
else { Write-Info "(tip 的 pyproject.toml 無法解析版本——版本驗證將降級為只比對 tip sha)" }
Write-Info ("gateway 現況:" + (Get-GatewayStateSummary))

# --- 步驟 2:rescue tag(不覆蓋;重名帶序號)---------------------------------
Write-Step "步驟 2/8:rescue tag"
$verToken = "upgrade"
if ($expectedVersion) { $verToken = ($expectedVersion -replace "\.", "") }
$tagBase = "rescue/pre-" + $verToken + "-" + (Get-Date -Format "yyyyMMdd")
$existing = Invoke-Git @("tag", "-l", ($tagBase + "*"))
if ($alreadyAtTip) {
    if ($existing) {
        $script:RescueTag = ($existing | Select-Object -Last 1)
        Write-SkipMsg "已在 tip,rescue tag 已存在:$script:RescueTag"
    } else {
        Write-SkipMsg "已在 tip(rescue tag 應建於升級前 HEAD;現無需補建)"
    }
} else {
    $tagName = $tagBase
    $suffix = 1
    while ($true) {
        $hit = Invoke-Git @("tag", "-l", $tagName)
        if (-not $hit) { break }
        $at = ((Invoke-Git @("rev-parse", ($tagName + "^{commit}")) -AllowFail) -join "").Trim()
        if ($at -eq $headFull) { break }  # 同名且就指在目前 HEAD → 直接複用
        $suffix = $suffix + 1
        $tagName = $tagBase + "-" + $suffix
    }
    $script:RescueTag = $tagName
    $hit = Invoke-Git @("tag", "-l", $tagName)
    if ($hit) {
        Write-SkipMsg "rescue tag 已存在且指向目前 HEAD,複用:$tagName"
    } elseif ($DryRun) {
        Write-Host "  [DryRun] git tag $tagName $($headFull.Substring(0,8))"
    } else {
        $null = Invoke-Git @("tag", $tagName, $headFull)
        Write-Ok "rescue tag 建立:$tagName → $($headFull.Substring(0,8))(不覆蓋既有 tag)"
    }
}

# --- 步驟 3:停 gateway + 確認無殘留 ----------------------------------------
Write-Step "步驟 3/8:停 gateway"
$hermesExe = Join-Path $RepoPath "venv\Scripts\hermes.exe"
if ($SkipGateway) {
    Write-SkipMsg "-SkipGateway:略過(分段重跑/沙箱測試用)"
} elseif ($DryRun) {
    Write-Host "  [DryRun] $hermesExe gateway stop;確認無殘留 gateway run 進程"
} else {
    if (-not (Test-Path $hermesExe)) { Fail "找不到 $hermesExe(live venv 目錄名是 venv,不是 .venv)" }
    $running = Get-GatewayRunProcesses
    if (-not $running) {
        Write-SkipMsg "沒有 gateway run 進程在跑(可能已停/本就停機)——stop 視為已完成"
    } else {
        Write-Info ("現有 gateway 進程:" + (($running | ForEach-Object { $_.ProcessId }) -join ", "))
        $out = Invoke-Native $hermesExe @("gateway", "stop")
        if ($script:LastNativeExit -ne 0) {
            Fail ("hermes gateway stop 失敗(exit=$script:LastNativeExit):`n" + ($out -join "`n"))
        }
        Start-Sleep -Seconds 5
        $residual = Get-GatewayRunProcesses
        if ($residual) {
            $pids = ($residual | ForEach-Object { $_.ProcessId }) -join ", "
            $killHints = ($residual | ForEach-Object { "  taskkill /PID $($_.ProcessId) /F /T" }) -join "`n"
            Fail ("gateway stop 後仍有殘留 gateway run 進程(PID: $pids)。不自動殺;確認來源後手動:`n" + $killHints)
        }
        Write-Ok "gateway 已停,無殘留 gateway run 進程"
    }
}

# --- 步驟 4:ff merge 到整合 tip ---------------------------------------------
Write-Step "步驟 4/8:git merge --ff-only $tipShort"
if ($alreadyAtTip) {
    Write-SkipMsg "HEAD 已在 tip,跳過"
} elseif ($DryRun) {
    Write-Host "  [DryRun] git merge --ff-only $script:TipFull"
} else {
    $dirty2 = Invoke-Git @("status", "--porcelain")
    if ($dirty2) { Fail ("merge 前工作樹變髒(重跑情境?):`n" + ($dirty2 -join "`n")) }
    $null = Invoke-Git @("merge", "--ff-only", $script:TipFull)
    $nowHead = ((Invoke-Git @("rev-parse", "HEAD")) -join "").Trim()
    if ($nowHead -ne $script:TipFull) { Fail "merge 後 HEAD($nowHead)不等於整合 tip——不明狀態,人工檢查" }
    Write-Ok "main 已 ff 前進到 $tipShort"
}

# --- 步驟 5:pip editable 重裝 + extra pins ----------------------------------
Write-Step "步驟 5/8:pip install -e `".[messaging]`" + extra pins"
$pyExe = Join-Path $RepoPath "venv\Scripts\python.exe"
if ($SkipInstall) {
    Write-SkipMsg "-SkipInstall:略過(分段重跑/沙箱測試用)"
} elseif ($DryRun) {
    Write-Host "  [DryRun] $pyExe -m pip install -e `".[messaging]`";再依 $ExtraPinsFile 補 pins"
} else {
    if (-not (Test-Path $pyExe)) { Fail "找不到 $pyExe(live venv 目錄名是 venv,不是 .venv)" }
    $installedVersion = $null
    $showOut = Invoke-Native $pyExe @("-m", "pip", "show", "hermes-agent")
    if ($script:LastNativeExit -eq 0) {
        foreach ($line in $showOut) {
            if ($line -match "^Version:\s*(.+)$") { $installedVersion = $Matches[1].Trim() }
        }
    }
    if ($expectedVersion -and ($installedVersion -eq $expectedVersion)) {
        Write-SkipMsg "editable 已是 $installedVersion(目標同版),跳過重裝"
    } else {
        Write-Info ("目前 dist-info 版本:" + $installedVersion + " → 目標:" + $expectedVersion)
        $out = Invoke-Native $pyExe @("-m", "pip", "install", "-e", ".[messaging]") $RepoPath
        if ($script:LastNativeExit -ne 0) {
            Fail ("pip install -e 失敗(exit=$script:LastNativeExit),末段輸出:`n" + (($out | Select-Object -Last 25) -join "`n"))
        }
        Write-Ok "editable 重裝完成"
    }
    # extra pins:版本特定,不硬編——由升級調查(engineering)維護 pins 檔
    if (Test-Path $ExtraPinsFile) {
        $pins = @()
        foreach ($line in (Get-Content $ExtraPinsFile)) {
            $t = $line.Trim()
            if ($t -and (-not $t.StartsWith("#"))) { $pins += $t }
        }
        if ($pins.Count -gt 0) {
            Write-Info ("extra pins(" + $ExtraPinsFile + "):" + ($pins -join ", "))
            $out = Invoke-Native $pyExe (@("-m", "pip", "install") + $pins)
            if ($script:LastNativeExit -ne 0) {
                Fail ("extra pins 安裝失敗(exit=$script:LastNativeExit):`n" + (($out | Select-Object -Last 25) -join "`n"))
            }
            Write-Ok "extra pins 已滿足(pip 對已滿足的 pin 為 no-op,天然冪等)"
        } else {
            Write-SkipMsg "pins 檔存在但無有效條目,跳過"
        }
    } else {
        Write-SkipMsg "無 pins 檔($ExtraPinsFile),跳過——若本次升級有版本特定 pin,升級調查時應先更新該檔"
    }
}

# --- 步驟 6:web build + 工作樹噪音處理 --------------------------------------
Write-Step "步驟 6/8:web build(npm install + build)"
if ($SkipWebBuild) {
    Write-SkipMsg "-SkipWebBuild:略過(分段重跑/沙箱測試用)"
} elseif ($DryRun) {
    Write-Host "  [DryRun] cd web; npm install; npm run build;之後檢查工作樹(root package-lock.json 噪音自動 restore)"
} else {
    $webDir = Join-Path $RepoPath "web"
    if (-not (Test-Path $webDir)) { Fail "web/ 目錄不存在:$webDir" }
    $out = Invoke-Native "npm" @("install") $webDir
    if ($script:LastNativeExit -ne 0) { Fail ("npm install 失敗(exit=$script:LastNativeExit):`n" + (($out | Select-Object -Last 20) -join "`n")) }
    $out = Invoke-Native "npm" @("run", "build") $webDir
    if ($script:LastNativeExit -ne 0) { Fail ("npm run build 失敗(exit=$script:LastNativeExit):`n" + (($out | Select-Object -Last 20) -join "`n")) }
    Write-Ok "web build 完成"
    # 2026-08-04 實證的坑:npm 會對 root package-lock.json 產生重解析噪音——
    # 只有「恰好就是它」時自動 restore;任何其他預期外的髒 → fail-loud 停下。
    $dirt = Invoke-Git @("status", "--porcelain")
    if ($dirt) {
        $noiseOnly = $true
        foreach ($line in $dirt) {
            $path = $line.Substring(3).Trim()
            if ($path -ne "package-lock.json") { $noiseOnly = $false }
        }
        if ($noiseOnly) {
            $null = Invoke-Git @("restore", "package-lock.json")
            $recheck = Invoke-Git @("status", "--porcelain")
            if ($recheck) { Fail ("restore 後工作樹仍髒:`n" + ($recheck -join "`n")) }
            Write-Ok "root package-lock.json 重解析噪音已自動 git restore(2026-08-04 實證)"
        } else {
            Fail ("web build 後出現預期外的工作樹變更(不只 package-lock.json)——不自動清理:`n" + ($dirt -join "`n"))
        }
    } else {
        Write-Ok "web build 後工作樹乾淨"
    }
}

# --- 步驟 7:重啟 gateway + S7 驗證 ------------------------------------------
Write-Step "步驟 7/8:gateway 重啟 + S7 驗證"
if ($SkipGateway) {
    Write-SkipMsg "-SkipGateway:略過 gateway 重啟與驗證(分段重跑/沙箱測試用)"
} elseif ($DryRun) {
    Write-Host "  [DryRun] $hermesExe gateway start;等狀態檔(~3.5 分鐘);--version/doctor/allowlist 負面/message-key dedup"
} else {
    $startedAt = Get-Date
    $running = Get-GatewayRunProcesses
    if ($running) {
        Write-SkipMsg ("gateway 已在跑(PID " + (($running | ForEach-Object { $_.ProcessId }) -join ", ") + "),跳過 start")
    } else {
        $out = Invoke-Native $hermesExe @("gateway", "start")
        if ($script:LastNativeExit -ne 0) { Fail ("hermes gateway start 失敗(exit=$script:LastNativeExit):`n" + ($out -join "`n")) }
        Write-Ok "gateway start 已送出"
    }

    # S7-1 版本字串
    $verOut = (Invoke-Native $hermesExe @("--version")) -join " "
    if ($script:LastNativeExit -ne 0) { Fail "hermes --version 執行失敗" }
    Write-Info ("版本字串:" + $verOut)
    if ($expectedVersion -and ($verOut -notmatch [regex]::Escape($expectedVersion))) {
        Fail "版本字串不含預期版本 $expectedVersion"
    }
    if ($verOut -notmatch $tipShort.Substring(0, 8)) {
        Fail "版本字串不含預期 tip $tipShort(editable 生效碼與 git HEAD 不一致?)"
    }
    Write-Ok "版本字串含預期版本與 tip"

    # S7-2 等 gateway 狀態檔(啟動後 ~3.5 分鐘才寫,memory:hermes-gateway-init-slow)
    $statePath = Join-Path (Split-Path $RepoPath -Parent) "gateway_state.json"
    Write-Info "等待 gateway 狀態檔更新(上限 $GatewayReadySeconds 秒;gateway 初始化 ~3.5 分鐘屬正常)…"
    $deadline = (Get-Date).AddSeconds($GatewayReadySeconds)
    $ready = $false
    while ((Get-Date) -lt $deadline) {
        if (Test-Path $statePath) {
            $m = (Get-Item $statePath).LastWriteTime
            if ($m -gt $startedAt) { $ready = $true; break }
        }
        Start-Sleep -Seconds 10
    }
    if (-not $ready) {
        Fail "gateway 狀態檔在 $GatewayReadySeconds 秒內未更新——啟動可能失敗(也可能只是特別慢:人工確認後可重跑,已完成步驟會跳過)"
    }
    Write-Ok ("gateway 狀態檔已更新:" + (Get-GatewayStateSummary))

    # S7-3 doctor(唯讀)
    $out = Invoke-Native $hermesExe @("gateway", "doctor", "--json")
    if ($script:LastNativeExit -ne 0) { Fail ("gateway doctor --json 異常(exit=$script:LastNativeExit):`n" + (($out | Select-Object -Last 20) -join "`n")) }
    Write-Ok "gateway doctor --json 通過(exit=0)"

    # S7-4/5 Slack 實測(預設執行,與計畫 S7 一致;-SkipSlackTests 可略)
    if ($SkipSlackTests) {
        Write-SkipMsg "-SkipSlackTests:略過 allowlist 負面與 dedup 兩項 Slack 實測"
    } else {
        # allowlist 負面:送非白名單頻道,必須 fail-closed(exit 1)
        $msg = "upgrade-verify allowlist negative " + (Get-Date -Format "yyyyMMdd-HHmmss")
        $out = Invoke-Native $hermesExe @("send", "--to", ("slack:" + $SlackNegativeChannel), $msg, "--quiet")
        if ($script:LastNativeExit -eq 0) {
            Fail "allowlist 負面測試竟然送出成功(exit=0)——fail-closed 防線失效,立即人工檢查!"
        }
        if ($script:LastNativeExit -ne 1) {
            Fail "allowlist 負面測試 exit=$script:LastNativeExit(預期 1=被拒;2=用法錯誤,請檢查 script)"
        }
        Write-Ok "allowlist 負面測試通過(非白名單頻道被拒,exit=1)"

        # message-key dedup:同 key 送兩次,第二次必須 dedup(key 含執行時間戳)
        $key = "upgrade-verify-" + (Get-Date -Format "yyyyMMdd-HHmmss")
        $body = "upgrade verify dedup test (key=$key) — 同 key 兩次,頻道應只出現一則"
        $out1 = Invoke-Native $hermesExe @("send", "--to", ("slack:" + $SlackTestChannel), $body, "--message-key", $key)
        if ($script:LastNativeExit -ne 0) { Fail ("dedup 測試第一送失敗(exit=$script:LastNativeExit):`n" + ($out1 -join "`n")) }
        $out2 = Invoke-Native $hermesExe @("send", "--to", ("slack:" + $SlackTestChannel), $body, "--message-key", $key)
        if ($script:LastNativeExit -ne 0) { Fail ("dedup 測試第二送 exit=$script:LastNativeExit(預期 0=dedup 跳過):`n" + ($out2 -join "`n")) }
        $secondText = $out2 -join " "
        if ($secondText -notmatch "already") {
            Fail ("dedup 第二送 exit=0 但輸出無 'already sent' 跡象——可能重複送出,請人工檢查頻道 " + $SlackTestChannel + ":`n" + $secondText)
        }
        Write-Ok "message-key dedup 通過(第二送被跳過,頻道應只有一則;key=$key)"
    }
}

# --- 步驟 8:push(main + rescue tag)-----------------------------------------
Write-Step "步驟 8/8:push origin(main + rescue tag)"
if ($SkipPush) {
    Write-SkipMsg "-SkipPush:略過(分段重跑/沙箱測試用)"
} elseif ($DryRun) {
    Write-Host "  [DryRun] git push origin main;git push origin <rescue-tag>"
} else {
    $null = Invoke-Git @("push", "origin", "main")
    Write-Ok "git push origin main 完成(已是最新亦為成功——冪等)"
    if ($script:RescueTag) {
        $null = Invoke-Git @("push", "origin", $script:RescueTag)
        Write-Ok ("git push origin " + $script:RescueTag + " 完成")
    } else {
        Write-SkipMsg "本次未建立/辨識 rescue tag(已在 tip 的重跑情境)——如需補推:git push origin <tag>"
    }
}

Write-Host "`n=== 完成 ===" -ForegroundColor Green
Write-Host ("HEAD = " + $tipShort + ";rescue tag = " + $script:RescueTag)
Write-Host "後續:WSL 對齊請跑 scripts\hermes_sync_wsl.ps1(順序:Windows push 完才動 WSL,"
Write-Host ("見 " + $script:PlanDoc + " §8 的「未 push 即失效」弱點)")
exit 0
