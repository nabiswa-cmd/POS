import os
import time
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from core import backup, backup_rules, windows_restore
from tills.models import CashierShift


class Command(BaseCommand):
    help = "Safely stop the Windows POS tasks, restore a staged server backup, and restart the shop server."

    def add_arguments(self, parser):
        parser.add_argument("backup_name", help="Validated backup file name in the server-downloads folder.")

    def handle(self, *args, **options):
        if os.name != "nt":
            raise CommandError("Automatic server restore is available only on the installed Windows shop server.")

        name = options["backup_name"]
        parsed = backup_rules.parse_name(name)
        if not parsed or parsed[1] or Path(name).name != name:
            raise CommandError("The server backup name is invalid.")
        state = windows_restore.read_status() or {}
        if state.get("state") != "scheduled" or state.get("backup") != name:
            raise CommandError("There is no matching, confirmed server restore queued.")
        if not (Path(windows_restore.status_path().parent) / windows_restore.LOCK_NAME).is_file():
            raise CommandError("The restore lock is missing; refusing to change the database.")

        server_stopped = False
        printer_stopped = False
        error = ""
        safety_copy = ""
        try:
            time.sleep(3)
            path = Path(windows_restore.status_path().parent) / "server-downloads" / name
            if not path.is_file():
                raise windows_restore.RestoreOperationError("The staged server backup is missing.")
            if CashierShift.objects.filter(closed_at__isnull=True).exists():
                raise windows_restore.RestoreOperationError("A cashier shift is still open. Close all tills and try again.")

            summary = backup.verify_backup(path)
            backup_rules.check_manifest(
                summary["manifest"], vendor=backup_rules.engine_key(connection.vendor),
                code_migrations=backup.code_migrations())
            tasks = windows_restore.task_states()
            if tasks.get(windows_restore.SERVER_TASK) != "Running":
                raise windows_restore.RestoreOperationError("The WinesPOS Server task is not running.")
            if tasks.get(windows_restore.BACKUP_TASK) == "Running":
                raise windows_restore.RestoreOperationError("A local backup is running; retry when it finishes.")

            windows_restore.set_state("stopping", message="Stopping the printer and shop server.")
            time.sleep(3)
            if tasks.get(windows_restore.PRINTER_TASK) == "Running":
                printer_stopped = True
                windows_restore.stop_task(windows_restore.PRINTER_TASK)
            server_stopped = True
            windows_restore.stop_task(windows_restore.SERVER_TASK)
            windows_restore.wait_for_server(False)

            windows_restore.set_state("restoring", message="Replacing the local database and product images.")
            result = backup.restore_backup(path)
            safety_copy = str(result["safety_copy"])
            windows_restore.set_state("starting", safety_copy=safety_copy,
                                      message="Starting the shop server again.")
        except Exception as exc:
            error = str(exc) or exc.__class__.__name__
        finally:
            restart_server = server_stopped
            if error and restart_server:
                try:
                    connection.close()
                    connection.ensure_connection()
                except Exception as exc:
                    restart_server = False
                    error = f"{error} The active database is not accessible; the server remains stopped: {exc}".strip()
            if restart_server:
                try:
                    if windows_restore.task_states().get(windows_restore.SERVER_TASK) != "Running":
                        windows_restore.start_task(windows_restore.SERVER_TASK)
                    windows_restore.wait_for_server(True)
                except windows_restore.RestoreOperationError as exc:
                    error = f"{error} Server restart failed: {exc}".strip()
            if printer_stopped and restart_server:
                try:
                    if windows_restore.task_states().get(windows_restore.PRINTER_TASK) != "Running":
                        windows_restore.start_task(windows_restore.PRINTER_TASK)
                except windows_restore.RestoreOperationError as exc:
                    error = f"{error} Printer task restart failed: {exc}".strip()

            try:
                if error:
                    windows_restore.set_state("failed", error=error[:500], safety_copy=safety_copy,
                                              finished_at=time.time(), message="Restore did not complete successfully.")
                else:
                    windows_restore.set_state("succeeded", error="", safety_copy=safety_copy,
                                              finished_at=time.time(),
                                              message="Server data restored and the shop server restarted.")
            finally:
                windows_restore.release_lock()

        if error:
            raise CommandError(error)
        self.stdout.write(self.style.SUCCESS(f"Restored {name}; safety backup: {safety_copy}"))

