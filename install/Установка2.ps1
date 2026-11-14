# Step 2: create the project virtual environment and install dependencies.
# Install creates .venv and installs requirements.txt into it.
# Run with -Uninstall to remove .venv. Python itself is not uninstalled.
param(
    [string]$ProjectRoot,
    [string]$RequirementsFile,
    [string]$PythonExe,
    [switch]$ForceVenv,
    [switch]$ForceInstallPython,
    [switch]$Uninstall
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

# Any Python 3.10.x starting from this patch version is accepted.
$MinimumVersion = '3.10.6'
$PythonSeries = '3.10'
# Version installed when no suitable Python is found (last 3.10 release with an installer).
$InstallerVersion = '3.10.11'
$VenvDirName = '.venv'

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

function Test-PythonVersion {
    param([string]$Version)

    if ([string]::IsNullOrWhiteSpace($Version)) {
        return $false
    }

    if (-not $Version.StartsWith("$PythonSeries.")) {
        return $false
    }

    try {
        return ([version]$Version -ge [version]$MinimumVersion)
    }
    catch {
        return $false
    }
}

function Test-PythonPath {
    param([string]$Path)

    if (-not $Path -or -not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $false
    }

    $version = Get-PythonVersion -Exe $Path
    return (Test-PythonVersion -Version $version)
}

function Get-PythonExecutable {
    if ($PythonExe) {
        if (Test-PythonPath -Path $PythonExe) {
            return $PythonExe
        }

        throw "Specified Python executable is not Python $MinimumVersion or newer (3.10.x): $PythonExe"
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

        if (Test-PythonVersion -Version $version) {
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

function Install-Python310 {
    Write-Info "Python $MinimumVersion or newer (3.10.x) not found. Installing Python $InstallerVersion..."

    $is64 = [Environment]::Is64BitOperatingSystem
    $fileName = if ($is64) { "python-$InstallerVersion-amd64.exe" } else { "python-$InstallerVersion.exe" }
    $url = "https://www.python.org/ftp/python/$InstallerVersion/$fileName"

    $tempDir = Join-Path $env:TEMP "python-$InstallerVersion-setup"

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
    Write-Info 'Checking project paths...'

    if (-not $ProjectRoot) {
        $ProjectRoot = (Get-Location).Path

        if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot 'src\requirements.txt') -PathType Leaf)) {
            $fallback = Get-ProjectRoot -ScriptRoot $PSScriptRoot

            if (Test-Path -LiteralPath (Join-Path $fallback 'src\requirements.txt') -PathType Leaf) {
                $ProjectRoot = $fallback
            }
        }
    }

    if (Test-Path -LiteralPath $ProjectRoot -PathType Container) {
        $ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).ProviderPath
    }

    $venvDir = Join-Path $ProjectRoot $VenvDirName
    $venvPython = Join-Path $venvDir 'Scripts\python.exe'

    if ($Uninstall) {
        if (Test-Path -LiteralPath $venvDir) {
            Write-Info "Removing virtual environment: $venvDir"
            Remove-Item -LiteralPath $venvDir -Recurse -Force
            Write-Success "Removed: $venvDir"
        }
        else {
            Write-Info "Virtual environment not found, nothing to remove: $venvDir"
        }

        Write-Success 'Uninstall completed.'
        exit 0
    }

    if (-not $RequirementsFile) {
        $RequirementsFile = Join-Path $ProjectRoot 'src\requirements.txt'
    }

    if (-not (Test-Path -LiteralPath $RequirementsFile -PathType Leaf)) {
        throw "requirements.txt not found: $RequirementsFile"
    }

    Write-Info "Checking Python $MinimumVersion or newer (3.10.x)..."

    $python = Get-PythonExecutable

    if (-not $python) {
        $other310 = Get-Any310Version

        if ($other310 -and -not (Test-PythonVersion -Version $other310) -and -not $ForceInstallPython) {
            throw "Found Python $other310, but version $MinimumVersion or newer (3.10.x) is required. Run again with -ForceInstallPython to install Python $InstallerVersion."
        }

        Install-Python310

        $python = Get-PythonExecutable

        if (-not $python) {
            $defaultCandidate = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python310\python.exe'

            if (Test-PythonPath -Path $defaultCandidate) {
                $python = $defaultCandidate
            }
        }
    }

    if (-not $python) {
        throw "Could not find or install Python $MinimumVersion or newer (3.10.x)."
    }

    Write-Info "Using Python $((Get-PythonVersion -Exe $python)) : $python"

    if ($ForceVenv -and (Test-Path -LiteralPath $venvDir)) {
        Write-Info 'Removing existing .venv because -ForceVenv was specified.'
        Remove-Item -LiteralPath $venvDir -Recurse -Force
    }

    if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
        $venvVersion = Get-PythonVersion -Exe $venvPython

        if (-not (Test-PythonVersion -Version $venvVersion)) {
            throw "Existing .venv uses Python $venvVersion, but version $MinimumVersion or newer (3.10.x) is required. Run again with -ForceVenv to recreate .venv."
        }

        Write-Info "Using existing virtual environment: $venvDir"
    }
    else {
        if (Test-Path -LiteralPath $venvDir) {
            $items = Get-ChildItem -LiteralPath $venvDir -Force | Measure-Object

            if ($items.Count -gt 0) {
                throw '.venv already exists but python.exe is missing. Run again with -ForceVenv to recreate .venv.'
            }
        }

        Write-Info "Creating virtual environment: $venvDir"

        & $python -m venv "$venvDir"

        if ($LASTEXITCODE -ne 0) {
            throw 'Failed to create virtual environment.'
        }

        if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
            throw "Virtual environment was created but python.exe was not found: $venvPython"
        }
    }

    Write-Info 'Ensuring pip is available...'
    & $venvPython -m ensurepip --upgrade

    if ($LASTEXITCODE -ne 0) {
        throw 'Failed to ensure pip.'
    }

    Write-Info 'Upgrading pip...'
    & $venvPython -m pip install --upgrade pip

    if ($LASTEXITCODE -ne 0) {
        throw 'Failed to upgrade pip.'
    }

    Write-Info "Installing dependencies from: $RequirementsFile"
    & $venvPython -m pip install -r "$RequirementsFile"

    if ($LASTEXITCODE -ne 0) {
        throw 'Failed to install dependencies.'
    }

    Write-Success 'Done.'
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
