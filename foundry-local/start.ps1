param(
    [ValidateSet("qwen2.5-coder-7b", "qwen2.5-coder-1.5b", "qwen2.5-coder-0.5b", "all")]
    [string]$Model = "all"
)

$models = @(
    #"qwen2.5-coder-0.5b",
    #"qwen2.5-coder-1.5b"
    "qwen2.5-coder-7b"
)

if ($Model -ne "all") {
    $models = @($Model)
}

Write-Host "Starting Foundry Local server..." -ForegroundColor Cyan
foundry server start -p 56194

foreach ($modelName in $models) {
    Write-Host "Loading model: $modelName" -ForegroundColor Yellow
    foundry model load $modelName
}

foundry status

Write-Host ""
Write-Host "Available models:" -ForegroundColor Cyan
foreach ($modelName in $models) {
    Write-Host "  - $modelName"
}