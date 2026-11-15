import time

from django.core.management.base import BaseCommand

from core import runtime, sync


class Command(BaseCommand):
    help = ("Shop PC: make a backup, send it to the online server and fetch M-PESA payments. "
            "Use --loop 300 to keep doing it every 5 minutes (or let the server do it by itself). Safe to run any time.")

    def add_arguments(self, parser):
        parser.add_argument("--loop", type=int, default=0, help="repeat every N seconds until stopped")
        parser.add_argument("--force", action="store_true", help="make and send a new backup even if one was just sent")

    def handle(self, *args, **opts):
        while True:
            runtime.apply(force=True)
            ok, text = sync.run_cycle(force=opts["force"])
            (self.stdout.write if ok else self.stderr.write)(text or "Done.")
            if not opts["loop"]:
                return
            try:
                time.sleep(max(30, opts["loop"]))
            except KeyboardInterrupt:
                return
