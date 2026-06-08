@echo off
REM UI-only launcher (CMD avoids PowerShell execution-policy issues with npm.ps1)
setlocal
cd /d "%~dp0..\apps\migration-ui"

if not exist node_modules (
  echo Installing dependencies...
  call npm.cmd install
)

if "%NEXT_PUBLIC_ACCELERATOR_URL%"=="" set NEXT_PUBLIC_ACCELERATOR_URL=http://localhost:8080
if "%NEXT_PUBLIC_AGENT_URL%"=="" set NEXT_PUBLIC_AGENT_URL=http://localhost:8090

echo Web UI -^> http://localhost:3000
call npm.cmd run dev
