# Run lightweight local agent stack (accelerator + agent only).
# For full stack with UI, use scripts/run-local.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))
Set-Location $Root

. (Join-Path $Root "scripts\dev\_local-common.ps1")

if (-not $env:ADO2GH_CONFIG) { $env:ADO2GH_CONFIG = "$Root\migration.yaml" }

$env:ADO2GH_STORAGE_BACKEND = "sqlite"
$env:ADO2GH_SQLITE_PATH = "$Root\migration_state.db"
$env:ADO2GH_DATA_DIR = "$Root\data"
$env:ADO2GH_LIGHTWEIGHT_MODE = "true"
$env:LLM_PROVIDER = "stub"
$env:ADO2GH_AUTH_ENABLED = "false"
$env:ADO2GH_LOCAL_PROFILE = "lightweight"

Ensure-LocalDataDir -Root $Root

$Python = Resolve-PythonExe -Root $Root
$Shell = Resolve-ShellExe
$PyInvoke = Format-PythonInvocation -Python $Python

Install-PythonDeps -Python $Python -Root $Root

$accelEnv = @(
    "`$env:ADO2GH_STORAGE_BACKEND='sqlite'",
    "`$env:ADO2GH_SQLITE_PATH='$Root\migration_state.db'",
    "`$env:ADO2GH_DATA_DIR='$Root\data'",
    "`$env:ADO2GH_CONFIG='$($env:ADO2GH_CONFIG)'",
    "`$env:ADO2GH_LIGHTWEIGHT_MODE='true'"
) -join "; "

$agentEnv = @(
    "`$env:ACCELERATOR_URL='http://localhost:8080'",
    "`$env:ADO2GH_DATA_DIR='$Root\data'",
    "`$env:ADO2GH_SQLITE_PATH='$Root\migration_state.db'",
    "`$env:ADO2GH_STORAGE_BACKEND='sqlite'",
    "`$env:LLM_PROVIDER='stub'",
    "`$env:ADO2GH_LOCAL_PROFILE='lightweight'"
) -join "; "

Write-Host "Starting Accelerator API on http://localhost:8080 ..."
Start-LocalServiceWindow -Shell $Shell -WorkingDirectory $Root -Command "$accelEnv; $PyInvoke -m uvicorn services.accelerator_api.main:app --host 0.0.0.0 --port 8080"

Start-Sleep -Seconds 2

Write-Host "Starting Agent API on http://localhost:8090 ..."
Start-LocalServiceWindow -Shell $Shell -WorkingDirectory $Root -Command "$agentEnv; $PyInvoke -m uvicorn services.agent.main:app --host 0.0.0.0 --port 8090"

Write-Host @"

Lightweight agent stack starting.
  Accelerator: http://localhost:8080/docs
  Agent:       http://localhost:8090/docs

Python: $($Python.Exe) $($Python.PrefixArgs -join ' ')

Smoke test: .\scripts\ide-check.ps1
Full stack + UI: .\scripts\run-local.ps1
"@
