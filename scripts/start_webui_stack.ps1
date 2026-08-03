<#
.SYNOPSIS
一鍵啟動 Web UI 觀測面 stack(冪等;2026-08-03 拍板,任務 1)。

.DESCRIPTION
stack 組成(兩個獨立背景程序):
  1. webui launcher(node scripts/agentos-local.mjs,= `npm run local`)
     —— 同時帶起 Bridge 8787 + PTY server 8801 + Vite 5173(cwd=webui/)
  2. 唯讀 API(.venv\Scripts\python.exe dashboard/api.py,port 8799,
     **必須在 repo 根目錄跑**;與 webui/src/api.ts 的 API_START_COMMAND 一致)

冪等行為:
  - 先探測 8787/8801/5173/8799 是否已在監聽;已在跑的部分跳過,只補缺的。
  - 四個 port 全部已在監聽 → 輸出「已全部運行」,直接開瀏覽器。
  - webui launcher 是「一個 process 管三個 port」:三個 port 都沒起才啟動;
    部分在跑(例如 PTY 掛了但 Bridge/Vite 還活著)時**不啟動第二份**——
    bridge.listen() 會 port 衝突直接崩潰——誠實回報部分運行狀態與處理建議。
  - 啟動後輪詢等 5173 與 8799 就緒(各最多 60 秒),就緒才開瀏覽器;任一
    timeout 誠實回報哪個沒起來 + log 路徑(隱藏視窗模式下另彈出訊息框),
    不假裝成功。

視窗隱藏:子程序用 Start-Process -WindowStyle Hidden;本 script 自身的
主控台視窗由 start_webui_stack.vbs 以 wscript + Run(...,0,...) 隱藏
(比照 hermes/windows/hermes-wsl-keepalive.vbs 既有慣例)。

log:stdout/stderr 導到 logs\webui_stack_<時間戳>_<名稱>.{out,err}.log,
launcher 自身輸出同步落 logs\webui_stack_<時間戳>_launcher.log
(logs/ 與 *.log 均在 .gitignore)。

桌面捷徑目標(零黑窗閃爍;捷徑檔本身由主 session 建立,此處只記目標字串):
  wscript.exe "C:\Users\razer\dev\ClaudeCodeOSWin\scripts\start_webui_stack.vbs"
若不介意短暫出現主控台視窗,也可直接:
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Users\razer\dev\ClaudeCodeOSWin\scripts\start_webui_stack.ps1"

.PARAMETER CheckOnly
只回報四個 port 的監聽狀態,不啟動任何程序、不開瀏覽器。

.PARAMETER NoBrowser
啟動/等待照做,但不開瀏覽器(給排程或測試用;本輪不做 Task Scheduler,
此參數只是把「開瀏覽器」這個副作用獨立出來)。

.PARAMETER AsLibrary
dot-source 模式:只載入函式與常數即返回,不執行主流程
(scripts/test_start_webui_stack.ps1 用)。
#>
param(
    [switch]$CheckOnly,
    [switch]$NoBrowser,
    [switch]$AsLibrary
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ---- 常數(路徑一律由 script 位置推得,不依賴呼叫時的 cwd)----
$RepoRoot = Split-Path -Parent $PSScriptRoot
$WebuiDir = Join-Path $RepoRoot "webui"
$LogDir = Join-Path $RepoRoot "logs"
$PythonExe = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$UiUrl = "http://127.0.0.1:5173"
$ReadyTimeoutSec = 60   # 5173 與 8799 各自的就緒等待上限(vite 冷啟要幾秒)

# port 表:webui launcher 一個 process 管前三個;唯讀 API 獨立
$Ports = [ordered]@{
    Bridge = 8787
    Pty    = 8801
    Vite   = 5173
    Api    = 8799
}

# ---- 可測函式 ----

# 探測本機 port 是否在監聽:TcpClient 實連 127.0.0.1(比 Get-NetTCPConnection
# 穩定——不依賴 NetTCPIP 模組,且「連得上」就是服務可用的直接證據)。
function Test-PortListening {
    param(
        [Parameter(Mandatory)][int]$Port,
        [int]$TimeoutMs = 500
    )
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne($TimeoutMs)) { return $false }
        $client.EndConnect($async)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

# 冪等啟動計畫(純函式,不碰網路/程序;test_start_webui_stack.ps1 直接斷言):
# webui launcher 三個 port 全下才 StartLocal;部分在跑 → LocalPartial(不啟動
# 第二份,避免 bridge port 衝突);API 獨立判斷。
function Get-StackPlan {
    param(
        [Parameter(Mandatory)][bool]$BridgeUp,
        [Parameter(Mandatory)][bool]$PtyUp,
        [Parameter(Mandatory)][bool]$ViteUp,
        [Parameter(Mandatory)][bool]$ApiUp
    )
    $localUpCount = @($BridgeUp, $PtyUp, $ViteUp).Where({ $_ }).Count
    return [pscustomobject]@{
        StartLocal   = ($localUpCount -eq 0)
        LocalPartial = ($localUpCount -gt 0 -and $localUpCount -lt 3)
        StartApi     = (-not $ApiUp)
        AllUp        = ($localUpCount -eq 3 -and $ApiUp)
    }
}

# 輪詢等 port 就緒;回傳 $true/$false(不丟例外,timeout 由呼叫端誠實回報)
function Wait-PortReady {
    param(
        [Parameter(Mandatory)][int]$Port,
        [int]$TimeoutSec = 60
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (Test-PortListening -Port $Port) { return $true }
        Start-Sleep -Milliseconds 1000
    }
    return (Test-PortListening -Port $Port)
}

if ($AsLibrary) { return }

# ---- 主流程 ----

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }
$LauncherLog = Join-Path $LogDir "webui_stack_${timestamp}_launcher.log"

