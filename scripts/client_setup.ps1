param(
    [int]$Port = 8000,
    [switch]$NoLaunch,
    [switch]$NoShortcut,
    [switch]$SkipTests,
    [switch]$ForceConfig
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Warn {
    param([string]$Message)
    Write-Host "WARNING: $Message" -ForegroundColor Yellow
}

function Resolve-PythonCommand {
    $commands = @(
        @{ File = "py"; Args = @("-3") },
        @{ File = "python"; Args = @() },
        @{ File = "python3"; Args = @() }
    )

    foreach ($cmd in $commands) {
        $found = Get-Command $cmd.File -ErrorAction SilentlyContinue
        if (-not $found) {
            continue
        }

        try {
            $version = & $cmd.File @($cmd.Args + @("--version")) 2>&1
            if ($LASTEXITCODE -eq 0 -and "$version" -match "Python") {
                return $cmd
            }
        } catch {
            continue
        }
    }

    return $null
}

Write-Host "INDUS Dispatch Agent client setup"
Write-Host "Project: $Root"

Write-Step "Checking required local files"
$requiredFiles = @(
    "requirements.txt",
    "Start-IndusConsole.ps1",
    ".env.example",
    "dialer_config.example.json",
    "src\web_app.py"
)
foreach ($relative in $requiredFiles) {
    $path = Join-Path $Root $relative
    if (-not (Test-Path -LiteralPath $path)) {
        throw "Missing required file: $relative"
    }
}

Write-Step "Checking Python"
$venvPython = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython)) {
    $pythonCmd = Resolve-PythonCommand
    if (-not $pythonCmd) {
        throw "Python 3.10+ was not found. Install Python from https://www.python.org/downloads/windows/ and check 'Add python.exe to PATH', then run Install-Client.bat again."
    }

    $versionText = & $pythonCmd.File @($pythonCmd.Args + @("-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}'); raise SystemExit(0 if sys.version_info >= (3, 10) else 1)")) 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Python 3.10+ is required. Found: $versionText"
    }

    Write-Step "Creating virtual environment"
    & $pythonCmd.File @($pythonCmd.Args + @("-m", "venv", ".venv"))
    if ($LASTEXITCODE -ne 0) {
        throw "Could not create .venv."
    }
}

if (-not (Test-Path -LiteralPath $venvPython)) {
    throw "Virtual environment python was not created: $venvPython"
}

Write-Step "Installing Python dependencies"
& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
    throw "pip upgrade failed."
}
& $venvPython -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    throw "Dependency install failed."
}

Write-Step "Preparing local config"
$envPath = Join-Path $Root ".env"
$envExample = Join-Path $Root ".env.example"
$configPath = Join-Path $Root "dialer_config.json"
$configExample = Join-Path $Root "dialer_config.example.json"

if ($ForceConfig -or -not (Test-Path -LiteralPath $envPath)) {
    Copy-Item -LiteralPath $envExample -Destination $envPath -Force
    Write-Host "Created .env from .env.example"
} else {
    Write-Host ".env already exists, keeping it"
}

if ($ForceConfig -or -not (Test-Path -LiteralPath $configPath)) {
    Copy-Item -LiteralPath $configExample -Destination $configPath -Force
    Write-Host "Created dialer_config.json from example"
} else {
    Write-Host "dialer_config.json already exists, keeping it"
}

Write-Step "Creating runtime folders"
$folders = @(
    "audio",
    "chrome_profiles",
    "connected_calls",
    "data",
    "failed_calls",
    "logs",
    "voicemail_calls"
)
foreach ($folder in $folders) {
    New-Item -ItemType Directory -Force -Path (Join-Path $Root $folder) | Out-Null
}

Write-Step "Checking Chrome and audio prerequisites"
if (-not (Get-Command chrome.exe -ErrorAction SilentlyContinue)) {
    $chromePaths = @(
        "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
        "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
        "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
    )
    if (-not ($chromePaths | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1)) {
        Write-Warn "Google Chrome was not found. Install Chrome before live dialing."
    }
}

try {
    $devices = & $venvPython -c "import sounddevice as sd; print('\n'.join(str(d.get('name','')) for d in sd.query_devices()))" 2>$null
    if (-not (($devices -join "`n") -match "CABLE")) {
        Write-Warn "VB-CABLE device was not detected. Install VB-CABLE or configure the correct audio devices before live calls."
    }
} catch {
    Write-Warn "Could not inspect audio devices. Run the web Preflight page after launch."
}

if (-not $SkipTests) {
    Write-Step "Running quick verification"
    & $venvPython -m compileall -q src
    if ($LASTEXITCODE -ne 0) {
        throw "Python compile verification failed."
    }
    & $venvPython -m pytest tests/test_google_voice_state.py tests/test_conversation_loop.py -q
    if ($LASTEXITCODE -ne 0) {
        throw "Focused verification tests failed."
    }
}

if (-not $NoShortcut) {
    Write-Step "Creating desktop shortcut"
    $desktop = [Environment]::GetFolderPath("Desktop")
    $shortcutPath = Join-Path $desktop "Indus Dispatch Agent Console.lnk"
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = "powershell.exe"
    $shortcut.WorkingDirectory = $Root
    $shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$Root\Start-IndusConsole.ps1`" -Port $Port"
    $shortcut.IconLocation = "$env:SystemRoot\System32\shell32.dll,220"
    $shortcut.Description = "Start INDUS Dispatch Agent Console"
    $shortcut.Save()
    Write-Host "Shortcut: $shortcutPath"
}

Write-Step "Client setup complete"
Write-Host "Before live calls, edit these files:"
Write-Host "  $envPath"
Write-Host "  $configPath"
Write-Host ""
Write-Host "Required values: GROQ_API_KEY, CALLBACK_NUMBER, contacts file, Chrome profile, playback/capture devices."
Write-Host "Open the Preflight page in the console before dialing."

if (-not $NoLaunch) {
    Write-Step "Launching console"
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root "Start-IndusConsole.ps1") -Port $Port
}
