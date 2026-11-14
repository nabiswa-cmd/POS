from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F, Q

from . import movement_rules as mr


class StockMovement(models.Model):
    """Every change to stock, forever. Append-only (model guard + PostgreSQL trigger).

    quantity is signed base units. balance_after is the product's stock right after this movement.
    total_cost is the cost basis of the units moved (positive number) for stock leaving or entering.
    """

    TYPE_CHOICES = [(k, v) for k, v in mr.LABELS.items()]

    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT, related_name="movements")
    movement_type = models.CharField(max_length=16, choices=TYPE_CHOICES, db_index=True)
    quantity = models.IntegerField(help_text="Signed whole base units. Zero only for volume-only bottle movements.")
    balance_after = models.IntegerField()
    # Partial quantity selling: set only for bottle movements (see movement_rules.BOTTLE_TYPES).
    bottle = models.ForeignKey("inventory.Bottle", null=True, blank=True, on_delete=models.PROTECT,
                               related_name="movements")
    source_movement = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT,
                                        related_name="volume_returns")
    refund_item = models.ForeignKey("sales.RefundItem", null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="stock_movements")
    volume_ml = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True,
                                    help_text="Signed millilitres added to (+) or removed from (-) the bottle.")
    bottle_remaining_after_ml = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    operation_key = models.CharField(max_length=80, null=True, blank=True)
    operation_sequence = models.PositiveIntegerField(default=0)
    unit_cost = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    total_cost = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="stock_movements")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="stock_movements_approved")
    reference = models.CharField(max_length=60, blank=True)
    reason = models.CharField(max_length=250, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["product", "-created_at"]), models.Index(fields=["bottle", "-created_at"])]
        constraints = [
            models.CheckConstraint(
                condition=~Q(quantity=0) | (Q(volume_ml__isnull=False) & ~Q(volume_ml=0)),
                name="movement_has_quantity_or_volume"),
            models.CheckConstraint(
                condition=Q(volume_ml__isnull=True) | Q(bottle__isnull=False),
                name="movement_volume_needs_bottle"),
            models.CheckConstraint(
                condition=(Q(movement_type=mr.VOLUME_REFUND, source_movement__isnull=False,
                            refund_item__isnull=False)
                           | (~Q(movement_type=mr.VOLUME_REFUND) & Q(source_movement__isnull=True)
                              & Q(refund_item__isnull=True))),
                name="volume_refund_has_provenance"),
            models.UniqueConstraint(fields=["operation_key", "operation_sequence"],
                                    condition=Q(operation_key__isnull=False),
                                    name="uniq_stock_movement_operation_sequence"),
        ]

    def __str__(self):
        return f"{self.get_movement_type_display()} {self.quantity:+d} {self.product_id}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise PermissionError("Stock movements cannot be modified.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("Stock movements cannot be deleted.")


class Bottle(models.Model):
    """One open (or about-to-be-opened) bottle whose liquid is sold in measures.

    Sealed bottles stay counted in Product.stock_qty; they do NOT get a row here. A row is created when a
    sealed bottle is opened (BOTTLE_OPEN movement takes one bottle out of stock_qty and puts its volume here).
    SEALED exists for completeness and for bottles that are individually tracked; the opening workflow
    (a later phase) is what moves a bottle to OPENED.
    """

    SEALED, OPENED, EMPTY = "SEALED", "OPENED", "EMPTY"
    STATUS = [(SEALED, "Sealed"), (OPENED, "Opened"), (EMPTY, "Empty")]

    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT, related_name="bottles")
    capacity_ml = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    remaining_ml = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal("0"))])
    status = models.CharField(max_length=6, choices=STATUS, default=SEALED, db_index=True)
    opened_at = models.DateTimeField(null=True, blank=True)
    opened_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                  related_name="bottles_opened")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["opened_at", "id"]  # oldest opened first, so the oldest bottle is finished first
        indexes = [models.Index(fields=["product", "status", "opened_at"])]
        constraints = [
            models.CheckConstraint(condition=Q(capacity_ml__gt=0), name="bottle_capacity_positive"),
            models.CheckConstraint(condition=Q(remaining_ml__gte=0) & Q(remaining_ml__lte=F("capacity_ml")),
                                   name="bottle_remaining_within_capacity"),
            models.CheckConstraint(condition=Q(status__in=["SEALED", "OPENED", "EMPTY"]), name="bottle_status_valid"),
            models.CheckConstraint(
                condition=(Q(status="SEALED") & Q(remaining_ml=F("capacity_ml")) & Q(opened_at__isnull=True))
                | (Q(status="OPENED") & Q(remaining_ml__gt=0) & Q(opened_at__isnull=False))
                | (Q(status="EMPTY") & Q(remaining_ml=0)),
                name="bottle_status_matches_volume"),
        ]

    def __str__(self):
        return f"{self.product_id} bottle {self.remaining_ml}/{self.capacity_ml} ml ({self.get_status_display()})"


