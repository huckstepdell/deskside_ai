<#
.SYNOPSIS
    Installs OpenVINO and its runtime/conversion dependencies.

.BEHAVIOR
    1. Uses $HOME\venvs\default if it exists.
    2. Otherwise prompts for a venv path and creates it with Python 3.11.
    3. Installs OpenVINO, OpenVINO GenAI, Optimum Intel/NNCF,
       and Hugging Face Hub.
    4. Verifies that OpenVINO sees the NPU.
    5. Creates the local models directory.
    6. Writes the selected venv path to .env.
    7. Does not authenticate, export models, or run inference.

.USAGE
    Set-ExecutionPolicy -Scope Process Bypass
    . .\install-openvino-npu.ps1
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Set-ExecutionPolicy -Scope Process Bypass -Force

$scriptRoot = $PSScriptRoot

$modelsRoot = Join-Path `
    -Path $scriptRoot `
    -ChildPath 'models'

New-Item `
    -ItemType Directory `
    -Path $modelsRoot `
    -Force |
    Out-Null

function Resolve-UserPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $expanded = [Environment]::ExpandEnvironmentVariables(
        $Path.Trim().Trim('"')
    )

    if (-not [IO.Path]::IsPathRooted($expanded)) {
        $expanded = Join-Path `
            -Path (Get-Location).Path `
            -ChildPath $expanded
    }

    return [IO.Path]::GetFullPath($expanded)
}

$defaultVenv = Join-Path `
    -Path $HOME `
    -ChildPath 'venvs\default'

$venvPath = $null

if (Test-Path -LiteralPath $defaultVenv -PathType Container) {
    $venvPath = [IO.Path]::GetFullPath($defaultVenv)

    Write-Host `
        "Using existing default venv: $venvPath" `
        -ForegroundColor Cyan
}
else {
    Write-Host `
        "Default venv not found at: $defaultVenv" `
        -ForegroundColor Yellow

    $requestedPath = Read-Host `
        'Enter the path where the OpenVINO venv should be created'

    if ([string]::IsNullOrWhiteSpace($requestedPath)) {
        throw 'No venv path was provided. Nothing was installed.'
    }

    $venvPath = Resolve-UserPath $requestedPath
}

$pythonPath = Join-Path `
    -Path $venvPath `
    -ChildPath 'Scripts\python.exe'

$activatePath = Join-Path `
    -Path $venvPath `
    -ChildPath 'Scripts\Activate.ps1'

if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    $parentPath = Split-Path -Parent $venvPath

    New-Item `
        -ItemType Directory `
        -Path $parentPath `
        -Force |
        Out-Null

    $pyLauncher = Get-Command `
        py.exe `
        -ErrorAction SilentlyContinue

    if ($null -eq $pyLauncher) {
        throw `
            'Python Launcher (py.exe) was not found. Install Python 3.11+ first.'
    }

    Write-Host `
        "Creating the virtual environment at: $venvPath" `
        -ForegroundColor Cyan

    & $pyLauncher.Source -3.11 -m venv $venvPath

    if ($LASTEXITCODE -ne 0) {
        throw `
            "Failed to create the virtual environment at $venvPath."
    }
}

if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw `
        "The selected path does not contain a valid Python virtual environment: $venvPath"
}

if (-not (Test-Path -LiteralPath $activatePath -PathType Leaf)) {
    throw `
        "The virtual environment activation script was not found: $activatePath"
}

. $activatePath

Write-Host `
    'Installing OpenVINO runtime and model-support packages...' `
    -ForegroundColor Cyan

& $pythonPath -m pip install --upgrade pip

& $pythonPath -m pip install --upgrade `
    openvino `
    openvino-genai `
    'optimum-intel[nncf]' `
    huggingface_hub

if ($LASTEXITCODE -ne 0) {
    throw 'OpenVINO package installation failed.'
}

Write-Host `
    'Checking OpenVINO device visibility...' `
    -ForegroundColor Cyan

$devices = & $pythonPath -c `
    "from openvino import Core; print('\n'.join(Core().available_devices))"

if ($LASTEXITCODE -ne 0) {
    throw 'Unable to query OpenVINO devices.'
}

$devices | ForEach-Object {
    Write-Host "  $_"
}

if (($devices -join "`n") -notmatch '(?m)^NPU$') {
    throw `
        'OpenVINO did not report an NPU. Confirm Intel AI Boost is visible in Device Manager and reboot if necessary.'
}

Write-Host `
    'NPU detected. Reading device name...' `
    -ForegroundColor Green

try {
    & $pythonPath -c `
        "from openvino import Core; print(Core().get_property('NPU', 'FULL_DEVICE_NAME'))"
}
catch {
    Write-Warning `
        'NPU was detected, but the detailed device name was unavailable.'
}

$envFilePath = Join-Path `
    -Path $scriptRoot `
    -ChildPath '.env'

@"
OPENVINO_VENV="$venvPath"
"@ | Set-Content `
    -LiteralPath $envFilePath `
    -Encoding utf8

Write-Host ''
Write-Host `
    'OpenVINO installation and NPU verification completed successfully.' `
    -ForegroundColor Green

Write-Host "Venv:   $venvPath"
Write-Host "Models: $modelsRoot"
Write-Host "Config: $envFilePath"
