from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q

# A sale in these states is history: it can never be edited or deleted. The only fields that may
# still change on a completed sale are the receipt-print counters (core.db_hardening enforces the
# same rule in PostgreSQL, so raw SQL and QuerySet.update() cannot get around the model guard).
FINAL_STATES = ("COMPLETED", "VOIDED")
PRINT_FIELDS = {"reprint_count", "printed_at"}


class Sale(models.Model):
    OPEN, HELD, COMPLETED, VOIDED = "OPEN", "HELD", "COMPLETED", "VOIDED"
    STATUS = [(OPEN, "In progress"), (HELD, "On hold"), (COMPLETED, "Completed"), (VOIDED, "Voided before completion")]

    # Assigned at completion, inside the same database transaction, so completed sales are gapless.
    number = models.CharField(max_length=24, unique=True, null=True, blank=True, editable=False)
    status = models.CharField(max_length=10, choices=STATUS, default=OPEN, db_index=True)
    till = models.ForeignKey("tills.Till", on_delete=models.PROTECT, related_name="sales")
    shift = models.ForeignKey("tills.CashierShift", on_delete=models.PROTECT, related_name="sales")
    cashier = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="sales")
    # The supervisor/manager who authorized the POS session. NOT the person who made the sale.
    authorized_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                      related_name="sales_authorized")
    customer_name = models.CharField(max_length=120, blank=True)
    hold_label = models.CharField(max_length=60, blank=True)

    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    discount_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    created_at = models.DateTimeField(auto_now_add=True)
    held_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True, db_index=True)
    printed_at = models.DateTimeField(null=True, blank=True)
    reprint_count = models.PositiveIntegerField(default=0)

    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                  related_name="+")
    void_approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                         on_delete=models.PROTECT, related_name="+")
    void_reason = models.CharField(max_length=250, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            # One cart in progress per shift. Held carts do not count, so a cashier can hold and start again.
            models.UniqueConstraint(fields=["shift"], condition=Q(status="OPEN"), name="one_open_sale_per_shift"),
            models.CheckConstraint(condition=~Q(status="COMPLETED") | Q(number__isnull=False),
                                   name="completed_sale_has_number"),
            models.CheckConstraint(condition=Q(total__gte=0), name="sale_total_not_negative"),
        ]

    def __str__(self):
        return self.number or f"Sale #{self.pk} ({self.get_status_display()})"

    @classmethod
    def from_db(cls, db, field_names, values):
        obj = super().from_db(db, field_names, values)
        obj._db_status = obj.__dict__.get("status")
        return obj

    def save(self, *args, **kwargs):
        if not self._state.adding and getattr(self, "_db_status", None) in FINAL_STATES:
            changed = kwargs.get("update_fields")
            if changed is None or not set(changed) <= PRINT_FIELDS:
                raise PermissionError("A completed or voided sale cannot be modified.")
        super().save(*args, **kwargs)
        self._db_status = self.status

    def delete(self, *args, **kwargs):
        if self.status in FINAL_STATES or getattr(self, "_db_status", None) in FINAL_STATES:
            raise PermissionError("A completed or voided sale cannot be deleted.")
        super().delete(*args, **kwargs)


class SaleItem(models.Model):
    """One line of a sale. Everything needed for history is copied here at the time of sale."""

    sale = models.ForeignKey(Sale, on_delete=models.PROTECT, related_name="items")
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT, related_name="sale_items")
    packaging = models.ForeignKey("catalog.Packaging", null=True, blank=True, on_delete=models.PROTECT,
                                  related_name="+")
    product_name = models.CharField(max_length=160)
    pack_name = models.CharField(max_length=40, blank=True)
    units_per_sale_unit = models.PositiveIntegerField(default=1)

    quantity = models.PositiveIntegerField()
    list_price = models.DecimalField(max_digits=12, decimal_places=2, help_text="Catalogue price when added.")
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, help_text="Price actually charged per unit.")
    discount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"),
                                   help_text="Total discount on this line.")
    line_total = models.DecimalField(max_digits=12, decimal_places=2)

    # Cost basis of the stock sold, fixed when the sale completes (profit never uses today's cost).
    cost_total = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)

    price_approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                          on_delete=models.PROTECT, related_name="+")
    discount_approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                             on_delete=models.PROTECT, related_name="+")
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        indexes = [models.Index(fields=["product", "-added_at"])]
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gte=1), name="sale_item_quantity_positive"),
            models.CheckConstraint(condition=Q(discount__gte=0), name="sale_item_discount_not_negative"),
            models.CheckConstraint(condition=Q(line_total__gte=0), name="sale_item_total_not_negative"),
        ]

    def __str__(self):
        return f"{self.quantity} x {self.product_name}"

    @property
    def base_qty(self):
        return self.quantity * self.units_per_sale_unit

    @property
    def gross(self):
        return (self.unit_price * self.quantity).quantize(Decimal("0.01"))

    def _sale_is_final(self):
        return Sale.objects.filter(pk=self.sale_id, status__in=FINAL_STATES).exists()

    def save(self, *args, **kwargs):
        if self._sale_is_final():
            raise PermissionError("Lines of a completed or voided sale cannot be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self._sale_is_final():
            raise PermissionError("Lines of a completed or voided sale cannot be deleted.")
        super().delete(*args, **kwargs)


class Payment(models.Model):
    """What the customer paid. Phase 3 records cash only; Phase 4 adds M-PESA, card, bank and split payments."""

    CASH = "CASH"
    METHODS = [(CASH, "Cash")]
    CONFIRMED = "CONFIRMED"
    STATUSES = [(CONFIRMED, "Confirmed")]

    sale = models.ForeignKey(Sale, on_delete=models.PROTECT, related_name="payments")
    method = models.CharField(max_length=12, choices=METHODS, default=CASH)
    status = models.CharField(max_length=12, choices=STATUSES, default=CONFIRMED)
    amount = models.DecimalField(max_digits=12, decimal_places=2, help_text="Amount counted against the sale.")
    tendered = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True,
                                   help_text="Cash handed over by the customer.")
    change_given = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    reference = models.CharField(max_length=60, blank=True)
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        constraints = [models.CheckConstraint(condition=Q(amount__gt=0), name="payment_amount_positive")]

    def __str__(self):
        return f"{self.method} {self.amount} for sale {self.sale_id}"
