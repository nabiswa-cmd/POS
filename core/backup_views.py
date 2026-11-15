"""Manager screen: /manage/backups/ - see the backups, back up now, check one. Restoring is deliberately NOT here:
it replaces all of today's data, so it is done from the command line with the server stopped (docs/BACKUP_AND_RESTORE.md)."""
from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect, render

from . import backup
from .decorators import perm_required
from .http import client_ip


@perm_required("settings.manage")
def backups(request):
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "backup_now":
                r = backup.create_backup(user=request.user, note="Made from the Backups screen")
                messages.success(request, f"Backup made: {r['path'].name} ({r['size'] / 1024 / 1024:.1f} MB).")
                if r["copy_error"]:
                    messages.warning(request, r["copy_error"])
            elif action == "verify":
                known = {row["name"]: row for row in backup.list_backups()}
                row = known.get(request.POST.get("name", ""))      # only files in the backup folder, never a typed path
                if row is None:
                    messages.error(request, "That backup was not found.")
                else:
                    v = backup.verify_backup(row["path"])
                    messages.success(request, f"{row['name']} is good: {v['checked']}.")
            else:
                messages.error(request, "Unknown action.")
        except backup.BackupError as exc:
            messages.error(request, str(exc))
        return redirect(request.path)
    state, hours, newest = backup.current_status()
    return render(request, "core/manage_backups.html", {
        "rows": backup.list_backups(), "state": state, "hours": hours, "keep": settings.BACKUP_KEEP,
        "backup_dir": settings.BACKUP_DIR, "copy_dir": (settings.BACKUP_COPY_DIR or "").strip(),
        "stale_hours": settings.BACKUP_STALE_HOURS})
