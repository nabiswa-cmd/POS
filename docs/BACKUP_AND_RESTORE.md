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
This replaces the active database and product images with the backup, so it is not on a screen and needs an administrator.
1. Tell the cashiers to stop selling. Close the POS windows.
2. On the server PC, administrator prompt: `Stop-ScheduledTask "WinesPOS Server"`
3. `"C:\Program Files\WinesPOS\WinesPOSAdmin.exe" manage list_backups` then
   `... manage restore_db "C:\ProgramData\WinesPOS\data\backups\winespos-20261108-230000.zip" --yes`
4. `Start-ScheduledTask "WinesPOS Server"`, open the Manager screen and check the newest sale and the stock.

The tool verifies the file, refuses a backup from a **newer** program version or a different database engine, and writes and verifies **`...-prerestore.zip`** first. PostgreSQL restores go into a new empty staging database, never over the migrated live database. Compatible migrations run there, including a check/repair for the receipt-printer column. Only after validation does the tool switch databases. The former database is retained as `winespos_before_restore_<timestamp>` and old media is moved aside, not deleted. If staging, migration, or validation fails, the active database is not switched. If the switch or media swap fails, setup stops and attempts to restore the previous database; errors identify any retained recovery database.
Sales made after the backup was taken are **not** in it: check the day's paper slips and the M-PESA statement for the gap, and the Cash-ups screen will show the difference when the drawers are counted.

On the Windows shop server, **Manager > Server sync > Replace local data with live server database** automates the restore: it requests a fresh snapshot from the configured online server's active POS database, refuses while any cashier shift is open, saves a pre-restore safety backup, stops the WinesPOS Printer and Server tasks, stages the restored database and product images, applies compatible migrations, and switches only after validation. The manager page reconnects when the server is back. All tills must be closed first, and sales/data not contained in the online database snapshot will be replaced. This is different from the newest shop backup uploaded to the sync inbox.

**Download live server database snapshot** remains available when you only want the archive. It requests a fresh snapshot from the configured online server's active database. The separate sync-inbox download is not the same source. On non-Windows systems, use the manual restore steps above; the automatic task restart is specific to the installed Windows shop server.

## Moving to a new PC
Choose the online restoration path during installation only if the online server's backup should replace the active local shop data. After confirmation, the installer restores into a new, validated staging database and switches only after verification. It creates a pre-restore backup and retains the former database and media. Keep the protected configuration and PostgreSQL administrator password file private. The backup does not contain `DJANGO_SECRET_KEY` or `DESKTOP_SHELL_SECRET`; if those values must remain the same, transfer the protected `winespos.env` securely before installation.

## What is not inside a backup
The settings file `winespos.env` (it holds secrets; keep a copy somewhere safe and private, separate from the backups) and the PostgreSQL admin password file.
