param(
    [switch]$SkipDependencyInstall,
    [switch]$SkipExternalInstall,
    [switch]$NoPause
)

$ErrorActionPreference = "Stop"
$ProjectName = "SPECTRUM CODEX"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$RequiredPackages = @(
    "selenium",
    "webdriver-manager",
    "pandas<3",
    "openpyxl",
    "python-dotenv",
    "groq",
    "sounddevice",
    "soundfile",
    "pyttsx3",
    "edge-tts",
    "soundcard",
    "keyboard",
    "numpy<2.3",
    "pytest",
    "pytest-mock",
    "fastapi",
    "uvicorn[standard]",
    "jinja2",
    "python-multipart",
    "httpx"
)

$RequiredFolders = @(
    "logs",
    "data",
    "audio",
    "connected_calls",
    "failed_calls",
    "voicemail_calls",
    "chrome_profiles",
    "test_tmp"
)

$WingetPackages = @(
    @{ Id = "Python.Python.3.12"; Name = "Python 3.12"; Required = $true },
    @{ Id = "Google.Chrome"; Name = "Google Chrome"; Required = $true },
    @{ Id = "Microsoft.VCRedist.2015+.x64"; Name = "Microsoft Visual C++ Redistributable"; Required = $false },
    @{ Id = "Gyan.FFmpeg"; Name = "FFmpeg"; Required = $false }
)

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Ok {
    param([string]$Message)
    Write-Host "[OK] $Message" -ForegroundColor Green
}

function Write-Warn {
    param([string]$Message)
    Write-Host "[WARN] $Message" -ForegroundColor Yellow
}

function Write-Fail {
    param([string]$Message)
    Write-Host "[ERROR] $Message" -ForegroundColor Red
}

function Pause-End {
    if (-not $NoPause) {
        Write-Host ""
        Read-Host "Press Enter to close"
    }
}

function Resolve-Python312 {
    try {
        $cmd = Get-Command "py" -ErrorAction Stop
        $versionText = & $cmd.Source @("-3.12", "-c", "import sys; print('.'.join(map(str, sys.version_info[:3])))") 2>$null
        if ($LASTEXITCODE -eq 0 -and $versionText) {
            $version = [Version](($versionText | Select-Object -First 1).Trim())
            if ($version.Major -eq 3 -and $version.Minor -eq 12) {
                return @{
                    Exe = $cmd.Source
                    Args = @("-3.12")
                    Version = $version.ToString()
                }
            }
        }
    } catch {
        return $null
    }
    return $null
}

function Resolve-Winget {
    try {
        $cmd = Get-Command "winget" -ErrorAction Stop
        return $cmd.Source
    } catch {
        return $null
    }
}

function Install-WingetPackage {
    param(
        [string]$WingetPath,
        [string]$Id,
        [string]$Name
    )

    Write-Warn "$Name was not found or may be missing. Attempting winget install: $Id"
    & $WingetPath install --id $Id --exact --silent --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "winget could not install $Name automatically. You may need to install it manually."
        return $false
    }
    Write-Ok "$Name install command completed"
    return $true
}

function Invoke-Python {
    param(
        [hashtable]$Python,
        [string[]]$Arguments
    )
    & $Python.Exe @($Python.Args + $Arguments)
}

function Test-VenvPython {
    param([string]$VenvPython)
    if (-not (Test-Path $VenvPython)) {
        return @{
            Ok = $false
            Reason = "python.exe is missing"
            Version = ""
        }
    }

    try {
        $versionText = & $VenvPython -c "import sys; print('.'.join(map(str, sys.version_info[:3])))" 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $versionText) {
            return @{
                Ok = $false
                Reason = "python.exe exists but cannot run"
                Version = ""
            }
        }

        $version = [Version](($versionText | Select-Object -First 1).Trim())
        if ($version.Major -ne 3 -or $version.Minor -ne 12) {
            return @{
                Ok = $false
                Reason = "venv uses Python $version; Python 3.12 is required"
                Version = $version.ToString()
            }
        }

        return @{
            Ok = $true
            Reason = ""
            Version = $version.ToString()
        }
    } catch {
        return @{
            Ok = $false
            Reason = "python.exe failed: $($_.Exception.Message)"
            Version = ""
        }
    }
}

