param(
    [string]$ProjectRoot,
    [string]$RequirementsFile,
    [string]$PythonExe,
    [switch]$ForceVenv,
    [switch]$ForceInstallPython
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$RequiredVersion = '3.10.6'
$VenvDirName = '.venv'

function Write-Info {
    param([string]$Message)
    Write-Host $Message -ForegroundColor White
}

function Write-Success {
    param([string]$Message)
    Write-Host $Message -ForegroundColor Green
}

function Get-CommandList {
    return @(
        @{ Command = 'py'; Args = @('-3.10') },
        @{ Command = 'python'; Args = @() },
        @{ Command = 'python3'; Args = @() }
    )
}

function Get-PathCandidates {
    $candidates = @()

    if ($env:LOCALAPPDATA) {
        $candidates += Join-Path $env:LOCALAPPDATA 'Programs\Python\Python310\python.exe'
        $candidates += Join-Path $env:LOCALAPPDATA 'Programs\Python\Python310-32\python.exe'
    }

    if ($env:ProgramFiles) {
        $candidates += Join-Path $env:ProgramFiles 'Python310\python.exe'
    }

    if (${env:ProgramFiles(x86)}) {
        $candidates += Join-Path ${env:ProgramFiles(x86)} 'Python310\python.exe'
    }

    return $candidates
}

function Invoke-External {
    param(
        [string]$Command,
        [string[]]$Arguments
    )

    try {
        $output = & $Command @Arguments 2>$null | Out-String
        if ($LASTEXITCODE -eq 0) {
            return $output.Trim()
        }
    }
    catch {
    }

    return $null
}

function Get-LastLine {
    param([string]$Text)

    if ([string]::IsNullOrWhiteSpace($Text)) {
        return $null
    }

    $last = $Text -split "`r?`n" |
        Where-Object { $_.Trim() -ne '' } |
        Select-Object -Last 1

    if ($null -eq $last) {
        return $null
    }

    return ([string]$last).Trim()
}

function Get-PythonVersion {
    param([string]$Exe)

    $raw = Invoke-External -Command $Exe -Arguments @('-c', "import sys; print('.'.join(map(str, sys.version_info[:3])))")
    return Get-LastLine -Text $raw
}

function Test-PythonPath {
    param([string]$Path)

    if (-not $Path -or -not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $false
    }

    $version = Get-PythonVersion -Exe $Path
    return ($version -eq $RequiredVersion)
}

function Get-PythonExecutable {
    if ($PythonExe) {
        if (Test-PythonPath -Path $PythonExe) {
            return $PythonExe
        }

        throw "Specified Python executable is not Python $RequiredVersion : $PythonExe"
    }

    foreach ($entry in Get-CommandList) {
        $commandInfo = Get-Command $entry.Command -ErrorAction SilentlyContinue

        if (-not $commandInfo) {
            continue
        }

        # Skip Microsoft Store app aliases to avoid Store popups.
        if ($commandInfo.Source -like '*WindowsApps*') {
            continue
        }

        $versionArgs = $entry.Args + @('-c', "import sys; print('.'.join(map(str, sys.version_info[:3])))")
        $versionRaw = Invoke-External -Command $entry.Command -Arguments $versionArgs
        $version = Get-LastLine -Text $versionRaw

        if ($version -eq $RequiredVersion) {
            $exeArgs = $entry.Args + @('-c', 'import sys; print(sys.executable)')
            $exeRaw = Invoke-External -Command $entry.Command -Arguments $exeArgs
            $exePath = Get-LastLine -Text $exeRaw

            if ($exePath -and (Test-Path -LiteralPath $exePath -PathType Leaf)) {
                return $exePath
            }
        }
    }

    foreach ($candidate in Get-PathCandidates) {
        if (Test-PythonPath -Path $candidate) {
            return $candidate
        }
    }

    return $null
}

function Get-Any310Version {
    foreach ($entry in Get-CommandList) {
        $commandInfo = Get-Command $entry.Command -ErrorAction SilentlyContinue

        if (-not $commandInfo) {
            continue
        }

        # Skip Microsoft Store app aliases to avoid Store popups.
        if ($commandInfo.Source -like '*WindowsApps*') {
            continue
        }

        $versionArgs = $entry.Args + @('-c', "import sys; print('.'.join(map(str, sys.version_info[:3])))")
        $versionRaw = Invoke-External -Command $entry.Command -Arguments $versionArgs
        $version = Get-LastLine -Text $versionRaw

        if ($version -like '3.10.*') {
            return $version
        }
    }

    foreach ($candidate in Get-PathCandidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            $version = Get-PythonVersion -Exe $candidate

            if ($version -like '3.10.*') {
                return $version
            }
        }
    }

    return $null
}

