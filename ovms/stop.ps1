[CmdletBinding()]
param(
    [string]$OVMSInstallRoot = "C:\Program Files\OVMS"
)

$ErrorActionPreference = "Stop"

# Use OVMS install root directly (no version subdirectory)
Write-Host "Using OVMS installation: $OVMSInstallRoot"

# Source setupvars
$SetupVarsPath = Join-Path $OVMSInstallRoot "setupvars.ps1"
if (Test-Path $SetupVarsPath) {
    Write-Host "Sourcing setupvars..."
    . $SetupVarsPath
}

# Get OVMS service name
$ServiceName = "OVMS"
Write-Host "Stopping OVMS service: $ServiceName..."

# Stop the service
Stop-Service -Name $ServiceName -Force

# Wait for service to stop
Start-Sleep -Seconds 2

# Verify service is stopped
$Service = Get-Service -Name $ServiceName
if ($Service.Status -eq 'Stopped') {
    Write-Host "OVMS service stopped successfully."
} else {
    Write-Warning "OVMS service status: $($Service.Status)"
}
