param(
    [int]$PauseSeconds = 20,
    [string]$Model = 'llama3.1:8b'
)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$py = Join-Path (Get-Location) '.venv\Scripts\python.exe'
$batch = 'data\research_jobs\asx1000-20261029'
$status = Join-Path $batch 'status.json'
$full = 'data\asx_universe_full_20261029'
$remaining = 'data\asx_remaining_20261029.json'
$remainingBatch = 'data\research_jobs\asx-remaining-20261029'
$profiles = 'data\asx_entity_profiles_full_20261029'
$snapshot = 'data\asx_opportunity_snapshot_full_20261029'
$deepQueue = 'data\asx_deep_dive_top50_20261029.json'
$deepBatch = 'data\research_jobs\deepdives-top50-20261029'

function Invoke-Research([string]$batchFile, [string]$batchDir, [switch]$Resume) {
    $args = @('-m','assistant_api.finance.research','--batch-file',$batchFile,'--batch-dir',$batchDir,'--data-dir','data','--allow-network','--model',$Model)
    if ($Resume) { $args += '--resume' }
    & $py @args
    if ($LASTEXITCODE -ne 0) { throw "Research runner failed: $batchDir" }
}

while ((Test-Path $status) -and ((Get-Content $status -Raw | ConvertFrom-Json).status -eq 'running')) {
    Start-Sleep -Seconds 60
}

& $py scripts\build_asx_opportunity_universe.py --snapshot data\asx200_ingest\asx200-10y-20261027-2356\constituents_snapshot.csv --additional data\asx_universe_1000_source.csv --limit 5000 --output $full
$all = (Get-Content "$full\instruments.json" -Raw | ConvertFrom-Json).instruments
$done = if (Test-Path $status) { (Get-Content $status -Raw | ConvertFrom-Json).items | Where-Object status -eq 'completed' | ForEach-Object instrument } else { @() }
$remainingItems = @($all | Where-Object { $done -notcontains $_.instrument })
@{instruments=$remainingItems} | ConvertTo-Json -Depth 5 | Set-Content $remaining -Encoding UTF8
if ($remainingItems.Count -gt 0) { Invoke-Research $remaining $remainingBatch }

& $py scripts\build_asx_entity_profiles.py --batch-dir $batch --output $profiles
& $py scripts\build_asx_entity_profiles.py --batch-dir $remainingBatch --output "$profiles\remaining" 2>$null
& $py scripts\build_asx_first_snapshot.py --profiles "$profiles\profiles.json" --output $snapshot --limit 50
$ranked = (Get-Content "$snapshot\snapshot.json" -Raw | ConvertFrom-Json).deep_dive_queue
@{instruments=@($ranked | ForEach-Object { @{instrument=$_.instrument} })} | ConvertTo-Json -Depth 5 | Set-Content $deepQueue -Encoding UTF8
Invoke-Research $deepQueue $deepBatch
Write-Host "Overnight ASX pipeline complete. Deep-dive results: $deepBatch"
