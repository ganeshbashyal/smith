$ErrorActionPreference = 'Continue'
Set-Location 'D:\Mr_Smith'
$stamp = Get-Date -Format 'yyyyMMdd'
$log = "D:\Mr_Smith\data\daily_snapshots\scheduler-$stamp.log"
New-Item -ItemType Directory -Force -Path 'D:\Mr_Smith\data\daily_snapshots' | Out-Null
& 'D:\Mr_Smith\.venv\Scripts\python.exe' 'D:\Mr_Smith\scripts\refresh_asx_daily_snapshot.py' `
  --instruments 'D:\Mr_Smith\data\asx_universe_full_20261029\instruments.json' `
  --data-dir 'D:\Mr_Smith\data' `
  --pause-seconds 2 *>> $log
exit $LASTEXITCODE
