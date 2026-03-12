[CmdletBinding()]
param(
    [switch]$SmokeTest
)

$ErrorActionPreference = "Stop"

function Invoke-Step {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ScriptPath,
        [string[]]$Arguments = @()
    )

    & C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -ExecutionPolicy Bypass -File $ScriptPath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$([System.IO.Path]::GetFileName($ScriptPath)) failed with exit code $LASTEXITCODE"
    }
}

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

Invoke-Step -ScriptPath "$repoRoot\scripts\start-claude-code-adapter.ps1"
Invoke-Step -ScriptPath "$repoRoot\scripts\start-agent-service.ps1"

if ($SmokeTest) {
    Invoke-Step -ScriptPath "$repoRoot\scripts\smoke-agent-e2e.ps1" -Arguments @("-ProjectRoot", $repoRoot)
}
