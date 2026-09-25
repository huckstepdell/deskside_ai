<#
.SYNOPSIS
    Exports, validates, starts, and tests the OpenVINO server.

.DESCRIPTION
    models.txt format:

    name|relative path|device|task|source model|weight format|symmetric|group size|ratio

    After model validation, this script starts server.py on port 4001,
    runs API tests, and leaves the server running if all tests pass.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$scriptRoot = $PSScriptRoot

$envFilePath = Join-Path `
    $scriptRoot `
    '.env'

$modelsFile = Join-Path `
    $scriptRoot `
    'models.txt'

$modelsRoot = Join-Path `
    $scriptRoot `
    'models'

$serverScript = Join-Path `
    $scriptRoot `
    'server.py'

$serverHost = '127.0.0.1'
$serverPort = 4001

$serverBaseUrl = `
    "http://${serverHost}:${serverPort}"

$serverLog = Join-Path `
    $scriptRoot `
    'server.stdout.log'

$serverErrorLog = Join-Path `
    $scriptRoot `
    'server.stderr.log'

$serverProcess = $null
$startedServer = $false

# ---------------------------------------------------------------------------
# Validate files
# ---------------------------------------------------------------------------

if (-not (Test-Path -LiteralPath $envFilePath -PathType Leaf)) {
    throw "Environment file not found: $envFilePath"
}

if (-not (Test-Path -LiteralPath $modelsFile -PathType Leaf)) {
    throw "Model list not found: $modelsFile"
}

if (-not (Test-Path -LiteralPath $serverScript -PathType Leaf)) {
    throw "Server script not found: $serverScript"
}

New-Item `
    -ItemType Directory `
    -Path $modelsRoot `
    -Force |
    Out-Null

# ---------------------------------------------------------------------------
# Resolve Python environment
# ---------------------------------------------------------------------------

$venvLine = Get-Content -LiteralPath $envFilePath |
    Where-Object {
        $_ -match '^\s*OPENVINO_VENV\s*='
    } |
    Select-Object -First 1

if ($null -eq $venvLine) {
    throw "OPENVINO_VENV was not defined in $envFilePath"
}

$venvPath = (
    $venvLine -split '=', 2
)[1].Trim().Trim('"').Trim("'")

$venvPath = [Environment]::ExpandEnvironmentVariables($venvPath)

if (-not [IO.Path]::IsPathRooted($venvPath)) {
    $venvPath = Join-Path `
        $scriptRoot `
        $venvPath
}

$venvPath = [IO.Path]::GetFullPath($venvPath)

$pythonPath = Join-Path `
    $venvPath `
    'Scripts\python.exe'

$optimumCli = Join-Path `
    $venvPath `
    'Scripts\optimum-cli.exe'

if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw "Python executable not found: $pythonPath"
}

if (-not (Test-Path -LiteralPath $optimumCli -PathType Leaf)) {
    throw "Optimum CLI not found: $optimumCli"
}

Write-Host "Using venv: $venvPath" -ForegroundColor Cyan
Write-Host "Models directory: $modelsRoot" -ForegroundColor Cyan
Write-Host "Server endpoint: $serverBaseUrl" -ForegroundColor Cyan

# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

