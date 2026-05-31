param(
    [string]$ExePath,
    [switch]$NoStartup,
    [switch]$StartupNoBrowser,
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
if (-not $ExePath) {
    $ExePath = Join-Path $Root "dist\IndusDispatchConsole.exe"
}

$ExePath = (Resolve-Path -LiteralPath $ExePath).Path
if (-not (Test-Path -LiteralPath $ExePath)) {
    throw "EXE not found: $ExePath. Run scripts\build_windows_exe.ps1 first."
}

$Shell = New-Object -ComObject WScript.Shell
$Desktop = [Environment]::GetFolderPath("Desktop")
$Startup = [Environment]::GetFolderPath("Startup")

function New-AppShortcut {
    param(
        [Parameter(Mandatory = $true)][string]$ShortcutPath,
        [string]$Arguments = ""
    )

    $Shortcut = $Shell.CreateShortcut($ShortcutPath)
    $Shortcut.TargetPath = $ExePath
    $Shortcut.WorkingDirectory = Split-Path -Parent $ExePath
    $Shortcut.Arguments = $Arguments
    $Shortcut.IconLocation = "$ExePath,0"
    $Shortcut.Description = "INDUS TRANSPORTS LLC Dispatch Agent Console"
    $Shortcut.Save()
}

$DesktopShortcut = Join-Path $Desktop "Indus Dispatch Agent.lnk"
New-AppShortcut -ShortcutPath $DesktopShortcut

$StartupShortcut = $null
if (-not $NoStartup) {
    $StartupShortcut = Join-Path $Startup "Indus Dispatch Agent.lnk"
    $Args = "--port $Port"
    if ($StartupNoBrowser) {
        $Args = "$Args --no-browser"
    }
    New-AppShortcut -ShortcutPath $StartupShortcut -Arguments $Args
}

Write-Host "Shortcuts installed:"
Write-Host "  Desktop: $DesktopShortcut"
if ($StartupShortcut) {
    Write-Host "  Startup: $StartupShortcut"
} else {
    Write-Host "  Startup: skipped"
}
