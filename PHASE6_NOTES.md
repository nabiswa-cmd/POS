# Phase 6: desktop deployment (last phase)

Spec items: Windows packaging, installer, desktop shortcuts, local database configuration, printer configuration, backup configuration, testing.

## Honest status
Built **and tested here (Linux, SQLite)**: backup / verify / restore, the first-run configuration tool, the self-check, the Backups screen, the single program entry point, the traceability of the 20 required tests.
Written but **never run (no Windows, no PowerShell, no Inno Setup, no PostgreSQL here)**: the PyInstaller spec, `build.ps1`, the Inno Setup installer `WinesPOS.iss` (wizard pages are Pascal script), `post_install.ps1`, `pre_uninstall.ps1`, `verify_install.ps1`, PostgreSQL provisioning, the PostgreSQL `pg_dump/pg_restore` path, silent receipt printing.
Expect to fix small things the first time you build on Windows. `docs/WINDOWS_INSTALL.md` has a first-install procedure and where the logs are; `verify_install.ps1` is the automated shortcut check (spec test 19) and only runs on Windows.

## What was added
| Item | Where |
|---|---|
| One program for every shortcut: `WinesPOS.exe pos / manager / receiving`, `WinesPOSAdmin.exe server / manage ... / configure / printers` | `desktop/main.py` |
| Installer (server PC or till PC wizard, shortcuts, tasks, firewall, uninstall that keeps data) | `packaging/WinesPOS.iss`, `post_install.ps1`, `pre_uninstall.ps1` |
| Windows build | `packaging/winespos.spec`, `build.ps1` |
| Settings and local database config: private settings file in ProgramData, generated secrets kept on re-run, PostgreSQL login + database created | `desktop/configure.py`, `desktop/paths.py`, `config/settings.py` (reads `WINESPOS_ENV_FILE`) |
| Backups: one zip (database + product images + checksum manifest), nightly task, second copy, retention, verify, restore with safety copy, refuses newer-version / wrong-engine backups | `core/backup.py`, `backup_rules.py`, commands `backup_db`, `verify_backup`, `restore_db`, `list_backups`; screen **Manager > Backups** |
| Health check in plain words | `manage selfcheck` (`core/selfcheck.py`) |
| Printers: list, test slip, optional silent receipt printing | `desktop/printers.py`, `desktop/launcher.py` |
| Server log file for the hidden server task | `desktop/run_server.py` |
| Docs | `docs/WINDOWS_INSTALL.md`, `BACKUP_AND_RESTORE.md`, `UAT_CHECKLIST.md`, `.env.example` |
| Tests | `core/test_backup.py` (21), `tests_pure/test_phase6_backup_rules.py`, `tests_pure/test_phase6_desktop.py` |

Spec test 20 (backups restore) is `core/test_backup.py::RoundTripTests`: backup, change data and images, restore, the old data and images are back and a safety copy exists; and a restored safety copy undoes the restore.

## Not done, by design
* **Fingerprint (Windows Hello)** is still off. It is optional in the spec ("never compulsory"), the signing side exists in `desktop/biometric.py`, and the Windows call can only be written and tested on a PC with a fingerprint reader. Passwords/PINs work as before.
* **No restore button** on a screen: restoring replaces all data, so it is command-line only, with the server stopped.
* **No code signing** of the installer: Windows SmartScreen will warn "unknown publisher" until you buy a certificate.
* PostgreSQL is not bundled; put its installer in `packaging/prereqs\` if you want a one-step install.

## Problems found in the code you gave me (not changed; they need your decision)
The full suite has **18 failing tests that were already failing before I started** (unchanged by Phases 5 and 6). They are not random; they come from two leftovers:
1. **`sales/test_phase4c.py` (10 tests): a second design of the offline M-PESA relay that was never wired in.** The code for it is half there (`sales/mpesa_relay.py`, `MpesaCallbackLog`, IP allow-list helpers) but the callback views do not use it, and `ALLOWED_IPS` / `TRUST_PROXY` are not in the settings. Your notes (`PHASE4_NOTES.md`) document a different, working design (`sales/relay.py`, passing `test_relay.py`). Decide which to keep and delete or finish the other. **One real consequence:** the live callback endpoints are protected only by the secret in the address; there is **no check that the caller is Safaricom**. The allow-list in the unfinished design would add that.
2. **`sales/tests_screen.py` (8 tests): most of them test an older sales page (`sell.html` + `pos.js`, routed by `sales/page_urls.py`) that is no longer served; the live page is `pos.html`.** `page_urls.py` is not included in `config/urls.py`, so `/manage/sales/` and `/manage/sales/<id>/` (the manager's sales search and sale detail pages) return 404. The "Tills & sales" menu item (`/manage/tills/`) is served by a different, working view. Three of the eight (`ApprovalHandshakeTests` x2, settings validation) fail on a response-shape / shortcut-format mismatch and may be stale tests or real bugs; I did not dig further. These tests also cover spec test 17 (shortcuts), so that one is by hand until settled.
Until then `build.ps1` stops at the test step: build with `-SkipTests`, or remove/repair those tests first.
