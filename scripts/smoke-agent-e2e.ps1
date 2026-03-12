[CmdletBinding()]
param(
    [string]$ProjectRoot = (Get-Location).Path,
    [string]$Prompt = "Reply with exactly 'OK' and do not modify any files.",
    [int]$TimeoutSec = 180
)

$ErrorActionPreference = "Stop"
$baseUrl = "http://127.0.0.1:8000/api/v1"

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
    messageCount = @($history.messages).Count
    lastMessage = if (@($history.messages).Count -gt 0) { $history.messages[-1].content } else { $null }
    artifactCount = if ($artifacts) { @($artifacts.items).Count } else { 0 }
} | ConvertTo-Json -Depth 10

if ($status.activeRunStatus -ne "succeeded") {
    exit 1
}
