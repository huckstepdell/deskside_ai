<#
.SYNOPSIS
  Stop the npmplus container stack.
.DESCRIPTION
  Tears down the npmplus Docker Compose stack.
.EXAMPLE
  .\stop-npmplus.ps1
  .\stop-npmplus.ps1 -StopOnly
  .\stop-npmplus.ps1 -Purge
#>

[CmdletBinding()]
param(
    [switch]$StopOnly,
    [switch]$Purge
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
Push-Location $scriptDir
try {
    # Try 'docker compose' first (CLI plugin), fall back to 'docker-compose' (standalone)
    $composeCmd = if (Get-Command "docker-compose" -ErrorAction SilentlyContinue) { "docker-compose" } else { "docker compose" }
    if ($Purge) {
        & $composeCmd down -v
    } elseif ($StopOnly) {
        & $composeCmd stop
    } else {
        & $composeCmd down
    }

    if ($LASTEXITCODE -ne 0) {
        throw "$composeCmd command failed with exit code $LASTEXITCODE."
    }

    Write-Host "`n[npmplus] Stack stopped successfully." -ForegroundColor Green
}
finally {
    Pop-Location
}