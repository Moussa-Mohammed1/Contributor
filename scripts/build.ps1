# Build standalone Windows executables with PyInstaller.
# Run from the repository root:  powershell -ExecutionPolicy Bypass -File scripts\build.ps1

$ErrorActionPreference = "Stop"

Set-Location (Join-Path $PSScriptRoot "..")

if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment..." -ForegroundColor Cyan
    python -m venv .venv
}

$python = Join-Path $PSScriptRoot "..\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $python = "python"
}

Write-Host "Installing build dependencies..." -ForegroundColor Cyan
& $python -m pip install -U pip
& $python -m pip install -e ".[dev]" --quiet

$dist = Join-Path $PSScriptRoot "..\dist"
New-Item -ItemType Directory -Force -Path $dist | Out-Null

Write-Host "Building keeper.exe (CLI + daemon)..." -ForegroundColor Cyan
& $python -m PyInstaller `
    --noconfirm `
    --clean `
    --name keeper `
    --distpath $dist `
    --workpath .build\pyinstaller-cli `
    --specpath .build\specs `
    --console `
    --add-data "keeper\gui\pages;keeper\gui\pages" `
    keeper\__main__.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Building keeper-gui.exe (Textual dashboard)..." -ForegroundColor Cyan
& $python -m PyInstaller `
    --noconfirm `
    --clean `
    --name keeper-gui `
    --distpath $dist `
    --workpath .build\pyinstaller-gui `
    --specpath .build\specs `
    --console `
    --add-data "keeper\gui\pages;keeper\gui\pages" `
    --onefile `
    --collect-all textual `
    keeper\gui\app.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "Done. Artifacts:" -ForegroundColor Green
Get-ChildItem $dist -Filter "*.exe" | ForEach-Object { Write-Host "  $($_.FullName)" }
Write-Host ""
Write-Host "Run the daemon with:  .\dist\keeper.exe start" -ForegroundColor Yellow
