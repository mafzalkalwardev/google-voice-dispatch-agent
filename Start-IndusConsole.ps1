param(
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    $Python = "python"
}

$Url = "http://127.0.0.1:$Port/run"

Write-Host "Starting INDUS TRANSPORTS LLC Dispatch Agent Console..."
Write-Host "Backend: python -m src.web_app"
Write-Host "Opening: $Url"

Start-Process -FilePath $Python -ArgumentList @("-m", "src.web_app", "--port", "$Port") -WorkingDirectory $Root
Start-Sleep -Seconds 3
Start-Process $Url
