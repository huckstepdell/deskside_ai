#requires -Version 5.1

[CmdletBinding()]
param(
    [switch]$SkipTests,
    [switch]$Down,
    [switch]$Follow,
    [string]$EnvFile = ".env",
    [string]$RouterUrl = "http://localhost:4000",
    [int]$HealthTimeoutSec = 90
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path `
    -Parent `
    $MyInvocation.MyCommand.Definition

Set-Location $ScriptDir

function Write-Step {
    param([string]$Message)
    Write-Host "`n=== $Message ===" -ForegroundColor Cyan
}

function Write-Ok {
    param([string]$Message)
    Write-Host "  [OK] $Message" -ForegroundColor Green
}

function Write-Warn {
    param([string]$Message)
    Write-Host "  [!!] $Message" -ForegroundColor Yellow
}

function Write-Err {
    param([string]$Message)
    Write-Host "  [XX] $Message" -ForegroundColor Red
}

function Invoke-ChatSmokeTest {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Model,

        [Parameter(Mandatory = $true)]
        [string]$Prompt,

        [Parameter(Mandatory = $true)]
        [int]$MaxTokens,

        [Parameter(Mandatory = $true)]
        [double]$Temperature,

        [Parameter(Mandatory = $true)]
        [hashtable]$Headers,

        [Parameter(Mandatory = $true)]
        [int]$TimeoutSec
    )

    $body = @{
        model = $Model
        messages = @(
            @{
                role = "user"
                content = $Prompt
            }
        )
        max_tokens = $MaxTokens
        temperature = $Temperature
    } |
        ConvertTo-Json -Depth 6

    try {
        $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()

        $response = Invoke-RestMethod `
            -Uri "$RouterUrl/v1/chat/completions" `
            -Method Post `
            -ContentType "application/json" `
            -Headers $Headers `
            -Body $body `
            -TimeoutSec $TimeoutSec

        $stopwatch.Stop()

        $message = $response.choices[0].message
        $text = "$($message.content)".Trim()
        $reasoning = "$($message.reasoning_content)".Trim()

        if ([string]::IsNullOrWhiteSpace($text)) {
            if (-not [string]::IsNullOrWhiteSpace($reasoning)) {
                Write-Warn `
                    ("{0,-42} returned reasoning but no content in {1,5} ms" -f `
                        $Model,
                        $stopwatch.ElapsedMilliseconds)

                return [pscustomobject]@{
                    Endpoint = $Model
                    Type     = "Chat"
                    Status   = "EMPTY_CONTENT"
                    Ms       = $stopwatch.ElapsedMilliseconds
                }
            }

            Write-Warn `
                ("{0,-42} EMPTY in {1,5} ms" -f `
                    $Model,
                    $stopwatch.ElapsedMilliseconds)

            return [pscustomobject]@{
                Endpoint = $Model
                Type     = "Chat"
                Status   = "EMPTY"
                Ms       = $stopwatch.ElapsedMilliseconds
            }
        }

        Write-Ok `
            ("{0,-42} responded in {1,5} ms -> {2}" -f `
                $Model,
                $stopwatch.ElapsedMilliseconds,
                ($text -replace '\s+', ' ').Trim())

        return [pscustomobject]@{
            Endpoint = $Model
            Type     = "Chat"
            Status   = "OK"
            Ms       = $stopwatch.ElapsedMilliseconds
        }
    }
    catch {
        Write-Warn `
            ("{0,-42} FAILED: {1}" -f `
                $Model,
                $_.Exception.Message)

        return [pscustomobject]@{
            Endpoint = $Model
            Type     = "Chat"
            Status   = "FAIL"
            Ms       = $null
        }
    }
}

