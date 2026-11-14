# Step 1: register ACE as a custom executable in ModOrganizer.ini.
# Install creates ModOrganizer.ini.ace_backup (once) and adds the ACE entry.
# Run with -Uninstall to remove the ACE entry and restore the original file from backup.
param(
    [string]$Path = '../MO2/ModOrganizer.ini',
    [switch]$Uninstall
)

$ErrorActionPreference = 'Stop'

$BackupSuffix = '.ace_backup'
$bs = [string][char]92
$AceTitle = 'ACE'

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

function Resolve-ExistingFile {
    param([string]$FilePath)

    if (Test-Path -LiteralPath $FilePath -PathType Leaf) {
        return (Resolve-Path -LiteralPath $FilePath).ProviderPath
    }

    if (-not [System.IO.Path]::IsPathRooted($FilePath)) {
        $candidate = Join-Path (Get-ProjectRoot -ScriptRoot $PSScriptRoot) $FilePath

        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return (Resolve-Path -LiteralPath $candidate).ProviderPath
        }
    }

    return $null
}

function Get-AbsoluteTargetPath {
    param([string]$FilePath)

    if ([System.IO.Path]::IsPathRooted($FilePath)) {
        return [System.IO.Path]::GetFullPath($FilePath)
    }

    return [System.IO.Path]::GetFullPath((Join-Path (Get-ProjectRoot -ScriptRoot $PSScriptRoot) $FilePath))
}

function Read-IniFile {
    param([string]$FilePath)

    # Detect INI encoding by BOM.
    $bytes = [System.IO.File]::ReadAllBytes($FilePath)

    if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
        $enc = New-Object System.Text.UTF8Encoding($true)
    }
    elseif ($bytes.Length -ge 2 -and $bytes[0] -eq 0xFF -and $bytes[1] -eq 0xFE) {
        $enc = [System.Text.Encoding]::Unicode
    }
    elseif ($bytes.Length -ge 2 -and $bytes[0] -eq 0xFE -and $bytes[1] -eq 0xFF) {
        $enc = [System.Text.Encoding]::BigEndianUnicode
    }
    else {
        $enc = New-Object System.Text.UTF8Encoding($false)
    }

    $lines = New-Object 'System.Collections.Generic.List[string]'
    $lines.AddRange([System.IO.File]::ReadAllLines($FilePath, $enc))

    return [pscustomobject]@{
        Lines    = $lines
        Encoding = $enc
    }
}

function Write-IniFile {
    param(
        [string]$FilePath,
        $Lines,
        $Encoding
    )

    [System.IO.File]::WriteAllLines($FilePath, $Lines, $Encoding)
}

function Get-SectionBounds {
    param(
        $Lines,
        [string]$SectionName
    )

    $start = -1

    for ($i = 0; $i -lt $Lines.Count; $i++) {
        if ($Lines[$i].Trim() -ieq $SectionName) {
            $start = $i
            break
        }
    }

    if ($start -lt 0) {
        return $null
    }

    # Section end: empty line or next section.
    $end = $Lines.Count

    for ($i = $start + 1; $i -lt $Lines.Count; $i++) {
        $trimmed = $Lines[$i].Trim()

        if ($trimmed -eq '' -or $trimmed.StartsWith('[')) {
            $end = $i
            break
        }
    }

    return [pscustomobject]@{
        Start = $start
        End   = $end
    }
}

function Test-AcePath {
    param(
        [string]$Value,
        [string]$RelativePath
    )

    $normalized = $Value.Trim().Replace('/', $bs)
    $relative = $RelativePath.Replace('/', $bs)

    foreach ($folder in $AceFolderNames) {
        $pattern = '(?i)(^|' + [regex]::Escape($bs) + ')' +
            [regex]::Escape($folder + $bs + $relative) + '$'

        if ($normalized -match $pattern) {
            return $true
        }
    }

    return $false
}

