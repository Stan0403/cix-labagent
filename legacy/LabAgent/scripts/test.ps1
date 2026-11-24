$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
New-Item -ItemType Directory -Force -Path '.tmp','test-results' | Out-Null
$taskTemp = (Resolve-Path -LiteralPath '.tmp').Path
$env:TEMP = $taskTemp
$env:TMP = $taskTemp
$env:PYTHONUTF8 = '1'
& '.\.venv\Scripts\python.exe' -m pytest --cov=labagent --cov-report=term-missing --junitxml=test-results/junit.xml
exit $LASTEXITCODE
