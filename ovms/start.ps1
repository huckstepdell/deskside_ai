[CmdletBinding()]
param(
    [string]$OVMSInstallRoot = "C:\ovms\app",
    [int]$Port = 4000,
    [int]$RestPort = 4001,
    [string]$ModelRepoPath = "C:\ovms\models"
)

$ErrorActionPreference = "Stop"

# Use OVMS install root directly (no version subdirectory)
Write-Host "Using OVMS installation: $OVMSInstallRoot"

# Model repository path
Write-Host "Model repository path: $ModelRepoPath"

# Check if model repository exists
if (-not (Test-Path $ModelRepoPath)) {
    throw "Model repository not found at '$ModelRepoPath'. Please run setup-models.ps1 first."
}

# Source setupvars
$SetupVarsPath = Join-Path $OVMSInstallRoot "setupvars.ps1"
if (-not (Test-Path $SetupVarsPath)) {
    throw "setupvars.ps1 not found at '$SetupVarsPath'"
}
Write-Host "Sourcing setupvars..."
. $SetupVarsPath

# Start OVMS
$OVMSExePath = Join-Path $OVMSInstallRoot "ovms.exe"
if (-not (Test-Path $OVMSExePath)) {
    throw "ovms.exe not found at '$OVMSExePath'"
}

Write-Host "`nStarting OVMS..."
Write-Host "  Port: $Port"
Write-Host "  REST Port: $RestPort"
Write-Host "  Config Path: $ModelRepoPath"

& $OVMSExePath --config_path $ModelRepoPath --port $Port --rest_port $RestPort
