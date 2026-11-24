$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) { throw 'Run .\scripts\setup.ps1 first' }
$env:PYTHONUTF8 = '1'
& '.\.venv\Scripts\python.exe' -m labagent
exit $LASTEXITCODE
