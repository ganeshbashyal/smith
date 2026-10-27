<#
.SYNOPSIS
  Run Mr Smith's pytest suite in an isolated writable temporary directory.
#>
param(
    [string]$TempDirectory,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PytestArgs = @()
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Project virtual environment not found. Run .\scripts\start-mr-smith.ps1 once to set it up."
}

$configuredTemp = if ($TempDirectory) { $TempDirectory } elseif ($env:MR_SMITH_TEST_TEMP) {
    $env:MR_SMITH_TEST_TEMP
} else {
    $null
}

$candidates = if ($configuredTemp) {
    @($configuredTemp)
} else {
    $fallbacks = @((Join-Path ([System.IO.Path]::GetTempPath()) "MrSmith\pytest"))
    if ($env:LOCALAPPDATA) {
        $fallbacks += Join-Path $env:LOCALAPPDATA "MrSmith\pytest"
    }
    $fallbacks
}

$writableParent = $null
foreach ($candidate in $candidates) {
    try {
        New-Item -ItemType Directory -Path $candidate -Force -ErrorAction Stop | Out-Null
        $probe = Join-Path $candidate (".write-test-" + [guid]::NewGuid().ToString("N"))
        [System.IO.File]::WriteAllText($probe, "test")
        Remove-Item -LiteralPath $probe -Force
        $writableParent = $candidate
        break
    } catch {
        if ($configuredTemp) {
            throw "Configured temp directory '$candidate' is not writable: $($_.Exception.Message)"
        }
    }
}
if (-not $writableParent) {
    throw "No writable test temp directory found. Set MR_SMITH_TEST_TEMP or pass -TempDirectory with a writable parent."
}

# pytest clears --basetemp, so always isolate it in a new subdirectory.
$baseTemp = Join-Path $writableParent ("run-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $baseTemp -Force | Out-Null
Write-Host "Pytest temporary directory: $baseTemp"
& $python -m pytest "--basetemp=$baseTemp" @PytestArgs
$testExitCode = $LASTEXITCODE
try {
    Remove-Item -LiteralPath $baseTemp -Recurse -Force -ErrorAction Stop
} catch {
    Write-Warning "Could not remove this run's pytest directory '$baseTemp': $($_.Exception.Message)"
}
exit $testExitCode
