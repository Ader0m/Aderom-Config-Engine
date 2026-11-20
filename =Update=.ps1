# Updates the project from the Git repository.
# Installs Git if it is missing, initializes the repository when needed,
# and pulls the latest master. Run this script again to update later.
param(
    [string]$ProjectRoot = '',
    [string]$RemoteUrl = 'https://github.com/Ader0m/Aderom-Config-Engine',
    [string]$Branch = 'master'
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

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

function Get-GitCandidates {
    $candidates = @()

    if ($env:ProgramFiles) {
        $candidates += Join-Path $env:ProgramFiles 'Git\cmd\git.exe'
        $candidates += Join-Path $env:ProgramFiles 'Git\bin\git.exe'
    }

    if (${env:ProgramFiles(x86)}) {
        $candidates += Join-Path ${env:ProgramFiles(x86)} 'Git\cmd\git.exe'
        $candidates += Join-Path ${env:ProgramFiles(x86)} 'Git\bin\git.exe'
    }

    if ($env:LOCALAPPDATA) {
        $candidates += Join-Path $env:LOCALAPPDATA 'Programs\Git\cmd\git.exe'
        $candidates += Join-Path $env:LOCALAPPDATA 'Programs\Git\bin\git.exe'
    }

    return $candidates
}

function Get-GitExecutable {
    $command = Get-Command 'git.exe' -ErrorAction SilentlyContinue

    if ($command -and $command.Source) {
        return $command.Source
    }

    foreach ($candidate in Get-GitCandidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return $candidate
        }
    }

    return $null
}

function Update-SessionPath {
    $machinePath = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    $parts = @($machinePath, $userPath) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }

    if ($parts.Count -gt 0) {
        $env:Path = $parts -join ';'
    }
}

function Get-GitInstallerUrl {
    $headers = @{ 'User-Agent' = 'ACE-Updater' }
    $release = Invoke-RestMethod -Uri 'https://api.github.com/repos/git-for-windows/git/releases/latest' -Headers $headers

    $bitness = if ([Environment]::Is64BitOperatingSystem) { '64' } else { '32' }
    $asset = $release.assets |
        Where-Object { $_.name -match "^Git-.*-$bitness-bit\.exe$" } |
        Select-Object -First 1

    if (-not $asset) {
        $asset = $release.assets |
            Where-Object { $_.name -match '^Git-.*-64-bit\.exe$' } |
            Select-Object -First 1
    }

    if (-not $asset) {
        throw 'No Git for Windows installer found in the latest release.'
    }

    return $asset.browser_download_url
}

function Install-GitFromGitHub {
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

    $url = Get-GitInstallerUrl
    $fileName = Split-Path -Leaf $url
    $tempDir = Join-Path $env:TEMP 'git-for-windows-setup'

    if (-not (Test-Path -LiteralPath $tempDir)) {
        New-Item -ItemType Directory -Path $tempDir | Out-Null
    }

    $installer = Join-Path $tempDir $fileName
    $installDir = Join-Path $env:LOCALAPPDATA 'Programs\Git'

    Write-Info "Downloading: $url"
    Invoke-WebRequest -Uri $url -OutFile $installer -UseBasicParsing
    Unblock-File -Path $installer -ErrorAction SilentlyContinue

    $installerArgs = @(
        '/VERYSILENT',
        '/NORESTART',
        '/NOCANCEL',
        '/SP-',
        '/SUPPRESSMSGBOXES',
        ('/DIR="' + $installDir + '"')
    )

    Write-Info "Installing Git to: $installDir"
    $process = Start-Process -FilePath $installer -ArgumentList $installerArgs -Wait -PassThru

    if ($process.ExitCode -ne 0 -and $process.ExitCode -ne 3010) {
        throw "Git installer failed with exit code $($process.ExitCode)."
    }
}

function Install-GitWithWinget {
    $winget = Get-Command 'winget.exe' -ErrorAction SilentlyContinue

    if (-not $winget) {
        return $false
    }

    Write-Info 'Trying to install Git with winget...'

    & $winget.Source install --id Git.Git -e --source winget --silent --accept-package-agreements --accept-source-agreements

    return ($LASTEXITCODE -eq 0)
}

