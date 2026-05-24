param(
    [switch]$SkipTests,
    [switch]$BuildInstaller
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    Write-Host "Creating .venv..."
    python -m venv .venv
    $Python = Join-Path $Root ".venv\Scripts\python.exe"
}

Write-Host "Installing runtime dependencies..."
& $Python -m pip install --upgrade pip
& $Python -m pip install -r requirements.txt

Write-Host "Installing build dependencies..."
& $Python -m pip install -r requirements-build.txt

if (-not $SkipTests) {
    Write-Host "Running tests..."
    & $Python -m pytest tests/ -q --tb=short
    & $Python -m compileall -q src
}

Write-Host "Building IndusDispatchConsole.exe..."
$Exe = Join-Path $Root "dist\IndusDispatchConsole.exe"
if (Test-Path $Exe) { Remove-Item $Exe -Force }
& $Python -m PyInstaller --noconfirm --clean packaging\IndusDispatchConsole.spec
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE."
}

if (-not (Test-Path $Exe)) {
    throw "Build failed: $Exe was not created."
}

$ReleaseDir = Join-Path $Root "release"
New-Item -ItemType Directory -Force -Path $ReleaseDir | Out-Null

$Readme = Join-Path $ReleaseDir "INSTALL.txt"
@"
INDUS TRANSPORTS LLC Dispatch Agent Console

1. Install Google Chrome.
2. Install VB-CABLE if you need live Google Voice audio routing.
3. Run IndusDispatchConsole.exe once.
4. The app opens the operator console automatically.
5. Put .env, contacts, logs, audio, and Chrome profiles in:
   %LOCALAPPDATA%\IndusDispatchAgent
6. If port 8000 is already busy, run:
   IndusDispatchConsole.exe --port 8787

Important:
- The EXE does not include your .env, contacts, call logs, transcripts, audio, or Chrome profile.
- Copy .env.example to .env in the runtime folder and set GROQ_API_KEY/CALLBACK_NUMBER.
- Run Preflight before dialing.
"@ | Set-Content -Encoding UTF8 $Readme

$Zip = Join-Path $ReleaseDir "IndusDispatchConsole-portable.zip"
if (Test-Path $Zip) { Remove-Item $Zip -Force }
Compress-Archive -Path $Exe, $Readme -DestinationPath $Zip

Write-Host ""
Write-Host "Build complete:"
Write-Host "  EXE: $Exe"
Write-Host "  ZIP: $Zip"

if ($BuildInstaller) {
    $Iscc = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if (-not $Iscc) {
        Write-Warning "Inno Setup ISCC.exe was not found. Install Inno Setup to build an installer."
    } else {
        Write-Host "Building installer with Inno Setup..."
        & $Iscc.Path "installer\IndusDispatchConsole.iss"
    }
}
