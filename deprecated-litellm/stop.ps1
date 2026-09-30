#requires -Version 5.1
<#
.SYNOPSIS
  Stop the LiteLLM router stack.
.DESCRIPTION
  Tears down the LiteLLM Docker Compose stack that fronts the three-tier
  AI stack (Pro 7 NPU / Blackwell / GB10). Teardown references no secrets,
  so no 1Password (op) session is required.
.PARAMETER StopOnly
  Stop containers but do not remove them (docker compose stop). Fastest restart.
.PARAMETER Purge
  Also remove named volumes (docker compose down -v). Full cleanup.
.EXAMPLE
  .\stop-litellm.ps1
  .\stop-litellm.ps1 -StopOnly
  .\stop-litellm.ps1 -Purge
#>
[CmdletBinding()]
param(
    [switch]$StopOnly,
    [switch]$Purge
)

$ErrorActionPreference = 'Stop'

# --- Anchor to this script's own folder ---
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

$ComposeFile = Join-Path $ScriptDir 'docker-compose.yml'

function Write-Info { param($m) Write-Host "[*] $m" -ForegroundColor Cyan }
function Write-Ok   { param($m) Write-Host "[OK] $m" -ForegroundColor Green }
function Write-Err  { param($m) Write-Host "[X] $m" -ForegroundColor Red }

# --- Preflight ---
if (-not (Test-Path $ComposeFile)) {
    Write-Err "docker-compose.yml not found in $ScriptDir"
    exit 1
}

try {
    docker info *> $null
    if ($LASTEXITCODE -ne 0) { throw }
} catch {
    Write-Err "Docker daemon not reachable. Start Docker Desktop / the engine and retry."
    exit 1
}

# --- Tear down ---
if ($StopOnly) {
    Write-Info "Stopping LiteLLM container (keeping it for fast restart)..."
    docker compose -f $ComposeFile stop
}
elseif ($Purge) {
    Write-Info "Stopping stack and removing container, network, AND named volumes..."
    docker compose -f $ComposeFile down -v
}
else {
    Write-Info "Stopping stack and removing container + network (volumes kept)..."
    docker compose -f $ComposeFile down
}

if ($LASTEXITCODE -ne 0) {
    Write-Err "Teardown returned a non-zero exit code. Check 'docker compose ps' above."
    exit 1
}

# --- Report ---
Write-Ok "LiteLLM stack stopped."
Write-Info "Remaining containers for this project:"
docker compose -f $ComposeFile ps
Write-Host ""
Write-Info "Note: Ollama on the Blackwell and GB10 are separate stacks on other machines and are untouched."
