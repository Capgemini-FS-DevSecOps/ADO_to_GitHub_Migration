# Stop migration-ui Next.js dev servers and unlock .next (fixes EPERM on Windows).
$ErrorActionPreference = "SilentlyContinue"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$UiDir = Join-Path $Root "apps\migration-ui"

$procs = Get-CimInstance Win32_Process -Filter "name='node.exe'" |
    Where-Object { $_.CommandLine -match [regex]::Escape($UiDir) }

foreach ($p in $procs) {
    Write-Host "Stopping PID $($p.ProcessId)..."
    Stop-Process -Id $p.ProcessId -Force
}

Start-Sleep -Seconds 1
Remove-Item -Recurse -Force (Join-Path $UiDir ".next") -ErrorAction SilentlyContinue
Write-Host "Migration UI stopped; .next cache cleared."
