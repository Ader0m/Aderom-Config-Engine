# Step 4: create the ACE shortcut in the project root.
# Install creates ACE.lnk pointing at ..\MO2\ModOrganizer.exe with the
# moshortcut argument for the ACE executable registered by step 1.
# Paths are computed from the actual install location, so the shortcut
# must be generated per machine and never committed to the repo.
# Run with -Uninstall to remove the shortcut.
param(
    [string]$ProjectRoot,
    [string]$Mo2Dir = '..\MO2',
    [string]$ShortcutName = 'ACE',
    [string]$Arguments = 'moshortcut://:ACE',
    [switch]$Uninstall
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

try {
    if (-not $ProjectRoot) {
        $ProjectRoot = Get-ProjectRoot -ScriptRoot $PSScriptRoot
    }
    if (-not (Test-Path -LiteralPath $ProjectRoot -PathType Container)) {
        throw "Project root folder not found: $ProjectRoot"
    }
    $ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).ProviderPath
    $shortcutPath = Join-Path $ProjectRoot ($ShortcutName + '.lnk')

    if ($Uninstall) {
        if (Test-Path -LiteralPath $shortcutPath -PathType Leaf) {
            $shell = New-Object -ComObject WScript.Shell
            $existing = $shell.CreateShortcut($shortcutPath)
            $target = $existing.TargetPath
            if ($target -and ((Split-Path -Leaf $target) -ieq 'ModOrganizer.exe')) {
                Remove-Item -LiteralPath $shortcutPath -Force
                Write-Success "Removed shortcut: $shortcutPath"
            }
            else {
                Write-Warn "Shortcut does not point to ModOrganizer.exe, leaving it untouched: $shortcutPath"
            }
        }
        else {
            Write-Info "Shortcut not found, nothing to remove: $shortcutPath"
        }
        Write-Success 'Uninstall completed.'
        exit 0
    }

    $resolvedMo2 = Resolve-ExistingFolder -FolderPath $Mo2Dir
    if (-not $resolvedMo2) {
        throw "MO2 folder not found: $Mo2Dir"
    }
    $mo2Exe = Join-Path $resolvedMo2 'ModOrganizer.exe'
    if (-not (Test-Path -LiteralPath $mo2Exe -PathType Leaf)) {
        throw "ModOrganizer.exe not found: $mo2Exe"
    }

    Write-Info "Creating shortcut: $shortcutPath"
    $shell = New-Object -ComObject WScript.Shell
    $lnk = $shell.CreateShortcut($shortcutPath)
    $lnk.TargetPath       = $mo2Exe
    $lnk.Arguments        = '"' + $Arguments + '"'
    $lnk.WorkingDirectory = $resolvedMo2
    $lnk.IconLocation     = "$mo2Exe,0"
    $lnk.Description      = 'Run ACE with ModOrganizer'
    $lnk.Save()

    Write-Success "Shortcut target: $mo2Exe"
    Write-Success "Shortcut arguments: $($lnk.Arguments)"
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