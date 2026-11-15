from django.conf import settings
from django.db import models
from django.db.models import Q

from . import rules


class ExpenseCategory(models.Model):
    name = models.CharField(max_length=80, unique=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "expense categories"

    def __str__(self):
        return self.name


class Expense(models.Model):
    """Money the shop spent. Never edited and never deleted: a wrong entry is voided (with a reason) and re-entered.

    Only the void fields can change, and only once. Amounts here are what the business paid; profit reports
    (Phase 5B) subtract the non-voided ones from gross profit to give net profit.
    """

    CASH_TILL, CASH_OTHER, MPESA, BANK = rules.CASH_TILL, rules.CASH_OTHER, rules.MPESA, rules.BANK
    PAID_FROM = rules.PAID_FROM          # one list, shared with the pure rules
    VOID_FIELDS = {"voided_at", "voided_by", "void_reason"}

    date = models.DateField(db_index=True, help_text="The day the money was spent.")
    category = models.ForeignKey(ExpenseCategory, on_delete=models.PROTECT, related_name="expenses")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    description = models.CharField(max_length=200)
    payee = models.CharField(max_length=120, blank=True)
    reference = models.CharField(max_length=60, blank=True, help_text="Receipt, invoice or M-PESA code.")
    paid_from = models.CharField(max_length=10, choices=PAID_FROM)
    # Only for cash taken out of a till drawer: the (open) shift whose cash-up it reduces.
    shift = models.ForeignKey("tills.CashierShift", null=True, blank=True, on_delete=models.PROTECT,
                              related_name="expenses")

    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    recorded_at = models.DateTimeField(auto_now_add=True)
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                  related_name="+")
    void_reason = models.CharField(max_length=250, blank=True)

    class Meta:
        ordering = ["-date", "-id"]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="expense_amount_positive"),
            models.CheckConstraint(condition=~Q(paid_from="CASH_TILL") | Q(shift__isnull=False),
                                   name="till_cash_expense_has_shift"),
            models.CheckConstraint(condition=Q(voided_at__isnull=True) | ~Q(void_reason=""),
                                   name="voided_expense_has_reason"),
        ]

    def __str__(self):
        return f"{self.date} {self.category} KES {self.amount}"

    @property
    def is_void(self):
        return self.voided_at is not None

    def save(self, *args, **kwargs):
        if not self._state.adding:
            changed = kwargs.get("update_fields")
            if type(self).objects.filter(pk=self.pk, voided_at__isnull=False).exists():
                raise PermissionError("A voided expense cannot be changed.")
            if changed is None or not set(changed) <= self.VOID_FIELDS:
                raise PermissionError("An expense cannot be edited. Void it and enter it again.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("Expenses cannot be deleted. Void the entry instead.")
