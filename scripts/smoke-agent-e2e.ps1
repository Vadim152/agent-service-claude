[CmdletBinding()]
param(
    [string]$ProjectRoot = (Get-Location).Path,
    [string]$Prompt = "Reply with exactly 'OK' and do not modify any files.",
    [string]$ExpectedModel = "gigachat/GigaChat-2-Pro",
    [int]$TimeoutSec = 180
)

$ErrorActionPreference = "Stop"
$baseUrl = "http://127.0.0.1:8000/api/v1"
$adapterDebugUrl = "http://127.0.0.1:8011/debug/runtime"

function Invoke-Json {
    param(
        [string]$Method,
        [string]$Url,
        [object]$Body = $null
    )

    $json = if ($null -ne $Body) { $Body | ConvertTo-Json -Depth 10 } else { $null }
    if ($null -ne $json) {
        return Invoke-RestMethod -Method $Method -Uri $Url -ContentType "application/json" -Body $json -TimeoutSec 30
    }
    return Invoke-RestMethod -Method $Method -Uri $Url -TimeoutSec 30
}

$adapterDebug = Invoke-Json -Method GET -Url $adapterDebugUrl
if ($adapterDebug.runnerType -ne "claude_code") {
    throw "Adapter runnerType must be 'claude_code' for external smoke. Actual: $($adapterDebug.runnerType)"
}
if (-not $adapterDebug.preflightReady) {
    throw "Adapter runtime preflight is blocked: $(($adapterDebug.preflightIssues | ConvertTo-Json -Depth 10))"
}
if (-not $adapterDebug.gatewayReady) {
    throw "Embedded Anthropic gateway is not ready: $(($adapterDebug | ConvertTo-Json -Depth 10))"
}
if (-not $adapterDebug.gigachatAuthReady) {
    throw "GigaChat auth is not ready: $(($adapterDebug | ConvertTo-Json -Depth 10))"
}

$resolvedModel = $null
if ($adapterDebug.forcedModel) {
    $resolvedModel = [string]$adapterDebug.forcedModel
}
elseif ($adapterDebug.resolvedModel) {
    $resolvedModel = [string]$adapterDebug.resolvedModel
}

if ([string]::IsNullOrWhiteSpace($resolvedModel)) {
    throw "Adapter runtime did not expose a resolved or forced model: $(($adapterDebug | ConvertTo-Json -Depth 10))"
}
if ($resolvedModel -ne $ExpectedModel) {
    throw "Adapter model mismatch. Expected $ExpectedModel but got $resolvedModel"
}

$session = Invoke-Json -Method POST -Url "$baseUrl/sessions" -Body @{
    projectRoot = $ProjectRoot
    source = "smoke-test"
    profile = "agent"
    runtime = "agent"
    reuseExisting = $true
}

$accepted = Invoke-Json -Method POST -Url "$baseUrl/sessions/$($session.sessionId)/messages" -Body @{
    role = "user"
    content = $Prompt
}

$deadline = (Get-Date).AddSeconds($TimeoutSec)
$status = $null
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 1500
    $status = Invoke-Json -Method GET -Url "$baseUrl/sessions/$($session.sessionId)/status"
    if ($status.activeRunStatus -in @("succeeded", "failed", "cancelled")) {
        break
    }
}

if ($null -eq $status) {
    throw "Failed to read session status"
}
if ($status.activeRunStatus -notin @("succeeded", "failed", "cancelled")) {
    throw "Agent run did not finish within timeout. Last status: $($status.activeRunStatus), activity: $($status.activity), action: $($status.currentAction)"
}

$history = Invoke-Json -Method GET -Url "$baseUrl/sessions/$($session.sessionId)/history"
$run = $null
$artifacts = $null
if ($status.activeRunId) {
    $run = Invoke-Json -Method GET -Url "$baseUrl/runs/$($status.activeRunId)"
    $artifacts = Invoke-Json -Method GET -Url "$baseUrl/runs/$($status.activeRunId)/artifacts"
}

[pscustomobject]@{
    sessionId = $session.sessionId
    runtime = $session.runtime
    runId = $accepted.runId
    activeRunId = $status.activeRunId
    activeRunStatus = $status.activeRunStatus
    activity = $status.activity
    currentAction = $status.currentAction
    backend = $status.activeRunBackend
    model = $resolvedModel
    preflightStatus = $adapterDebug.preflightStatus
    messageCount = @($history.messages).Count
    lastMessage = if (@($history.messages).Count -gt 0) { $history.messages[-1].content } else { $null }
    artifactCount = if ($artifacts) { @($artifacts.items).Count } else { 0 }
} | ConvertTo-Json -Depth 10

if ($status.activeRunBackend -ne "claude_code") {
    throw "Expected activeRunBackend=claude_code, got $($status.activeRunBackend)"
}
if ($status.activeRunStatus -ne "succeeded") {
    exit 1
}
