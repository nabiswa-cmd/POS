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
    customer_pin = models.CharField("Customer KRA PIN", max_length=11, blank=True)
    hold_label = models.CharField(max_length=60, blank=True)

    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    discount_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    # Fixed at checkout. vat_applied records whether the shop was VAT registered when this sale was made,
    # so changing the setting later never rewrites how an old receipt reads. tax_total is VAT inside total.
    vat_applied = models.BooleanField(default=False)
    tax_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

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

    # Tax snapshot, fixed at checkout (all zero when the shop is not VAT registered). Prices include VAT.
    tax_class = models.CharField(max_length=1, default="B")
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0.00"))
    taxable_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

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

    CASH, MPESA, CARD, BANK = "CASH", "MPESA", "CARD", "BANK"
    METHODS = [(CASH, "Cash"), (MPESA, "M-PESA"), (CARD, "Card"), (BANK, "Bank transfer")]
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
    # Phase 4A: the confirmed M-PESA payment behind this row. One-to-one: a receipt code can pay only one sale.
    mpesa_transaction = models.OneToOneField("sales.MpesaTransaction", null=True, blank=True,
                                             on_delete=models.PROTECT, related_name="payment")
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        constraints = [models.CheckConstraint(condition=Q(amount__gt=0), name="payment_amount_positive")]

    def __str__(self):
        return f"{self.method} {self.amount} for sale {self.sale_id}"


