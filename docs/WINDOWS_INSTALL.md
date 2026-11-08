# Windows installation

> Written and scripted on Linux. The installer, PyInstaller build and PowerShell scripts have **not** been run on Windows yet.
> Follow "First install on a spare PC" below before using them in the shop, and fix what the logs tell you.

## What you need
| | |
|---|---|
| Build PC (once) | Windows 10/11 64-bit, **Python 3.12 (64-bit)**, **Inno Setup 6**. Optional: the PostgreSQL 16 Windows installer (EDB, `postgresql-16.x-windows-x64.exe`) copied to `packaging\prereqs\` |
| Shop PCs | Windows 10/11 64-bit. The server PC needs ~2 GB free. Nothing else to install |

## 1. Build the installer (on the build PC)
```
powershell -ExecutionPolicy Bypass -File packaging\build.ps1 -Version 1.0.0
```
This runs the tests (stops if any fail; `-SkipTests` skips them), collects the screens' static files, packs the program with
PyInstaller (`dist\WinesPOS\`) and builds `dist\installer\WinesPOS-Setup-1.0.0.exe`.
*Known:* 18 tests in the code you gave me fail for reasons unrelated to installing (see `PHASE6_NOTES.md`), so use `-SkipTests` until those are decided, or the build stops.

## 2. Install the server PC (the one that holds the data)
Run the installer as administrator and answer:
1. **Shop server PC**.
2. Shop name, manager login name, and (only if a second till will connect) this PC's shop-network address, e.g. `192.168.1.10`. Give the PC a fixed address in the router first.
3. A manager password (10+ characters). **PostgreSQL password:** leave empty if the installer should install PostgreSQL itself (a bundled installer is needed); otherwise type your existing `postgres` password.

It then: writes the settings file `C:\ProgramData\WinesPOS\config\winespos.env` (private: administrators only), creates the database,
creates the tables, creates the manager, starts the **WinesPOS Server** task (starts at boot, restarts if it stops),
schedules **WinesPOS Backup** every night at 23:00, and, if you gave an address, opens port 8000 to the shop network only.
Shortcuts on the desktop: **POS (Cashier)**, **Manager**, **Stock Receiving**.

Where things live: program `C:\Program Files\WinesPOS` (replaced on update) · data `C:\ProgramData\WinesPOS\data` (`media`, `backups`) · logs `C:\ProgramData\WinesPOS\logs` (`install.log`, `server.log`). **Never delete ProgramData\WinesPOS: it is the shop.**

## 3. Install the second till
Run the same installer on that PC, choose **Till PC**, and type `http://192.168.1.10:8000` (the server's address). It gets only the **POS** shortcut and no database. Both tills use the one database on the server (never two databases).

## 4. Check it
On each PC, as administrator:
```
powershell -ExecutionPolicy Bypass -File "C:\Program Files\WinesPOS\scripts\verify_install.ps1"
```
It prints PASS/FAIL for: shortcuts exist and start the right window, settings file is private, the server and backup tasks exist, the server answers, and the shop health check (`selfcheck`) is clean. Then do `UAT_CHECKLIST.md`.

## 5. Printers
Default: the normal Windows print dialog; any printer works. For a thermal receipt printer:
1. Install its Windows driver. Set the paper size to its roll width (80 mm or 58 mm) and margins to 0 in Printing preferences.
2. List the exact names: `"C:\Program Files\WinesPOS\WinesPOS.exe" printers`. Test one: `... printers --test "EPSON TM-T20III Receipt"`.
3. For no dialog at all, add one line to `winespos.env` on **each till PC** and restart the POS window: `WINESPOS_RECEIPT_PRINTER=EPSON TM-T20III Receipt`.
*Silent printing is new and untested on real hardware.* If a receipt does not come out or the page is cut wrongly, remove that line to go back to the dialog.

## 6. Update
Build a new installer with a higher `-Version` and run it on the server first, then the tills. Settings, data and backups stay; the tables are updated automatically. **Make a backup first** (Manager > Backups).

## 7. Uninstall
Settings > Apps > Wines & Spirits POS. It removes the program, the tasks and the firewall rule and **keeps the data**. To erase the shop completely, delete `C:\ProgramData\WinesPOS` yourself.

## Problems
| Symptom | Look at |
|---|---|
| Installer says the PC could not be set up | `C:\ProgramData\WinesPOS\logs\install.log`; fix; run the installer again (safe over an existing install) |
| POS window says it cannot reach the server | Is the server PC on? On it: `Get-ScheduledTask "WinesPOS Server"`; `logs\server.log`. On a till: is the address right, is the PC on the same network, is the firewall rule there |
| Anything feels wrong | Administrator prompt: `WinesPOSAdmin.exe manage selfcheck` |
| Forgot the manager password | Administrator prompt: `WinesPOSAdmin.exe manage changepassword <login>` |
