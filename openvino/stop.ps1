<#
.SYNOPSIS
    Stops the running OpenVINO server process.

.USAGE
    Set-ExecutionPolicy -Scope Process Bypass
    .\stop-openvino.ps1
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$scriptRoot = $PSScriptRoot

# This must match server.py and start.ps1.
$pidFile = Join-Path `
    -Path $scriptRoot `
    -ChildPath 'server.pid'

if (-not (Test-Path -LiteralPath $pidFile -PathType Leaf)) {
    Write-Host `
        "No OpenVINO PID file was found at '$pidFile'. Nothing is running." `
        -ForegroundColor Yellow

    exit 0
}

$pidText = (
    Get-Content `
        -LiteralPath $pidFile `
        -Raw
).Trim()

$serverPid = 0

$validPid = [int]::TryParse(
    $pidText,
    [Globalization.NumberStyles]::Integer,
    [Globalization.CultureInfo]::InvariantCulture,
    [ref]$serverPid
)

if (-not $validPid -or $serverPid -le 0) {
    Remove-Item `
        -LiteralPath $pidFile `
        -Force `
        -ErrorAction SilentlyContinue

    throw `
        "The OpenVINO PID file contains an invalid process ID: $pidText"
}

$serverProcess = Get-Process `
    -Id $serverPid `
    -ErrorAction SilentlyContinue

if ($null -eq $serverProcess) {
    Write-Host `
        "Process $serverPid is no longer running." `
        -ForegroundColor Yellow

    Remove-Item `
        -LiteralPath $pidFile `
        -Force `
        -ErrorAction SilentlyContinue

    exit 0
}

Write-Host `
    "Stopping OpenVINO process $serverPid..." `
    -ForegroundColor Cyan

try {
    Stop-Process `
        -Id $serverPid `
        -ErrorAction Stop

    try {
        Wait-Process `
            -Id $serverPid `
            -Timeout 10 `
            -ErrorAction SilentlyContinue
    }
    catch {
        # The process may already have exited.
    }

    $remainingProcess = Get-Process `
        -Id $serverPid `
        -ErrorAction SilentlyContinue

    if ($null -ne $remainingProcess) {
        Write-Host `
            'Process did not exit cleanly; terminating it.' `
            -ForegroundColor Yellow

        Stop-Process `
            -Id $serverPid `
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
