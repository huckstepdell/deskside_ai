#requires -Version 5.1
<#
    start-litellm.ps1
    -----------------
    Brings up the LiteLLM router stack with secrets pulled from 1Password,
    waits for the container to be healthy, then smoke-tests all three tiers:
        tier1-local-npu   -> Pro 7  (Foundry Local, NPU, via host.docker.internal)
        tier2-blackwell   -> Pro Max (Ollama)
        tier3-gb10        -> GB10    (Ollama)

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
    # If no valid session this throws; --raw keeps it quiet on success
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

# --- 5. Smoke-test each tier --------------------------------------------------
if (-not $SkipTests) {
    Write-Step "Smoke-testing all three tiers"
    $tiers = @("tier1-local-npu", "tier2-blackwell", "tier3-gb10")
    $results = @()
    foreach ($tier in $tiers) {
        $body = @{
            model    = $tier
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
            Write-Ok ("{0,-18} responded in {1,5} ms  -> {2}" -f $tier, $sw.ElapsedMilliseconds, ($text -replace '\s+',' ').Trim())
            $results += [pscustomobject]@{ Tier=$tier; Status="OK"; Ms=$sw.ElapsedMilliseconds }
        } catch {
            Write-Warn ("{0,-18} FAILED: {1}" -f $tier, $_.Exception.Message)
            $results += [pscustomobject]@{ Tier=$tier; Status="FAIL"; Ms=$null }
        }
    }

    Write-Step "Summary"
    $results | Format-Table -AutoSize

    if ($results.Where({$_.Status -eq "FAIL"}).Count -gt 0) {
        Write-Warn "One or more tiers failed. Reminders:"
        Write-Warn "  - tier1 fails  -> check 'foundry service status' (port is dynamic; was 56194)."
        Write-Warn "  - tier2/3 fail -> check Tailscale is up + Ollama bound to 0.0.0.0:11435 on that host."
        Write-Warn "  - Fallbacks route failed tiers down to tier1-local-npu (NPU) automatically."
    } else {
        Write-Ok "All three tiers responded. Stack is live end to end."
    }
}

# --- 6. Optionally follow logs -----------------------------------------------
if ($Follow) {
    Write-Step "Following litellm logs (Ctrl+C to detach)"
    docker compose logs -f litellm
}

Write-Host "`nClients -> Base URL: $RouterUrl/v1   (key pulled from 1Password)" -ForegroundColor Cyan
