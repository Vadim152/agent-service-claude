[CmdletBinding()]
param(
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8011
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
try {
    $existing = Invoke-WebRequest -UseBasicParsing $healthUrl -TimeoutSec 2
    if ($existing.StatusCode -eq 200) {
        Write-Output "OpenCode adapter is already running at $healthUrl"
        exit 0
    }
}
catch {
}

$logDir = Join-Path $repoRoot ".agent\opencode-adapter"
New-Item -ItemType Directory -Force $logDir | Out-Null
$stdoutLog = Join-Path $logDir "adapter.stdout.log"
$stderrLog = Join-Path $logDir "adapter.stderr.log"
$pidFile = Join-Path $logDir "adapter.pid"
foreach ($logFile in @($stdoutLog, $stderrLog)) {
    if (Test-Path $logFile) {
        Clear-Content $logFile -ErrorAction SilentlyContinue
    }
}

$env:PYTHONPATH = "src"
$process = Start-Process `
    -FilePath $python `
    -ArgumentList "-m", "opencode_adapter_app.main" `
    -WorkingDirectory $repoRoot `
    -PassThru `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog

$process.Id | Set-Content $pidFile

$deadline = (Get-Date).AddSeconds(15)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 500
    try {
        $response = Invoke-WebRequest -UseBasicParsing $healthUrl -TimeoutSec 2
        if ($response.StatusCode -eq 200) {
            Write-Output "OpenCode adapter started on $healthUrl (PID $($process.Id))"
            exit 0
        }
    }
    catch {
    }
}

if (Test-Path $stderrLog) {
    Get-Content $stderrLog | Select-Object -Last 40 | Write-Output
}
throw "OpenCode adapter did not become ready at $healthUrl"
