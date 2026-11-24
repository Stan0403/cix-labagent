param([string]$Name = 'cix-labagent', [ValidateSet('private','public')][string]$Visibility = 'private')
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot
# Trust only this explicitly selected repository for this process and child Git calls.
# This handles files prepared by a sandbox account without changing global Git settings.
$taskConfigCount = 0
if ($env:GIT_CONFIG_COUNT) { $taskConfigCount = [int]$env:GIT_CONFIG_COUNT }
[Environment]::SetEnvironmentVariable("GIT_CONFIG_KEY_$taskConfigCount", 'safe.directory', 'Process')
[Environment]::SetEnvironmentVariable("GIT_CONFIG_VALUE_$taskConfigCount", $taskRoot.Replace('\','/'), 'Process')
$env:GIT_CONFIG_COUNT = [string]($taskConfigCount + 1)
if (-not (Get-Command gh -ErrorAction SilentlyContinue)) { throw 'Install GitHub CLI, then run gh auth login --hostname github.com --web' }
& gh auth status --hostname github.com
if ($LASTEXITCODE -ne 0) { throw 'Run gh auth login --hostname github.com --web, then retry' }
$taskUser = & gh api user | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'Could not read the authenticated GitHub account' }
if (-not (Test-Path -LiteralPath '.git')) {
    & git init -b main
    if ($LASTEXITCODE -ne 0) { throw 'git init failed' }
}
& git config user.name $taskUser.login
if ($LASTEXITCODE -ne 0) { throw 'Could not set repository author name' }
& git config user.email "$($taskUser.id)+$($taskUser.login)@users.noreply.github.com"
if ($LASTEXITCODE -ne 0) { throw 'Could not set repository author email' }
if (@(& git remote) -contains 'origin') {
    $taskRemote = & git remote get-url origin
    throw "origin already exists: $taskRemote. Review it before pushing again."
}
& git add --all
if ($LASTEXITCODE -ne 0) { throw 'git add failed' }
& git diff --cached --quiet
if ($LASTEXITCODE -eq 1) {
    & git commit -m 'Deliver CIX LabAgent source, reproducible deployment and technical whitepaper'
    if ($LASTEXITCODE -ne 0) { throw 'git commit failed' }
}
& gh repo create $Name "--$Visibility" --source . --remote origin --push
if ($LASTEXITCODE -ne 0) { throw 'GitHub creation/push failed; inspect the local and remote state before retrying' }
& gh repo view --json url --jq .url
