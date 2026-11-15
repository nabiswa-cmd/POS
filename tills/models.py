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
    cancelled_at = models.DateTimeField(null=True, blank=True)

    @property
    def authorized_by(self):
        return self.approval.approver


class CashierShift(models.Model):
    """A cashier's shift on one till. Closing it records a CashUp (the drawer count)."""

    till = models.ForeignKey(Till, on_delete=models.PROTECT, related_name="shifts")
    cashier = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="shifts")
    authorized_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                      related_name="shifts_authorized")
    opened_at = models.DateTimeField(auto_now_add=True)
    opening_float = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    locked_at = models.DateTimeField(null=True, blank=True)
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


class CashUp(models.Model):
    """The count of a till drawer when a shift is closed. A record, never edited.

    Every figure is copied from the books at the moment of closing, so the slip never changes later.
    Reopening a shift (needs approval) does not erase this row: it is marked superseded, and the next close
    writes a new one. `counts` holds the pieces counted per note or coin.
    """

    SUPERSEDE_FIELDS = {"superseded_at", "superseded_by", "supersede_reason"}

    shift = models.ForeignKey(CashierShift, on_delete=models.PROTECT, related_name="cashups")
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+",
                                  help_text="Usually the cashier; a supervisor if they counted for the cashier.")
    closed_at = models.DateTimeField(auto_now_add=True)

    opening_float = models.DecimalField(max_digits=12, decimal_places=2)
    cash_sales = models.DecimalField(max_digits=14, decimal_places=2)
    cash_refunds = models.DecimalField(max_digits=14, decimal_places=2, help_text="Cash handed back to customers from this drawer.")
    cash_paid_out = models.DecimalField(max_digits=14, decimal_places=2, help_text="Expenses paid in cash from this drawer.")
    expected_cash = models.DecimalField(max_digits=14, decimal_places=2)
    counted_cash = models.DecimalField(max_digits=14, decimal_places=2)
    variance = models.DecimalField(max_digits=14, decimal_places=2, help_text="Counted minus expected. Negative = short.")
    counts = models.JSONField(default=dict, help_text="Pieces counted per note or coin.")
    reason = models.CharField(max_length=250, blank=True)

    # Not cash: shown beside the count so nobody mixes them up, and never part of expected cash.
    mpesa_sales = models.DecimalField(max_digits=14, decimal_places=2)
    mpesa_refunds_paid = models.DecimalField(max_digits=14, decimal_places=2)
    other_sales = models.DecimalField(max_digits=14, decimal_places=2, help_text="Card and bank transfer.")
    sales_count = models.PositiveIntegerField()
    sales_total = models.DecimalField(max_digits=14, decimal_places=2)

    superseded_at = models.DateTimeField(null=True, blank=True)
    superseded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                      related_name="+")
    supersede_reason = models.CharField(max_length=250, blank=True)

    class Meta:
        ordering = ["-closed_at", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["shift"], condition=Q(superseded_at__isnull=True),
                                    name="one_active_cashup_per_shift"),
            models.CheckConstraint(condition=Q(counted_cash__gte=0), name="cashup_counted_not_negative"),
        ]

    def __str__(self):
        return f"Cash-up {self.shift} ({self.variance:+})"

    @property
    def is_active(self):
        return self.superseded_at is None

    def save(self, *args, **kwargs):
        if not self._state.adding:
            changed = kwargs.get("update_fields")
            if type(self).objects.filter(pk=self.pk, superseded_at__isnull=False).exists():
                raise PermissionError("A superseded cash-up cannot be changed.")
            if changed is None or not set(changed) <= self.SUPERSEDE_FIELDS:
                raise PermissionError("A cash-up cannot be edited.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("A cash-up cannot be deleted.")