function Convert-ToBoolean {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Value,

        [Parameter(Mandatory = $true)]
        [string]$FieldName
    )

    switch ($Value.Trim().ToLowerInvariant()) {
        'true' {
            return $true
        }

        'false' {
            return $false
        }

        default {
            throw `
                "$FieldName must be true or false. Received '$Value'."
        }
    }
}

function Convert-ToOptionalInteger {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Value,

        [Parameter(Mandatory = $true)]
        [string]$FieldName
    )

    $normalized = $Value.Trim().ToLowerInvariant()

    if (
        [string]::IsNullOrWhiteSpace($normalized) -or
        $normalized -in @('none', 'default', 'null', '-')
    ) {
        return $null
    }

    $parsed = 0

    $success = [int]::TryParse(
        $normalized,
        [Globalization.NumberStyles]::Integer,
        [Globalization.CultureInfo]::InvariantCulture,
        [ref]$parsed
    )

    if (-not $success) {
        throw `
            "$FieldName must be an integer or none. Received '$Value'."
    }

    if ($parsed -le 0) {
        throw `
            "$FieldName must be greater than zero. Received '$Value'."
    }

    return $parsed
}

function Convert-ToOptionalDouble {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Value,

        [Parameter(Mandatory = $true)]
        [string]$FieldName
    )

    $normalized = $Value.Trim().ToLowerInvariant()

    if (
        [string]::IsNullOrWhiteSpace($normalized) -or
        $normalized -in @('none', 'default', 'null', '-')
    ) {
        return $null
    }

    $parsed = 0.0

    $success = [double]::TryParse(
        $normalized,
        [Globalization.NumberStyles]::Float,
        [Globalization.CultureInfo]::InvariantCulture,
        [ref]$parsed
    )

    if (-not $success) {
        throw `
            "$FieldName must be a number or none. Received '$Value'."
    }

    if ($parsed -lt 0.0 -or $parsed -gt 1.0) {
        throw `
            "$FieldName must be between 0.0 and 1.0. Received '$Value'."
    }

    return $parsed
}

function Format-OptionalInteger {
    param(
        [AllowNull()]
        [object]$Value
    )

    if ($null -eq $Value) {
        return 'none'
    }

    return ([int]$Value).ToString(
        [Globalization.CultureInfo]::InvariantCulture
    )
}

function Format-OptionalDouble {
    param(
        [AllowNull()]
        [object]$Value
    )

    if ($null -eq $Value) {
        return 'none'
    }

    return ([double]$Value).ToString(
        '0.################',
        [Globalization.CultureInfo]::InvariantCulture
    )
}

# ---------------------------------------------------------------------------
# Read models.txt
# ---------------------------------------------------------------------------

$modelEntries = @(
    foreach ($line in Get-Content -LiteralPath $modelsFile) {
        $trimmed = $line.Trim()

        if ([string]::IsNullOrWhiteSpace($trimmed)) {
            continue
        }

        if ($trimmed.StartsWith('#')) {
            continue
        }

        $parts = $trimmed -split '\|', 9

        if ($parts.Count -ne 9) {
            throw @"
Invalid models.txt line:

$line

Expected format:

name|relative path|device|task|source model|weight format|symmetric|group size|ratio
"@
        }

        $name = $parts[0].Trim()
        $relativePath = $parts[1].Trim()
        $device = $parts[2].Trim().ToUpperInvariant()
        $task = $parts[3].Trim().ToLowerInvariant()
        $sourceModel = $parts[4].Trim()
        $weightFormat = $parts[5].Trim().ToLowerInvariant()

        $symmetric = Convert-ToBoolean `
            -Value $parts[6] `
            -FieldName "$name symmetric"

        $groupSize = Convert-ToOptionalInteger `
            -Value $parts[7] `
            -FieldName "$name group size"

        $ratio = Convert-ToOptionalDouble `
            -Value $parts[8] `
            -FieldName "$name ratio"

        if ([string]::IsNullOrWhiteSpace($name)) {
            throw 'Model name cannot be empty.'
        }

        if ([string]::IsNullOrWhiteSpace($relativePath)) {
            throw "$name has an empty relative model path."
        }

        if ([string]::IsNullOrWhiteSpace($sourceModel)) {
            throw "$name has an empty source model."
        }

        if ($device -notin @('CPU', 'GPU', 'NPU')) {
            throw `
                "$name has unsupported device '$device'."
        }

        if ($task -notin @('fim', 'chat')) {
            throw `
                "$name has unsupported task '$task'."
        }

        if ([string]::IsNullOrWhiteSpace($weightFormat)) {
            throw `
                "$name has an empty weight format."
        }

        [pscustomobject]@{
            Name         = $name
            RelativePath = $relativePath
            Device       = $device
            Task         = $task
            SourceModel  = $sourceModel
            WeightFormat = $weightFormat
            Symmetric    = $symmetric
            GroupSize    = $groupSize
            Ratio        = $ratio
        }
    }
)

if ($modelEntries.Count -eq 0) {
    throw 'No models were configured in models.txt.'
}

$baseModel = $modelEntries |
    Where-Object {
        $_.Task -eq 'fim'
    } |
    Select-Object -First 1

$instructModel = $modelEntries |
    Where-Object {
        $_.Task -eq 'chat'
    } |
    Select-Object -First 1

if ($null -eq $baseModel) {
    throw 'models.txt must contain one task=fim model.'
}

if ($null -eq $instructModel) {
    throw 'models.txt must contain one task=chat model.'
}

