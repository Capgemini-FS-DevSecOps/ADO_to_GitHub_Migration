# Run ADO2GH stack locally without Docker (Windows PowerShell).
# Uses npm.cmd to avoid PowerShell execution-policy blocks on npm.ps1.

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

if (-not $env:ADO2GH_CONFIG) { $env:ADO2GH_CONFIG = "$Root\migration.yaml" }

# Local/dev always uses SQLite (see .env.example)
$env:ADO2GH_STORAGE_BACKEND = "sqlite"
$env:ADO2GH_SQLITE_PATH = "$Root\migration_state.db"
$env:ADO2GH_DATA_DIR = "$Root\data"

# npm resolves to npm.ps1 under strict execution policy; npm.cmd is the safe Windows entrypoint.
$Npm = Join-Path $env:ProgramFiles "nodejs\npm.cmd"
if (-not (Test-Path $Npm)) {
    $Npm = (Get-Command npm.cmd -ErrorAction SilentlyContinue).Source
}
if (-not $Npm) {
    throw "npm.cmd not found. Install Node.js from https://nodejs.org/"
}

$UiDir = Join-Path $Root "apps\migration-ui"
if (-not (Test-Path (Join-Path $UiDir "node_modules"))) {
    Write-Host "Installing UI dependencies (first run)..."
    Push-Location $UiDir
    & $Npm install
    Pop-Location
}

Write-Host "Starting Accelerator API on http://localhost:8080 ..."
Start-Process powershell -ArgumentList @(
    "-NoExit", "-Command",
    "Set-Location '$Root'; `$env:ADO2GH_STORAGE_BACKEND='sqlite'; `$env:ADO2GH_SQLITE_PATH='$Root\migration_state.db'; `$env:ADO2GH_DATA_DIR='$Root\data'; `$env:ADO2GH_CONFIG='$($env:ADO2GH_CONFIG)'; `$env:CORS_ORIGINS='http://localhost:3000'; python -m uvicorn services.accelerator_api.main:app --host 0.0.0.0 --port 8080"
)

Start-Sleep -Seconds 2

Write-Host "Starting Agent API on http://localhost:8090 ..."
Start-Process powershell -ArgumentList @(
    "-NoExit", "-Command",
    "Set-Location '$Root'; `$env:ACCELERATOR_URL='http://localhost:8080'; `$env:ADO2GH_DATA_DIR='$Root\data'; `$env:ADO2GH_SQLITE_PATH='$Root\migration_state.db'; `$env:CORS_ORIGINS='http://localhost:3000'; python -m uvicorn services.agent.main:app --host 0.0.0.0 --port 8090"
)

Start-Sleep -Seconds 2

Write-Host "Starting Web UI on http://localhost:3000 ..."
Start-Process powershell -ArgumentList @(
    "-NoExit", "-Command",
    "Set-Location '$UiDir'; `$env:NEXT_PUBLIC_ACCELERATOR_URL='http://localhost:8080'; `$env:NEXT_PUBLIC_AGENT_URL='http://localhost:8090'; & '$Npm' run dev"
)

Write-Host @"

Local stack starting in separate windows.
  Accelerator: http://localhost:8080/docs
  Agent:       http://localhost:8090/docs
  Web UI:      http://localhost:3000

For agent-only lightweight dev (no UI/redis/worker), use: .\scripts\run-local-agent.ps1

Set credentials before use:
  `$env:ADO_ORG_URL='https://dev.azure.com/YOUR_ORG'
  `$env:ADO_PAT='...'
  `$env:GH_TOKEN='...'

UI only (from repo root):
  .\scripts\run-ui.ps1

For production Postgres: docker compose -f docker-compose.yml -f docker-compose.prod.yml up --build
For serverless DynamoDB: docker compose -f docker-compose.yml -f docker-compose.serverless.yml up --build
"@
