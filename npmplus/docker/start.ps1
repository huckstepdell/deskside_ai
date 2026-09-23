#requires -Version 5.1
<#
    start-npmplus.ps1
    -----------------
    Brings up the npmplus container stack.

    Usage:
        .\start-npmplus.ps1                # bring up the stack
        .\start-npmplus.ps1 -Down          # tear the stack down
        .\start-npmplus.ps1 -Follow        # bring up + tail logs
#>

[CmdletBinding()]
param(
    [switch]$Down,
    [switch]$Follow
)

$ErrorActionPreference = "Stop"

# --- Always operate from the script's own directory (where compose + .env live) ---
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $ScriptDir

# Determine compose command (CLI plugin 'docker compose' or standalone 'docker-compose')
$composeCmd = if (Get-Command "docker-compose" -ErrorAction SilentlyContinue) { "docker-compose" } else { "docker compose" }

function Write-Step($msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "  [OK] $msg"   -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "  [!!] $msg"   -ForegroundColor Yellow }
function Write-Err($msg)  { Write-Host "  [XX] $msg"   -ForegroundColor Red }

# --- Tear-down path -----------------------------------------------------------
if ($Down) {
    Write-Step "Stopping npmplus stack"
    & $composeCmd down
    Write-Ok "Stack stopped."
    return
}

# --- 1. Preflight checks ------------------------------------------------------
Write-Step "Preflight"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Err "Docker CLI not found on PATH."; exit 1
}
try { docker info *> $null; Write-Ok "Docker daemon reachable." }
catch { Write-Err "Docker daemon not reachable. Start Docker Desktop and retry."; exit 1 }

# Check for docker compose (either as CLI plugin or standalone)
if (-not (Get-Command "docker-compose" -ErrorAction SilentlyContinue) -and -not (Get-Command "docker compose" -ErrorAction SilentlyContinue)) {
    Write-Err "Docker Compose not found. Install: https://docs.docker.com/compose/install/"; exit 1
}
Write-Ok "Docker Compose available."

if (-not (Test-Path "docker-compose.yml")) { Write-Err "docker-compose.yml not found in $ScriptDir."; exit 1 }
Write-Ok "Docker Compose file present."

# --- 2. Bring up the stack ----------------------------------------------------
Write-Step "Starting npmplus container ($composeCmd up -d)"
$composeArgs = @("up", "-d")
& $composeCmd @composeArgs
Write-Ok "Compose up issued."

# --- 3. Wait for container to be healthy --------------------------------------
Write-Step "Waiting for container to become healthy"
$deadline = (Get-Date).AddSeconds(60)
$healthy = $false
while ((Get-Date) -lt $deadline) {
    $status = & $composeCmd ps --format json 2>$null | ConvertFrom-Json
    if ($status -and $status.Health -eq "healthy") {
        $healthy = $true
        break
    }
    Start-Sleep -Seconds 2
}

if ($healthy) {
    Write-Ok "Container is healthy."
} else {
    Write-Warn "Container health check timed out. Check: $composeCmd ps"
}

# --- 4. Optionally follow logs ------------------------------------------------
if ($Follow) {
    Write-Step "Following npmplus logs (Ctrl+C to detach)"
    & $composeCmd logs -f npmplus
}

Write-Host "`nnpmplus should now be running. Check status with: $composeCmd ps" -ForegroundColor Cyan