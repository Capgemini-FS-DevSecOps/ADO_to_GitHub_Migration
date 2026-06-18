# Start only the migration UI (avoids npm.ps1 execution-policy issues).
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$UiDir = Join-Path $Root "apps\migration-ui"
$StopScript = Join-Path $Root "scripts\stop-ui.ps1"

. (Join-Path $Root "scripts\_local-common.ps1")

$Npm = Resolve-NpmCmd

# Avoid EPERM on .next/trace when a previous dev server is still running.
& $StopScript

Set-Location $UiDir

if (-not (Test-Path "node_modules")) {
    Write-Host "Installing dependencies..."
    & $Npm install
}

if (-not $env:NEXT_PUBLIC_ACCELERATOR_URL) {
    $env:NEXT_PUBLIC_ACCELERATOR_URL = "http://localhost:8080"
}
if (-not $env:NEXT_PUBLIC_AGENT_URL) {
    $env:NEXT_PUBLIC_AGENT_URL = "http://localhost:8090"
}

Write-Host "Web UI -> http://localhost:3000"
Write-Host "Accelerator API -> $env:NEXT_PUBLIC_ACCELERATOR_URL"
Write-Host "Agent API -> $env:NEXT_PUBLIC_AGENT_URL"

& $Npm run dev
