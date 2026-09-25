<#
.SYNOPSIS
    Stops the running OpenVINO adapter process.

.USAGE
    Set-ExecutionPolicy -Scope Process Bypass
    .\stop-openvino.ps1
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$scriptRoot = $PSScriptRoot

$pidFile = Join-Path `
    -Path $scriptRoot `
    -ChildPath '.openvino.pid'

if (-not (Test-Path -LiteralPath $pidFile -PathType Leaf)) {
    Write-Host `
        'No OpenVINO PID file was found. Nothing is running.' `
        -ForegroundColor Yellow

    exit 0
}

$pidText = (
    Get-Content `
        -LiteralPath $pidFile `
        -Raw
).Trim()

$adapterPid = 0

if (-not [int]::TryParse($pidText, [ref]$adapterPid)) {
    Remove-Item `
        -LiteralPath $pidFile `
        -Force `
        -ErrorAction SilentlyContinue

    throw `
        "The OpenVINO PID file contains an invalid process ID: $pidText"
}

$adapterProcess = Get-Process `
    -Id $adapterPid `
    -ErrorAction SilentlyContinue

if ($null -eq $adapterProcess) {
    Write-Host `
        "Process $adapterPid is no longer running." `
        -ForegroundColor Yellow

    Remove-Item `
        -LiteralPath $pidFile `
        -Force `
        -ErrorAction SilentlyContinue

    exit 0
}

Write-Host `
    "Stopping OpenVINO process $adapterPid..." `
    -ForegroundColor Cyan

try {
    Stop-Process `
        -Id $adapterPid `
        -ErrorAction Stop

    try {
        Wait-Process `
            -Id $adapterPid `
            -Timeout 10 `
            -ErrorAction SilentlyContinue
    }
    catch {
        # The process may already have exited.
    }

    $remainingProcess = Get-Process `
        -Id $adapterPid `
        -ErrorAction SilentlyContinue

    if ($null -ne $remainingProcess) {
        Write-Host `
            'Process did not exit cleanly; terminating it.' `
            -ForegroundColor Yellow

        Stop-Process `
            -Id $adapterPid `
            -Force `
            -ErrorAction Stop
    }

    Write-Host `
        'OpenVINO stopped successfully.' `
        -ForegroundColor Green
}
finally {
    Remove-Item `
        -LiteralPath $pidFile `
        -Force `
        -ErrorAction SilentlyContinue
}
