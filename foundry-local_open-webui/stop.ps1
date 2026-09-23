param(
    [switch]$Status,
    [string]$EnvFile = ".env"
)

function Write-Step($msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "  [OK] $msg"   -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "  [!!] $msg"   -ForegroundColor Yellow }
function Write-Err($msg)  { Write-Host "  [XX] $msg"   -ForegroundColor Red }

Write-Step "Stopping Foundry Local server"
Write-Host "Stopping Foundry Local server..." -ForegroundColor Yellow
foundry server stop

Write-Step "Stopping Open WebUI"
op run --env-file="$EnvFile" -- docker compose -f docker-compose.yml down

if ($Status) {
    Write-Step "Foundry Local status"
    foundry server status
}