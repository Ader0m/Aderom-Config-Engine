param(
    [string]$Path = '../MO2/ModOrganizer.ini'
)

$ErrorActionPreference = 'Stop'

# Backslash as variable to avoid escaping confusion
$bs = [string][char]92

if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    throw "File not found: $Path"
}

$Path = (Resolve-Path -LiteralPath $Path).ProviderPath

$stamp = Get-Date -Format 'yyyyMMddHHmmssfff'
$backup = "$Path.$stamp.bak"
Copy-Item -LiteralPath $Path -Destination $backup -Force

# Detect INI encoding by BOM
$bytes = [System.IO.File]::ReadAllBytes($Path)

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

$lines = [System.Collections.Generic.List[string]]::new()
$lines.AddRange([System.IO.File]::ReadAllLines($Path, $enc))

# Find [customExecutables]
$sectionStart = -1
for ($i = 0; $i -lt $lines.Count; $i++) {
    if ($lines[$i].Trim() -ieq '[customExecutables]') {
        $sectionStart = $i
        break
    }
}

if ($sectionStart -lt 0) {
    throw 'Section [customExecutables] not found.'
}

# Section end: empty line or next section
$sectionEnd = $lines.Count
for ($i = $sectionStart + 1; $i -lt $lines.Count; $i++) {
    $trimmed = $lines[$i].Trim()

    if ($trimmed -eq '' -or $trimmed.StartsWith('[')) {
        $sectionEnd = $i
        break
    }
}

# Find size = N
$sizeIdx = -1
$oldN = -1

for ($i = $sectionStart + 1; $i -lt $sectionEnd; $i++) {
    if ($lines[$i] -match '^\s*size\s*=\s*(\d+)') {
        $sizeIdx = $i
        $oldN = [int]$Matches[1]
        break
    }
}

if ($sizeIdx -lt 0) {
    throw 'Field size not found in section [customExecutables].'
}

$newN = $oldN + 1

# Safety check: new index should not already exist
$newPrefixPattern = '^\s*' + [regex]::Escape([string]$newN + $bs)
for ($i = $sectionStart + 1; $i -lt $sectionEnd; $i++) {
    if ($lines[$i] -match $newPrefixPattern) {
        throw "Section already contains lines like '$newN$bs...'. Config may be inconsistent."
    }
}

# Replace size = N with size = N+1
$lines[$sizeIdx] = $lines[$sizeIdx] -replace '(?i)^(\s*size\s*=\s*)\d+', ('${1}' + $newN)

$insert = [System.Collections.Generic.List[string]]::new()

# New ACE entry only; do NOT touch existing entries
$insert.Add([string]$newN + $bs + 'arguments=..\ACE\src\main.py')
$insert.Add([string]$newN + $bs + 'binary=../ACE/.venv/Scripts/python.exe')
$insert.Add([string]$newN + $bs + 'hide=false')
$insert.Add([string]$newN + $bs + 'ownicon=false')
$insert.Add([string]$newN + $bs + 'steamAppID=')
$insert.Add([string]$newN + $bs + 'title=ACE')
$insert.Add([string]$newN + $bs + 'toolbar=false')
$insert.Add([string]$newN + $bs + 'workingDirectory=.' + $bs)

# Insert at end of section
$lines.InsertRange($sectionEnd, $insert)

[System.IO.File]::WriteAllLines($Path, $lines, $enc)

Write-Host ("Done. size: {0} -> {1}. Backup: {2}" -f $oldN, $newN, $backup)