function Test-AceEntry {
    param(
        $Lines,
        $Bounds
    )

    for ($i = $Bounds.Start + 1; $i -lt $Bounds.End; $i++) {
        if ($Lines[$i] -match ('^\s*\d+\\title\s*=\s*' + [regex]::Escape($AceTitle) + '\s*$')) {
            return $true
        }

        if ($Lines[$i] -match '^\s*\d+\\(arguments|binary)\s*=\s*(.+)$') {
            $value = $Matches[2]

            if (Test-AcePath -Value $value -RelativePath 'src/main.py') {
                return $true
            }

            if (Test-AcePath -Value $value -RelativePath '.venv/Scripts/python.exe') {
                return $true
            }
        }
    }

    return $false
}

function Add-AceEntry {
    param(
        $Lines,
        $Bounds
    )

    $body = New-Object 'System.Collections.Generic.List[string]'

    for ($i = $Bounds.Start + 1; $i -lt $Bounds.End; $i++) {
        $body.Add($Lines[$i])
    }

    # Find size = N.
    $sizeIdx = -1
    $oldN = -1

    for ($i = 0; $i -lt $body.Count; $i++) {
        if ($body[$i] -match '^\s*size\s*=\s*(\d+)') {
            $sizeIdx = $i
            $oldN = [int]$Matches[1]
            break
        }
    }

    if ($sizeIdx -lt 0) {
        throw 'Field size not found in section [customExecutables].'
    }

    $newN = $oldN + 1

    # Safety check: new index should not already exist.
    $newPrefixPattern = '^\s*' + [regex]::Escape([string]$newN + $bs)

    for ($i = 0; $i -lt $body.Count; $i++) {
        if ($body[$i] -match $newPrefixPattern) {
            throw "Section already contains lines like '$newN$bs...'. Config may be inconsistent."
        }
    }

    # Replace size = N with size = N+1.
    $body[$sizeIdx] = $body[$sizeIdx] -replace '(?i)^(\s*size\s*=\s*)\d+', ('${1}' + $newN)

    # New ACE entry only; do NOT touch existing entries.
    $body.Add([string]$newN + $bs + 'arguments=' + $AceArguments)
    $body.Add([string]$newN + $bs + 'binary=' + $AceBinary)
    $body.Add([string]$newN + $bs + 'hide=false')
    $body.Add([string]$newN + $bs + 'ownicon=false')
    $body.Add([string]$newN + $bs + 'steamAppID=')
    $body.Add([string]$newN + $bs + 'title=' + $AceTitle)
    $body.Add([string]$newN + $bs + 'toolbar=false')
    $body.Add([string]$newN + $bs + 'workingDirectory=./')

    $newLines = New-Object 'System.Collections.Generic.List[string]'

    for ($i = 0; $i -le $Bounds.Start; $i++) {
        $newLines.Add($Lines[$i])
    }

    $newLines.AddRange($body)

    for ($i = $Bounds.End; $i -lt $Lines.Count; $i++) {
        $newLines.Add($Lines[$i])
    }

    $Lines.Clear()
    $Lines.AddRange($newLines)
}

