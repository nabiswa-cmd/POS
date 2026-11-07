from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from . import shortcuts


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
        max_length=20, default=POLICY_EVERY_LOGIN,
        choices=[
            (POLICY_EVERY_LOGIN, "Supervisor authorizes every POS login"),
            (POLICY_RESUME, "Supervisor authorizes; the same cashier may log back in while their shift is open"),
        ],
    )

    pos_shortcuts = models.JSONField(
        default=dict, blank=True,
        help_text='Optional keyboard shortcut changes for the sales screen, e.g. {"hold": "Alt+H", "discount": "Alt+D"}. '
                  "Leave {} for the defaults. Actions: " + ", ".join(shortcuts.DEFAULTS) + ".")

    class Meta:
        verbose_name_plural = "Shop settings"

    def clean(self):
        super().clean()
        _merged, errors = shortcuts.merge(self.pos_shortcuts)
        if errors:
            raise ValidationError({"pos_shortcuts": errors})

    def __str__(self):
        return self.shop_name


class ReceiptSettings(SingletonModel):
    paper_width_mm = models.PositiveSmallIntegerField(default=80, choices=[(58, "58 mm"), (80, "80 mm")])
    header_text = models.CharField(max_length=200, blank=True)
    footer_text = models.CharField(max_length=200, default="Thank you for shopping with us!")
    logo_path = models.CharField(max_length=300, blank=True)
    show_cashier = models.BooleanField(default=True)
    show_authorized_by = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "Receipt settings"

    def __str__(self):
        return "Receipt settings"


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
