param(
    [switch]$Status
)

Write-Host "Stopping Foundry Local server..." -ForegroundColor Yellow
foundry server stop

if ($Status) {
    Write-Host ""
    foundry server status
}
