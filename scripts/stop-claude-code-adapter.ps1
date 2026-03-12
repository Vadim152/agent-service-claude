[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$pidFile = Join-Path $repoRoot ".agent\claude-code-adapter\adapter.pid"
$healthUrl = "http://127.0.0.1:8011/health"

function Stop-ProcessTree {
    param(
        [Parameter(Mandatory = $true)]
        [int]$RootPid
    )

    $children = Get-CimInstance Win32_Process -Filter "ParentProcessId = $RootPid" -ErrorAction SilentlyContinue
    foreach ($child in $children) {
        Stop-ProcessTree -RootPid ([int]$child.ProcessId)
    }

    $process = Get-Process -Id $RootPid -ErrorAction SilentlyContinue
    if ($process) {
        Stop-Process -Id $RootPid -Force -ErrorAction SilentlyContinue
    }
}

if (-not (Test-Path $pidFile)) {
    try {
        $response = Invoke-WebRequest -UseBasicParsing $healthUrl -TimeoutSec 2
        if ($response.StatusCode -eq 200) {
            throw "Adapter is running, but PID file is missing: $healthUrl"
        }
    }
    catch {
        Write-Output "Claude Code adapter is not running"
        exit 0
    }
}

$processId = [int](Get-Content $pidFile | Select-Object -First 1)
Stop-ProcessTree -RootPid $processId
Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
Write-Output "Claude Code adapter stopped (PID $processId)"
