from datetime import datetime

from django.core.management.base import BaseCommand
from django.utils import timezone

from sales import reconcile


class Command(BaseCommand):
    help = "Print the M-PESA reconciliation for a day (default today) and anything that needs a person."

    def add_arguments(self, parser):
        parser.add_argument("--date", help="YYYY-MM-DD (default: today)")

    def handle(self, *args, **opts):
        day = datetime.strptime(opts["date"], "%Y-%m-%d").date() if opts.get("date") else timezone.localdate()
        r = reconcile.day_report(day)
        out = self.stdout.write
        out(f"M-PESA reconciliation for {day:%d %b %Y}")
        out(f"  Received:        {r['received']['count']} payments, KES {r['received']['total']:,.2f}")
        out(f"  Used on sales:   KES {r['used_today']:,.2f}")
        out(f"  Waiting:         {r['waiting']['count']} payments, KES {r['waiting']['total']:,.2f} "
            f"({r['old_waiting']['count']} older than {r['old_hours']}h)")
        out(f"  Set aside:       {r['ignored']['count']} payments, KES {r['ignored']['total']:,.2f}")
        out(f"  Checks run: {r['checks']}. " + ("All passed." if not r["problems"] else f"{len(r['problems'])} to look at:"))
        for p in r["problems"]:
            out("   - " + p)
