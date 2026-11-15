from django.core.management.base import BaseCommand
from django.conf import settings

from core import backup


class Command(BaseCommand):
    help = "List the backups in the backup folder, newest first."

    def handle(self, *args, **options):
        rows = backup.list_backups()
        self.stdout.write(f"Backup folder: {settings.BACKUP_DIR}")
        for r in rows:
            tag = "  (safety copy before a restore)" if r["prerestore"] else ""
            self.stdout.write(f"{r['name']}  {r['size'] / 1024 / 1024:8.1f} MB{tag}")
        state, hours, _ = backup.current_status()
        self.stdout.write({"none": "No backup has been made yet.", "ok": f"Newest backup is {hours:.1f} hours old.",
                           "stale": f"WARNING: newest backup is {hours:.1f} hours old."}[state])