function Remove-AceEntries {
    param(
        $Lines,
        $Bounds
    )

    $body = New-Object 'System.Collections.Generic.List[string]'

    for ($i = $Bounds.Start + 1; $i -lt $Bounds.End; $i++) {
        $body.Add($Lines[$i])
    }

    # Collect indexes of ACE entries.
    $remove = New-Object 'System.Collections.Generic.HashSet[int]'

    foreach ($line in $body) {
        if ($line -match '^\s*(\d+)\\(.+)$') {
            $idx = [int]$Matches[1]
            $rest = $Matches[2]

            if ($rest -match '^(?i)(arguments|binary|title)\s*=\s*(.+)$') {
                $key = $Matches[1].ToLowerInvariant()
                $value = $Matches[2].Trim()

                if ($key -eq 'arguments' -and (Test-AcePath -Value $value -RelativePath 'src/main.py')) {
                    [void]$remove.Add($idx)
                }
                elseif ($key -eq 'binary' -and (Test-AcePath -Value $value -RelativePath '.venv/Scripts/python.exe')) {
                    [void]$remove.Add($idx)
                }
                elseif ($key -eq 'title' -and $value -ieq $AceTitle) {
                    [void]$remove.Add($idx)
                }
            }
        }
    }

    if ($remove.Count -eq 0) {
        return $false
    }

    # Remove every line that belongs to an ACE entry.
    $kept = New-Object 'System.Collections.Generic.List[string]'

    foreach ($line in $body) {
        if ($line -match '^\s*(\d+)\\') {
            $idx = [int]$Matches[1]

            if ($remove.Contains($idx)) {
                continue
            }
        }

        $kept.Add($line)
    }

    # Renumber remaining entries so indexes stay contiguous.
    $indexMap = @{}
    $nextIndex = 1

    for ($i = 0; $i -lt $kept.Count; $i++) {
        if ($kept[$i] -match '^(\s*)(\d+)(\\.*)$') {
            $indent = $Matches[1]
            $oldIndex = [int]$Matches[2]
            $suffix = $Matches[3]

            if (-not $indexMap.ContainsKey($oldIndex)) {
                $indexMap[$oldIndex] = $nextIndex
                $nextIndex++
            }

            $kept[$i] = $indent + ([string]$indexMap[$oldIndex]) + $suffix
        }
    }

    # Update size to the number of remaining entries.
    for ($i = 0; $i -lt $kept.Count; $i++) {
        if ($kept[$i] -match '^\s*size\s*=\s*(\d+)') {
            $kept[$i] = $kept[$i] -replace '(?i)^(\s*size\s*=\s*)\d+', ('${1}' + ($nextIndex - 1))
            break
        }
    }

    $newLines = New-Object 'System.Collections.Generic.List[string]'

    for ($i = 0; $i -le $Bounds.Start; $i++) {
        $newLines.Add($Lines[$i])
    }

    $newLines.AddRange($kept)

    for ($i = $Bounds.End; $i -lt $Lines.Count; $i++) {
        $newLines.Add($Lines[$i])
    }

    $Lines.Clear()
    $Lines.AddRange($newLines)

    return $true
}

