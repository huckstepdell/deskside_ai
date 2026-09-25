param(
    [string]$EnvFile = ".env",
    [string]$WebUiUrl = "http://localhost:8080",
    [int]$HealthTimeoutSec = 60
)

function Write-Step($msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "  [OK] $msg"   -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "  [!!] $msg"   -ForegroundColor Yellow }
function Write-Err($msg)  { Write-Host "  [XX] $msg"   -ForegroundColor Red }

# --- Preflight checks ---------------------------------------------------------
Write-Step "Preflight"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $ScriptDir

if (-not (Get-Command op -ErrorAction SilentlyContinue)) {
    Write-Err "1Password CLI (op) not found. Install: winget install AgileBits.1Password.CLI"; exit 1
}
if (-not (Test-Path $EnvFile)) { Write-Err "Env file '$EnvFile' not found in $ScriptDir."; exit 1 }
if (-not (Test-Path "docker-compose.yml")) { Write-Err "docker-compose.yml not found in $ScriptDir."; exit 1 }
Write-Ok "op, $EnvFile, and docker-compose.yml present."

# --- Ensure a 1Password session ---------------------------------------------
Write-Step "1Password sign-in"
try {
    op whoami *> $null
    Write-Ok "Existing 1Password session found."
} catch {
    Write-Warn "No active session - signing in..."
    op signin
    Write-Ok "Signed in."
}

# --- Start Open WebUI via op run to resolve 1Password secrets at runtime -----
Write-Step "Starting Open WebUI"
op run --env-file="$EnvFile" -- docker compose -f docker-compose.yml up -d open-webui
Write-Ok "Compose up issued."

# --- Wait for Open WebUI to answer -------------------------------------------
Write-Step "Waiting for Open WebUI to become healthy (timeout ${HealthTimeoutSec}s)"
$deadline = (Get-Date).AddSeconds($HealthTimeoutSec)
$healthy = $false
while ((Get-Date) -lt $deadline) {
    try {
        $null = Invoke-WebRequest -Uri "$WebUiUrl/health" -TimeoutSec 5 -UseBasicParsing
        $healthy = $true
        break
    } catch {
        Start-Sleep -Seconds 3
        Write-Host "  ...waiting" -ForegroundColor DarkGray
    }
}
if (-not $healthy) {
    Write-Err "Open WebUI did not respond within ${HealthTimeoutSec}s. Check: docker compose logs open-webui"
    exit 1
}
Write-Ok "Open WebUI is answering on $WebUiUrl."