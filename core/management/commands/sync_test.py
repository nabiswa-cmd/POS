from django.core.management.base import BaseCommand, CommandError

from core import runtime, sync


class Command(BaseCommand):
    help = "Check that this computer can reach the online server with the saved address and token."

    def handle(self, *args, **opts):
        runtime.apply(force=True)
        try:
            sync.ping()
        except sync.SyncError as exc:
            raise CommandError(str(exc))
        self.stdout.write(self.style.SUCCESS("Connected: the online server accepted our token."))
