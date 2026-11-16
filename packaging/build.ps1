<#
 Builds the Windows installer.  Run on a Windows PC with Python 3.12 or newer (64-bit) and Inno Setup 6 installed:
     powershell -ExecutionPolicy Bypass -File packaging\build.ps1 -Version 1.0.0
 Output: dist\installer\WinesPOS-Setup-<version>.exe
 Put the PostgreSQL 16 Windows installer (the EDB one) in packaging\prereqs\ if the installer should be able to set
 PostgreSQL up by itself on a PC that does not have it. Without it the installer asks for an existing PostgreSQL.
#>
param([string]$Version = "1.0.0", [switch]$SkipTests)
# "Continue": in Windows PowerShell 5.1 "Stop" turns any text a program (pip, PyInstaller) writes to stderr into a crash.
# Every program call below is checked through its exit code instead.
$ErrorActionPreference = "Continue"
Set-Location (Split-Path $PSScriptRoot)

if (-not (Test-Path .build-venv)) {
    # Find a 64-bit Python 3.12 or newer: the py launcher first, then plain "python" on the PATH.
    function Test-Py([string]$exe, [string[]]$prefix) {
        $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
        try {
            $out = & $exe @prefix -c "import sys; print(sys.version_info[0]*100+sys.version_info[1], sys.maxsize > 2**32)" 2>$null
            if ($LASTEXITCODE -ne 0 -or -not $out) { return $false }
            $parts = ("$out").Trim().Split(" ")
            return ([int]$parts[0] -ge 312 -and $parts[1] -eq "True")
        } catch { return $false } finally { $ErrorActionPreference = $old }
    }
    $pyExe = $null; $pyPrefix = @()
    foreach ($v in "3.14", "3.13", "3.12") {
        if ((Get-Command py -ErrorAction SilentlyContinue) -and (Test-Py "py" @("-$v"))) { $pyExe = "py"; $pyPrefix = @("-$v"); break }
    }
    if (-not $pyExe -and (Get-Command python -ErrorAction SilentlyContinue) -and (Test-Py "python" @())) { $pyExe = "python" }
    if (-not $pyExe) { throw "Python 3.12 or newer (64-bit) was not found. Install it from python.org, then run again." }
    Write-Host "Using $pyExe $($pyPrefix -join ' ')"
    & $pyExe @pyPrefix -m venv .build-venv
    if ($LASTEXITCODE -ne 0) { throw "Could not create the build environment." }
}
$py = ".\.build-venv\Scripts\python.exe"
& $py -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed." }
& $py -m pip install -r requirements.txt pyinstaller
if ($LASTEXITCODE -ne 0) { throw "Installing the requirements failed." }

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
