<#
 After installing, run on each PC (as administrator) to check it really works:
     powershell -ExecutionPolicy Bypass -File "C:\Program Files\WinesPOS\scripts\verify_install.ps1"
 Prints PASS / FAIL per line and exits 1 if anything failed. Covers: shortcuts exist and point at the program,
 settings file is private, the server, configured printer and backup tasks exist, the server answers, the shop health check is clean.
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
$clientEnvFile = Join-Path $env:ProgramData "WinesPOS\config\winespos-client.env"
Check "Settings file exists" (Test-Path $envFile) $envFile
Check "Desktop client settings exist" (Test-Path $clientEnvFile) $clientEnvFile

if ($serverPc) {
    $acl = (& icacls $envFile 2>&1) -join " "
    $envAclCode = $LASTEXITCODE
    Check "Settings file ACL is readable by administrators and SYSTEM only" (($envAclCode -eq 0) -and ($acl -notmatch "Users:|Everyone:|Authenticated Users:")) $acl
    $clientAcl = (& icacls $clientEnvFile 2>&1) -join " "
    $clientAclCode = $LASTEXITCODE
    $clientSettings = if (Test-Path $clientEnvFile) { Get-Content $clientEnvFile } else { @() }
    $hasServerUrl = [bool]($clientSettings | Where-Object { $_ -match "^WINESPOS_SERVER_URL=https?://" })
    $hasNoSecrets = -not [bool]($clientSettings | Where-Object { $_ -match "PASSWORD|SECRET|TOKEN" })
    Check "Desktop client settings are readable by standard users and contain no secrets" `
        (($clientAclCode -eq 0) -and ($clientAcl -match "Users:\(R\)") -and $hasServerUrl -and $hasNoSecrets) $clientAcl
    $pgAdminFile = Join-Path $env:ProgramData "WinesPOS\config\postgres-admin.txt"
    if (Test-Path $pgAdminFile) {
        $pgAcl = (& icacls $pgAdminFile 2>&1) -join " "
        $pgAclCode = $LASTEXITCODE
        Check "PostgreSQL administrator password is private" (($pgAclCode -eq 0) -and ($pgAcl -notmatch "Users:|Everyone:|Authenticated Users:")) $pgAcl
    }
    Check "Server task exists" ([bool](Get-ScheduledTask -TaskName "WinesPOS Server" -ErrorAction SilentlyContinue))
    $printerTask = [bool](Get-ScheduledTask -TaskName "WinesPOS Printer" -ErrorAction SilentlyContinue)
    Check "Receipt printer task exists" $printerTask
    Check "Nightly backup task exists" ([bool](Get-ScheduledTask -TaskName "WinesPOS Backup" -ErrorAction SilentlyContinue))
    $ok = $false
    try { $ok = (Invoke-WebRequest "http://127.0.0.1:8000/health/" -UseBasicParsing -TimeoutSec 5).StatusCode -eq 200 } catch {}
    Check "Server answers on this PC" $ok
    $env:WINESPOS_ENV_FILE = $envFile
    & (Join-Path $AppDir "WinesPOSAdmin.exe") manage selfcheck
    Check "Shop health check has no FAIL" ($LASTEXITCODE -eq 0)
} else {
    $clientAcl = (& icacls $clientEnvFile 2>&1) -join " "
    $clientAclCode = $LASTEXITCODE
    Check "Till client settings are readable by standard users" (($clientAclCode -eq 0) -and ($clientAcl -match "Users:\(R\)")) $clientAcl
    $url = ((Get-Content $envFile | Where-Object { $_ -like "WINESPOS_SERVER_URL=*" }) -replace "WINESPOS_SERVER_URL=", "")
    $ok = $false
    try { $ok = (Invoke-WebRequest "$url/health/" -UseBasicParsing -TimeoutSec 5).StatusCode -eq 200 } catch {}
    Check "Till can reach the server at $url" $ok
}
if ($failed) { Write-Host "`n$failed check(s) FAILED." -ForegroundColor Red; exit 1 }
Write-Host "`nAll checks passed."
