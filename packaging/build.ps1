<#
 Builds the Windows installer.  Run on a Windows PC with Python 3.12 (64-bit) and Inno Setup 6 installed:
     powershell -ExecutionPolicy Bypass -File packaging\build.ps1 -Version 1.0.0
 Output: dist\installer\WinesPOS-Setup-<version>.exe
 Put the PostgreSQL 16 Windows installer (the EDB one) in packaging\prereqs\ if the installer should be able to set
 PostgreSQL up by itself on a PC that does not have it. Without it the installer asks for an existing PostgreSQL.
#>
param([string]$Version = "1.0.0", [switch]$SkipTests)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot)

if (-not (Test-Path .build-venv)) { py -3.12 -m venv .build-venv }
$py = ".\.build-venv\Scripts\python.exe"
& $py -m pip install --upgrade pip
& $py -m pip install -r requirements.txt pyinstaller

# Build-time settings only: the installed program uses the shop's own settings file, never these.
$env:DJANGO_SECRET_KEY = "build-only-not-used-by-the-shop"
$env:DB_ENGINE = "sqlite"
$env:DJANGO_DEBUG = "false"

if (-not $SkipTests) {
    $env:DJANGO_DEBUG = "true"
    & $py -m unittest discover -s tests_pure -t .
    if ($LASTEXITCODE -ne 0) { throw "Unit tests failed. Fix them before building." }
    & $py manage.py test
    if ($LASTEXITCODE -ne 0) { throw "Tests failed. Fix them before building." }
    $env:DJANGO_DEBUG = "false"
}

& $py manage.py collectstatic --noinput
if ($LASTEXITCODE -ne 0) { throw "collectstatic failed." }
& $py -m PyInstaller --noconfirm --clean packaging\winespos.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed." }

$iscc = (Get-Command iscc.exe -ErrorAction SilentlyContinue).Source
if (-not $iscc) { $iscc = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" }
if (-not (Test-Path $iscc)) { throw "Inno Setup 6 was not found. Install it from jrsoftware.org." }
& $iscc "/DAppVersion=$Version" packaging\WinesPOS.iss
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed." }
Write-Host "Done: dist\installer\WinesPOS-Setup-$Version.exe"
