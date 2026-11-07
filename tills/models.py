from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q


class Till(models.Model):
    number = models.PositiveSmallIntegerField(unique=True)
    name = models.CharField(max_length=40)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["number"]

    def __str__(self):
        return self.name


class PosAuthorization(models.Model):
    """A supervisor/manager authorized a POS session on a till; a cashier then logs in under it.

    Records both people: `approval.approver` authorized, `cashier` sold. The supervisor is
    never logged in as the cashier.
    """

    till = models.ForeignKey(Till, on_delete=models.PROTECT, related_name="authorizations")
    approval = models.OneToOneField("accounts.Approval", on_delete=models.PROTECT, related_name="pos_authorization")
    cashier = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                related_name="pos_sessions")
    created_at = models.DateTimeField(auto_now_add=True)
    cashier_logged_in_at = models.DateTimeField(null=True, blank=True)

    @property
    def authorized_by(self):
        return self.approval.approver


class CashierShift(models.Model):
    """A cashier's shift on one till. Full cash-up and closing arrive in Phase 5."""

    till = models.ForeignKey(Till, on_delete=models.PROTECT, related_name="shifts")
    cashier = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="shifts")
    authorized_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                      related_name="shifts_authorized")
    opened_at = models.DateTimeField(auto_now_add=True)
    opening_float = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-opened_at"]
        constraints = [
            models.UniqueConstraint(fields=["till"], condition=Q(closed_at__isnull=True),
                                    name="one_open_shift_per_till"),
            models.CheckConstraint(condition=Q(opening_float__gte=0), name="opening_float_not_negative"),
        ]

    def __str__(self):
        return f"{self.till} / {self.cashier} / {self.opened_at:%Y-%m-%d %H:%M}"
