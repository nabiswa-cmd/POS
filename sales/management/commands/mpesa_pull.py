import time

from django.core.management.base import BaseCommand, CommandError

from sales import relay, relay_rules


class Command(BaseCommand):
    help = ("Shop PC: fetch M-PESA payments the online server recorded (for example while this shop was offline). "
            "Safe to run as often as you like. Use --loop 60 to keep it running.")

    def add_arguments(self, parser):
        parser.add_argument("--hours", type=int, default=relay_rules.DEFAULT_HOURS, help="how far back to look (default 72)")
        parser.add_argument("--loop", type=int, default=0, help="repeat every N seconds until stopped (Ctrl+C)")

    def handle(self, *args, **opts):
        hours = max(1, min(opts["hours"], relay_rules.MAX_HOURS))
        while True:
            try:
                t = relay.pull(hours=hours)
                self.stdout.write(f"new {t['new']}, already had {t['known']}, conflicts {t['conflict']}, unreadable {t['bad']}")
            except relay.RelayError as exc:
                if not opts["loop"]:
                    raise CommandError(str(exc))
                self.stderr.write(f"Relay problem (will try again): {exc}")
            if not opts["loop"]:
                return
            try:
                time.sleep(max(10, opts["loop"]))
            except KeyboardInterrupt:
                return
