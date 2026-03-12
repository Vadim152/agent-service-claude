[CmdletBinding()]
param(
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8011
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

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$python = Resolve-PythonLauncher -RepoRoot $repoRoot
$healthUrl = "http://$HostAddress`:$Port/health"
$debugUrl = "http://$HostAddress`:$Port/debug/runtime"

$existing = $null
try {
    $existing = Invoke-WebRequest -UseBasicParsing $healthUrl -TimeoutSec 2
}
catch {
}

if ($existing -and $existing.StatusCode -eq 200) {
    $debug = Invoke-RestMethod -Method GET -Uri $debugUrl -TimeoutSec 10 -ErrorAction SilentlyContinue
    if ($debug -and $debug.runnerType -eq "claude_code" -and ((-not $debug.preflightReady) -or (-not $debug.gatewayReady) -or (-not $debug.gigachatAuthReady))) {
        $debug | ConvertTo-Json -Depth 10 | Write-Output
        throw "Claude Code adapter is running but headless runtime preflight is blocked."
    }
    Write-Output "Claude Code adapter is already running at $healthUrl"
    exit 0
}

$logDir = Join-Path $repoRoot ".agent\claude-code-adapter"
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
$arguments = @($python.Prefix + @("-m", "claude_code_adapter_app.main"))
$process = Start-Process `
    -FilePath $python.FilePath `
    -ArgumentList $arguments `
    -WorkingDirectory $repoRoot `
    -PassThru `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog

$process.Id | Set-Content $pidFile

$deadline = (Get-Date).AddSeconds(20)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 500
    $response = $null
    try {
        $response = Invoke-WebRequest -UseBasicParsing $healthUrl -TimeoutSec 2
    }
    catch {
    }
    if ($response -and $response.StatusCode -eq 200) {
        $debug = Invoke-RestMethod -Method GET -Uri $debugUrl -TimeoutSec 10
        if ($debug.runnerType -eq "claude_code" -and ((-not $debug.preflightReady) -or (-not $debug.gatewayReady) -or (-not $debug.gigachatAuthReady))) {
            $debug | ConvertTo-Json -Depth 10 | Write-Output
            Stop-ProcessTree -RootPid $process.Id
            Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
            throw "Claude Code adapter runtime preflight failed. Fix the reported issues and try again."
        }
        Write-Output "Claude Code adapter started on $healthUrl (PID $($process.Id))"
        exit 0
    }
}

if (Test-Path $stderrLog) {
    Get-Content $stderrLog | Select-Object -Last 60 | Write-Output
}
Stop-ProcessTree -RootPid $process.Id
Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
throw "Claude Code adapter did not become ready at $healthUrl"