# ---------------------------------------------------------------------------
# Device detection
# ---------------------------------------------------------------------------

$deviceOutput = & $pythonPath -c `
    "from openvino import Core; print('\n'.join(Core().available_devices))"

if ($LASTEXITCODE -ne 0) {
    throw 'Unable to query OpenVINO devices.'
}

$availableDevices = @(
    $deviceOutput |
        ForEach-Object {
            "$_".Trim()
        } |
        Where-Object {
            $_
        }
)

Write-Host ''
Write-Host 'Available OpenVINO devices:' -ForegroundColor Green

foreach ($device in $availableDevices) {
    Write-Host "  $device"
}

# ---------------------------------------------------------------------------
# Hugging Face authentication
# ---------------------------------------------------------------------------

$script:hfAuthChecked = $false

function Ensure-HuggingFaceLogin {
    $tokenCheck = & $pythonPath -c `
        "from huggingface_hub import get_token; raise SystemExit(0 if get_token() else 1)"

    if ($LASTEXITCODE -eq 0) {
        Write-Host `
            'Hugging Face authentication already available.' `
            -ForegroundColor Green

        return
    }

    Write-Host ''
    Write-Host `
        'A model export is required, but no Hugging Face login was found.' `
        -ForegroundColor Yellow

    $secureToken = Read-Host `
        'Enter your Hugging Face token, or press Enter to continue anonymously' `
        -AsSecureString

    $tokenPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR(
        $secureToken
    )

    $hfToken = $null

    try {
        $hfToken = [Runtime.InteropServices.Marshal]::PtrToStringBSTR(
            $tokenPointer
        )
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($tokenPointer)
    }

    if ([string]::IsNullOrWhiteSpace($hfToken)) {
        Write-Host `
            'Continuing without Hugging Face authentication.' `
            -ForegroundColor Yellow

        return
    }

    $previousHfToken = $env:HF_TOKEN

    try {
        $env:HF_TOKEN = $hfToken

        & $pythonPath -c `
            "import os; from huggingface_hub import login; login(token=os.environ['HF_TOKEN'], add_to_git_credential=False)"

        if ($LASTEXITCODE -ne 0) {
            throw 'Hugging Face authentication failed.'
        }

        Write-Host `
            'Hugging Face authentication completed.' `
            -ForegroundColor Green
    }
    finally {
        if ($null -eq $previousHfToken) {
            Remove-Item `
                Env:HF_TOKEN `
                -ErrorAction SilentlyContinue
        }
        else {
            $env:HF_TOKEN = $previousHfToken
        }

        $hfToken = $null
    }
}

# ---------------------------------------------------------------------------
# Export settings and model export
# ---------------------------------------------------------------------------

function Get-ExportSettingsStamp {
    param(
        [Parameter(Mandatory = $true)]
        [pscustomobject]$Model
    )

    $symmetricText = $Model.Symmetric.ToString().ToLowerInvariant()
    $groupSizeText = Format-OptionalInteger $Model.GroupSize
    $ratioText = Format-OptionalDouble $Model.Ratio

    return (
        "source=$($Model.SourceModel)" +
        "|format=$($Model.WeightFormat)" +
        "|device=$($Model.Device)" +
        "|symmetric=$symmetricText" +
        "|group-size=$groupSizeText" +
        "|ratio=$ratioText"
    )
}

function Ensure-OpenVINOExport {
    param(
        [Parameter(Mandatory = $true)]
        [pscustomobject]$Model
    )

    $modelPath = Join-Path `
        $modelsRoot `
        $Model.RelativePath

    $modelMarker = Join-Path `
        $modelPath `
        'openvino_model.xml'

    $settingsMarker = Join-Path `
        $modelPath `
        '.openvino-export-settings'

    $expectedSettings = Get-ExportSettingsStamp $Model
    $settingsMatch = $false

    if (Test-Path -LiteralPath $settingsMarker -PathType Leaf) {
        $actualSettings = (
            Get-Content `
                -LiteralPath $settingsMarker `
                -Raw
        ).Trim()

        $settingsMatch = (
            $actualSettings -eq $expectedSettings
        )
    }

    if (
        (Test-Path -LiteralPath $modelMarker -PathType Leaf) -and
        $settingsMatch
    ) {
        Write-Host `
            "  Existing export matches models.txt: $modelPath" `
            -ForegroundColor Green

        return
    }

    if (-not $script:hfAuthChecked) {
        Ensure-HuggingFaceLogin
        $script:hfAuthChecked = $true
    }

    $stagingPath = "$modelPath.__exporting"

    if (Test-Path -LiteralPath $stagingPath) {
        Remove-Item `
            -LiteralPath $stagingPath `
            -Recurse `
            -Force
    }

    $stagingParent = Split-Path `
        -Path $stagingPath `
        -Parent

    New-Item `
        -ItemType Directory `
        -Path $stagingParent `
        -Force |
        Out-Null

    New-Item `
        -ItemType Directory `
        -Path $stagingPath `
        -Force |
        Out-Null

    $exportArguments = @(
        'export'
        'openvino'
        '--model'
        $Model.SourceModel
        '--weight-format'
        $Model.WeightFormat
    )

    if ($Model.Symmetric) {
        $exportArguments += @(
            '--sym'
        )
    }

    if ($null -ne $Model.GroupSize) {
        $exportArguments += @(
            '--group-size'
            (
                Format-OptionalInteger $Model.GroupSize
            )
        )
    }

    if ($null -ne $Model.Ratio) {
        $exportArguments += @(
            '--ratio'
            (
                Format-OptionalDouble $Model.Ratio
            )
        )
    }

    $exportArguments += @(
        $stagingPath
    )

    Write-Host ''
    Write-Host `
        "Exporting $($Model.SourceModel)..." `
        -ForegroundColor Yellow

    Write-Host `
        "Export settings: $expectedSettings" `
        -ForegroundColor DarkGray

    & $optimumCli @exportArguments

    if ($LASTEXITCODE -ne 0) {
        throw `
            "OpenVINO export failed for $($Model.Name)."
    }

    $stagingModelMarker = Join-Path `
        $stagingPath `
        'openvino_model.xml'

    if (-not (Test-Path -LiteralPath $stagingModelMarker -PathType Leaf)) {
        throw `
            "Export completed without producing $stagingModelMarker"
    }

    $stagingSettingsMarker = Join-Path `
        $stagingPath `
        '.openvino-export-settings'

    $expectedSettings |
        Set-Content `
            -LiteralPath $stagingSettingsMarker `
            -Encoding utf8

    if (Test-Path -LiteralPath $modelPath) {
        $timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
        $partialPath = "$modelPath.partial-$timestamp"

        Move-Item `
            -LiteralPath $modelPath `
            -Destination $partialPath

        Write-Host `
            "Previous export preserved at: $partialPath" `
            -ForegroundColor Yellow
    }

    Move-Item `
        -LiteralPath $stagingPath `
        -Destination $modelPath

    Write-Host `
        "Export completed: $modelPath" `
        -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# Model smoke tests
