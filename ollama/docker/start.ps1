$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$modelListDir = Join-Path $scriptDir "..\model_lists"

Write-Host "Select the target device:"
Write-Host "  1) Blackwell 4000"
Write-Host "  2) GB10"

do {
    $device = Read-Host "Device [1-2]"

    switch ($device) {
        "1" {
            $modelList = "blackwell_4000.txt"
            $composeFiles = @("-f", "docker-compose.yml", "-f", "docker-compose.blackwell.yml")
        }
        "2" {
            $modelList = "gb10.txt"
            $composeFiles = @(
                "-f", "docker-compose.yml",
                "-f", "docker-compose.gb10.yml",
                "-f", "docker-compose.webui.yml"
            )
        }
        default {
            Write-Host "Please enter 1 or 2."
            $modelList = $null
        }
    }
} while ($null -eq $modelList)

$modelListPath = Join-Path $modelListDir $modelList
$env:PULL_MODELS = (Get-Content -Path $modelListPath -Raw).Trim()
$env:CONTAINER_DATA_DIR = "D:/container_data"
Write-Host "Using model list: $modelList"

New-Item -ItemType Directory -Force -Path "D:\container_data\ollama\ollama-data" | Out-Null
if ($composeFiles -contains "docker-compose.webui.yml") {
    New-Item -ItemType Directory -Force -Path "D:\container_data\open-webui\open-webui-data" | Out-Null
}

Push-Location $scriptDir
try {
    & docker compose @composeFiles pull
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose pull failed with exit code $LASTEXITCODE."
    }

    & docker compose @composeFiles up -d --remove-orphans
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose up failed with exit code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}