function Remove-BrokenVenv {
    param([string]$VenvPath)
    if (-not (Test-Path $VenvPath)) {
        return
    }

    $rootFull = [System.IO.Path]::GetFullPath($Root)
    $venvFull = [System.IO.Path]::GetFullPath($VenvPath)
    $expected = [System.IO.Path]::GetFullPath((Join-Path $Root ".venv"))

    if ($venvFull -ne $expected -or -not $venvFull.StartsWith($rootFull, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Safety check failed; refusing to delete unexpected venv path: $venvFull"
    }

    Write-Warn "Removing broken copied virtual environment: $venvFull"
    Remove-Item -LiteralPath $venvFull -Recurse -Force
}

function Ensure-Requirements {
    $requirementsPath = Join-Path $Root "requirements.txt"
    if (Test-Path $requirementsPath) {
        Write-Ok "requirements.txt found"
        return
    }

    Write-Warn "requirements.txt missing; creating default dependency list"
    Set-Content -Path $requirementsPath -Value ($RequiredPackages -join [Environment]::NewLine) -Encoding UTF8
    Add-Content -Path $requirementsPath -Value ""
    Write-Ok "Created requirements.txt"
}

function Ensure-Folders {
    foreach ($folder in $RequiredFolders) {
        $path = Join-Path $Root $folder
        if (-not (Test-Path $path)) {
            New-Item -ItemType Directory -Path $path | Out-Null
            Write-Ok "Created folder: $folder"
        } else {
            Write-Ok "Folder exists: $folder"
        }
    }
}

function Ensure-Env {
    $envPath = Join-Path $Root ".env"
    $examplePath = Join-Path $Root ".env.example"
    if (Test-Path $envPath) {
        Write-Ok ".env exists; not overwriting"
        return
    }

    if (Test-Path $examplePath) {
        Copy-Item -Path $examplePath -Destination $envPath
        Write-Ok "Created .env from .env.example"
        return
    }

    $envTemplate = @"
# SPECTRUM CODEX environment
# Fill these values before live calling.
GROQ_API_KEY=your_groq_api_key_here
GROQ_MODEL=llama-3.3-70b-versatile
CONTACTS_FILE=data/contacts.csv
PROFILE_NAME=sales_profile
CALLBACK_NUMBER=+15551234567
AGENT_NAME=Jason
COMPANY_NAME=FT Solutions
COMPANY_WEBSITE=
COMPANY_CONTEXT=Spectrum Business has recently expanded its pure fiber network in the prospect's area. The goal is to qualify the business and schedule a technician visit.
LOOPBACK_DEVICE=CABLE Input
CAPTURE_DEVICE=default
TTS_VOICE=en-US-GuyNeural
STT_MODEL=whisper-large-v3-turbo
VAD_THRESHOLD=0.015
VAD_SILENCE_FRAMES=7
VAD_SPEECH_FRAMES=2
STT_RETRY_COUNT=1
ANSWERED_SPEAK_DELAY_SECONDS=0.6
WAIT_FOR_HUMAN_AUDIO=true
HUMAN_AUDIO_TIMEOUT_SECONDS=2.5
ANSWER_CONFIRM_POLLS=2
MIN_RING_SECONDS=2
MAX_RING_SECONDS=45
VOICEMAIL_DETECT_SECONDS=15
CALL_COOLDOWN_SECONDS=10
TTS_WARMUP=true
"@
    Set-Content -Path $envPath -Value $envTemplate -Encoding UTF8
    Write-Ok "Created basic .env template"
}

function Ensure-EnvExample {
    $examplePath = Join-Path $Root ".env.example"
    if (Test-Path $examplePath) {
        Write-Ok ".env.example exists"
        return
    }

    $example = @"
# SPECTRUM CODEX example environment
# Copy to .env and fill real values on each PC.
GROQ_API_KEY=your_groq_api_key_here
GROQ_MODEL=llama-3.3-70b-versatile
CONTACTS_FILE=data/contacts.csv
PROFILE_NAME=sales_profile
CALLBACK_NUMBER=+15551234567
AGENT_NAME=Jason
COMPANY_NAME=FT Solutions
COMPANY_WEBSITE=
COMPANY_CONTEXT=Spectrum Business has recently expanded its pure fiber network in the prospect's area. The goal is to qualify the business and schedule a technician visit.
LOOPBACK_DEVICE=CABLE Input
CAPTURE_DEVICE=default
TTS_VOICE=en-US-GuyNeural
STT_MODEL=whisper-large-v3-turbo
VAD_THRESHOLD=0.015
VAD_SILENCE_FRAMES=7
VAD_SPEECH_FRAMES=2
STT_RETRY_COUNT=1
ANSWERED_SPEAK_DELAY_SECONDS=0.6
WAIT_FOR_HUMAN_AUDIO=true
HUMAN_AUDIO_TIMEOUT_SECONDS=2.5
ANSWER_CONFIRM_POLLS=2
MIN_RING_SECONDS=2
MAX_RING_SECONDS=45
VOICEMAIL_DETECT_SECONDS=15
CALL_COOLDOWN_SECONDS=10
TTS_WARMUP=true
"@
    Set-Content -Path $examplePath -Value $example -Encoding UTF8
    Write-Ok "Created .env.example"
}

function Ensure-StarterContacts {
    $contactsPath = Join-Path $Root "data\contacts.csv"
    if (Test-Path $contactsPath) {
        Write-Ok "data\contacts.csv exists"
        return
    }
    $dataDir = Join-Path $Root "data"
    if (-not (Test-Path $dataDir)) {
        New-Item -ItemType Directory -Path $dataDir | Out-Null
    }
    Set-Content -Path $contactsPath -Value "Name,Phone`nTest Business,+15551234567" -Encoding UTF8
    Write-Ok "Created starter data\contacts.csv"
}

function Find-Chrome {
    $chromeCommands = @("chrome.exe", "msedge.exe", "chromium.exe")
    foreach ($command in $chromeCommands) {
        try {
            $found = Get-Command $command -ErrorAction Stop
            return $found.Source
        } catch {
        }
    }

    $paths = @(
        "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
        "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
        "$env:LocalAppData\Google\Chrome\Application\chrome.exe",
        "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
        "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe"
    )

    foreach ($path in $paths) {
        if ($path -and (Test-Path $path)) {
            return $path
        }
    }
    return $null
}

function Find-Ffmpeg {
    try {
        $cmd = Get-Command "ffmpeg" -ErrorAction Stop
        return $cmd.Source
    } catch {
        $paths = @(
            "$env:ProgramFiles\ffmpeg\bin\ffmpeg.exe",
            "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-*\bin\ffmpeg.exe"
        )
        foreach ($path in $paths) {
            $match = Get-ChildItem -Path $path -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($match) {
                return $match.FullName
            }
        }
    }
    return $null
}

function Test-VCRedist {
    $roots = @(
        "HKLM:\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64",
        "HKLM:\SOFTWARE\WOW6432Node\Microsoft\VisualStudio\14.0\VC\Runtimes\x64"
    )
    foreach ($root in $roots) {
        try {
            $item = Get-ItemProperty -Path $root -ErrorAction Stop
            if ($item.Installed -eq 1) {
                return $true
            }
        } catch {
        }
    }
    return $false
}

function Test-VBCableDevice {
    $names = @()
    try {
        $devices = Get-CimInstance Win32_SoundDevice -ErrorAction Stop
        $names = @($devices | ForEach-Object { $_.Name })
    } catch {
        try {
            $devices = Get-PnpDevice -Class AudioEndpoint -ErrorAction Stop
            $names = @($devices | ForEach-Object { $_.FriendlyName })
        } catch {
            return $false
        }
    }
    foreach ($name in $names) {
        if ([string]::IsNullOrWhiteSpace($name)) {
            continue
        }
        $lower = $name.ToLowerInvariant()
        if ($lower.Contains("vb-audio") -or $lower.Contains("cable input") -or $lower.Contains("cable output")) {
            return $true
        }
    }
    return $false
}

function Ensure-ExternalPrerequisites {
    param([string]$WingetPath)

    if ($SkipExternalInstall) {
        Write-Warn "Skipping external prerequisite installs because -SkipExternalInstall was supplied"
        return
    }

    if (-not $WingetPath) {
        Write-Warn "winget was not found. Automatic installs for Python/Chrome/VC++/FFmpeg are unavailable."
        return
    }

    if (-not (Resolve-Python312)) {
        Install-WingetPackage -WingetPath $WingetPath -Id "Python.Python.3.12" -Name "Python 3.12" | Out-Null
    } else {
        Write-Ok "Python 3.12 already available"
    }

    if (-not (Find-Chrome)) {
        Install-WingetPackage -WingetPath $WingetPath -Id "Google.Chrome" -Name "Google Chrome" | Out-Null
    } else {
        Write-Ok "Chrome/Chromium browser already available"
    }

    if (-not (Test-VCRedist)) {
        Install-WingetPackage -WingetPath $WingetPath -Id "Microsoft.VCRedist.2015+.x64" -Name "Microsoft Visual C++ Redistributable" | Out-Null
    } else {
        Write-Ok "Microsoft Visual C++ Redistributable appears installed"
    }

    if (-not (Find-Ffmpeg)) {
        Install-WingetPackage -WingetPath $WingetPath -Id "Gyan.FFmpeg" -Name "FFmpeg" | Out-Null
    } else {
        Write-Ok "FFmpeg already available"
    }
}

function Write-LauncherIfMissing {
    param(
        [string]$FileName,
        [string]$Content
    )
    $path = Join-Path $Root $FileName
    if (Test-Path $path) {
        Write-Ok "$FileName exists; not overwriting"
        return
    }
    Set-Content -Path $path -Value $Content -Encoding ASCII
    Write-Ok "Created $FileName"
}

function Ensure-Launchers {
    $backend = @'
@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Missing .venv. Run setup_project.bat first.
  pause
  exit /b 1
)
echo Starting SPECTRUM CODEX backend at http://127.0.0.1:8000
".venv\Scripts\python.exe" -m uvicorn src.web_app:app --host 127.0.0.1 --port 8000
pause
'@

    $tests = @'
@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Missing .venv. Run setup_project.bat first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m pytest tests -q
pause
'@

    $quickstart = @'
@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Missing .venv. Run setup_project.bat first.
  pause
  exit /b 1
)
set "PATH=%CD%\.venv\Scripts;%PATH%"
call "%~dp0spectrum_business_quickstart.bat" %*
'@

    $agent = @'
