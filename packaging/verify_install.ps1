<#
 After installing, run on each PC (as administrator) to check it really works:
     powershell -ExecutionPolicy Bypass -File "C:\Program Files\WinesPOS\scripts\verify_install.ps1"
 Prints PASS / FAIL per line and exits 1 if anything failed. Covers: shortcuts exist and point at the program,
 settings file is private, the server task and backup task exist, the server answers, the shop health check is clean.
#>
param([string]$AppDir = (Split-Path $PSScriptRoot))
$failed = 0
function Check($name, [bool]$ok, $detail = "") {
    if ($ok) { Write-Host "PASS  $name" } else { Write-Host "FAIL  $name  $detail" -ForegroundColor Red; $script:failed++ }
}
$shell = New-Object -ComObject WScript.Shell
$exe = Join-Path $AppDir "WinesPOS.exe"
Check "Program is installed" (Test-Path $exe) $exe

$serverPc = [bool](Get-ScheduledTask -TaskName "WinesPOS Server" -ErrorAction SilentlyContinue)
$wanted = @{ "POS (Cashier)" = "pos" }
if ($serverPc) { $wanted["Manager"] = "manager"; $wanted["Stock Receiving"] = "receiving" }
foreach ($name in $wanted.Keys) {
    $link = Join-Path ([Environment]::GetFolderPath("CommonDesktopDirectory")) "$name.lnk"
    $exists = Test-Path $link
    Check "Desktop shortcut '$name' exists" $exists $link
    if ($exists) {
        $s = $shell.CreateShortcut($link)
        Check "'$name' starts the right window" (($s.TargetPath -eq $exe) -and ($s.Arguments.Trim() -eq $wanted[$name])) "$($s.TargetPath) $($s.Arguments)"
    }
}
$envFile = Join-Path $env:ProgramData "WinesPOS\config\winespos.env"
Check "Settings file exists" (Test-Path $envFile) $envFile

if ($serverPc) {
    $acl = (& icacls $envFile) -join " "
    Check "Settings file (holds secrets) is not readable by ordinary users" ($acl -notmatch "Users:|Everyone:|Authenticated Users:") $acl
    Check "Server task exists" ([bool](Get-ScheduledTask -TaskName "WinesPOS Server" -ErrorAction SilentlyContinue))
    Check "Nightly backup task exists" ([bool](Get-ScheduledTask -TaskName "WinesPOS Backup" -ErrorAction SilentlyContinue))
    $ok = $false
    try { $ok = (Invoke-WebRequest "http://127.0.0.1:8000/health/" -UseBasicParsing -TimeoutSec 5).StatusCode -eq 200 } catch {}
    Check "Server answers on this PC" $ok
    $env:WINESPOS_ENV_FILE = $envFile
    & (Join-Path $AppDir "WinesPOSAdmin.exe") manage selfcheck
    Check "Shop health check has no FAIL" ($LASTEXITCODE -eq 0)
} else {
    $url = ((Get-Content $envFile | Where-Object { $_ -like "WINESPOS_SERVER_URL=*" }) -replace "WINESPOS_SERVER_URL=", "")
    $ok = $false
    try { $ok = (Invoke-WebRequest "$url/health/" -UseBasicParsing -TimeoutSec 5).StatusCode -eq 200 } catch {}
    Check "Till can reach the server at $url" $ok
}
if ($failed) { Write-Host "`n$failed check(s) FAILED." -ForegroundColor Red; exit 1 }
Write-Host "`nAll checks passed."
