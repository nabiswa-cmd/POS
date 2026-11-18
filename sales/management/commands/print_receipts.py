import time

from django.core.management.base import BaseCommand, CommandError

from sales import printing
from sales.models import ReceiptPrintJob


class Command(BaseCommand):
    help = "Print queued receipts on the server computer's configured receipt printer."

    def add_arguments(self, parser):
        parser.add_argument("--loop", action="store_true", help="Keep checking for new receipts.")
        parser.add_argument("--interval", type=float, default=2, help="Seconds between checks when looping.")

    def handle(self, *args, **options):
        if options["interval"] <= 0:
            raise CommandError("--interval must be greater than zero.")

        from PySide6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication(["WinesPOS receipt printer"])
        while True:
            job_id = printing.claim_receipt_print_job()
            if job_id is None:
                if not options["loop"]:
                    return
                time.sleep(options["interval"])
                continue

            printer_name = printing.configured_printer()
            job = ReceiptPrintJob.objects.get(pk=job_id)
            try:
                if not printer_name:
                    raise ValueError("No receipt printer is configured in Receipt settings.")
                printing.print_receipt_payload(job.payload, printer_name)
            except (OSError, RuntimeError, ValueError) as exc:
                message = str(exc) or exc.__class__.__name__
                printing.finish_receipt_print_job(job_id, error=message)
                self.stderr.write(self.style.ERROR(f"Receipt job {job_id} failed: {message}"))
            else:
                printing.finish_receipt_print_job(job_id)
                self.stdout.write(self.style.SUCCESS(f"Receipt job {job_id} printed on '{printer_name}'."))

            if not options["loop"]:
                return
