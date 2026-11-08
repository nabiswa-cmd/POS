# Backup and restore

A backup is **one zip file**: the whole database, every product image, and a manifest with a checksum. File name: `winespos-YYYYMMDD-HHMMSS.zip` (time in UTC).

## What happens automatically
* **Every night at 23:00** the *WinesPOS Backup* task runs `manage backup_db`. If the PC was off, it runs when it next starts.
* Files go to `C:\ProgramData\WinesPOS\data\backups`. The newest **30** are kept (`BACKUP_KEEP`). Safety copies made before a restore are kept separately (newest 3).
* If `WINESPOS_BACKUP_COPY_DIR` is set (e.g. `E:\WinesPOS-Backups`, a USB drive, or `\\OFFICE-PC\Backups`) a **second copy** is written there. If the drive is missing the backup still succeeds and says the second copy failed.
* **Manager > Backups** shows the newest backup and goes red when it is older than 26 hours. "Back up now" and "Check it" are there too.

**Put the second copy on a different disk.** A backup on the same disk is lost with the disk. Take the USB drive home weekly.

## Check a backup is good
Manager > Backups > *Check it*, or `WinesPOSAdmin.exe manage verify_backup <file.zip>`. It checks the zip, the checksum and that the database opens. **Do this now and then, and after setting up: a backup nobody has tried to read is only a hope.**

## Restore (only when you really need it)
This **replaces all of today's data** with the backup, so it is not on a screen and needs an administrator.
1. Tell the cashiers to stop selling. Close the POS windows.
2. On the server PC, administrator prompt: `Stop-ScheduledTask "WinesPOS Server"`
3. `"C:\Program Files\WinesPOS\WinesPOSAdmin.exe" manage list_backups` then
   `... manage restore_db "C:\ProgramData\WinesPOS\data\backups\winespos-20261108-230000.zip" --yes`
4. `Start-ScheduledTask "WinesPOS Server"`, open the Manager screen and check the newest sale and the stock.

Before replacing anything the tool: verifies the file, refuses a backup from a **newer** program version, refuses one from a different kind of database, and writes **`...-prerestore.zip`** (the state just before). Old product images are moved aside, not deleted. If you restored the wrong file, restore the `-prerestore.zip` to undo it.
Sales made after the backup was taken are **not** in it: check the day's paper slips and the M-PESA statement for the gap, and the Cash-ups screen will show the difference when the drawers are counted.

## Moving to a new PC
Install on the new PC (empty database), copy the newest backup and the old `winespos.env` over, then restore as above. Keep the old `DJANGO_SECRET_KEY` and `DESKTOP_SHELL_SECRET`, otherwise saved logins stop working.

## What is not inside a backup
The settings file `winespos.env` (it holds secrets; keep a copy somewhere safe and private, separate from the backups) and the PostgreSQL admin password file.
