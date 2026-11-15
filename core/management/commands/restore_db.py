from django.core.management.base import BaseCommand, CommandError

from core import backup


class Command(BaseCommand):
    help = ("Restore a backup zip over the CURRENT database and product images. Stop the POS server first. "
            "A safety backup of the current state is written before anything is replaced.")

    def add_arguments(self, parser):
        parser.add_argument("path", help="The backup zip to restore.")
        parser.add_argument("--yes", action="store_true", help="Confirm: yes, replace today's data with this backup.")

    def handle(self, *args, **options):
        if not options["yes"]:
            raise CommandError("This REPLACES all current data with the backup. Stop the server, then run again with --yes.")
        try:
            r = backup.restore_backup(options["path"])
        except backup.BackupError as exc:
            raise CommandError(str(exc))
        self.stdout.write(self.style.SUCCESS(f"Restored {r['restored']} (made {r['created_at']})."))
        self.stdout.write(f"What was there before is saved as: {r['safety_copy']}")
        if r["old_media"]:
            self.stdout.write(f"Previous product images moved to: {r['old_media']}")
        self.stdout.write("Start the server again, then check the latest sale and stock figures.")
