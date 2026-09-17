$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$env:CONTAINER_DATA_DIR = "D:/container_data"

Push-Location $scriptDir
try {
    docker compose -f docker-compose.yml -f docker-compose.webui.yml down
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose down failed with exit code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}