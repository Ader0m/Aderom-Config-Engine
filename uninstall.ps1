# Runs the numbered install scripts in reverse order with -Uninstall.
# Each script removes what it created and restores its .ace_backup if present.
param(
    [string]$ProjectRoot = '',
    [string]$InstallDir = ''
)

$ErrorActionPreference = 'Stop'

function Write-Info {
    param([string]$Message)
    Write-Host $Message -ForegroundColor White
}

function Write-Success {
    param([string]$Message)
    Write-Host $Message -ForegroundColor Green
}

function Write-Warn {
    param([string]$Message)
    Write-Host $Message -ForegroundColor Yellow
}

function Get-NumberedScript {
    param(
        [string]$Dir,
        [string]$Number
    )

    # Matches names ending with the given number, but not with another digit before it.
    # Example: script ending with 1.ps1, but not 21.ps1.
    $pattern = '(?<!\d)' + $Number + '$'

    $found = @(
        Get-ChildItem -LiteralPath $Dir -File -Filter '*.ps1' |
            Where-Object { $_.BaseName -match $pattern } |
            Sort-Object FullName
    )

    if ($found.Count -eq 0) {
        throw "No script ending with '$Number.ps1' found in: $Dir"
    }

    if ($found.Count -gt 1) {
        $names = ($found | ForEach-Object { $_.Name }) -join ', '
        throw "Multiple scripts ending with '$Number.ps1' found in: $Dir. Files: $names"
    }

    return $found[0].FullName
}

try {
    if (-not $ProjectRoot) {
        if ($PSScriptRoot) {
            $ProjectRoot = $PSScriptRoot
        }
        else {
            $ProjectRoot = (Get-Location).Path
        }
    }

    if (-not $InstallDir) {
        $InstallDir = Join-Path $ProjectRoot 'install'
    }

    if (-not (Test-Path -LiteralPath $InstallDir -PathType Container)) {
        throw "Install folder not found: $InstallDir"
    }

    $ps1Files = @(Get-ChildItem -LiteralPath $InstallDir -File -Filter '*.ps1')

    if ($ps1Files.Count -eq 0) {
        $psFiles = @(Get-ChildItem -LiteralPath $InstallDir -File -Filter '*.ps')

        if ($psFiles.Count -gt 0) {
            throw "Found .ps files in: $InstallDir. Rename them to .ps1 and run again."
        }

        throw "No .ps1 scripts found in: $InstallDir"
    }

    # Prefer Windows PowerShell 5.1 for compatibility.
    $psExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'

    if (-not (Test-Path -LiteralPath $psExe -PathType Leaf)) {
        $psExe = Join-Path $PSHOME 'powershell.exe'
    }

    if (-not (Test-Path -LiteralPath $psExe -PathType Leaf)) {
        $psExe = [System.Diagnostics.Process]::GetCurrentProcess().Path
    }

    # Undo the installation in reverse order.
    $scripts = @(
        Get-NumberedScript -Dir $InstallDir -Number '3'
        Get-NumberedScript -Dir $InstallDir -Number '2'
        Get-NumberedScript -Dir $InstallDir -Number '1'
    )

    Write-Info "Project root: $ProjectRoot"
    Write-Info 'Scripts to run (uninstall mode):'

    foreach ($script in $scripts) {
        Write-Info "  $script"
    }

    Push-Location $ProjectRoot

    try {
        foreach ($script in $scripts) {
            Write-Info "Running: $script -Uninstall"

            & $psExe -NoProfile -ExecutionPolicy Bypass -File $script -Uninstall
            $exitCode = $LASTEXITCODE

            if ($exitCode -ne 0) {
                throw "Script failed with exit code $exitCode : $script"
            }

            Write-Success "Completed: $script"
        }
    }
    finally {
        Pop-Location
    }

    Write-Success 'All uninstall scripts completed successfully.'
    exit 0
}
catch {
    $msg = $_.Exception.Message

    if (-not $msg) {
        $msg = [string]$_
    }

    Write-Host $msg -ForegroundColor Red
    exit 1
}
