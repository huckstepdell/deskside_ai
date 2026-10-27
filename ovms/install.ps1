[CmdletBinding()]
param(
    [string]$Version = "latest",
    [string]$InstallRoot = "C:\ovms\app",
    [int]$Port = 4000,
    [int]$RestPort = 4001,
    [switch]$RetainZip
)

$ErrorActionPreference = "Stop"

# Resolve "latest" to a concrete version via the GitHub API
if ($Version -eq "latest") {
    Write-Host "Resolving latest OVMS version from GitHub..."
    $ApiUrl = "https://api.github.com/repos/openvinotoolkit/model_server/releases/latest"
    $Release = Invoke-RestMethod -Uri $ApiUrl -Headers @{ "User-Agent" = "ovms-installer" }
    # tag_name looks like "v2026.4.0" — strip the leading "v"
    $Version = $Release.tag_name -replace "^v", ""
    Write-Host "Latest version: $Version"
}

# Use install root directly (no version subdirectory)
# Version is still available in $Version for download purposes

$AssetName = "ovms_windows_${Version}_python_on.zip"
$ReleaseBase = "https://github.com" +
    "/openvinotoolkit/model_server/releases/download/v$Version"
$DownloadUrl = "$ReleaseBase/$AssetName"

# Use a stable temp dir so -RetainZip actually lets you reuse the zip across runs
$TempRoot = Join-Path $env:TEMP "ovms-install"
$ZipPath = Join-Path $TempRoot $AssetName
$ExtractRoot = Join-Path $TempRoot "extracted"

try {
    New-Item -ItemType Directory -Path $TempRoot -Force | Out-Null
    New-Item -ItemType Directory -Path $ExtractRoot -Force | Out-Null

    if (Test-Path $ZipPath) {
        Write-Host "Using existing zip: $ZipPath"
    } else {
        Write-Host "Downloading OVMS $Version..."
        Invoke-WebRequest -Uri $DownloadUrl -OutFile $ZipPath
    }

    Write-Host "Extracting package..."
    Expand-Archive -Path $ZipPath -DestinationPath $ExtractRoot -Force

    $OvmsExe = Get-ChildItem `
        -Path $ExtractRoot `
        -Filter "ovms.exe" `
        -File `
        -Recurse |
        Select-Object -First 1

    if (-not $OvmsExe) {
        throw "ovms.exe was not found in the downloaded archive."
    }

    $PackageRoot = $OvmsExe.Directory.FullName

    # Clear destination if it exists to avoid copy conflicts
    if (Test-Path $InstallRoot) {
        Write-Host "Clearing existing installation at $InstallRoot..."
        Stop-Process -Name ovms -Force -ErrorAction SilentlyContinue
        Remove-Item $InstallRoot -Recurse -Force
    }

    Copy-Item `
        -Path (Join-Path $PackageRoot "*") `
        -Destination $InstallRoot `
        -Recurse `
        -Force

    # Run setupvars.ps1
    $SetupVars = Join-Path $InstallRoot "setupvars.ps1"
    if (Test-Path $SetupVars) {
        Write-Host "Running setupvars.ps1..."
        & $SetupVars
    }

    # Set environment variables (user-level since no admin required)
    [Environment]::SetEnvironmentVariable("OVMS_HOME", $InstallRoot, "User")
    $envPath = [Environment]::GetEnvironmentVariable("PATH", "User")
    if (-not $envPath.Contains($InstallRoot)) {
        $envPath = "$InstallRoot;$envPath"
        [Environment]::SetEnvironmentVariable("PATH", $envPath, "User")
    }

    # Install and start OVMS service on configured ports
    $ServiceScript = Join-Path $InstallRoot "install_ovms_service.bat"
    if (Test-Path $ServiceScript) {
        Write-Host "Installing OVMS service on gRPC port $Port and REST port $RestPort..."
        # Create model repository directory and config for service
        $ModelRepo = "C:\ovms\models"
        if (-not (Test-Path $ModelRepo)) {
            New-Item -ItemType Directory -Path $ModelRepo -Force | Out-Null
        }
        $ConfigPath = Join-Path $InstallRoot "config.json"
        $ConfigContent = @{
            model_repository_manager = @{
                repository_count = 1
                base_path = $ModelRepo
            }
        } | ConvertTo-Json -Depth 10
        $ConfigContent | Set-Content $ConfigPath -Encoding utf8

        # Modify service script to use configured ports
        $ServiceScriptContent = Get-Content $ServiceScript -Raw
        $ServiceScriptContent = $ServiceScriptContent -replace '--port 9000', "--port $Port"
        $ServiceScriptContent = $ServiceScriptContent -replace '--rest_port 8000', "--rest_port $RestPort"
        $ServiceScriptContent | Set-Content $ServiceScript -Encoding utf8

        # Run service installation script
        & $ServiceScript $ModelRepo
    }

    $InstalledOvms = Join-Path $InstallRoot "ovms.exe"

    Write-Host "`nInstalled OVMS:"
    & $InstalledOvms --version

    Write-Host "`nInstall location: $InstallRoot"
    Write-Host "Model repository: C:\ovms\models"
    Write-Host "OVMS service installed on gRPC port $Port and REST port $RestPort"
    Write-Host "Before using OVMS in a new PowerShell session, run:"
    Write-Host ". `"$SetupVars`""
}
finally {
    if (Test-Path $TempRoot) {
        if ($RetainZip) {
            Write-Host "`nRetaining zip file at: $ZipPath"
        } else {
            Remove-Item $TempRoot -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