function Install-Python3106 {
    Write-Info "Python $RequiredVersion not found. Installing..."

    $is64 = [Environment]::Is64BitOperatingSystem
    $fileName = if ($is64) { 'python-3.10.6-amd64.exe' } else { 'python-3.10.6.exe' }
    $url = "https://www.python.org/ftp/python/3.10.6/$fileName"

    $tempDir = Join-Path $env:TEMP 'python-3.10.6-setup'

    if (-not (Test-Path -LiteralPath $tempDir)) {
        New-Item -ItemType Directory -Path $tempDir | Out-Null
    }

    $installer = Join-Path $tempDir $fileName

    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

    Invoke-WebRequest -Uri $url -OutFile $installer -UseBasicParsing
    Unblock-File -Path $installer -ErrorAction SilentlyContinue

    $installerArgs = @(
        '/quiet',
        'InstallAllUsers=0',
        'PrependPath=1',
        'Include_test=0',
        'Include_launcher=1',
        'Include_pip=1'
    )

    $process = Start-Process -FilePath $installer -ArgumentList $installerArgs -Wait -PassThru

    if ($process.ExitCode -ne 0) {
        throw "Python installer failed with exit code $($process.ExitCode)."
    }
}

try {
    Write-Info "Checking project paths..."

    if (-not $ProjectRoot) {
        $ProjectRoot = (Get-Location).Path

        if ($PSScriptRoot -and
            -not (Test-Path -LiteralPath (Join-Path $ProjectRoot 'requirements.txt') -PathType Leaf) -and
            (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'requirements.txt') -PathType Leaf)) {
            $ProjectRoot = $PSScriptRoot
        }
    }

    if (-not $RequirementsFile) {
        $RequirementsFile = Join-Path $ProjectRoot 'requirements.txt'
    }

    if (-not (Test-Path -LiteralPath $RequirementsFile -PathType Leaf)) {
        throw "requirements.txt not found: $RequirementsFile"
    }

    $venvDir = Join-Path $ProjectRoot $VenvDirName
    $venvPython = Join-Path $venvDir 'Scripts\python.exe'

    Write-Info "Checking Python $RequiredVersion..."

    $python = Get-PythonExecutable

    if (-not $python) {
        $other310 = Get-Any310Version

        if ($other310 -and $other310 -ne $RequiredVersion -and -not $ForceInstallPython) {
            throw "Found Python $other310, but required version is $RequiredVersion. Run again with -ForceInstallPython if you really want to install Python $RequiredVersion."
        }

        Install-Python3106

        $python = Get-PythonExecutable

        if (-not $python) {
            $defaultCandidate = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python310\python.exe'

            if (Test-PythonPath -Path $defaultCandidate) {
                $python = $defaultCandidate
            }
        }
    }

    if (-not $python) {
        throw "Could not find or install Python $RequiredVersion."
    }

    Write-Info "Using Python $RequiredVersion : $python"

    if ($ForceVenv -and (Test-Path -LiteralPath $venvDir)) {
        Write-Info "Removing existing .venv because -ForceVenv was specified."
        Remove-Item -LiteralPath $venvDir -Recurse -Force
    }

    if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
        $venvVersion = Get-PythonVersion -Exe $venvPython

        if ($venvVersion -ne $RequiredVersion) {
            throw "Existing .venv uses Python $venvVersion, but required version is $RequiredVersion. Run again with -ForceVenv to recreate .venv."
        }

        Write-Info "Using existing virtual environment: $venvDir"
    }
    else {
        if (Test-Path -LiteralPath $venvDir) {
            $items = Get-ChildItem -LiteralPath $venvDir -Force | Measure-Object

            if ($items.Count -gt 0) {
                throw ".venv already exists but python.exe is missing. Run again with -ForceVenv to recreate .venv."
            }
        }

        Write-Info "Creating virtual environment: $venvDir"

        & $python -m venv "$venvDir"

        if ($LASTEXITCODE -ne 0) {
            throw "Failed to create virtual environment."
        }

        if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
            throw "Virtual environment was created but python.exe was not found: $venvPython"
        }
    }

    Write-Info "Ensuring pip is available..."
    & $venvPython -m ensurepip --upgrade

    if ($LASTEXITCODE -ne 0) {
        throw "Failed to ensure pip."
    }

    Write-Info "Upgrading pip..."
    & $venvPython -m pip install --upgrade pip

    if ($LASTEXITCODE -ne 0) {
        throw "Failed to upgrade pip."
    }

    Write-Info "Installing dependencies from: $RequirementsFile"
    & $venvPython -m pip install -r "$RequirementsFile"

    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install dependencies."
    }

    Write-Success "Done."
    Write-Info "Virtual environment Python: $venvPython"
}
catch {
    $msg = $_.Exception.Message

    if (-not $msg) {
        $msg = [string]$_
    }

    Write-Host $msg -ForegroundColor Red
    exit 1
}