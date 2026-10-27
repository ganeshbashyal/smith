<#
.SYNOPSIS
  Opt-in, read-only acceptance checks against an already-running local Mr Smith.

.DESCRIPTION
  Does not start/stop processes or load a model. -RunInference explicitly opts
  into one chat request, which can load the selected model and save history.
#>
param(
    [uri]$BaseUrl = [uri]"http://127.0.0.1:8000",
    [switch]$OpenBrowser,
    [switch]$RunInference
)

$ErrorActionPreference = "Stop"
if ($BaseUrl.Scheme -ne "http" -or $BaseUrl.Host -notin @("127.0.0.1", "localhost")) {
    throw "Live acceptance is restricted to http://127.0.0.1 or http://localhost."
}

$base = $BaseUrl.AbsoluteUri.TrimEnd("/")
function Get-LocalJson([string]$Path) {
    Invoke-RestMethod -Uri "$base$Path" -TimeoutSec 10
}

$checks = @(
    @{ Path = "/morning"; Name = "Morning page" },
    @{ Path = "/admin"; Name = "Admin page" },
    @{ Path = "/api/personas"; Name = "Persona API" },
    @{ Path = "/api/knowledge/status"; Name = "Knowledge status" },
    @{ Path = "/api/sources"; Name = "Source status" }
)
foreach ($check in $checks) {
    $response = Invoke-WebRequest -Uri "$base$($check.Path)" -TimeoutSec 10
    if ($response.StatusCode -ne 200) {
        throw "$($check.Name) returned HTTP $($response.StatusCode)."
    }
    Write-Host "$($check.Name): HTTP 200" -ForegroundColor Green
}

$models = Get-LocalJson "/api/models"
if (-not $models.selected_model) {
    throw "Model status did not report a selected model."
}
if ($models.selected_model -notin $models.installed_models) {
    throw "Selected model '$($models.selected_model)' is not in Ollama's installed model list."
}
$ollama = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 5
if ($models.selected_model -notin @($ollama.models | ForEach-Object { $_.name })) {
    throw "Selected model '$($models.selected_model)' is not installed in the local Ollama service."
}
Write-Host "Selected model: $($models.selected_model)" -ForegroundColor Green
Write-Host "Ollama installed models: $($ollama.models.Count)" -ForegroundColor Green
Write-Host "Loaded model: $(if ($models.active_model) { $models.active_model } else { 'none (idle)' })"

if ($env:MR_SMITH_ADMIN_TOKEN) {
    $headers = @{ Authorization = "Bearer $env:MR_SMITH_ADMIN_TOKEN" }
    $serviceStatus = Invoke-RestMethod -Uri "$base/api/admin/services" -Headers $headers -TimeoutSec 15
    Write-Host "Resource status: $($serviceStatus.resources.status); Ollama: $($serviceStatus.ollama.status)"
} else {
    Write-Host "Admin resource check skipped: MR_SMITH_ADMIN_TOKEN is not set in this PowerShell process."
}

if ($RunInference) {
    $payload = @{ question = "In one sentence, explain what Mr Smith does."; persona = "general" } |
        ConvertTo-Json -Compress
    $answer = Invoke-RestMethod -Uri "$base/api/chat" -Method Post -ContentType "application/json" -Body $payload -TimeoutSec 300
    if (-not $answer.answer) {
        throw "The inference check returned an empty answer."
    }
    Write-Host "Inference: received a response from $($answer.route.model); a history entry was saved." -ForegroundColor Green
}

if ($OpenBrowser) {
    Start-Process "$base/morning"
}

Write-Host "Acceptance checks passed. No services were started or stopped."
