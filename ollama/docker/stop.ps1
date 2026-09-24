$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# PULL_MODELS is only required by compose for variable interpolation; value is irrelevant for teardown.
if (-not $env:PULL_MODELS) { $env:PULL_MODELS = "unused" }
if (-not $env:CONTAINER_DATA_DIR) { $env:CONTAINER_DATA_DIR = "D:/container_data" }

Push-Location $scriptDir
try {
    # Base file alone is sufficient: device overlays only patch env vars on the same services/containers.
    docker compose -f docker-compose.yml down --remove-orphans
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose down failed with exit code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}