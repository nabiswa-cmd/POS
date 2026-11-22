from django.core.management.base import BaseCommand, CommandError

from core import sync


class Command(BaseCommand):
    help = ("Copy a fresh snapshot of the online server's live POS database into this PC. "
            "Stop the shop server first. Refuses to replace local data unless --force.")

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="Replace existing data on this PC (a safety backup is made).")

    def handle(self, *args, **opts):
        try:
            r = sync.seed_from_server(force=opts["force"])
        except sync.SyncError as exc:
            raise CommandError(str(exc))
        self.stdout.write(self.style.SUCCESS(f"This PC now starts from the online copy {r['restored']} (made {r['created_at']})."))