function Write-Log {
    param([string]$Message)
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-ddTHH:mm:ss"), $Message
    Write-Host $line
    Add-Content -Path $LauncherLog -Value $line -Encoding UTF8
}

# 隱藏視窗模式下 stdout 沒人看得到;失敗時彈出訊息框誠實告知(30 秒自動關)
function Show-FailurePopup {
    param([string]$Message)
    try {
        $shell = New-Object -ComObject WScript.Shell
        [void]$shell.Popup($Message, 30, "AgentOS Web UI 啟動器", 48)
    } catch {
        # 彈窗失敗(無桌面 session 等)不影響結果——log 裡已有完整記錄
    }
}

$up = @{}
foreach ($name in @($Ports.Keys)) {
    $up[$name] = Test-PortListening -Port $Ports[$name]
}
Write-Log ("port 狀態:Bridge 8787={0} | PTY 8801={1} | Vite 5173={2} | API 8799={3}" -f `
        $up.Bridge, $up.Pty, $up.Vite, $up.Api)

if ($CheckOnly) {
    Write-Log "CheckOnly 模式:只回報狀態,不啟動、不開瀏覽器。"
    exit 0
}

$plan = Get-StackPlan -BridgeUp $up.Bridge -PtyUp $up.Pty -ViteUp $up.Vite -ApiUp $up.Api

if ($plan.AllUp) {
    Write-Log "已全部運行(四個 port 皆在監聽)——直接開瀏覽器。"
    if (-not $NoBrowser) { Start-Process $UiUrl }
    exit 0
}

$problems = @()

if ($plan.LocalPartial) {
    # 誠實面對部分運行:不啟動第二份(bridge 8787 會 EADDRINUSE 直接崩潰)
    $msg = "webui stack 部分運行(Bridge=$($up.Bridge)、PTY=$($up.Pty)、Vite=$($up.Vite))——" +
    "不啟動第二份 launcher(會 port 衝突)。請先結束殘留的 node 程序(工作管理員找 node.exe," +
    "或關掉原本跑 npm run local 的視窗)再重跑本 script。"
    Write-Log "WARN: $msg"
    $problems += $msg
} elseif ($plan.StartLocal) {
    # 等同 `npm run local`(webui/package.json 的 local script 就是這條指令);
    # 直接用 node 執行入口,避免 npm.cmd shim 在隱藏視窗/重導向下的不穩定
    $outLog = Join-Path $LogDir "webui_stack_${timestamp}_local.out.log"
    $errLog = Join-Path $LogDir "webui_stack_${timestamp}_local.err.log"
    Write-Log "啟動 webui launcher(node scripts/agentos-local.mjs,cwd=webui)→ $outLog"
    Start-Process -FilePath "node" -ArgumentList "scripts/agentos-local.mjs" `
        -WorkingDirectory $WebuiDir -WindowStyle Hidden `
        -RedirectStandardOutput $outLog -RedirectStandardError $errLog
} else {
    Write-Log "webui launcher 三個 port 已全部在監聽,跳過。"
}

if ($plan.StartApi) {
    if (-not (Test-Path $PythonExe)) {
        $msg = "找不到 $PythonExe——唯讀 API 無法啟動(venv 未建立?見 scripts/requirements.txt)。"
        Write-Log "ERROR: $msg"
        $problems += $msg
    } else {
        $outLog = Join-Path $LogDir "webui_stack_${timestamp}_api.out.log"
        $errLog = Join-Path $LogDir "webui_stack_${timestamp}_api.err.log"
        Write-Log "啟動唯讀 API(dashboard/api.py,cwd=repo 根,port 8799)→ $outLog"
        Start-Process -FilePath $PythonExe -ArgumentList "dashboard/api.py" `
            -WorkingDirectory $RepoRoot -WindowStyle Hidden `
            -RedirectStandardOutput $outLog -RedirectStandardError $errLog
    }
} else {
    Write-Log "唯讀 API 8799 已在監聽,跳過。"
}

# ---- 等待就緒(5173 與 8799;各最多 60 秒)----
$viteReady = Wait-PortReady -Port $Ports.Vite -TimeoutSec $ReadyTimeoutSec
if ($viteReady) {
    Write-Log "Vite 5173 就緒。"
} else {
    $msg = "Vite(5173)在 ${ReadyTimeoutSec} 秒內未就緒——UI 沒起來。log:logs\webui_stack_${timestamp}_local.*.log"
    Write-Log "ERROR: $msg"
    $problems += $msg
}

$apiReady = Wait-PortReady -Port $Ports.Api -TimeoutSec $ReadyTimeoutSec
if ($apiReady) {
    Write-Log "唯讀 API 8799 就緒。"
} else {
    $msg = "唯讀 API(8799)在 ${ReadyTimeoutSec} 秒內未就緒。log:logs\webui_stack_${timestamp}_api.*.log"
    Write-Log "ERROR: $msg"
    $problems += $msg
}

# ---- 收尾:兩者都就緒才開瀏覽器;有任何問題誠實回報,不假裝成功 ----
if ($viteReady -and $apiReady -and -not $NoBrowser) {
    Start-Process $UiUrl
    Write-Log "已開啟 $UiUrl"
}

if ($problems.Count -gt 0) {
    $summary = ($problems -join "`n") + "`n`n完整記錄:$LauncherLog"
    Write-Log "結束(有未解決的問題,exit 1)。"
    Show-FailurePopup $summary
    exit 1
}

Write-Log "結束(全部就緒)。"
exit 0
