[CmdletBinding()]
param(
    [string]$OpenVINOModelsPath = "C:\Users\colin\repos\deskside_ai\openvino\models",
    [string]$OVMSInstallRoot = "C:\ovms\app",
    [string]$ModelRepoPath = "C:\ovms\models"
)

$ErrorActionPreference = "Stop"

# Find the latest OVMS installation
Write-Host "Finding latest OVMS installation..."
$OVMSVersions = Get-ChildItem -Path $OVMSInstallRoot -Directory | Sort-Object Name -Descending
if (-not $OVMSVersions) {
    throw "No OVMS installation found in '$OVMSInstallRoot'. Please run install.ps1 first."
}

$LatestOVMS = $OVMSVersions[0]
Write-Host "Using OVMS: $($LatestOVMS.FullName)"

# Model repository path
Write-Host "Model repository path: $ModelRepoPath"

# Create model repository directory if it doesn't exist
if (-not (Test-Path $ModelRepoPath)) {
    New-Item -ItemType Directory -Path $ModelRepoPath -Force | Out-Null
}

# Read models.txt to get model names
$ModelsTxtPath = "C:\Users\colin\repos\deskside_ai\openvino\models.txt"
if (-not (Test-Path $ModelsTxtPath)) {
    throw "models.txt not found at '$ModelsTxtPath'"
}

Write-Host "`nSetting up models in OVMS repository..."

# Process each model from models.txt
$Models = Get-Content $ModelsTxtPath | Where-Object { $_ -notmatch "^#" -and $_ -match "\S" }
foreach ($ModelLine in $Models) {
    # Parse model line: name|relative path|device|task|source model|weight format|symmetric|group size|ratio
    $Parts = $ModelLine -split "\|"
    if ($Parts.Count -lt 5) {
        Write-Warning "Skipping invalid line: $ModelLine"
        continue
    }

    $ModelName = $Parts[0].Trim()
    $RelativePath = $Parts[1].Trim()
    $Device = $Parts[2].Trim()

    Write-Host "`nProcessing model: $ModelName"

    # Construct full path to OpenVINO model
    $OpenVINOModelPath = Join-Path $OpenVINOModelsPath $RelativePath
    if (-not (Test-Path $OpenVINOModelPath)) {
        Write-Warning "Model directory not found: $OpenVINOModelPath"
        continue
    }

    # Create OVMS model directory structure (versioned)
    $OVMSModelPath = Join-Path $ModelRepoPath $ModelName
    $OVMSVersionPath = Join-Path $OVMSModelPath "1"
    New-Item -ItemType Directory -Path $OVMSVersionPath -Force | Out-Null

    # Copy OpenVINO IR files to OVMS model directory
    Write-Host "  Copying OpenVINO IR files..."
    Copy-Item -Path (Join-Path $OpenVINOModelPath "openvino_model.xml") -Destination (Join-Path $OVMSVersionPath "openvino_model.xml") -Force
    Copy-Item -Path (Join-Path $OpenVINOModelPath "openvino_model.bin") -Destination (Join-Path $OVMSVersionPath "openvino_model.bin") -Force

    # Copy tokenizer files if they exist
    if (Test-Path (Join-Path $OpenVINOModelPath "openvino_tokenizer.xml")) {
        Copy-Item -Path (Join-Path $OpenVINOModelPath "openvino_tokenizer.xml") -Destination (Join-Path $OVMSVersionPath "openvino_tokenizer.xml") -Force
        Copy-Item -Path (Join-Path $OpenVINOModelPath "openvino_tokenizer.bin") -Destination (Join-Path $OVMSVersionPath "openvino_tokenizer.bin") -Force
    }
    if (Test-Path (Join-Path $OpenVINOModelPath "openvino_detokenizer.xml")) {
        Copy-Item -Path (Join-Path $OpenVINOModelPath "openvino_detokenizer.xml") -Destination (Join-Path $OVMSVersionPath "openvino_detokenizer.xml") -Force
        Copy-Item -Path (Join-Path $OpenVINOModelPath "openvino_detokenizer.bin") -Destination (Join-Path $OVMSVersionPath "openvino_detokenizer.bin") -Force
    }

    # Create config.pbtxt for OVMS (OpenVINO IR format)
    $ConfigPath = Join-Path $OVMSModelPath "config.pbtxt"
    $ConfigContent = @"
name: "$ModelName"
backend: "openvino_toolkit"
platform: "openvino_ir"
input [
  {
    name: "input_ids"
    data_type: TYPE_INT32
    dims: [ -1, -1 ]
  },
  {
    name: "attention_mask"
    data_type: TYPE_INT32
    dims: [ -1, -1 ]
  }
]
output [
  {
    name: "logits"
    data_type: TYPE_FP32
    dims: [ -1, -1, -1 ]
  }
]
parameters: {
  key: "OVMS_MODEL_TYPE"
  value: {
    string_value: "LLM"
  }
}
"@
    $ConfigContent | Set-Content $ConfigPath -Encoding utf8
    Write-Host "  Created config.pbtxt"
}

Write-Host "`n=== OVMS Model Repository Setup Complete ==="
Write-Host "Model repository: $ModelRepoPath"
Write-Host "`nModels setup:"
foreach ($ModelLine in $Models) {
    $Parts = $ModelLine -split "\|"
    if ($Parts.Count -ge 1) {
        Write-Host "  - $($Parts[0].Trim())"
    }
}
Write-Host "`nTo start OVMS with this repository, run:"
Write-Host ". `"$($LatestOVMS.FullName)\setupvars.ps1`""
Write-Host "& `"$($LatestOVMS.FullName)\ovms.exe`" --config_path `"$ModelRepoPath`" --port 9000 --rest_port 8000"
Write-Host "`nTo test the models, you can use the OVMS REST API:"
Write-Host "curl -X POST http://localhost:8001/v2/models/$($Parts[0].Trim())/infer -H 'Content-Type: application/json' -d '{\"inputs\": ..., \"outputs\": ...}'"
