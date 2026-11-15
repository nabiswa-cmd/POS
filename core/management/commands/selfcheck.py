import sys

from django.core.management.base import BaseCommand

from core import selfcheck


class Command(BaseCommand):
    help = "Plain-words health check of this installation. Exit code 1 if anything FAILs."

    def handle(self, *args, **options):
        lines = selfcheck.run_checks()
        for level, text in lines:
            style = {"OK": self.style.SUCCESS, "WARN": self.style.WARNING, "FAIL": self.style.ERROR}[level]
            self.stdout.write(style(f"[{level:4}] ") + text)
        if any(level == selfcheck.FAIL for level, _ in lines):
            self.stdout.write(self.style.ERROR("Not ready to trade: fix the FAIL lines."))
            sys.exit(1)
        self.stdout.write(self.style.SUCCESS("Ready."))
