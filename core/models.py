from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class SingletonModel(models.Model):
    """One row per table (pk=1). Single shop, so settings are singletons."""

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("Settings cannot be deleted.")

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class ShopSettings(SingletonModel):
    COSTING_WAVG = "WAVG"
    COSTING_FIFO = "FIFO"
    POLICY_EVERY_LOGIN = "EVERY_LOGIN"
    POLICY_RESUME = "RESUME_OPEN_SHIFT"
    POLICY_CASHIER_LOGIN = "CASHIER_LOGIN"

    shop_name = models.CharField(max_length=120, default="My Wines & Spirits")
    address = models.CharField(max_length=250, blank=True)
    phone = models.CharField(max_length=60, blank=True)
    email = models.EmailField(blank=True)
    kra_pin = models.CharField("KRA PIN", max_length=20, blank=True)
    currency = models.CharField(max_length=3, default="KES")
    costing_method = models.CharField(
        max_length=4, default=COSTING_WAVG,
        choices=[(COSTING_WAVG, "Weighted average"), (COSTING_FIFO, "FIFO")],
    )
    costing_locked = models.BooleanField(
        default=False, help_text="Set automatically after the first sale; the method can then no longer change.",
    )
    receiving_requires_approval = models.BooleanField(
        default=False,
        help_text="If on, deliveries recorded by a receiving clerk wait for a supervisor/manager before stock changes.",
    )
    pos_open_policy = models.CharField(
        "POS opening policy", max_length=20, default=POLICY_EVERY_LOGIN,
        help_text="Cashier-only login skips supervisor authorization and records the cashier as the authorizer.",
        choices=[
            (POLICY_EVERY_LOGIN, "Supervisor authorizes every POS login"),
            (POLICY_RESUME, "Supervisor authorizes; the same cashier may log back in while their shift is open"),
            (POLICY_CASHIER_LOGIN, "Cashier may log in and open a shift without a supervisor"),
        ],
    )

    vat_registered = models.BooleanField(
        "VAT registered", default=False,
        help_text="If on, receipts show the VAT inside each price. Leave off until you are VAT registered with KRA.")
    vat_standard_rate = models.DecimalField(
        "Standard VAT rate (%)", max_digits=5, decimal_places=2, default=Decimal("16.00"),
        help_text="Applies to tax class B. Kenya's standard rate is 16%. Prices always include VAT.")
    pos_shortcuts = models.JSONField(
        default=dict, blank=True,
        help_text='Optional key changes for the till, e.g. {"print": "Alt+P"}. Actions: help, search, scan, quantity, '
                  'hold, retrieve, discount, payment, complete, print, shift, approval, customer.')

    class Meta:
        verbose_name_plural = "Shop settings"

    def clean(self):
        from sales import shortcuts
        try:
            shortcuts.merged(self.pos_shortcuts)
        except shortcuts.ShortcutError as exc:
            raise ValidationError({"pos_shortcuts": str(exc)})
        if not (Decimal("0") <= self.vat_standard_rate <= Decimal("50")):
            raise ValidationError({"vat_standard_rate": "Enter a rate between 0 and 50."})

    def __str__(self):
        return self.shop_name


class ReceiptSettings(SingletonModel):
    paper_width_mm = models.PositiveSmallIntegerField(default=80, choices=[(58, "58 mm"), (80, "80 mm")])
    printer_name = models.CharField(max_length=120, blank=True,
                                    help_text="Optional Windows printer name used for silent receipt printing.")
    header_text = models.CharField(max_length=200, blank=True)
    footer_text = models.CharField(max_length=200, default="Thank you for shopping with us!")
    logo_path = models.CharField(max_length=300, blank=True)
    show_cashier = models.BooleanField(default=True)
    show_authorized_by = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "Receipt settings"

    def __str__(self):
        return "Receipt settings"