function Invoke-FimSmokeTest {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Model,

        [Parameter(Mandatory = $true)]
        [string]$Prompt,

        [Parameter(Mandatory = $true)]
        [hashtable]$Headers,

        [Parameter(Mandatory = $true)]
        [int]$TimeoutSec
    )

    $body = @{
        model = $Model
        prompt = $Prompt
        max_tokens = 32
        temperature = 0.0
    } |
        ConvertTo-Json -Depth 6

    try {
        $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()

        $response = Invoke-RestMethod `
            -Uri "$RouterUrl/v1/completions" `
            -Method Post `
            -ContentType "application/json" `
            -Headers $Headers `
            -Body $body `
            -TimeoutSec $TimeoutSec

        $stopwatch.Stop()

        $text = "$(
            $response.choices[0].text
        )".Trim()

        if ([string]::IsNullOrWhiteSpace($text)) {
            Write-Warn `
                ("{0,-42} EMPTY in {1,5} ms" -f `
                    $Model,
                    $stopwatch.ElapsedMilliseconds)

            return [pscustomobject]@{
                Endpoint = $Model
                Type     = "FIM"
                Status   = "EMPTY"
                Ms       = $stopwatch.ElapsedMilliseconds
            }
        }

        Write-Ok `
            ("{0,-42} completed in {1,5} ms -> {2}" -f `
                $Model,
                $stopwatch.ElapsedMilliseconds,
                ($text -replace '\s+', ' ').Trim())

        return [pscustomobject]@{
            Endpoint = $Model
            Type     = "FIM"
            Status   = "OK"
            Ms       = $stopwatch.ElapsedMilliseconds
        }
    }
    catch {
        Write-Warn `
            ("{0,-42} FAILED: {1}" -f `
                $Model,
                $_.Exception.Message)

        return [pscustomobject]@{
            Endpoint = $Model
            Type     = "FIM"
            Status   = "FAIL"
            Ms       = $null
        }
    }
}

if ($Down) {
    Write-Step "Stopping LiteLLM stack"

    docker compose down

    Write-Ok "Stack stopped."
    return
}

Write-Step "Preflight"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Err "Docker CLI not found on PATH."
    exit 1
}

try {
    docker info *> $null
    Write-Ok "Docker daemon reachable."
}
catch {
    Write-Err `
        "Docker daemon not reachable. Start Docker Desktop and retry."

    exit 1
}

if (-not (Get-Command op -ErrorAction SilentlyContinue)) {
    Write-Err `
        "1Password CLI not found. Install with: winget install AgileBits.1Password.CLI"

    exit 1
}

if (-not (Test-Path $EnvFile)) {
    Write-Err `
        "Env file '$EnvFile' not found in $ScriptDir."

    exit 1
}

if (-not (Test-Path "docker-compose.yml")) {
    Write-Err `
        "docker-compose.yml not found in $ScriptDir."

    exit 1
}

Write-Ok `
    "op, $EnvFile, and docker-compose.yml present."

Write-Step "1Password sign-in"

try {
    op whoami *> $null
    Write-Ok "Existing 1Password session found."
}
catch {
    Write-Warn "No active session - signing in..."

    op signin

    Write-Ok "Signed in."
}

Write-Step `
    "Starting LiteLLM container with 1Password-resolved secrets"

op run `
    --env-file="$EnvFile" `
    -- `
    docker compose up -d --force-recreate --remove-orphans

Write-Ok "Compose up issued."

Write-Step `
    "Waiting for router to become healthy (timeout ${HealthTimeoutSec}s)"

$key = op read `
    "op://Homelab/LiteLLM/LITELLM_MASTER_KEY"

$headers = @{
    Authorization = "Bearer $key"
}

$deadline = (
    Get-Date
).AddSeconds($HealthTimeoutSec)

$healthy = $false

while ((Get-Date) -lt $deadline) {
    try {
        $null = Invoke-RestMethod `
            -Uri "$RouterUrl/v1/models" `
            -Headers $headers `
            -TimeoutSec 5

        $healthy = $true
        break
    }
    catch {
        Start-Sleep -Seconds 3
        Write-Host "  ...waiting" -ForegroundColor DarkGray
    }
}

if (-not $healthy) {
    Write-Err `
        "Router did not respond within ${HealthTimeoutSec}s."

    Write-Err `
        "Check: docker compose logs litellm"

    exit 1
}

