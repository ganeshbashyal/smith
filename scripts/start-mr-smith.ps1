<#
.SYNOPSIS
  Fresh-start launcher for Mr Smith (Windows PowerShell).

.DESCRIPTION
  Idempotent: creates the virtualenv and installs the package only if missing,
  otherwise reuses what's there. Never hard-codes an admin token - pass one in
  or set $env:MR_SMITH_ADMIN_TOKEN yourself beforehand. Without a token, admin
  mutations (model switch, document ingest) return HTTP 503 by design; chat,
  morning brief and read-only finance endpoints still work.

.PARAMETER AdminToken
  Optional. Sets MR_SMITH_ADMIN_TOKEN for this run only (not persisted, not
  written to disk). Omit to reuse an already-exported env var, or to run
  without admin features.

.PARAMETER ExternalFeeds
  Switch. Enables live BBC/Yahoo/F1 feeds for this run (MR_SMITH_EXTERNAL_FEEDS=1).

.PARAMETER DailyModel
  Optional override for MR_SMITH_DAILY_MODEL.

.PARAMETER ResearchModel
  Optional override for MR_SMITH_RESEARCH_MODEL.

.PARAMETER Port
  Uvicorn port. Default 8000.

.PARAMETER Detached
  Switch. Launch uvicorn as a detached background process (no --reload) and
  return immediately, printing the PID. Omit to run attached in the
  foreground with --reload for local development.

.EXAMPLE
  # First-ever run, foreground dev mode, no admin features yet:
  .\scripts\start-mr-smith.ps1

.EXAMPLE
  # Fresh start with admin dashboard enabled and live feeds, detached:
  .\scripts\start-mr-smith.ps1 -AdminToken (Read-Host -AsSecureString "Admin token" | ConvertFrom-SecureString -AsPlainText) -ExternalFeeds -Detached
#>
param(
    [string]$AdminToken,
    [switch]$ExternalFeeds,
    [string]$DailyModel,
    [string]$ResearchModel,
    [int]$Port = 8000,
    [switch]$Detached
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

Write-Host "== Mr Smith fresh start ==" -ForegroundColor Cyan
Write-Host "Working directory: $repoRoot"

# 1) Virtualenv: create only if missing, never overwrite an existing one.
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "No .venv found - creating one..." -ForegroundColor Yellow
    python -m venv .venv
} else {
    Write-Host ".venv already present - reusing it."
}

# 2) Install/refresh the package in editable mode with test extras.
Write-Host "Installing/updating package (pip install -e .[test])..."
& $venvPython -m pip install -e ".[test]" --quiet
if ($LASTEXITCODE -ne 0) { throw "pip install failed (exit $LASTEXITCODE)" }

# 3) Ollama reachability check (non-fatal warning only).
try {
    $null = Invoke-RestMethod "http://127.0.0.1:11434/api/tags" -TimeoutSec 3
    Write-Host "Ollama reachable at 127.0.0.1:11434." -ForegroundColor Green
} catch {
    Write-Host "WARNING: Ollama not reachable at 127.0.0.1:11434 - start it (e.g. 'ollama serve') before chatting." -ForegroundColor Yellow
}

# 4) Environment for this process only.
if ($AdminToken) {
    $env:MR_SMITH_ADMIN_TOKEN = $AdminToken
    Write-Host "Admin token set for this run (not persisted to disk)." -ForegroundColor Green
} elseif ($env:MR_SMITH_ADMIN_TOKEN) {
    Write-Host "Reusing already-exported MR_SMITH_ADMIN_TOKEN." -ForegroundColor Green
} else {
    Write-Host "No admin token set - /admin dashboard model switch and finance mutations will return HTTP 503 until you set one." -ForegroundColor Yellow
}

if ($ExternalFeeds) {
    $env:MR_SMITH_EXTERNAL_FEEDS = "1"
    Write-Host "External feeds enabled (MR_SMITH_EXTERNAL_FEEDS=1)."
}

if ($DailyModel) { $env:MR_SMITH_DAILY_MODEL = $DailyModel }
if ($ResearchModel) { $env:MR_SMITH_RESEARCH_MODEL = $ResearchModel }

Write-Host ""
Write-Host "Chat:  http://127.0.0.1:$Port/morning"
Write-Host "Admin: http://127.0.0.1:$Port/admin"
Write-Host "Docs:  http://127.0.0.1:$Port/docs"
Write-Host ""

$uvicornArgs = @("-m", "uvicorn", "assistant_api.main:app", "--host", "127.0.0.1", "--port", $Port)

if ($Detached) {
    $proc = Start-Process -FilePath $venvPython -ArgumentList $uvicornArgs -WorkingDirectory $repoRoot -WindowStyle Hidden -PassThru
    Write-Host "Launched detached. PID: $($proc.Id)" -ForegroundColor Green
    Write-Host "Stop it later with: Stop-Process -Id $($proc.Id)"
} else {
    & $venvPython @uvicornArgs --reload
}
