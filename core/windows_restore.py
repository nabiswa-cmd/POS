"""Schedule a verified server-backup restore outside the live Windows server task."""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from django.conf import settings

from . import backup_rules

LOCK_NAME = "server-restore.lock"
STATUS_NAME = "server-restore-status.json"
LOG_NAME = "server-restore.log"
SERVER_TASK = "WinesPOS Server"
PRINTER_TASK = "WinesPOS Printer"
BACKUP_TASK = "WinesPOS Backup"


class RestoreOperationError(Exception):
    """A safe message for a refused or failed automatic restore."""


def _folder():
    folder = Path(settings.BACKUP_DIR)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def status_path():
    return _folder() / STATUS_NAME


def read_status():
    path = status_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RestoreOperationError(f"Cannot read the server-restore status file: {exc}") from exc


def write_status(**values):
    path = status_path()
    current = read_status() or {}
    current.update(values)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(current, indent=2), encoding="utf-8")
    os.replace(temporary, path)
    return current


def _powershell(script, *, timeout=30):
    if os.name != "nt":
        raise RestoreOperationError("Automatic server restore is available only on the Windows shop server.")
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RestoreOperationError(f"Could not control the WinesPOS scheduled tasks: {exc}") from exc
    if result.returncode:
        detail = (result.stderr or result.stdout or "").strip()[:300]
        raise RestoreOperationError(detail or "Windows could not control the WinesPOS scheduled tasks.")
    return result.stdout.strip()


def task_states():
    script = (
        "$names = @('WinesPOS Server', 'WinesPOS Printer', 'WinesPOS Backup'); "
        "foreach ($name in $names) { "
        "$task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue; "
        "if ($task) { Write-Output ($name + '=' + $task.State) } "
        "else { Write-Output ($name + '=Missing') } }"
    )
    states = {}
    for row in _powershell(script).splitlines():
        name, separator, state = row.partition("=")
        if separator:
            states[name.strip()] = state.strip()
    return states


def check_ready():
    if os.name != "nt":
        raise RestoreOperationError("Automatic restore requires the installed Windows shop server.")
    states = task_states()
    if states.get(SERVER_TASK) != "Running":
        raise RestoreOperationError("The WinesPOS Server scheduled task is not running; automatic restore cannot safely restart it.")
    if states.get(BACKUP_TASK) == "Running":
        raise RestoreOperationError("A local backup is running. Wait for it to finish, then try the restore again.")
    current = read_status()
    if current and current.get("state") in {"scheduled", "stopping", "restoring", "starting"}:
        raise RestoreOperationError("A server restore is already in progress.")
    lock = _folder() / LOCK_NAME
    if lock.exists():
        raise RestoreOperationError("A restore lock remains from an earlier attempt. Check server-restore.log before trying again.")
    return states


def _management_command(backup_name):
    if getattr(sys, "frozen", False):
        return [sys.executable, "manage", "restore_server_backup", backup_name]
    return [sys.executable, str(Path(settings.BASE_DIR) / "manage.py"), "restore_server_backup", backup_name]


def launch_restore(backup_name):
    """Start an independent helper process so it survives stopping the web-server scheduled task."""
    parsed = backup_rules.parse_name(backup_name)
    if not parsed or parsed[1] or Path(backup_name).name != backup_name:
        raise RestoreOperationError("The downloaded file name is not a valid server backup.")
    states = check_ready()
    folder = _folder()
    lock = folder / LOCK_NAME
    try:
        with open(lock, "x", encoding="utf-8") as stream:
            stream.write(json.dumps({"backup": backup_name, "started_at": time.time()}))
    except FileExistsError as exc:
        raise RestoreOperationError("A server restore is already queued or running.") from exc

    try:
        write_status(state="scheduled", backup=backup_name, started_at=time.time(), error="",
                     printer_was_running=states.get(PRINTER_TASK) == "Running", safety_copy="")
        with open(folder / LOG_NAME, "ab") as log:
            subprocess.Popen(
                _management_command(backup_name),
                cwd=str(settings.BASE_DIR),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                close_fds=True,
                creationflags=(subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                               | subprocess.CREATE_NO_WINDOW),
            )
    except (OSError, subprocess.SubprocessError) as exc:
        write_status(state="failed", error=f"Could not start the restore helper: {exc}", finished_at=time.time())
        lock.unlink(missing_ok=True)
        raise RestoreOperationError(f"Could not start the restore helper: {exc}") from exc
    return read_status()


def set_state(state, **values):
    values["state"] = state
    values["updated_at"] = time.time()
    return write_status(**values)


def _run_task_action(task, action):
    _powershell(f"{action}-ScheduledTask -TaskName '{task}' -ErrorAction Stop")


def _wait_task(task, running, seconds=45):
    expected = "Running" if running else "Ready"
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        state = task_states().get(task)
        if state == expected or (not running and state in {"Disabled", "Queued"}):
            return
        time.sleep(1)
    raise RestoreOperationError(f"The '{task}' task did not become {expected.lower()} in time.")


def stop_task(task):
    _run_task_action(task, "Stop")
    _wait_task(task, False)


def start_task(task):
    _run_task_action(task, "Start")
    _wait_task(task, True)


def _server_health():
    port = int(os.environ.get("WINESPOS_PORT", "8000"))
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health/", timeout=2) as response:
            return response.status == 200
    except urllib.error.HTTPError:
        return True
    except (urllib.error.URLError, OSError):
        return False


def wait_for_server(running, seconds=90):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if _server_health() == running:
            return
        time.sleep(1)
    state = "start" if running else "stop"
    raise RestoreOperationError(f"The WinesPOS Server did not {state} in time.")


def release_lock():
    (Path(settings.BACKUP_DIR) / LOCK_NAME).unlink(missing_ok=True)
