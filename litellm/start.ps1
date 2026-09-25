#requires -Version 5.1
<#
    start-litellm.ps1
    -----------------
    Brings up the LiteLLM router stack with secrets pulled from 1Password,
    waits for the container to be healthy, then smoke-tests the endpoints:
        blackwell-act-qwen25-coder-14b   -> Pro Max (Ollama, qwen2.5-coder:14b)
        gb10-act-qwen3-coder-next        -> GB10     (Ollama, qwen3-coder-next)
        blackwell-fim-qwen25-coder-1p5b  -> Pro Max (Ollama, qwen2.5-coder:1.5b-base, FIM)

    Usage:
        .\start-litellm.ps1                # bring up + smoke test
        .\start-litellm.ps1 -SkipTests     # bring up only
        .\start-litellm.ps1 -Down          # tear the stack down
        .\start-litellm.ps1 -Follow        # bring up + tail logs
#>

[CmdletBinding()]
param(
    [switch]$SkipTests,
    [switch]$Down,
    [switch]$Follow,
    [string]$EnvFile   = ".env",
    [string]$RouterUrl = "http://localhost:4000",
    [int]$HealthTimeoutSec = 90
)

$ErrorActionPreference = "Stop"

# --- Always operate from the script's own directory (where compose + .env live) ---
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $ScriptDir

function Write-Step($msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "  [OK] $msg"   -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "  [!!] $msg"   -ForegroundColor Yellow }
function Write-Err($msg)  { Write-Host "  [XX] $msg"   -ForegroundColor Red }

# --- Tear-down path -----------------------------------------------------------
if ($Down) {
    Write-Step "Stopping LiteLLM stack"
    docker compose down
    Write-Ok "Stack stopped."
    return
}

# --- 1. Preflight checks ------------------------------------------------------
Write-Step "Preflight"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Err "Docker CLI not found on PATH."; exit 1
}
try { docker info *> $null; Write-Ok "Docker daemon reachable." }
catch { Write-Err "Docker daemon not reachable. Start Docker Desktop and retry."; exit 1 }

if (-not (Get-Command op -ErrorAction SilentlyContinue)) {
    Write-Err "1Password CLI (op) not found. Install: winget install AgileBits.1Password.CLI"; exit 1
}
if (-not (Test-Path $EnvFile))      { Write-Err "Env file '$EnvFile' not found in $ScriptDir."; exit 1 }
if (-not (Test-Path "docker-compose.yml")) { Write-Err "docker-compose.yml not found in $ScriptDir."; exit 1 }
Write-Ok "op, $EnvFile, and docker-compose.yml present."

# --- 2. Ensure a 1Password session -------------------------------------------
Write-Step "1Password sign-in"
try {
    op whoami *> $null
    Write-Ok "Existing 1Password session found."
} catch {
    Write-Warn "No active session - signing in..."
    op signin
    Write-Ok "Signed in."
}

# --- 3. Bring up the stack via op run (resolves op:// refs at runtime) --------
Write-Step "Starting LiteLLM container (op run -> docker compose up -d)"
op run --env-file="$EnvFile" -- docker compose up -d
Write-Ok "Compose up issued."

# --- 4. Wait for the router to answer ----------------------------------------
Write-Step "Waiting for router to become healthy (timeout ${HealthTimeoutSec}s)"
$key = op read "op://Homelab/LiteLLM/LITELLM_MASTER_KEY"
$headers = @{ Authorization = "Bearer $key" }
$deadline = (Get-Date).AddSeconds($HealthTimeoutSec)
$healthy = $false
while ((Get-Date) -lt $deadline) {
    try {
        $null = Invoke-RestMethod -Uri "$RouterUrl/v1/models" -Headers $headers -TimeoutSec 5
        $healthy = $true
        break
    } catch {
        Start-Sleep -Seconds 3
        Write-Host "  ...waiting" -ForegroundColor DarkGray
    }
}
if (-not $healthy) {
    Write-Err "Router did not respond within ${HealthTimeoutSec}s. Check: docker compose logs litellm"
    exit 1
}
Write-Ok "Router is answering on $RouterUrl."