# ---------------------------------------------------------------------------

function Invoke-ModelSmokeTest {
    param(
        [Parameter(Mandatory = $true)]
        [pscustomobject]$Model
    )

    $modelPath = Join-Path `
        $modelsRoot `
        $Model.RelativePath

    Write-Host ''
    Write-Host `
        "Running model smoke test: $($Model.Name)" `
        -ForegroundColor Yellow

    $oldModelDir = $env:OPENVINO_MODEL_DIR
    $oldDevice = $env:OPENVINO_DEVICE
    $oldTask = $env:OPENVINO_TASK

    try {
        $env:OPENVINO_MODEL_DIR = $modelPath
        $env:OPENVINO_DEVICE = $Model.Device
        $env:OPENVINO_TASK = $Model.Task

        $smokeTest = @'
import os
import openvino_genai as ov_genai

model_path = os.environ["OPENVINO_MODEL_DIR"]
device = os.environ["OPENVINO_DEVICE"]
task = os.environ["OPENVINO_TASK"]

pipe = ov_genai.LLMPipeline(model_path, device)

if task == "fim":
    prefix = "def add(a, b):\n    "
    suffix = "\n"

    prompt = (
        "<|fim_prefix|>"
        + prefix
        + "<|fim_suffix|>"
        + suffix
        + "<|fim_middle|>"
    )
else:
    prompt = (
        "Write one short sentence confirming "
        "that the model is running."
    )

result = pipe.generate(
    prompt,
    max_new_tokens=64
)

print("Smoke-test output:")
print(result)
'@

        $smokeTest | & $pythonPath -

        if ($LASTEXITCODE -ne 0) {
            throw `
                "Model smoke test failed for $($Model.Name)."
        }

        Write-Host `
            "  Passed: $($Model.Name)" `
            -ForegroundColor Green
    }
    finally {
        if ($null -eq $oldModelDir) {
            Remove-Item `
                Env:OPENVINO_MODEL_DIR `
                -ErrorAction SilentlyContinue
        }
        else {
            $env:OPENVINO_MODEL_DIR = $oldModelDir
        }

        if ($null -eq $oldDevice) {
            Remove-Item `
                Env:OPENVINO_DEVICE `
                -ErrorAction SilentlyContinue
        }
        else {
            $env:OPENVINO_DEVICE = $oldDevice
        }

        if ($null -eq $oldTask) {
            Remove-Item `
                Env:OPENVINO_TASK `
                -ErrorAction SilentlyContinue
        }
        else {
            $env:OPENVINO_TASK = $oldTask
        }
    }
}

# ---------------------------------------------------------------------------
# Server functions
# ---------------------------------------------------------------------------

function Invoke-ServerGet {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Uri
    )

    return Invoke-RestMethod `
        -Method Get `
        -Uri $Uri `
        -TimeoutSec 5
}

function Invoke-ServerPost {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Uri,

        [Parameter(Mandatory = $true)]
        [hashtable]$Payload
    )

    $json = $Payload |
        ConvertTo-Json -Depth 10

    return Invoke-RestMethod `
        -Method Post `
        -Uri $Uri `
        -ContentType 'application/json' `
        -Body $json `
        -TimeoutSec 120
}

function Test-ExistingServer {
    try {
        $health = Invoke-ServerGet `
            "$serverBaseUrl/health"

        return (
            $null -ne $health -and
            $health.status -eq 'ok'
        )
    }
    catch {
        return $false
    }
}

