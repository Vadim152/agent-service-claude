[CmdletBinding()]
param(
    [switch]$SmokeTest
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -ExecutionPolicy Bypass -Command "& '$repoRoot\scripts\start-claude-code-adapter.ps1'"
C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -ExecutionPolicy Bypass -Command "& '$repoRoot\scripts\start-agent-service.ps1'"

if ($SmokeTest) {
    C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -ExecutionPolicy Bypass -Command "& '$repoRoot\scripts\smoke-agent-e2e.ps1' -ProjectRoot '$repoRoot'"
}
