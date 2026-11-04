<#
.SYNOPSIS
    Inventory driver versions on a Dell machine.

.DESCRIPTION
    Reports the machine's identity (model, service tag, BIOS version), then
    lists installed device drivers with their versions, providers, and dates.
    Optionally filters to Dell-published drivers only, exports to CSV, and —
    if Dell Command | Update is installed — checks for pending driver updates.

.PARAMETER DellOnly
    Show only drivers published by Dell (firmware, docks, audio, etc.).
    Without it you get every signed driver, including Intel/NVIDIA/etc.

.PARAMETER CsvPath
    Also export the driver table to a CSV at this path.

.PARAMETER CheckUpdates
    Run Dell Command | Update's CLI (dcu-cli.exe) in scan mode, if present,
    to list drivers with newer versions available from Dell.

.EXAMPLE
    .\Get-DellDrivers.ps1
    .\Get-DellDrivers.ps1 -DellOnly
    .\Get-DellDrivers.ps1 -CsvPath "$env:USERPROFILE\Desktop\drivers.csv" -CheckUpdates
#>
[CmdletBinding()]
param(
    [switch]$DellOnly,
    [string]$CsvPath,
    [switch]$CheckUpdates
)

$ErrorActionPreference = 'Stop'

# ---- Machine identity -------------------------------------------------------
$cs   = Get-CimInstance Win32_ComputerSystem
$bios = Get-CimInstance Win32_BIOS
$os   = Get-CimInstance Win32_OperatingSystem

Write-Host ""
Write-Host "=== System ===" -ForegroundColor Cyan
[PSCustomObject]@{
    Manufacturer = $cs.Manufacturer
    Model        = $cs.Model
    ServiceTag   = $bios.SerialNumber          # Dell service tag lives in the BIOS serial
    BIOSVersion  = $bios.SMBIOSBIOSVersion
    BIOSDate     = $bios.ReleaseDate
    OS           = "$($os.Caption) $($os.Version)"
} | Format-List

if ($cs.Manufacturer -notmatch 'Dell') {
    Write-Warning "This machine reports manufacturer '$($cs.Manufacturer)' — not a Dell. Continuing anyway."
}

# ---- Driver inventory -------------------------------------------------------
Write-Host "=== Drivers ===" -ForegroundColor Cyan

$drivers = Get-CimInstance Win32_PnPSignedDriver |
    Where-Object { $_.DeviceName } |
    ForEach-Object {
        [PSCustomObject]@{
            Device   = $_.DeviceName
            Class    = $_.DeviceClass
            Version  = $_.DriverVersion
            Provider = $_.DriverProviderName
            Date     = if ($_.DriverDate) { $_.DriverDate.ToString('yyyy-MM-dd') } else { '' }
            InfName  = $_.InfName
        }
    }

if ($DellOnly) {
    $drivers = $drivers | Where-Object { $_.Provider -match 'Dell' }
}

# de-dupe (multi-instance devices repeat), newest class groups first
$drivers = $drivers | Sort-Object Class, Device, Version -Unique

$drivers | Format-Table Device, Class, Version, Provider, Date -AutoSize

Write-Host ("{0} driver entries{1}." -f $drivers.Count, $(if ($DellOnly) { ' (Dell-published only)' } else { '' }))

if ($CsvPath) {
    $drivers | Export-Csv -Path $CsvPath -NoTypeInformation -Encoding UTF8
    Write-Host "Exported to $CsvPath" -ForegroundColor Green
}

# ---- Pending updates via Dell Command | Update (optional) -------------------
if ($CheckUpdates) {
    $dcu = @(
        "$env:ProgramFiles\Dell\CommandUpdate\dcu-cli.exe",
        "${env:ProgramFiles(x86)}\Dell\CommandUpdate\dcu-cli.exe"
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1

    Write-Host ""
    Write-Host "=== Available updates (Dell Command | Update) ===" -ForegroundColor Cyan
    if ($dcu) {
        # /scan exits 0 (up to date), 1 (reboot pending) or 500 (updates found)
        & $dcu /scan -silent
        if ($LASTEXITCODE -eq 500) {
            Write-Host "Updates are available — run 'dcu-cli /applyUpdates' (elevated) to install." -ForegroundColor Yellow
        } elseif ($LASTEXITCODE -in 0, 1) {
            Write-Host "All drivers are current per Dell Command | Update." -ForegroundColor Green
        } else {
            Write-Warning "dcu-cli exited with code $LASTEXITCODE — see its log for details."
        }
    } else {
        Write-Warning "Dell Command | Update not found — install it to scan for driver updates from Dell."
    }
}