# --- 5. Smoke-test endpoints --------------------------------------------------
if (-not $SkipTests) {
    Write-Step "Smoke-testing chat endpoints"
    $endpoints = @("blackwell-act-qwen25-coder-14b", "gb10-act-qwen3-coder-next", "gb10-plan-qwen38-27b")
    $results = @()
    foreach ($endpoint in $endpoints) {
        $body = @{
            model    = $endpoint
            messages = @(@{ role = "user"; content = "Reply with the single word: OK" })
            max_tokens = 10
        } | ConvertTo-Json -Depth 5

        try {
            $sw = [System.Diagnostics.Stopwatch]::StartNew()
            $resp = Invoke-RestMethod -Uri "$RouterUrl/v1/chat/completions" `
                        -Method Post -ContentType "application/json" `
                        -Headers $headers -Body $body -TimeoutSec 120
            $sw.Stop()
            $text = $resp.choices[0].message.content
            Write-Ok ("{0,-18} responded in {1,5} ms  -> {2}" -f $endpoint, $sw.ElapsedMilliseconds, ($text -replace '\s+',' ').Trim())
            $results += [pscustomobject]@{ Endpoint=$endpoint; Status="OK"; Ms=$sw.ElapsedMilliseconds }
        } catch {
            Write-Warn ("{0,-18} FAILED: {1}" -f $endpoint, $_.Exception.Message)
            $results += [pscustomobject]@{ Endpoint=$endpoint; Status="FAIL"; Ms=$null }
        }
    }

    # --- 5b. FIM autocomplete endpoint (uses /v1/completions with prompt + suffix) ---
    Write-Step "Smoke-testing FIM autocomplete (blackwell-fim-qwen25-coder-1p5b)"
    $fimBody = @{
        model      = "blackwell-fim-qwen25-coder-1p5b"
        prompt     = "def reverse_string(s):`n    "
        suffix     = "`n    return result"
        max_tokens = 32
    } | ConvertTo-Json -Depth 5

    try {
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        $fimResp = Invoke-RestMethod -Uri "$RouterUrl/v1/completions" `
                    -Method Post -ContentType "application/json" `
                    -Headers $headers -Body $fimBody -TimeoutSec 60
        $sw.Stop()
        $fimText = $fimResp.choices[0].text
        if ([string]::IsNullOrWhiteSpace($fimText)) {
            Write-Warn ("{0,-18} responded EMPTY in {1} ms  -> router may be dropping `suffix`." -f "blackwell-fim-qwen25-coder-1p5b", $sw.ElapsedMilliseconds)
            $results += [pscustomobject]@{ Endpoint="blackwell-fim-qwen25-coder-1p5b"; Status="EMPTY"; Ms=$sw.ElapsedMilliseconds }
        } else {
            Write-Ok ("{0,-18} completed in {1,5} ms  -> {2}" -f "blackwell-fim-qwen25-coder-1p5b", $sw.ElapsedMilliseconds, ($fimText -replace '\s+',' ').Trim())
            $results += [pscustomobject]@{ Endpoint="blackwell-fim-qwen25-coder-1p5b"; Status="OK"; Ms=$sw.ElapsedMilliseconds }
        }
    } catch {
        Write-Warn ("{0,-18} FAILED: {1}" -f "blackwell-fim-qwen25-coder-1p5b", $_.Exception.Message)
        $results += [pscustomobject]@{ Endpoint="blackwell-fim-qwen25-coder-1p5b"; Status="FAIL"; Ms=$null }
    }

    Write-Step "Summary"
    $results | Format-Table -AutoSize

    if ($results.Where({$_.Status -in @("FAIL","EMPTY")}).Count -gt 0) {
        Write-Warn "One or more endpoints failed. Reminders:"
        Write-Warn "  - Blackwell/GB10 chat -> check Tailscale is up + Ollama bound to 0.0.0.0:11435 on that host."
        Write-Warn "  - blackwell-fim-qwen25-coder-1p5b -> 404/EMPTY means router drops FIM; use direct-to-Blackwell in Continue."
    } else {
        Write-Ok "All endpoints responded (chat + FIM). Stack is live end to end."
    }
}

# --- 6. Optionally follow logs -----------------------------------------------
if ($Follow) {
    Write-Step "Following litellm logs (Ctrl+C to detach)"
    docker compose logs -f litellm
}

Write-Host "`nClients -> Base URL: $RouterUrl/v1   (key pulled from 1Password)" -ForegroundColor Cyan