@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Missing .venv. Run setup_project.bat first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m src.main --agent-type spectrum --realtime %*
pause
'@

    Write-LauncherIfMissing "run_backend.bat" $backend
    Write-LauncherIfMissing "run_tests.bat" $tests
    Write-LauncherIfMissing "run_quickstart.bat" $quickstart

    if (Test-Path (Join-Path $Root "src\main.py")) {
        Write-LauncherIfMissing "run_agent.bat" $agent
    } else {
        Write-Warn "src\main.py not found; run_agent.bat was not created"
    }
}

function Install-Dependencies {
    param(
        [string]$VenvPython
    )
    if ($SkipDependencyInstall) {
        Write-Warn "Skipping pip install because -SkipDependencyInstall was supplied"
        return
    }

    Write-Step "Upgrading pip"
    & $VenvPython -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) {
        throw "pip upgrade failed. Check your internet connection, Python install, and antivirus/proxy settings."
    }

    Write-Step "Installing dependencies from requirements.txt"
    & $VenvPython -m pip install -r (Join-Path $Root "requirements.txt")
    if ($LASTEXITCODE -ne 0) {
        throw @"
Dependency installation failed.
Try these fixes:
  1. Run setup_project.bat as Administrator.
  2. Check internet connection.
  3. Upgrade Python to 3.10 or newer.
  4. For audio packages, install Microsoft Visual C++ Redistributable if Windows asks for it.
  5. Re-run: .venv\Scripts\python.exe -m pip install -r requirements.txt
"@
    }
    Write-Ok "Dependencies installed"
}

