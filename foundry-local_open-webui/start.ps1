param(
    [ValidateSet("qwen2.5-coder-7b", "qwen2.5-coder-1.5b", "qwen2.5-coder-0.5b", "all")]
    [string]$Model = "all",
    [string]$EnvFile = ".env"
)

function Write-Step($msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "  [OK] $msg"   -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "  [!!] $msg"   -ForegroundColor Yellow }
function Write-Err($msg)  { Write-Host "  [XX] $msg"   -ForegroundColor Red }

$models = @(
    "qwen2.5-coder-0.5b",
    "qwen2.5-coder-1.5b"
    #"qwen2.5-coder-7b"
)

if ($Model -ne "all") {
    $models = @($Model)
}

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

Write-Host "Starting Foundry Local server..." -ForegroundColor Cyan
foundry server start -p 56194

foreach ($modelName in $models) {
    Write-Host "Loading model: $modelName" -ForegroundColor Yellow
    foundry model load $modelName
}

foundry status

Write-Host ""
Write-Host "Available models:" -ForegroundColor Cyan
foreach ($modelName in $models) {
    Write-Host "  - $modelName"
}

# Start Open WebUI via op run to resolve 1Password secrets at runtime
Write-Host ""
Write-Host "Starting Open WebUI..." -ForegroundColor Cyan
op run --env-file="$EnvFile" -- docker compose -f docker-compose.yml up -d open-webui
Write-Ok "Open WebUI started on http://localhost:8080"