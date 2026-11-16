from django.core.management.base import BaseCommand, CommandError

from core import sync


class Command(BaseCommand):
    help = ("First install: copy the online server's newest backup into this PC so both start identical. "
            "Stop the shop server first. Refuses to run on a PC that already has data unless --force.")

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="Replace existing data on this PC (a safety backup is made).")
        parser.add_argument("--if-available", action="store_true",
                            help="Installer mode: if the online server has no backup yet or cannot be reached, "
                                 "say so and exit normally so a brand-new shop can still be set up.")

    def handle(self, *args, **opts):
        try:
            r = sync.seed_from_server(force=opts["force"])
        except sync.SyncError as exc:
            if opts["if_available"] and "already has data" not in str(exc):
                self.stdout.write(f"Not seeded: {exc}")
                return
            raise CommandError(str(exc))
        self.stdout.write(self.style.SUCCESS(f"This PC now starts from the online copy {r['restored']} (made {r['created_at']})."))
