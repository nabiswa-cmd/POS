# Run by the uninstaller. Stops and removes the tasks and firewall rule. NEVER touches the shop's data in ProgramData.
$ErrorActionPreference = "SilentlyContinue"
Stop-ScheduledTask -TaskName "WinesPOS Server"
Unregister-ScheduledTask -TaskName "WinesPOS Server" -Confirm:$false
Unregister-ScheduledTask -TaskName "WinesPOS Backup" -Confirm:$false
Get-NetFirewallRule -DisplayName "WinesPOS second till" | Remove-NetFirewallRule
