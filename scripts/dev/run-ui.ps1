# Start only the migration UI (avoids npm.ps1 execution-policy issues).
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))
$UiDir = Join-Path $Root "apps\migration-ui"

. (Join-Path $Root "scripts\dev\_local-common.ps1")

$Npm = Resolve-NpmCmd

# Check for node_modules without changing directory
if (-not (Test-Path (Join-Path $UiDir "node_modules"))) {
    Write-Host "Installing dependencies..."
    Push-Location $UiDir
    & $Npm install
    Pop-Location
}

# Clear corrupted Next.js build cache to prevent startup hangs
$nextDir = Join-Path $UiDir ".next"
if (Test-Path $nextDir) {
    Write-Host "Clearing Next.js build cache..."
    Remove-Item -Recurse -Force $nextDir -ErrorAction SilentlyContinue
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
Write-Host "Starting UI in new PowerShell window..."

$UiDirFull = (Resolve-Path $UiDir).Path

$PsFile = "$env:TEMP\run-ui.ps1"
@"
Set-Location '$UiDirFull'
`$env:NEXT_PUBLIC_ACCELERATOR_URL = '$env:NEXT_PUBLIC_ACCELERATOR_URL'
`$env:NEXT_PUBLIC_AGENT_URL = '$env:NEXT_PUBLIC_AGENT_URL'
Write-Host "Starting Next.js dev server..."
npm run dev
"@ | Out-File -FilePath $PsFile -Encoding UTF8

Start-Process powershell.exe -ArgumentList "-NoExit", "-ExecutionPolicy", "Bypass", "-File", $PsFile