function Install-Git {
    Write-Info 'Git not found. Installing Git for Windows...'

    try {
        Install-GitFromGitHub
    }
    catch {
        Write-Warn "Direct installation failed: $($_.Exception.Message)"
    }

    Update-SessionPath
    $git = Get-GitExecutable

    if ($git) {
        return $git
    }

    if (Install-GitWithWinget) {
        Update-SessionPath
        $git = Get-GitExecutable

        if ($git) {
            return $git
        }
    }

    throw 'Git installation failed. Install Git manually and run the update again.'
}

function Invoke-Git {
    param(
        [string]$GitExe,
        [string]$WorkDir,
        [string[]]$Arguments
    )

    & $GitExe -C $WorkDir @Arguments
    $exitCode = $LASTEXITCODE

    if ($exitCode -ne 0) {
        throw "git $($Arguments -join ' ') failed with exit code $exitCode."
    }
}

function Invoke-GitOutput {
    param(
        [string]$GitExe,
        [string]$WorkDir,
        [string[]]$Arguments
    )

    # A redirected stderr from a native command is reported as an error in
    # Windows PowerShell 5.1, so keep ErrorActionPreference from aborting on it.
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'

    try {
        $output = & $GitExe -C $WorkDir @Arguments 2>$null
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }

    if ($exitCode -ne 0) {
        return $null
    }

    return ($output | Out-String).Trim()
}

try {
    Write-Info 'Checking project paths...'

    if (-not $ProjectRoot) {
        $ProjectRoot = Get-ProjectRoot -ScriptRoot $PSScriptRoot
    }

    if (-not (Test-Path -LiteralPath $ProjectRoot -PathType Container)) {
        throw "Project root folder not found: $ProjectRoot"
    }

    $ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).ProviderPath
    Write-Info "Project root: $ProjectRoot"

    $git = Get-GitExecutable

    if (-not $git) {
        $git = Install-Git
    }

    $gitVersion = Invoke-GitOutput -GitExe $git -WorkDir $ProjectRoot -Arguments @('--version')
    Write-Success "Using Git: $gitVersion"

    $repositoryExists = Test-Path -LiteralPath (Join-Path $ProjectRoot '.git')
    $originUrl = $null
    $hasHead = $false

    if ($repositoryExists) {
        Write-Info 'Existing Git repository found.'
        $originUrl = Invoke-GitOutput -GitExe $git -WorkDir $ProjectRoot -Arguments @('remote', 'get-url', 'origin')
        $head = Invoke-GitOutput -GitExe $git -WorkDir $ProjectRoot -Arguments @('rev-parse', '--verify', 'HEAD')
        $hasHead = [bool]$head
    }
    else {
        Write-Info 'Git repository not found. Initializing...'
        Invoke-Git -GitExe $git -WorkDir $ProjectRoot -Arguments @('init')
    }

    if (-not $originUrl) {
        Write-Info "Adding remote origin: $RemoteUrl"
        Invoke-Git -GitExe $git -WorkDir $ProjectRoot -Arguments @('remote', 'add', 'origin', $RemoteUrl)
    }
    else {
        Write-Info "Remote origin: $originUrl"
    }

    if ($hasHead) {
        Write-Info "Pulling from origin/$Branch..."
        Invoke-Git -GitExe $git -WorkDir $ProjectRoot -Arguments @('pull', 'origin', $Branch)
    }
    else {
        Write-Info "Fetching origin/$Branch..."
        Invoke-Git -GitExe $git -WorkDir $ProjectRoot -Arguments @('fetch', 'origin', $Branch)

        Write-Info "Checking out origin/${Branch}..."
        Invoke-Git -GitExe $git -WorkDir $ProjectRoot -Arguments @('checkout', '-f', '-B', $Branch, "origin/$Branch")
    }

    Write-Success 'Update completed.'
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
