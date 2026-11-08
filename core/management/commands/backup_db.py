from django.core.management.base import BaseCommand, CommandError

from core import backup


class Command(BaseCommand):
    help = "Back up the database and product images into one zip (BACKUP_DIR, plus BACKUP_COPY_DIR if set)."

    def handle(self, *args, **options):
        try:
            r = backup.create_backup(note="Scheduled or command-line backup")
        except backup.BackupError as exc:
            raise CommandError(str(exc))
        self.stdout.write(self.style.SUCCESS(f"Backup written: {r['path']} ({r['size'] / 1024 / 1024:.1f} MB)"))
        if r["copy_path"]:
            self.stdout.write(f"Second copy: {r['copy_path']}")
        if r["copy_error"]:
            self.stderr.write(self.style.WARNING(r["copy_error"]))
        if r["pruned"]:
            self.stdout.write("Old backups removed: " + ", ".join(r["pruned"]))