class StockLot(models.Model):
    """A batch of stock bought at one cost. Lets FIFO work and keeps average cost honest."""

    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT, related_name="lots")
    movement = models.OneToOneField(StockMovement, on_delete=models.PROTECT, related_name="lot")
    received_qty = models.PositiveIntegerField()
    remaining_qty = models.PositiveIntegerField()
    unit_cost = models.DecimalField(max_digits=12, decimal_places=4)
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["received_at", "id"]
        indexes = [models.Index(fields=["product", "received_at"])]
        constraints = [models.CheckConstraint(condition=Q(remaining_qty__lte=F("received_qty")),
                                              name="lot_remaining_not_above_received")]


class Receiving(models.Model):
    """A delivery document (goods received note). Stock changes only when it is POSTED."""

    DRAFT, PENDING, POSTED, REJECTED = "DRAFT", "PENDING", "POSTED", "REJECTED"
    STATUS = [(DRAFT, "Draft"), (PENDING, "Awaiting approval"), (POSTED, "Posted to stock"), (REJECTED, "Rejected")]

    number = models.CharField(max_length=20, unique=True, editable=False)
    supplier = models.ForeignKey("catalog.Supplier", null=True, blank=True, on_delete=models.PROTECT,
                                 related_name="receivings")
    delivery_note = models.CharField(max_length=60, blank=True)
    invoice_ref = models.CharField(max_length=60, blank=True)
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=STATUS, default=DRAFT, db_index=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="receivings")
    created_at = models.DateTimeField(auto_now_add=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="receivings_decided")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=250, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "stock receiving"

    def __str__(self):
        return self.number

    @property
    def editable(self):
        return self.status == self.DRAFT


class ReceivingItem(models.Model):
    """One product line on a delivery. Quantities are in packs of `pack_size` base units."""

    receiving = models.ForeignKey(Receiving, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT, related_name="receiving_items")
    packaging = models.ForeignKey("catalog.Packaging", null=True, blank=True, on_delete=models.PROTECT,
                                  related_name="+")
    pack_size = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)],
                                            help_text="Base units per pack when this line was entered.")
    qty_good = models.PositiveIntegerField(default=0, help_text="Packs received in good condition.")
    qty_damaged = models.PositiveIntegerField(default=0, help_text="Packs damaged on arrival (not added to stock).")
    qty_missing = models.PositiveIntegerField(default=0, help_text="Packs on the delivery note but not delivered.")
    cost_per_pack = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0"))])

    class Meta:
        ordering = ["id"]
        constraints = [models.CheckConstraint(condition=Q(pack_size__gte=1), name="receiving_pack_size_positive")]

    @property
    def good_units(self):
        return self.qty_good * self.pack_size

    @property
    def line_cost(self):
        return (self.cost_per_pack * self.qty_good).quantize(Decimal("0.01"))
