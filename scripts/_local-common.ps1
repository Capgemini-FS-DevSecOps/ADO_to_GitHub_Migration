# Shared helpers for native Windows dev scripts (run-local.ps1, run-local-agent.ps1, run-ui.ps1).

function Test-Executable {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [string[]]$ArgumentList = @("-c", "import sys")
    )
    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) {
        return $false
    }
    try {
        $null = & $Path @ArgumentList 2>$null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function Resolve-PythonExe {
    param([string]$Root)
    $candidates = [System.Collections.Generic.List[string]]::new()

    $venvPy = Join-Path $Root ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPy) {
        $candidates.Add($venvPy)
    }

    $pyLauncher = (Get-Command py -ErrorAction SilentlyContinue).Source
    if ($pyLauncher) {
        $candidates.Add($pyLauncher)
    }

    foreach ($name in @("python3", "python")) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd -and $cmd.Source) {
            $candidates.Add($cmd.Source)
        }
    }

    foreach ($candidate in $candidates) {
        if ($candidate -match "\\WindowsApps\\") {
            continue
        }
        if ($candidate -like "*\py.exe") {
            if (Test-Executable -Path $candidate -ArgumentList @("-3", "-c", "import sys")) {
                return @{ Exe = $candidate; PrefixArgs = @("-3") }
            }
            continue
        }
        if (Test-Executable -Path $candidate) {
            return @{ Exe = $candidate; PrefixArgs = @() }
        }
    }

    throw @"
Python 3.9+ not found or not runnable (WinError 2 often means the Windows Store python stub).

Fix:
  1. Install Python from https://www.python.org/ and check "Add python.exe to PATH"
  2. Or create a venv in the repo root:
       py -3 -m venv .venv
       .\.venv\Scripts\activate
       python -m pip install -r requirements.txt
       python -m pip install -e ".[api]"
  3. Disable the Microsoft Store python alias: Settings -> Apps -> Advanced app settings -> App execution aliases -> turn off python.exe / python3.exe
"@
}

function Invoke-Python {
    param(
        [Parameter(Mandatory = $true)][hashtable]$Python,
        [Parameter(Mandatory = $true, ValueFromRemainingArguments = $true)][string[]]$PythonArgumentList
    )
  # Pass pip/python flags as an array so PowerShell does not treat -e/-r as common parameters.
    & $Python.Exe @($Python.PrefixArgs + $PythonArgumentList)
    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed (exit $LASTEXITCODE): $($PythonArgumentList -join ' ')"
    }
}

function Resolve-NpmCmd {
    $candidates = @(
        (Join-Path $env:ProgramFiles "nodejs\npm.cmd"),
        (Join-Path ${env:ProgramFiles(x86)} "nodejs\npm.cmd"),
        (Join-Path $env:LOCALAPPDATA "Programs\nodejs\npm.cmd")
    )
    foreach ($path in $candidates) {
        if (Test-Path -LiteralPath $path) {
            return $path
        }
    }
    $found = (Get-Command npm.cmd -ErrorAction SilentlyContinue).Source
    if ($found) {
        return $found
    }
    throw "npm.cmd not found. Install Node.js 20+ from https://nodejs.org/ (LTS) and restart the terminal."
}

function Resolve-ShellExe {
    $ps51 = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
    if (Test-Path -LiteralPath $ps51) {
        return $ps51
    }
    $pwsh = (Get-Command pwsh -ErrorAction SilentlyContinue).Source
    if ($pwsh) {
        return $pwsh
    }
    throw "PowerShell not found. Install PowerShell 5.1+ or PowerShell 7 (pwsh)."
}

function Install-PythonDeps {
    param(
        [hashtable]$Python,
        [string]$Root
    )
    $req = Join-Path $Root "requirements.txt"
    if (-not (Test-Path -LiteralPath $req)) {
        throw "requirements.txt not found at $req"
    }
    Write-Host "Installing Python dependencies from requirements.txt..."
    Invoke-Python -Python $Python -PythonArgumentList @("-m", "pip", "install", "-r", $req)
    Write-Host "Installing ado2gh package (editable)..."
    Invoke-Python -Python $Python -PythonArgumentList @("-m", "pip", "install", "-e", ".[api]")
}

function Ensure-LocalDataDir {
    param([string]$Root)
    $data = Join-Path $Root "data"
    if (-not (Test-Path -LiteralPath $data)) {
        New-Item -ItemType Directory -Path $data | Out-Null
    }
}

function Format-PythonInvocation {
    param([hashtable]$Python)
    $exe = $Python.Exe.Replace("'", "''")
    $prefix = ($Python.PrefixArgs -join ' ').Trim()
    if ($prefix) {
        return "& '$exe' $prefix"
    }
    return "& '$exe'"
}

function Start-LocalServiceWindow {
    param(
        [string]$Shell,
        [string]$Title,
        [string]$WorkingDirectory,
        [string]$Command
    )
    $wd = $WorkingDirectory.Replace("'", "''")
    $inner = "Set-Location '$wd'; $Command"
  if ($Shell -like "*\powershell.exe") {
        Start-Process -FilePath $Shell -ArgumentList @("-NoExit", "-Command", $inner)
    } else {
        Start-Process -FilePath $Shell -ArgumentList @("-NoExit", "-Command", $inner)
    }
}
