[CmdletBinding()]
param(
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"

function Resolve-PythonLauncher {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepoRoot
    )

    $venvCandidates = @(
        (Join-Path $RepoRoot ".venv311\Scripts\python.exe"),
        (Join-Path $RepoRoot ".venv\Scripts\python.exe")
    )
    foreach ($candidate in $venvCandidates) {
        if (Test-Path $candidate) {
            return @{
                FilePath = $candidate
                Prefix = @()
            }
        }
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        return @{
            FilePath = $python.Source
            Prefix = @()
        }
    }

    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        return @{
            FilePath = $pyLauncher.Source
            Prefix = @("-3.10")
        }
    }

    throw "Python executable not found. Install Python or create .venv/.venv311."
}

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$python = Resolve-PythonLauncher -RepoRoot $repoRoot
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
$arguments = @($python.Prefix + @("-m", "app.main"))
$process = Start-Process `
    -FilePath $python.FilePath `
    -ArgumentList $arguments `
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