class EtimsSubmission(models.Model):
    """One sale's journey to KRA eTIMS. The sale itself never changes; this row records the outcome.

    PENDING means KRA has not yet signed the invoice (offline, or the unit was busy). The sale stands and is
    retried later. Signature fields stay blank until KRA's unit really returns them; once SUBMITTED the row
    is frozen so a signed invoice can never be altered.
    """

    PENDING, SUBMITTED, REJECTED = "PENDING", "SUBMITTED", "REJECTED"
    STATUS = [(PENDING, "Waiting to be sent"), (SUBMITTED, "Signed by KRA"),
              (REJECTED, "Rejected by KRA: needs attention")]

    sale = models.OneToOneField(Sale, on_delete=models.PROTECT, related_name="etims")
    # eTIMS wants a plain, gapless whole-number invoice number; the shop's T1-261107-0042 is not one.
    invoice_no = models.PositiveBigIntegerField(unique=True, editable=False)
    receipt_type = models.CharField(max_length=1, default="S")          # S = sale (R = credit note, Phase 4)
    status = models.CharField(max_length=10, choices=STATUS, default=PENDING, db_index=True)
    attempts = models.PositiveIntegerField(default=0)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    submitted_at = models.DateTimeField(null=True, blank=True)

    scu_id = models.CharField(max_length=40, blank=True)
    scu_receipt_no = models.CharField(max_length=20, blank=True)         # the control unit's own receipt number
    scu_total_receipt_no = models.CharField(max_length=20, blank=True)
    scu_datetime = models.CharField(max_length=20, blank=True)
    internal_data = models.CharField(max_length=200, blank=True)
    signature = models.CharField(max_length=200, blank=True)
    qr_text = models.CharField(max_length=400, blank=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"eTIMS #{self.invoice_no} ({self.get_status_display()})"

    @classmethod
    def from_db(cls, db, field_names, values):
        obj = super().from_db(db, field_names, values)
        obj._db_status = obj.__dict__.get("status")
        return obj

    def save(self, *args, **kwargs):
        if not self._state.adding and getattr(self, "_db_status", None) == self.SUBMITTED:
            raise PermissionError("A signed eTIMS invoice cannot be changed.")
        super().save(*args, **kwargs)
        self._db_status = self.status

    def delete(self, *args, **kwargs):
        raise PermissionError("eTIMS records cannot be deleted.")


class EtimsCreditNote(models.Model):
    """One approved refund's journey to KRA eTIMS as a credit note (receipt type R). Same rules as EtimsSubmission:
    the refund itself never changes, signature fields stay blank until KRA's unit really returns them, and a
    signed credit note is frozen. It shares the invoice number sequence with sales because KRA numbers both
    in one series. `original_invoice_no` is the number of the signed sale invoice being credited."""

    PENDING, SUBMITTED, REJECTED = "PENDING", "SUBMITTED", "REJECTED"
    STATUS = [(PENDING, "Waiting to be sent"), (SUBMITTED, "Signed by KRA"),
              (REJECTED, "Rejected by KRA: needs attention")]

    refund = models.OneToOneField("Refund", on_delete=models.PROTECT, related_name="etims")
    invoice_no = models.PositiveBigIntegerField(unique=True, editable=False)
    original_invoice_no = models.PositiveBigIntegerField()
    receipt_type = models.CharField(max_length=1, default="R")
    status = models.CharField(max_length=10, choices=STATUS, default=PENDING, db_index=True)
    attempts = models.PositiveIntegerField(default=0)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    submitted_at = models.DateTimeField(null=True, blank=True)

    scu_id = models.CharField(max_length=40, blank=True)
    scu_receipt_no = models.CharField(max_length=20, blank=True)
    scu_total_receipt_no = models.CharField(max_length=20, blank=True)
    scu_datetime = models.CharField(max_length=20, blank=True)
    internal_data = models.CharField(max_length=200, blank=True)
    signature = models.CharField(max_length=200, blank=True)
    qr_text = models.CharField(max_length=400, blank=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"eTIMS credit note #{self.invoice_no} ({self.get_status_display()})"

    @classmethod
    def from_db(cls, db, field_names, values):
        obj = super().from_db(db, field_names, values)
        obj._db_status = obj.__dict__.get("status")
        return obj

    def save(self, *args, **kwargs):
        if not self._state.adding and getattr(self, "_db_status", None) == self.SUBMITTED:
            raise PermissionError("A signed eTIMS credit note cannot be changed.")
        super().save(*args, **kwargs)
        self._db_status = self.status

    def delete(self, *args, **kwargs):
        raise PermissionError("eTIMS records cannot be deleted.")


class MpesaTransaction(models.Model):
    """One confirmed M-PESA payment, keyed by Safaricom's receipt code. The unique code is what makes callbacks
    idempotent: Safaricom may deliver the same message many times and only one row can ever exist.
    Rows are history: the amount and code cannot change, and nothing is deleted."""

    C2B, STK = "C2B", "STK"
    SOURCES = [(C2B, "Paid to the Till / Paybill"), (STK, "STK push")]
    UNMATCHED, MATCHED, IGNORED = "UNMATCHED", "MATCHED", "IGNORED"
    STATUSES = [(UNMATCHED, "Received, not yet used on a sale"), (MATCHED, "Used on a sale"),
                (IGNORED, "Set aside by the manager (with a reason)")]

    trans_id = models.CharField("M-PESA receipt code", max_length=14, unique=True)
    source = models.CharField(max_length=3, choices=SOURCES)
    status = models.CharField(max_length=10, choices=STATUSES, default=UNMATCHED, db_index=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    # Only what Safaricom sent. Blank when it did not; the cashier may type a name on the sale instead.
    customer_name = models.CharField(max_length=120, blank=True)
    payer_ref = models.CharField(max_length=80, blank=True, help_text="Phone (STK) or Safaricom's hashed number (till).")
    shortcode = models.CharField(max_length=12, blank=True)
    bill_ref = models.CharField(max_length=60, blank=True)
    trans_time = models.CharField(max_length=14, blank=True)
    raw = models.JSONField(default=dict, blank=True)
    sale = models.ForeignKey(Sale, null=True, blank=True, on_delete=models.PROTECT, related_name="mpesa_transactions")
    matched_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    matched_at = models.DateTimeField(null=True, blank=True)
    # Reconciliation: why a payment that no sale used was set aside, and by whom. The money itself is never edited.
    resolution_reason = models.CharField(max_length=250, blank=True)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [models.CheckConstraint(condition=Q(amount__gt=0), name="mpesa_amount_positive")]

    def __str__(self):
        return f"{self.trans_id} KES {self.amount}"

    @classmethod
    def from_db(cls, db, field_names, values):
        obj = super().from_db(db, field_names, values)
        obj._orig = (obj.__dict__.get("trans_id"), obj.__dict__.get("amount"))
        return obj

    def save(self, *args, **kwargs):
        if not self._state.adding and getattr(self, "_orig", None) and (self.trans_id, self.amount) != self._orig:
            raise PermissionError("An M-PESA receipt code and amount cannot be changed.")
        super().save(*args, **kwargs)
        self._orig = (self.trans_id, self.amount)

    def delete(self, *args, **kwargs):
        raise PermissionError("M-PESA payment records cannot be deleted.")


class MpesaStkRequest(models.Model):
    """A payment prompt sent to a customer's phone. It only ever becomes CONFIRMED from Safaricom's callback."""

    PENDING, PROCESSING, CONFIRMED, FAILED, EXPIRED = "PENDING", "PROCESSING", "CONFIRMED", "FAILED", "EXPIRED"
    STATUSES = [(PENDING, "Not sent yet"), (PROCESSING, "Waiting for the customer"), (CONFIRMED, "Paid"),
                (FAILED, "Failed or cancelled"), (EXPIRED, "No answer")]
    OPEN_STATES = (PENDING, PROCESSING)

    sale = models.ForeignKey(Sale, on_delete=models.PROTECT, related_name="stk_requests")
    cashier = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    shift = models.ForeignKey("tills.CashierShift", on_delete=models.PROTECT, related_name="+")
    phone = models.CharField(max_length=12)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    merchant_request_id = models.CharField(max_length=60, blank=True)
    checkout_request_id = models.CharField(max_length=80, unique=True, null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUSES, default=PENDING, db_index=True)
    result_code = models.IntegerField(null=True, blank=True)
    result_desc = models.CharField(max_length=200, blank=True)
    transaction = models.ForeignKey(MpesaTransaction, null=True, blank=True, on_delete=models.PROTECT, related_name="stk_requests")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"STK {self.amount} to {self.phone[-3:]} ({self.get_status_display()})"

    def delete(self, *args, **kwargs):
        raise PermissionError("Payment requests cannot be deleted.")


class MpesaCallbackLog(models.Model):
    """Every well-formed message Safaricom sent to this server, exactly as received. This is what the online (VPS) server
    hands to the shop PC (see mpesa_relay), and the evidence if a payment is ever disputed. Append-only: never edited,
    never deleted (harden_db adds the database trigger)."""

    C2B, STK = "c2b", "stk"
    KINDS = [(C2B, "Till / paybill confirmation"), (STK, "STK push result")]

    kind = models.CharField(max_length=8, choices=KINDS)
    received_at = models.DateTimeField(auto_now_add=True, db_index=True)
    source_ip = models.GenericIPAddressField(null=True, blank=True)
    payload = models.JSONField()
    outcome = models.CharField(max_length=200, blank=True, help_text="'ok' when it was accepted and applied here.")

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.kind} #{self.pk} ({self.outcome})"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise PermissionError("Callback logs cannot be modified.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("Callback logs cannot be deleted.")


class MpesaRelayCursor(models.Model):
    """How far this shop PC has read the online server's callback log (one row). Moves forward only."""

    last_id = models.PositiveBigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class Refund(models.Model):
    """Money going back to a customer for goods from ONE completed sale.

    Life of a refund: REQUESTED (a cashier asks) -> APPROVED (someone else says yes; stock goes back if it is
    resaleable) -> PAID (the customer really has the money: cash handed over, or the M-PESA payback code
    recorded). Or REJECTED / CANCELLED. APPROVED and PAID are separate on purpose: an M-PESA refund can be
    approved today and only paid back when the manager has done it in Safaricom's portal.
    The sale itself is never edited: this row and its lines are the correction.
    """

    REQUESTED, APPROVED, PAID, REJECTED, CANCELLED = "REQUESTED", "APPROVED", "PAID", "REJECTED", "CANCELLED"
    STATUSES = [(REQUESTED, "Waiting for approval"), (APPROVED, "Approved, customer not paid back yet"),
                (PAID, "Paid back"), (REJECTED, "Rejected"), (CANCELLED, "Cancelled")]
    FINAL = (PAID, REJECTED, CANCELLED)
    CASH, MPESA = "CASH", "MPESA"
    METHODS = [(CASH, "Cash"), (MPESA, "M-PESA")]
    PRINT_FIELDS = {"print_count"}

    number = models.CharField(max_length=24, unique=True)
    sale = models.ForeignKey(Sale, on_delete=models.PROTECT, related_name="refunds")
    status = models.CharField(max_length=10, choices=STATUSES, default=REQUESTED, db_index=True)
    method = models.CharField(max_length=6, choices=METHODS)
    reason = models.CharField(max_length=250)
    total = models.DecimalField(max_digits=12, decimal_places=2)
    tax_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    till = models.ForeignKey("tills.Till", on_delete=models.PROTECT, related_name="refunds")
    shift = models.ForeignKey("tills.CashierShift", on_delete=models.PROTECT, related_name="refunds",
                              help_text="The shift of the cashier who asked for the refund.")
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="refunds_requested")
    requested_at = models.DateTimeField(auto_now_add=True)

    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    approved_at = models.DateTimeField(null=True, blank=True)
    approval_reason = models.CharField(max_length=250, blank=True)
    rejected_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    rejected_at = models.DateTimeField(null=True, blank=True)
    reject_reason = models.CharField(max_length=250, blank=True)

    paid_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    paid_at = models.DateTimeField(null=True, blank=True)
    paid_shift = models.ForeignKey("tills.CashierShift", null=True, blank=True, on_delete=models.PROTECT, related_name="refunds_paid",
                                   help_text="Cash refunds: the shift whose drawer the money came out of.")
    pay_reference = models.CharField(max_length=20, blank=True, help_text="M-PESA code of the payback.")
    print_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-requested_at", "-id"]
        constraints = [
            models.CheckConstraint(condition=Q(total__gt=0), name="refund_total_positive"),
            models.UniqueConstraint(fields=["pay_reference"], condition=~Q(pay_reference=""), name="refund_payback_code_unique"),
            models.CheckConstraint(condition=~Q(status="PAID") | Q(paid_at__isnull=False), name="paid_refund_has_paid_at"),
        ]

    def __str__(self):
        return f"{self.number} KES {self.total} ({self.get_status_display()})"

    @classmethod
    def from_db(cls, db, field_names, values):
        obj = super().from_db(db, field_names, values)
        obj._db_status = obj.__dict__.get("status")
        obj._orig = (obj.__dict__.get("sale_id"), obj.__dict__.get("total"), obj.__dict__.get("method"), obj.__dict__.get("number"))
        return obj

    def save(self, *args, **kwargs):
        if not self._state.adding:
            if getattr(self, "_db_status", None) in self.FINAL:
                changed = kwargs.get("update_fields")
                if changed is None or not set(changed) <= self.PRINT_FIELDS:
                    raise PermissionError("A finished refund cannot be changed.")
            if getattr(self, "_orig", None) and (self.sale_id, self.total, self.method, self.number) != self._orig:
                raise PermissionError("The sale, amount, method and number of a refund cannot be changed.")
        super().save(*args, **kwargs)
        self._db_status = self.status
        self._orig = (self.sale_id, self.total, self.method, self.number)

    def delete(self, *args, **kwargs):
        raise PermissionError("Refunds cannot be deleted.")


class RefundItem(models.Model):
    """One sale line (or part of it) being refunded. Amounts are fixed when the refund is requested."""

    refund = models.ForeignKey(Refund, on_delete=models.PROTECT, related_name="items")
    sale_item = models.ForeignKey(SaleItem, on_delete=models.PROTECT, related_name="refund_items")
    product_name = models.CharField(max_length=160)
    quantity = models.PositiveIntegerField()
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    # Tax snapshot of the refunded amount (same rate as the original sale line), for the eTIMS credit note.
    tax_class = models.CharField(max_length=1, default="B")
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0.00"))
    taxable_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    # restock = requested/decided "back on the shelf"; restocked = it really was returned to stock (at approval).
    restock = models.BooleanField(default=True)
    restocked = models.BooleanField(default=False)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gte=1), name="refund_item_quantity_positive"),
            models.CheckConstraint(condition=Q(amount__gt=0), name="refund_item_amount_positive"),
        ]

    def __str__(self):
        return f"{self.quantity} x {self.product_name} (refund)"

    def delete(self, *args, **kwargs):
        raise PermissionError("Refund lines cannot be deleted.")
