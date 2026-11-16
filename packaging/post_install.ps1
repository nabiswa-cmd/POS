<#
 Run by the installer after the files are copied (as administrator). Safe to run again (run the installer again to repair).
 Sets up: settings file, PostgreSQL + database, tables, first manager, the server and nightly backup tasks, firewall.
#>
param(
    [Parameter(Mandatory)][ValidateSet("server", "till")][string]$Role,
    [Parameter(Mandatory)][string]$AppDir,
    [string]$LanHost = "",
    [string]$ServerUrl = "",
    [string]$ShopName = "",
    [string]$ManagerUser = "",
    [string]$SecretsFile = "",     # a temp file the installer wrote: line 1 manager password, line 2 PostgreSQL admin password, line 3 sync token
    [string]$SyncUrl = ""          # optional: the online server, e.g. https://pos.devs.surf. With the token it connects this PC automatically.
)
$ErrorActionPreference = "Stop"
$Data = Join-Path $env:ProgramData "WinesPOS"
$Cfg = Join-Path $Data "config"
$Logs = Join-Path $Data "logs"
New-Item -ItemType Directory -Force -Path $Cfg, $Logs | Out-Null
Start-Transcript -Path (Join-Path $Logs "install.log") -Append | Out-Null
$Admin = Join-Path $AppDir "WinesPOSAdmin.exe"
$EnvFile = Join-Path $Cfg "winespos.env"
$env:WINESPOS_ENV_FILE = $EnvFile
$PgAdminFile = Join-Path $Cfg "postgres-admin.txt"

function Step($text) { Write-Host "`n== $text" }
function Run($exe, [string[]]$arguments) {
    & $exe @arguments
    if ($LASTEXITCODE -ne 0) { throw "'$([IO.Path]::GetFileName($exe)) $($arguments -join ' ')' failed (code $LASTEXITCODE)." }
}
function Lock-File($path, [bool]$usersMayRead) {
    $grants = @("*S-1-5-32-544:F", "*S-1-5-18:F")        # Administrators, SYSTEM (by SID: works on any Windows language)
    if ($usersMayRead) { $grants += "*S-1-5-32-545:R" }  # Users
    & icacls $path /inheritance:r | Out-Null
    foreach ($g in $grants) { & icacls $path /grant:r $g | Out-Null }
}
function Find-Pg($tool) {
    $c = Get-Command "$tool.exe" -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    $hit = Get-ChildItem "$env:ProgramFiles\PostgreSQL\*\bin\$tool.exe" -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | Select-Object -First 1
    if ($hit) { return $hit.FullName }
    return $null
}

