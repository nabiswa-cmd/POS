import os
from getpass import getpass

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth.password_validation import validate_password
from django.db import transaction

from accounts.models import ApprovalPolicy, Role, RolePermission
from accounts.permissions import DEFAULT_ROLES, RESTRICTED_ACTIONS
from core.models import ReceiptSettings, ShopSettings
from accounting.services import ensure_default_categories
from tills.models import Till


class Command(BaseCommand):
    help = "First-time setup: default roles, approval policies, tills, shop settings and the manager account."

    def add_arguments(self, parser):
        parser.add_argument("--shop-name", default=None)
        parser.add_argument("--manager-username", default=None, help="Create this manager account if missing.")
        parser.add_argument("--manager-name", default="", help="Manager's full name.")
        parser.add_argument("--tills", type=int, default=None, help="Number of tills (1 or 2).")

    @transaction.atomic
    def handle(self, *args, **opts):
        shop = ShopSettings.load()
        if opts["shop_name"]:
            shop.shop_name = opts["shop_name"]
            shop.save()
        ReceiptSettings.load()

        for name, spec in DEFAULT_ROLES.items():
            role, _ = Role.objects.get_or_create(
                name=name, defaults={"description": spec["description"], "is_system": True})
            if role.is_system:  # never overwrite a role the manager has customised after creation
                if not role.grants.exists():
                    RolePermission.objects.bulk_create(
                        [RolePermission(role=role, code=code) for code in spec["permissions"]])
        for action in RESTRICTED_ACTIONS:
            ApprovalPolicy.objects.get_or_create(action=action, defaults={"required": True})

        ensure_default_categories()

        count = opts["tills"] or settings.MAX_TILLS
        if not 1 <= count <= settings.MAX_TILLS:
            raise CommandError(f"Tills must be between 1 and {settings.MAX_TILLS}.")
        for number in range(1, count + 1):
            Till.objects.get_or_create(number=number, defaults={"name": f"Till {number}"})

        username = opts["manager_username"]
        if username:
            User = get_user_model()
            if User.objects.filter(username__iexact=username).exists():
                self.stdout.write(f"Manager '{username}' already exists; left unchanged.")
            else:
                password = os.environ.get("WINESPOS_MANAGER_PASSWORD") or self._ask_password()
                try:
                    validate_password(password)
                except ValidationError as exc:
                    raise CommandError("; ".join(exc.messages))
                manager = User(username=username, first_name=opts["manager_name"], is_manager=True)
                manager.set_password(password)
                manager.save()
                self.stdout.write(self.style.SUCCESS(f"Manager '{username}' created."))

        self.stdout.write(self.style.SUCCESS("Shop setup complete."))

    def _ask_password(self):
        first = getpass("Manager password: ")
        if first != getpass("Repeat password: "):
            raise CommandError("Passwords do not match.")
        return first
