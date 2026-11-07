from django.core.management.base import BaseCommand

from sales import etims


class Command(BaseCommand):
    help = "Send sales that eTIMS has not signed yet (run it now and then, or after the internet returns)."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=50)

    def handle(self, *args, **opts):
        if not etims.enabled():
            self.stdout.write("eTIMS is off (ETIMS_MODE=off). Nothing to do.")
            return
        problems = etims.config_problems()
        if problems:
            self.stdout.write("eTIMS is not ready: " + "; ".join(problems))
            return
        signed = etims.submit_pending(opts["limit"])
        self.stdout.write(f"Signed {signed}. Still waiting or rejected: {sum(v for k, v in etims.counts().items() if k != 'SUBMITTED')}.")