function Start-OpenVINOServer {
    if (Test-ExistingServer) {
        Write-Host `
            'A healthy server is already running on port 4001.' `
            -ForegroundColor Yellow

        return
    }

    if (Test-Path -LiteralPath $serverLog) {
        Remove-Item `
            -LiteralPath $serverLog `
            -Force
    }

    if (Test-Path -LiteralPath $serverErrorLog) {
        Remove-Item `
            -LiteralPath $serverErrorLog `
            -Force
    }

    $previousServerHost = $env:OPENVINO_SERVER_HOST
    $previousServerPort = $env:OPENVINO_SERVER_PORT

    $env:OPENVINO_SERVER_HOST = $serverHost
    $env:OPENVINO_SERVER_PORT = "$serverPort"

    try {
        $script:serverProcess = Start-Process `
            -FilePath $pythonPath `
            -ArgumentList @(
                $serverScript
            ) `
            -WorkingDirectory $scriptRoot `
            -RedirectStandardOutput $serverLog `
            -RedirectStandardError $serverErrorLog `
            -WindowStyle Hidden `
            -PassThru

        $script:startedServer = $true
    }
    finally {
        if ($null -eq $previousServerHost) {
            Remove-Item `
                Env:OPENVINO_SERVER_HOST `
                -ErrorAction SilentlyContinue
        }
        else {
            $env:OPENVINO_SERVER_HOST = $previousServerHost
        }

        if ($null -eq $previousServerPort) {
            Remove-Item `
                Env:OPENVINO_SERVER_PORT `
                -ErrorAction SilentlyContinue
        }
        else {
            $env:OPENVINO_SERVER_PORT = $previousServerPort
        }
    }

    Write-Host `
        "Started server process ID $($serverProcess.Id)." `
        -ForegroundColor Cyan
}

function Wait-ForOpenVINOServer {
    param(
        [int]$TimeoutSeconds = 90
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)

    while ((Get-Date) -lt $deadline) {
        if (
            $null -ne $serverProcess -and
            $serverProcess.HasExited
        ) {
            throw @"
OpenVINO server exited before becoming healthy.

Server stdout:
$serverLog

Server stderr:
$serverErrorLog
"@
        }

        try {
            $health = Invoke-ServerGet `
                "$serverBaseUrl/health"

            if (
                $null -ne $health -and
                $health.status -eq 'ok'
            ) {
                Write-Host `
                    'Server health check passed.' `
                    -ForegroundColor Green

                return
            }
        }
        catch {
            # Server may still be loading both models.
        }

        Start-Sleep -Seconds 1
    }

    throw @"
