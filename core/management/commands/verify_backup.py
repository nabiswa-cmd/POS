from django.core.management.base import BaseCommand, CommandError

from core import backup


class Command(BaseCommand):
    help = "Check that a backup zip is complete and undamaged (does not change anything)."

    def add_arguments(self, parser):
        parser.add_argument("path", help="The backup zip to check.")

    def handle(self, *args, **options):
        try:
            r = backup.verify_backup(options["path"])
        except backup.BackupError as exc:
            raise CommandError(str(exc))
        self.stdout.write(self.style.SUCCESS(
            f"OK: made {r['created_at']}, {r['engine']}, {r['media_files']} product image(s); {r['checked']}."))
