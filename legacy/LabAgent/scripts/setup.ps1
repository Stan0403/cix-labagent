param([string]$Python = "")
$ErrorActionPreference = "Stop"
$taskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot
New-Item -ItemType Directory -Force -Path '.tmp' | Out-Null
$taskTemp = (Resolve-Path -LiteralPath '.tmp').Path
$env:TEMP = $taskTemp
$env:TMP = $taskTemp
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    if (-not $Python) {
        $taskCandidate = Get-Command python -ErrorAction SilentlyContinue
        if ($taskCandidate) { $Python = $taskCandidate.Source }
    }
    if (-not $Python) { throw 'Install Python 3.11+ and rerun: .\scripts\setup.ps1 -Python C:\Path\python.exe' }
    & $Python -c "import sys; assert sys.version_info >= (3,11), 'Python 3.11+ required'"
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11+ required. Select it with -Python; the system Python will not be modified.' }
    & $Python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed' }
}
& '.\.venv\Scripts\python.exe' -m pip install -e '.[dev,vision,serial]'
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check network access to the configured package index.' }
if (-not (Test-Path -LiteralPath '.env')) { Copy-Item -LiteralPath '.env.example' -Destination '.env' }
Write-Output 'Ready. Run: .\scripts\start.ps1'