Timed out waiting for the OpenVINO server.

Server stdout:
$serverLog

Server stderr:
$serverErrorLog
"@
}

function Test-OpenVINOServer {
    Write-Host ''
    Write-Host `
        'Testing server endpoints...' `
        -ForegroundColor Yellow

    $health = Invoke-ServerGet `
        "$serverBaseUrl/health"

    if ($health.status -ne 'ok') {
        throw 'Server health endpoint did not return status=ok.'
    }

    Write-Host `
        '  /health passed.' `
        -ForegroundColor Green

    $modelsResponse = Invoke-ServerGet `
        "$serverBaseUrl/v1/models"

    $advertisedNames = @(
        $modelsResponse.data |
            ForEach-Object {
                "$($_.id)"
            }
    )

    foreach ($expectedModel in @($baseModel, $instructModel)) {
        if ($advertisedNames -notcontains $expectedModel.Name) {
            throw `
                "Server did not advertise model '$($expectedModel.Name)'."
        }
    }

    Write-Host `
        '  /v1/models passed.' `
        -ForegroundColor Green

    $chatResponse = Invoke-ServerPost `
        "$serverBaseUrl/v1/chat/completions" `
        @{
            model = $instructModel.Name
            messages = @(
                @{
                    role = 'user'
                    content = 'Reply with exactly one short sentence confirming that the server is running.'
                }
            )
            max_tokens = 32
            temperature = 0.0
        }

    $chatText = "$(
        $chatResponse.choices[0].message.content
    )".Trim()

    if ([string]::IsNullOrWhiteSpace($chatText)) {
        throw `
            'Instruct chat completion returned empty output.'
    }

    Write-Host `
        "  Instruct completion passed: $chatText" `
        -ForegroundColor Green

    $fimPrompt = (
        '<|fim_prefix|>' +
        "def add(a, b):`n    " +
        '<|fim_suffix|>' +
        "`n" +
        '<|fim_middle|>'
    )

    $fimResponse = Invoke-ServerPost `
        "$serverBaseUrl/v1/completions" `
        @{
            model = $baseModel.Name
            prompt = $fimPrompt
            max_tokens = 32
            temperature = 0.0
        }

    $fimText = "$(
        $fimResponse.choices[0].text
    )".Trim()

    if ([string]::IsNullOrWhiteSpace($fimText)) {
        throw `
            'Base FIM completion returned empty output.'
    }

    Write-Host `
        "  Base FIM completion passed: $fimText" `
        -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# Main workflow
# ---------------------------------------------------------------------------

try {
    foreach ($model in $modelEntries) {
        Write-Host ''
        Write-Host `
            "Preparing model: $($model.Name)" `
            -ForegroundColor Cyan

        if ($availableDevices -notcontains $model.Device) {
            throw `
                "Configured device '$($model.Device)' is not available."
        }

        Write-Host "  Device:        $($model.Device)"
        Write-Host "  Task:          $($model.Task)"
        Write-Host "  Source model:  $($model.SourceModel)"
        Write-Host "  Weight format: $($model.WeightFormat)"
        Write-Host `
            "  Symmetric:     $($model.Symmetric.ToString().ToLowerInvariant())"
        Write-Host `
            "  Group size:    $(Format-OptionalInteger $model.GroupSize)"
        Write-Host `
            "  Ratio:         $(Format-OptionalDouble $model.Ratio)"

        Ensure-OpenVINOExport $model
        Invoke-ModelSmokeTest $model
    }

    Start-OpenVINOServer
    Wait-ForOpenVINOServer
    Test-OpenVINOServer

    Write-Host ''
    Write-Host `
        'All model, server, and API tests passed.' `
        -ForegroundColor Green

    if ($startedServer) {
        Write-Host ''
        Write-Host `
            "Server is still running at $serverBaseUrl" `
            -ForegroundColor Cyan

        Write-Host `
            "Server process ID: $($serverProcess.Id)" `
            -ForegroundColor Cyan
    }
}
catch {
    if (
        $startedServer -and
        $null -ne $serverProcess -and
        -not $serverProcess.HasExited
    ) {
        Write-Warning `
            'Tests failed. Stopping the server process.'

        Stop-Process `
            -Id $serverProcess.Id `
            -Force `
            -ErrorAction SilentlyContinue
    }

    throw
}
