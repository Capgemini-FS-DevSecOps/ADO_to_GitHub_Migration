# Auth API smoke checks (local accelerator)
param(
    [string]$BaseUrl = "http://localhost:8080"
)

$ErrorActionPreference = "Stop"
Write-Host "Checking $BaseUrl/health ..."
$r = Invoke-WebRequest -Uri "$BaseUrl/health" -UseBasicParsing
if ($r.StatusCode -ne 200) { throw "Health failed" }

Write-Host "Checking bootstrap-status ..."
$status = Invoke-RestMethod -Uri "$BaseUrl/v1/auth/bootstrap-status"
Write-Host "needs_bootstrap=$($status.needs_bootstrap)"

if ($status.needs_bootstrap) {
    Write-Host "Bootstrap required - login page should show create admin"
} else {
    Write-Host "Bootstrap not required - login path available"
}

Write-Host "Auth smoke OK"
