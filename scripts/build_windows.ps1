# Build PySheets.exe (and optionally the installer) on Windows.
#   powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1 [-Installer]
param([switch]$Installer)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

Write-Host "==> Building the UI"
npm --prefix frontend ci
npm --prefix frontend run build

Write-Host "==> Installing Python dependencies"
uv sync --extra desktop --extra sql --group build

Write-Host "==> Running PyInstaller"
uv run pyinstaller packaging\pysheets.spec --noconfirm --clean

if ($Installer) {
    $iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
    if (-not (Test-Path $iscc)) { throw "Inno Setup 6 not found (choco install innosetup)" }
    $version = (uv run python -c "import pysheets; print(pysheets.__version__)").Trim()
    & $iscc "/DAppVersion=$version" packaging\installer.iss
}
Write-Host "Done: dist\PySheets\PySheets.exe"
