# Smoke test for local IDE agent stack
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$agent = "http://localhost:8090"
$fail = 0

function Check($name, $url, $method = "GET", $body = $null) {
    try {
        if ($method -eq "POST" -and $body) {
            $r = Invoke-RestMethod -Uri $url -Method POST -Body $body -ContentType "application/json"
        } else {
            $r = Invoke-RestMethod -Uri $url -Method GET
        }
        Write-Host "[OK] $name"
        return $r
    } catch {
        Write-Host "[FAIL] $name - $_"
        $script:fail = 1
        return $null
    }
}

Check "health" "$agent/health"
$sessionBody = '{"profile_id":"lightweight","prompt":"smoke","dry_run":true}'
Check "session" "$agent/v1/sessions" "POST" $sessionBody

$env:ACCELERATOR_URL = "http://localhost:8080"
$mcpLine = '{"method":"tools/list"}'
try {
    $out = echo $mcpLine | python -m services.agent.mcp_server 2>$null
    if ($out -match "ado2gh_discover") { Write-Host "[OK] mcp tools/list" } else { throw "missing tools" }
} catch {
    Write-Host "[FAIL] mcp tools/list - $_"
    $fail = 1
}

if ($fail -ne 0) { exit 1 }
Write-Host "All smoke checks passed."
