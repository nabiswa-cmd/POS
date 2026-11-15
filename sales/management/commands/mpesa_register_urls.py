from django.core.management.base import BaseCommand, CommandError

from sales import daraja


class Command(BaseCommand):
    help = "Register the till/paybill confirmation and validation URLs with Safaricom (run once, on the public server)."

    def handle(self, *args, **options):
        if not daraja.configured():
            raise CommandError("Fill in the MPESA_* settings in .env first (see .env.example).")
        try:
            result = daraja.register_c2b_urls()
        except daraja.DarajaError as exc:
            raise CommandError(str(exc))
        self.stdout.write(self.style.SUCCESS(f"Safaricom replied: {result}"))
