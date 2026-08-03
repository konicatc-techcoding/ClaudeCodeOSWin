<#
start_webui_stack.ps1 的可測函式測試(2026-08-03,任務 1 邊界:port 探測
邏輯抽成可測函式就補測)。零依賴(不需 Pester):dot-source -AsLibrary 載入
函式後直接斷言;port 探測用測試自建的 127.0.0.1 臨時 TcpListener,絕不碰
真實 stack 的 8787/8801/5173/8799,也不啟動任何背景程序。

用法: powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test_start_webui_stack.ps1
結束碼: 0=全部通過, 1=任一項失敗
#>
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot "start_webui_stack.ps1") -AsLibrary

$script:Failures = 0
$script:Total = 0

function Assert-True {
    param([bool]$Condition, [string]$Name)
    $script:Total += 1
    if ($Condition) {
        Write-Host "PASS - $Name"
    } else {
        $script:Failures += 1
        Write-Host "FAIL - $Name"
    }
}

# ---- Test-PortListening ----

# 1. 監聽中的 port(OS 配發臨時 port,不碰真實 stack)→ true
$listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, 0)
$listener.Start()
$tempPort = ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port
Assert-True (Test-PortListening -Port $tempPort) "Test-PortListening:監聽中的 port($tempPort)→ true"

# 2. 停止監聽後 → false
$listener.Stop()
Assert-True (-not (Test-PortListening -Port $tempPort)) "Test-PortListening:停止監聽後($tempPort)→ false"

# ---- Get-StackPlan(冪等決策矩陣)----

# 3. 全部在跑 → AllUp,啥都不啟動
$plan = Get-StackPlan -BridgeUp $true -PtyUp $true -ViteUp $true -ApiUp $true
Assert-True ($plan.AllUp -and -not $plan.StartLocal -and -not $plan.StartApi -and -not $plan.LocalPartial) `
    "Get-StackPlan:四 port 全上 → AllUp,不啟動任何東西"

# 4. 全部沒跑 → 兩個都啟動
$plan = Get-StackPlan -BridgeUp $false -PtyUp $false -ViteUp $false -ApiUp $false
Assert-True ($plan.StartLocal -and $plan.StartApi -and -not $plan.AllUp -and -not $plan.LocalPartial) `
    "Get-StackPlan:四 port 全下 → StartLocal + StartApi"

# 5. webui 三 port 部分在跑(PTY 掛了)→ LocalPartial,不得啟動第二份 launcher
$plan = Get-StackPlan -BridgeUp $true -PtyUp $false -ViteUp $true -ApiUp $true
Assert-True ($plan.LocalPartial -and -not $plan.StartLocal -and -not $plan.AllUp) `
    "Get-StackPlan:webui 部分運行 → LocalPartial 且不 StartLocal(防 port 衝突)"

# 6. 只有 API 在跑 → 啟動 webui launcher、跳過 API
$plan = Get-StackPlan -BridgeUp $false -PtyUp $false -ViteUp $false -ApiUp $true
Assert-True ($plan.StartLocal -and -not $plan.StartApi -and -not $plan.LocalPartial) `
    "Get-StackPlan:僅 API 在跑 → StartLocal、跳過 API"

# 7. 只有 webui 在跑 → 只補 API
$plan = Get-StackPlan -BridgeUp $true -PtyUp $true -ViteUp $true -ApiUp $false
Assert-True ($plan.StartApi -and -not $plan.StartLocal -and -not $plan.LocalPartial -and -not $plan.AllUp) `
    "Get-StackPlan:僅 webui 在跑 → 只補 API"

Write-Host ""
Write-Host ("結果:{0}/{1} 通過" -f ($script:Total - $script:Failures), $script:Total)
if ($script:Failures -gt 0) { exit 1 }
exit 0