try {
    $managerPw = ""; $pgAdminPw = ""; $syncToken = ""
    if ($SecretsFile -and (Test-Path $SecretsFile)) {
        $lines = Get-Content $SecretsFile
        if ($lines.Count -ge 1) { $managerPw = $lines[0] }
        if ($lines.Count -ge 2) { $pgAdminPw = $lines[1] }
        if ($lines.Count -ge 3) { $syncToken = $lines[2].Trim() }
        Remove-Item $SecretsFile -Force
    }

    if ($Role -eq "till") {
        Step "Configuring this PC as a till"
        Run $Admin @("configure", "--role", "till", "--server-url", $ServerUrl)
        Lock-File $EnvFile $true          # holds only the server address, so cashiers may read it
        try { Invoke-WebRequest "$ServerUrl/health/" -UseBasicParsing -TimeoutSec 8 | Out-Null; Write-Host "The server answered." }
        catch { Write-Warning "The server at $ServerUrl did not answer yet. Check it is on and the address is right." }
        Stop-Transcript | Out-Null
        exit 0
    }

    Step "PostgreSQL"
    if (-not (Find-Pg "pg_dump")) {
        $setup = Get-ChildItem (Join-Path $AppDir "prereqs\postgresql-*.exe") -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $setup) { throw "PostgreSQL is not installed and no PostgreSQL installer was bundled. Install PostgreSQL 16, then run the Wines & Spirits POS installer again." }
        if (-not $pgAdminPw) { $pgAdminPw = [Convert]::ToBase64String((1..24 | ForEach-Object { Get-Random -Maximum 256 }) -as [byte[]]).Replace("=", "") }
        Write-Host "Installing PostgreSQL (a few minutes)..."
        Run $setup.FullName @("--mode", "unattended", "--unattendedmodeui", "none", "--superpassword", $pgAdminPw, "--servicename", "postgresql-winespos", "--serverport", "5432")
        Set-Content -Path $PgAdminFile -Value $pgAdminPw -Encoding ASCII
        Lock-File $PgAdminFile $false      # only administrators can read the PostgreSQL admin password
    } elseif (-not $pgAdminPw -and (Test-Path $PgAdminFile)) {
        $pgAdminPw = (Get-Content $PgAdminFile -Raw).Trim()
    }
    if (-not $pgAdminPw) { throw "The PostgreSQL 'postgres' password is needed to create the shop database." }

    Step "Settings and database"
    $cfgArgs = @("configure", "--role", "server", "--provision-db")
    if ($LanHost) { $cfgArgs += @("--lan-host", $LanHost) }
    $env:WINESPOS_PG_ADMIN_PASSWORD = $pgAdminPw
    if ($SyncUrl -and $syncToken) { $cfgArgs += @("--sync-url", $SyncUrl); $env:WINESPOS_SYNC_TOKEN = $syncToken }
    Run $Admin $cfgArgs
    Remove-Item Env:\WINESPOS_SYNC_TOKEN -ErrorAction SilentlyContinue
    Remove-Item Env:\WINESPOS_PG_ADMIN_PASSWORD
    Lock-File $EnvFile $false               # secrets: administrators and the server task only

    Step "Creating the tables"
    Run $Admin @("manage", "migrate", "--noinput")
    Run $Admin @("manage", "harden_db")
    $seeded = $false
    if ($SyncUrl -and $syncToken) {
        Step "Copying the online server's data to this PC (first install)"
        $out = & $Admin manage sync_seed --if-available 2>&1 | Out-String
        Write-Host $out
        $seeded = $out -match "now starts from"
        if (-not $seeded) { Write-Warning "Nothing was copied from the online server (see the line above). Setting up a fresh shop instead." }
    }
    $setupArgs = @("manage", "setup_shop")
    if ($ShopName) { $setupArgs += @("--shop-name", $ShopName) }
    if ($ManagerUser -and -not $seeded) { $setupArgs += @("--manager-username", $ManagerUser) }   # a seeded shop already has its users
    if ($LanHost) { $setupArgs += @("--tills", "2") } else { $setupArgs += @("--tills", "1") }
    if ($managerPw) { $env:WINESPOS_MANAGER_PASSWORD = $managerPw }
    Run $Admin $setupArgs
    Remove-Item Env:\WINESPOS_MANAGER_PASSWORD -ErrorAction SilentlyContinue

    Step "Starting the shop server and the nightly backup"
    $account = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
    $serverAction = New-ScheduledTaskAction -Execute $Admin -Argument "server" -WorkingDirectory $AppDir
    $serverSettings = New-ScheduledTaskSettingsSet -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName "WinesPOS Server" -Action $serverAction -Trigger (New-ScheduledTaskTrigger -AtStartup) `
        -Principal $account -Settings $serverSettings -Force | Out-Null
    $backupAction = New-ScheduledTaskAction -Execute $Admin -Argument "manage backup_db" -WorkingDirectory $AppDir
    $backupSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName "WinesPOS Backup" -Action $backupAction -Trigger (New-ScheduledTaskTrigger -Daily -At 23:00) `
        -Principal $account -Settings $backupSettings -Force | Out-Null
    Start-ScheduledTask -TaskName "WinesPOS Server"

    if ($LanHost) {
        Step "Letting the second till reach the server (shop network only)"
        Get-NetFirewallRule -DisplayName "WinesPOS second till" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
        New-NetFirewallRule -DisplayName "WinesPOS second till" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8000 `
            -Profile Private -RemoteAddress LocalSubnet | Out-Null
    }

    if ($SyncUrl -and $syncToken) {
        Step "Connecting this PC to the online server (secure tunnel)"
        try {
            $ssh = "$env:SystemRoot\System32\OpenSSH\ssh.exe"; $keygen = "$env:SystemRoot\System32\OpenSSH\ssh-keygen.exe"
            if (-not (Test-Path $keygen)) { throw "Windows OpenSSH client is not installed (Settings > Optional features)." }
            $TunnelDir = Join-Path $Data "tunnel"; New-Item -ItemType Directory -Force $TunnelDir | Out-Null
            $key = Join-Path $TunnelDir "id_ed25519"
            if (-not (Test-Path $key)) { & $keygen -t ed25519 -f $key -N '""' -C "shop-pc" | Out-Null }
            & icacls $TunnelDir /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" | Out-Null
            $pub = (Get-Content "$key.pub" -Raw).Trim()
            $reply = Invoke-RestMethod -Method Post -Uri "$($SyncUrl.TrimEnd('/'))/api/sync/tunnel-key/" -TimeoutSec 20 `
                -Headers @{ Authorization = "Bearer $syncToken" } -ContentType "application/json" -Body (@{ public_key = $pub } | ConvertTo-Json)
            $remotePort = [int]$reply.port
            $hostName = ([Uri]$SyncUrl).Host
            $loop = @"
while (`$true) {
  & "$ssh" -N -T -R 127.0.0.1:${remotePort}:127.0.0.1:8000 -i "$key" -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$TunnelDir\known_hosts" tunnel@$hostName
  Start-Sleep -Seconds 10
}
"@
            Set-Content -Path (Join-Path $TunnelDir "tunnel.ps1") -Value $loop
            $tAction = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$TunnelDir\tunnel.ps1`""
            $tSettings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
            Register-ScheduledTask -TaskName "WinesPOS Tunnel" -Action $tAction -Trigger (New-ScheduledTaskTrigger -AtStartup) -Principal $account -Settings $tSettings -Force | Out-Null
            Start-ScheduledTask -TaskName "WinesPOS Tunnel"
            Write-Host "Tunnel registered ($($reply.result)) and started."
        } catch {
            Write-Warning "The secure tunnel was not set up: $($_.Exception.Message). Backups still sync; remote access (shop address) needs the manual tunnel steps."
        }
    }

    Step "Waiting for the server"
    $up = $false
    foreach ($i in 1..40) {
        try { if ((Invoke-WebRequest "http://127.0.0.1:8000/health/" -UseBasicParsing -TimeoutSec 3).StatusCode -eq 200) { $up = $true; break } } catch { Start-Sleep -Seconds 2 }
    }
    if (-not $up) { throw "The server did not start. See $Logs and run: WinesPOSAdmin.exe manage selfcheck" }
    Write-Host "The server is running."
    & $Admin manage selfcheck
    Stop-Transcript | Out-Null
    exit 0
}
catch {
    Write-Error $_
    Stop-Transcript | Out-Null
    exit 1
}