try {
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host " $ProjectName Windows Setup" -ForegroundColor Cyan
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host "Project folder: $Root"

    Write-Step "Checking external Windows prerequisites"
    $wingetPath = Resolve-Winget
    if ($wingetPath) {
        Write-Ok "winget found: $wingetPath"
    } else {
        Write-Warn "winget not found; automatic external installs will be skipped"
    }
    Ensure-ExternalPrerequisites -WingetPath $wingetPath

    Write-Step "Checking Python 3.12"
    $python = Resolve-Python312
    if (-not $python) {
        throw "Python 3.12 was not found. Install Python 3.12 from https://www.python.org/downloads/release/python-312/ and check 'Add Python to PATH'. Do not use Python 3.14 for this project."
    }

    $version = [Version]$python.Version
    Write-Ok "Python 3.12 found: $($python.Version) ($($python.Exe) -3.12)"
    if ($version.Major -ne 3 -or $version.Minor -ne 12) {
        throw "Python 3.12 is required. Found $($python.Version)."
    }

    Write-Step "Checking requirements.txt"
    Ensure-Requirements

    Write-Step "Creating required folders"
    Ensure-Folders
    Ensure-StarterContacts

    Write-Step "Checking .env"
    Ensure-EnvExample
    Ensure-Env

    Write-Step "Checking Chrome or Chromium"
    $chromePath = Find-Chrome
    if ($chromePath) {
        Write-Ok "Browser found: $chromePath"
    } else {
        Write-Warn "Chrome/Edge/Chromium not found. Install Google Chrome before using Google Voice automation."
    }

    if (Test-VBCableDevice) {
        Write-Ok "VB-CABLE-style audio device detected"
    } else {
        Write-Warn "VB-CABLE was not detected. Install it from https://vb-audio.com/Cable/ before live call audio routing."
    }

    if (Find-Ffmpeg) {
        Write-Ok "FFmpeg available"
    } else {
        Write-Warn "FFmpeg not found. Edge TTS may still work through soundfile, but FFmpeg is a useful fallback."
    }

    Write-Step "Creating virtual environment"
    $venvPath = Join-Path $Root ".venv"
    $venvPython = Join-Path $venvPath "Scripts\python.exe"

    $venvStatus = Test-VenvPython -VenvPython $venvPython
    if ((Test-Path $venvPath) -and -not $venvStatus.Ok) {
        Write-Warn ".venv is not usable: $($venvStatus.Reason)"
        Write-Warn "This often happens when a project is copied from another PC and .venv contains old absolute Python paths."
        Remove-BrokenVenv -VenvPath $venvPath
    }

    if (-not (Test-Path $venvPython)) {
        Invoke-Python $python @("-m", "venv", ".venv")
        if ($LASTEXITCODE -ne 0) {
            throw "Virtual environment creation failed."
        }
        Write-Ok "Created .venv with Python 3.12"
    } else {
        Write-Ok ".venv already exists and is usable with Python $($venvStatus.Version)"
    }

    $venvStatus = Test-VenvPython -VenvPython $venvPython
    if (-not $venvStatus.Ok) {
        throw ".venv was created but failed validation: $($venvStatus.Reason)"
    }

    $env:VIRTUAL_ENV = $venvPath
    $env:Path = (Join-Path $venvPath "Scripts") + [IO.Path]::PathSeparator + $env:Path
    Write-Ok ".venv activated for this setup session"

    Install-Dependencies -VenvPython $venvPython

    Write-Step "Creating launcher files"
    Ensure-Launchers

    Write-Step "Final verification"
    & $venvPython -c "import sys; print('Venv Python:', sys.version.split()[0])"
    if ($LASTEXITCODE -eq 0) {
        Write-Ok "Virtual environment is usable"
    } else {
        throw "Virtual environment Python check failed."
    }

    Write-Host ""
    Write-Host "========================================" -ForegroundColor Green
    Write-Host " Setup complete for $ProjectName" -ForegroundColor Green
    Write-Host "========================================" -ForegroundColor Green
    Write-Host "Next steps:"
    Write-Host "  1. Open .env and fill GROQ_API_KEY and callback settings."
    Write-Host "  2. Run run_backend.bat for the web console."
    Write-Host "  3. Run run_tests.bat to verify the install."
    Write-Host "  4. Log into Google Voice in the Chrome profile before live calling."
    Pause-End
    exit 0
} catch {
    Write-Host ""
    Write-Fail $_.Exception.Message
    Write-Host ""
    Write-Host "Setup did not delete or overwrite your data." -ForegroundColor Yellow
    Write-Host "If PowerShell blocks the script, run setup_project.bat or:" -ForegroundColor Yellow
    Write-Host "  powershell -NoProfile -ExecutionPolicy Bypass -File .\setup_project.ps1" -ForegroundColor Yellow
    Pause-End
    exit 1
}
