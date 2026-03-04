[CmdletBinding()]
param(
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$pythonCandidates = @(
    (Join-Path $repoRoot ".venv311\Scripts\python.exe"),
    (Join-Path $repoRoot ".venv\Scripts\python.exe")
)
$python = $pythonCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $python) {
    throw "Python executable not found in .venv311 or .venv"
}

$healthUrl = "http://$HostAddress`:$Port/health"
$pidFile = Join-Path $repoRoot ".agent\agent-service\agent-service.pid"

if (Test-Path $pidFile) {
    try {
        $existing = Invoke-WebRequest -UseBasicParsing $healthUrl -TimeoutSec 2
        if ($existing.StatusCode -eq 200) {
            Write-Output "agent-service is already running at $healthUrl"
            exit 0
        }
    }
    catch {
    }
}

$logDir = Join-Path $repoRoot ".agent\agent-service"
New-Item -ItemType Directory -Force $logDir | Out-Null
$stdoutLog = Join-Path $logDir "agent-service.stdout.log"
$stderrLog = Join-Path $logDir "agent-service.stderr.log"
foreach ($logFile in @($stdoutLog, $stderrLog)) {
    if (Test-Path $logFile) {
        Clear-Content $logFile -ErrorAction SilentlyContinue
    }
}

$env:PYTHONPATH = "src"
$process = Start-Process `
    -FilePath $python `
    -ArgumentList "-m", "app.main" `
    -WorkingDirectory $repoRoot `
    -PassThru `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog

$process.Id | Set-Content $pidFile

$deadline = (Get-Date).AddSeconds(45)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 1000
    try {
        $response = Invoke-WebRequest -UseBasicParsing $healthUrl -TimeoutSec 2
        if ($response.StatusCode -eq 200) {
            Write-Output "agent-service started on $healthUrl (PID $($process.Id))"
            exit 0
        }
    }
    catch {
    }
}

if (Test-Path $stderrLog) {
    Get-Content $stderrLog | Select-Object -Last 60 | Write-Output
}
throw "agent-service did not become ready at $healthUrl"