Write-Ok `
    "Router is answering on $RouterUrl."

if (-not $SkipTests) {
    $results = @()

    Write-Step "Testing LiteLLM model catalogue"

    try {
        $modelsResponse = Invoke-RestMethod `
            -Uri "$RouterUrl/v1/models" `
            -Headers $headers `
            -TimeoutSec 15

        $advertisedModels = @(
            $modelsResponse.data |
                ForEach-Object {
                    "$($_.id)"
                }
        )

        $expectedModels = @(
            "npu-act-qwen25-coder-0p5b-instruct"
            "npu-fim-qwen25-coder-0p5b"
            "blackwell-act-qwen25-coder-14b"
            "gb10-act-qwen3-coder-next"
            "gb10-plan-qwen38-27b"
            "blackwell-fim-qwen25-coder-1p5b"
        )

        foreach ($expectedModel in $expectedModels) {
            if ($advertisedModels -contains $expectedModel) {
                Write-Ok `
                    "Model advertised: $expectedModel"
            }
            else {
                Write-Warn `
                    "Model missing from /v1/models: $expectedModel"

                $results += [pscustomobject]@{
                    Endpoint = $expectedModel
                    Type     = "Catalogue"
                    Status   = "FAIL"
                    Ms       = $null
                }
            }
        }
    }
    catch {
        Write-Warn `
            "Model catalogue test failed: $($_.Exception.Message)"

        $results += [pscustomobject]@{
            Endpoint = "/v1/models"
            Type     = "Catalogue"
            Status   = "FAIL"
            Ms       = $null
        }
    }

    Write-Step "Smoke-testing chat endpoints"

    $chatTests = @(
        @{
            Model       = "npu-act-qwen25-coder-0p5b-instruct"
            Prompt      = "Reply with the single word: OK"
            MaxTokens   = 32
            Temperature = 0.0
        }
        @{
            Model       = "blackwell-act-qwen25-coder-14b"
            Prompt      = "Reply with the single word: OK"
            MaxTokens   = 32
            Temperature = 0.0
        }
        @{
            Model       = "gb10-act-qwen3-coder-next"
            Prompt      = "Reply with the single word: OK"
            MaxTokens   = 32
            Temperature = 0.0
        }
        @{
            Model       = "gb10-plan-qwen38-27b"
            Prompt      = "/no_think`nReply with exactly the word: OK"
            MaxTokens   = 64
            Temperature = 0.0
        }

    )

    foreach ($test in $chatTests) {
        $results += Invoke-ChatSmokeTest `
            -Model $test.Model `
            -Prompt $test.Prompt `
            -MaxTokens $test.MaxTokens `
            -Temperature $test.Temperature `
            -Headers $headers `
            -TimeoutSec 120
    }

    Write-Step "Smoke-testing NPU FIM autocomplete"

    $npuFimPrompt = (
        "<|fim_prefix|>" +
        "def reverse_string(s):`n    " +
        "<|fim_suffix|>" +
        "`n    return result" +
        "<|fim_middle|>"
    )

    $results += Invoke-FimSmokeTest `
        -Model "npu-fim-qwen25-coder-0p5b" `
        -Prompt $npuFimPrompt `
        -Headers $headers `
        -TimeoutSec 120

    Write-Step "Smoke-testing Blackwell FIM autocomplete"

    $blackwellFimPrompt = (
        "def reverse_string(s):`n    "
    )

    $results += Invoke-FimSmokeTest `
        -Model "blackwell-fim-qwen25-coder-1p5b" `
        -Prompt $blackwellFimPrompt `
        -Headers $headers `
        -TimeoutSec 120

    Write-Step "Smoke-test summary"

    $results |
        Format-Table -AutoSize

    $failedResults = @(
        $results |
            Where-Object {
                $_.Status -in @(
                    "FAIL",
                    "EMPTY",
                    "EMPTY_CONTENT"
                )
            }
    )

    if ($failedResults.Count -gt 0) {
        Write-Warn `
            "One or more endpoints failed or returned empty output."
    }
    else {
        Write-Ok `
            "All model catalogue, chat, and FIM tests passed."
    }
}
else {
    Write-Warn "Smoke tests skipped."
}

if ($Follow) {
    Write-Step `
        "Following LiteLLM logs (Ctrl+C to detach)"

    docker compose logs -f litellm
}

Write-Host ''
Write-Host `
    "Clients -> Base URL: $RouterUrl/v1" `
    -ForegroundColor Cyan

Write-Host `
    "Authentication key is pulled from 1Password." `
    -ForegroundColor Cyan