class IntegrationConfiguration(SingletonModel):
    SOURCE_ENV = "ENV"
    SOURCE_DATABASE = "DATABASE"
    SOURCES = [(SOURCE_ENV, "Environment file"), (SOURCE_DATABASE, "Database")]

    mpesa_source = models.CharField(max_length=8, choices=SOURCES, default=SOURCE_ENV)
    etims_source = models.CharField(max_length=8, choices=SOURCES, default=SOURCE_ENV)
    barcode_lookup_source = models.CharField(max_length=8, choices=SOURCES, default=SOURCE_ENV)
    mpesa_config_ciphertext = models.TextField(blank=True, editable=False)
    etims_config_ciphertext = models.TextField(blank=True, editable=False)
    barcode_lookup_config_ciphertext = models.TextField(blank=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Payment, tax, and lookup integration settings"
        verbose_name_plural = "Payment, tax, and lookup integration settings"

    def mpesa_values(self):
        from core.integration_settings import decrypt_values
        return decrypt_values(self.mpesa_config_ciphertext, "M-PESA")

    def etims_values(self):
        from core.integration_settings import decrypt_values
        return decrypt_values(self.etims_config_ciphertext, "eTIMS")

    def set_mpesa_values(self, values):
        from core.integration_settings import encrypt_values
        self.mpesa_config_ciphertext = encrypt_values(values)

    def set_etims_values(self, values):
        from core.integration_settings import encrypt_values
        self.etims_config_ciphertext = encrypt_values(values)

    def barcode_lookup_values(self):
        from core.integration_settings import decrypt_values
        return decrypt_values(self.barcode_lookup_config_ciphertext, "Barcode Lookup")

    def set_barcode_lookup_values(self, values):
        from core.integration_settings import encrypt_values
        self.barcode_lookup_config_ciphertext = encrypt_values(values)

    def __str__(self):
        return "Payment, tax, and lookup integrations"


class RuntimeSetting(models.Model):
    """One administrator-changeable setting (see core/runtime.py). Empty value = use the .env / default value."""

    name = models.CharField(max_length=60, unique=True, editable=False)
    value = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Server setting"
        verbose_name_plural = "Server settings"

    def __str__(self):
        from core import runtime
        d = runtime.BY_NAME.get(self.name)
        return d.label if d else self.name


class SyncState(SingletonModel):
    """Shop PC: what the last sync cycle did."""

    last_attempt_at = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=300, blank=True)
    last_uploaded_name = models.CharField(max_length=80, blank=True)
    last_uploaded_bytes = models.BigIntegerField(default=0)
    last_mpesa_text = models.CharField(max_length=200, blank=True)

    class Meta:
        verbose_name_plural = "Sync state"


class SyncReceipt(models.Model):
    """Online server: one backup received from a shop."""

    received_at = models.DateTimeField(auto_now_add=True, db_index=True)
    name = models.CharField(max_length=80)
    size = models.BigIntegerField()
    sha256 = models.CharField(max_length=64)
    source_ip = models.GenericIPAddressField(null=True, blank=True)
    checked = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-received_at", "-id"]

    def __str__(self):
        return f"{self.name} ({self.received_at:%Y-%m-%d %H:%M})"


class AuditLog(models.Model):
    """Append-only record of sensitive actions. Never edited or deleted.

    The model blocks ORM updates/deletes; `manage.py harden_db` adds a PostgreSQL
    trigger so even raw SQL or QuerySet.update() cannot change history.
    """

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                              related_name="audit_actions")
    approver = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                 related_name="audit_approvals")
    action = models.CharField(max_length=60, db_index=True)
    object_type = models.CharField(max_length=60, blank=True)
    object_ref = models.CharField(max_length=100, blank=True)
    previous_value = models.JSONField(null=True, blank=True)
    new_value = models.JSONField(null=True, blank=True)
    reason = models.TextField(blank=True)
    till_number = models.PositiveSmallIntegerField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.action}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise PermissionError("Audit log entries cannot be modified.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("Audit log entries cannot be deleted.")
