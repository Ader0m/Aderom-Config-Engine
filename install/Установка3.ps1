param(
    [string]$AnomalyRoot = '..\Anomaly 1.5.3 up',
    [switch]$NoBackup
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

function Invoke-SilentRobocopy {
    param(
        [string]$Source,
        [string]$Destination,
        [string[]]$ExtraArgs,
        [string]$OperationName
    )

    $robocopyArgs = @($Source, $Destination) + $ExtraArgs + @(
        '/R:1',
        '/W:1',
        '/NJH',
        '/NJS',
        '/NP',
        '/NDL',
        '/NFL',
        '/NS',
        '/NC'
    )

    & robocopy.exe @robocopyArgs *> $null
    $code = $LASTEXITCODE

    if ($code -ge 8) {
        throw "$OperationName failed. Robocopy exit code: $code."
    }
}

try {
    # If relative path is not found from current directory, try script directory.
    if (-not (Test-Path -LiteralPath $AnomalyRoot -PathType Container)) {
        if ($PSScriptRoot) {
            $candidate = Join-Path $PSScriptRoot $AnomalyRoot

            if (Test-Path -LiteralPath $candidate -PathType Container) {
                $AnomalyRoot = $candidate
            }
        }
    }

    if (-not (Test-Path -LiteralPath $AnomalyRoot -PathType Container)) {
        throw "Anomaly root folder not found: $AnomalyRoot"
    }

    $AnomalyRoot = (Resolve-Path -LiteralPath $AnomalyRoot).ProviderPath

    $toolsDir = Join-Path $AnomalyRoot 'tools'
    $batPath = Join-Path $toolsDir 'db_unpacker.bat'
    $unpackedDir = Join-Path $toolsDir '_unpacked'
    $gamedataDir = Join-Path $AnomalyRoot 'gamedata'

    if (-not (Test-Path -LiteralPath $batPath -PathType Leaf)) {
        throw "db_unpacker.bat not found: $batPath"
    }

    Write-Info "Running: $batPath"

    $startParams = @{
        FilePath = 'cmd.exe'
        ArgumentList = "/c `"$batPath`""
        WorkingDirectory = $toolsDir
        Wait = $true
        PassThru = $true
    }

    $process = Start-Process @startParams

    if ($process.ExitCode -ne 0) {
        throw "db_unpacker.bat exited with code $($process.ExitCode)."
    }

    Write-Success "db_unpacker.bat finished."

    if (-not (Test-Path -LiteralPath $unpackedDir -PathType Container)) {
        throw "Unpacked folder not found after db_unpacker.bat: $unpackedDir"
    }

    # Backup existing gamedata before overwriting/merging new files.
    if (Test-Path -LiteralPath $gamedataDir -PathType Container) {
        if ($NoBackup) {
            Write-Info "Backup skipped because -NoBackup was specified."
        }
        else {
            $existingItems = @(Get-ChildItem -LiteralPath $gamedataDir -Force)

            if ($existingItems.Count -eq 0) {
                Write-Info "Existing gamedata is empty, skipping backup."
            }
            else {
                $stamp = Get-Date -Format 'yyyyMMddHHmmssfff'
                $backupDir = Join-Path $AnomalyRoot "gamedata_backup_$stamp"

                Write-Info "Backing up existing gamedata to: $backupDir"

                Invoke-SilentRobocopy `
                    -Source $gamedataDir `
                    -Destination $backupDir `
                    -ExtraArgs @('/E') `
                    -OperationName 'Backup gamedata'

                Write-Success "Backup created: $backupDir"
            }
        }
    }
    else {
        Write-Info "gamedata folder does not exist yet, skipping backup."
    }

    if (-not (Test-Path -LiteralPath $gamedataDir -PathType Container)) {
        Write-Info "Creating gamedata folder: $gamedataDir"
        New-Item -ItemType Directory -Path $gamedataDir | Out-Null
    }

    Write-Info "Copying contents from: $unpackedDir"
    Write-Info "Copying contents to: $gamedataDir"

    Invoke-SilentRobocopy `
        -Source $unpackedDir `
        -Destination $gamedataDir `
        -ExtraArgs @('/E', '/IS', '/IT') `
        -OperationName 'Copy unpacked to gamedata'

    Write-Success "Copy completed."
}
catch {
    $msg = $_.Exception.Message

    if (-not $msg) {
        $msg = [string]$_
    }

    Write-Host $msg -ForegroundColor Red
    exit 1
}