try {
    $ProjectRoot = Get-ProjectRoot -ScriptRoot $PSScriptRoot
    $ProjectFolderName = Split-Path -Leaf $ProjectRoot

    # The project may be unpacked into any folder name (ACE, ACE-master, etc.).
    $AceFolderNames = @($ProjectFolderName, 'ACE') | Select-Object -Unique
    $AceArguments = '../' + $ProjectFolderName + '/src/main.py'
    $AceBinary = '../' + $ProjectFolderName + '/.venv/Scripts/python.exe'

    $resolvedPath = Resolve-ExistingFile -FilePath $Path

    if (-not $resolvedPath) {
        if ($Uninstall) {
            # The target file may already be gone, but a backup can still exist.
            $targetPath = Get-AbsoluteTargetPath -FilePath $Path
            $targetBackup = "$targetPath$BackupSuffix"
            $parentDir = Split-Path -Parent $targetPath

            if (Test-Path -LiteralPath $targetBackup -PathType Leaf) {
                if (-not (Test-Path -LiteralPath $parentDir -PathType Container)) {
                    New-Item -ItemType Directory -Path $parentDir -Force | Out-Null
                }

                Move-Item -LiteralPath $targetBackup -Destination $targetPath -Force
                Write-Success "Restored original file from backup: $targetBackup"
                Write-Success 'Uninstall completed.'
                exit 0
            }

            if (Test-Path -LiteralPath $parentDir -PathType Container) {
                $legacyBackups = @(
                    Get-ChildItem -LiteralPath $parentDir -File -Filter ((Split-Path -Leaf $targetPath) + '.*.bak') -ErrorAction SilentlyContinue |
                        Sort-Object LastWriteTime
                )

                if ($legacyBackups.Count -gt 0) {
                    $source = $legacyBackups[0].FullName
                    Move-Item -LiteralPath $source -Destination $targetPath -Force
                    Write-Success "Restored original file from legacy backup: $source"
                    Write-Success 'Uninstall completed.'
                    exit 0
                }
            }

            Write-Warn "File not found, nothing to restore: $Path"
            Write-Success 'Uninstall completed.'
            exit 0
        }

        throw "File not found: $Path"
    }

    $Path = $resolvedPath
    $backup = "$Path$BackupSuffix"

    if ($Uninstall) {
        $restored = $false

        # Prefer the stable original backup.
        if (Test-Path -LiteralPath $backup -PathType Leaf) {
            Remove-Item -LiteralPath $Path -Force
            Move-Item -LiteralPath $backup -Destination $Path -Force
            Write-Success "Restored original file from backup: $backup"
            $restored = $true
        }
        else {
            # Fall back to the oldest timestamped backup created by older versions.
            $legacyBackups = @(
                Get-ChildItem -LiteralPath (Split-Path -Parent $Path) -File -Filter ((Split-Path -Leaf $Path) + '.*.bak') -ErrorAction SilentlyContinue |
                    Sort-Object LastWriteTime
            )

            if ($legacyBackups.Count -gt 0) {
                $source = $legacyBackups[0].FullName
                Remove-Item -LiteralPath $Path -Force
                Move-Item -LiteralPath $source -Destination $Path -Force
                Write-Success "Restored original file from legacy backup: $source"
                $restored = $true
            }
        }

        # Scrub ACE entries even after a restore, in case the backup was made
        # after an earlier install.
        $ini = Read-IniFile -FilePath $Path
        $bounds = Get-SectionBounds -Lines $ini.Lines -SectionName '[customExecutables]'

        if ($null -eq $bounds) {
            if ($restored) {
                Write-Success 'Uninstall completed.'
                exit 0
            }

            throw 'Section [customExecutables] not found.'
        }

        $removed = Remove-AceEntries -Lines $ini.Lines -Bounds $bounds

        if ($removed) {
            Write-IniFile -FilePath $Path -Lines $ini.Lines -Encoding $ini.Encoding
            Write-Success "ACE entry removed from: $Path"
        }
        elseif (-not $restored) {
            Write-Info 'ACE entry not found, nothing to remove.'
        }

        Write-Success 'Uninstall completed.'
        exit 0
    }

    # Create the stable backup only once so the original file is preserved.
    if (-not (Test-Path -LiteralPath $backup -PathType Leaf)) {
        Copy-Item -LiteralPath $Path -Destination $backup -Force
        Write-Info "Backup created: $backup"
    }
    else {
        Write-Info "Backup already exists, keeping original: $backup"
    }

    $ini = Read-IniFile -FilePath $Path
    $bounds = Get-SectionBounds -Lines $ini.Lines -SectionName '[customExecutables]'

    if ($null -eq $bounds) {
        throw 'Section [customExecutables] not found.'
    }

    if (Test-AceEntry -Lines $ini.Lines -Bounds $bounds) {
        # Replace an existing ACE entry so its paths always use forward slashes.
        [void](Remove-AceEntries -Lines $ini.Lines -Bounds $bounds)
        $bounds = Get-SectionBounds -Lines $ini.Lines -SectionName '[customExecutables]'

        if ($null -eq $bounds) {
            throw 'Section [customExecutables] not found.'
        }

        Write-Info "Existing ACE entry replaced: $Path"
    }

    Add-AceEntry -Lines $ini.Lines -Bounds $bounds
    Write-IniFile -FilePath $Path -Lines $ini.Lines -Encoding $ini.Encoding

    Write-Success "Done. ACE entry added to: $Path"
    Write-Info "Backup: $backup"
}
catch {
    $msg = $_.Exception.Message

    if (-not $msg) {
        $msg = [string]$_
    }

    Write-Host $msg -ForegroundColor Red
    exit 1
}
