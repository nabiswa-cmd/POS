from django.core.management.base import BaseCommand
from django.db import connection

from core import db_hardening


class Command(BaseCommand):
    help = "Install PostgreSQL triggers that make audit/history tables append-only."

    def handle(self, *args, **options):
        tables = db_hardening.apply(connection)
        if not tables:
            self.stdout.write(self.style.WARNING(
                "Skipped: append-only triggers need PostgreSQL (current database: %s)." % connection.vendor))
            return
        self.stdout.write(self.style.SUCCESS("Append-only protection active on: " + ", ".join(tables)))
