# Step 3: unpack the game database into gamedata.
# Install creates gamedata.ace_backup (a folder) before unpacking, then copies tools\_unpacked to gamedata.
# Run with -Uninstall to remove the installed gamedata and restore it from gamedata.ace_backup.
# The tools\_unpacked folder is left in place.
param(
    [string]$AnomalyRoot = '..\Anomaly 1.5.3 up',
    [switch]$NoBackup,
    [switch]$Uninstall
)

$ErrorActionPreference = 'Stop'

$BackupSuffix = '.ace_backup'

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

function Get-ProjectRoot {
    param([string]$ScriptRoot)

    if (-not $ScriptRoot) {
        return (Get-Location).Path
    }

    if ((Split-Path -Leaf $ScriptRoot) -ieq 'install') {
        return (Split-Path -Parent $ScriptRoot)
    }

    return $ScriptRoot
}

function Resolve-ExistingFolder {
    param([string]$FolderPath)

    if (Test-Path -LiteralPath $FolderPath -PathType Container) {
        return (Resolve-Path -LiteralPath $FolderPath).ProviderPath
    }

    if (-not [System.IO.Path]::IsPathRooted($FolderPath)) {
        $candidate = Join-Path (Get-ProjectRoot -ScriptRoot $PSScriptRoot) $FolderPath

        if (Test-Path -LiteralPath $candidate -PathType Container) {
            return (Resolve-Path -LiteralPath $candidate).ProviderPath
        }
    }

    return $null
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
    $resolvedRoot = Resolve-ExistingFolder -FolderPath $AnomalyRoot

    if (-not $resolvedRoot) {
        if ($Uninstall) {
            Write-Warn "Anomaly root folder not found, nothing to uninstall: $AnomalyRoot"
            Write-Success 'Uninstall completed.'
            exit 0
        }

        throw "Anomaly root folder not found: $AnomalyRoot"
    }

    $AnomalyRoot = $resolvedRoot

    $toolsDir = Join-Path $AnomalyRoot 'tools'
    $unpackedDir = Join-Path $toolsDir '_unpacked'
    $gamedataDir = Join-Path $AnomalyRoot 'gamedata'
    $backupDir = "$gamedataDir$BackupSuffix"
    $noOriginalMarker = '.ace_no_original'

    if ($Uninstall) {
        # _unpacked is intentionally left in place: db_unpacker.bat creates it
        # and the user may want to reuse it.

        # Prefer the stable original backup, then the oldest timestamped backup.
        $restoreSource = $null
        $noOriginal = $false

        if (Test-Path -LiteralPath $backupDir -PathType Container) {
            if (Test-Path -LiteralPath (Join-Path $backupDir $noOriginalMarker) -PathType Leaf) {
                # gamedata did not exist before the install, so there is
                # nothing to restore.
                $noOriginal = $true
            }
            else {
                $restoreSource = $backupDir
            }
        }
        else {
            $legacyBackups = @(
                Get-ChildItem -LiteralPath $AnomalyRoot -Directory -Filter 'gamedata_backup_*' -ErrorAction SilentlyContinue |
                    Sort-Object LastWriteTime
            )

            if ($legacyBackups.Count -gt 0) {
                $restoreSource = $legacyBackups[0].FullName
                Write-Warn "Using legacy backup: $restoreSource"
            }
        }

        if ($restoreSource -or $noOriginal) {
            if (Test-Path -LiteralPath $gamedataDir -PathType Container) {
                Write-Info "Removing current gamedata: $gamedataDir"
                Remove-Item -LiteralPath $gamedataDir -Recurse -Force
                Write-Success "Removed: $gamedataDir"
            }
            else {
                Write-Info "gamedata folder not found, nothing to remove: $gamedataDir"
            }

            if ($restoreSource) {
                Move-Item -LiteralPath $restoreSource -Destination $gamedataDir -Force
                Write-Success "Restored gamedata from backup: $restoreSource"
            }
            else {
                Remove-Item -LiteralPath $backupDir -Recurse -Force
                Write-Success "Removed backup: $backupDir"
            }
        }
        else {
            if (Test-Path -LiteralPath $gamedataDir -PathType Container) {
                Write-Warn "No gamedata backup found; leaving gamedata untouched to avoid data loss: $gamedataDir"
            }
            else {
                Write-Info "gamedata folder not found, nothing to remove: $gamedataDir"
            }
        }

        Write-Success 'Uninstall completed.'
        exit 0
    }

    # Back up existing gamedata before running db_unpacker.bat, so the backup
    # exists even if unpacking fails.
    if ($NoBackup) {
        Write-Info 'Backup skipped because -NoBackup was specified.'
    }
    elseif (Test-Path -LiteralPath $backupDir -PathType Container) {
        Write-Info "Backup already exists, keeping original: $backupDir"
    }
    else {
        if (Test-Path -LiteralPath $gamedataDir -PathType Container) {
            Write-Info "Backing up existing gamedata to: $backupDir"
            New-Item -ItemType Directory -Path $backupDir -Force | Out-Null

            Invoke-SilentRobocopy `
                -Source $gamedataDir `
                -Destination $backupDir `
                -ExtraArgs @('/E') `
                -OperationName 'Backup gamedata'

            Write-Success "Backup created: $backupDir"
        }
        else {
            Write-Info "gamedata folder does not exist yet; creating empty backup: $backupDir"
            New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
            Set-Content -LiteralPath (Join-Path $backupDir $noOriginalMarker) -Value 'created by ACE installer' -Encoding ASCII
            Write-Success "Backup created: $backupDir"
        }
    }

    $batPath = Join-Path $toolsDir 'db_unpacker.bat'

    if (-not (Test-Path -LiteralPath $batPath -PathType Leaf)) {
        throw "db_unpacker.bat not found: $batPath"
    }

    Write-Info "Running: $batPath"

    $startParams = @{
        FilePath         = 'cmd.exe'
        ArgumentList     = "/c `"$batPath`""
        WorkingDirectory = $toolsDir
        Wait             = $true
        PassThru         = $true
    }

    $process = Start-Process @startParams

    if ($process.ExitCode -ne 0) {
        throw "db_unpacker.bat exited with code $($process.ExitCode)."
    }

    Write-Success 'db_unpacker.bat finished.'

    if (-not (Test-Path -LiteralPath $unpackedDir -PathType Container)) {
        throw "Unpacked folder not found after db_unpacker.bat: $unpackedDir"
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

    Write-Success 'Copy completed.'
    Write-Success 'Done.'
}
catch {
    $msg = $_.Exception.Message

    if (-not $msg) {
        $msg = [string]$_
    }

    Write-Host $msg -ForegroundColor Red
    exit 1
}
