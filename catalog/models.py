from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.db.models import F, Q

from . import codes, units

MONEY = dict(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0"))])


class Category(models.Model):
    name = models.CharField(max_length=80, unique=True)
    active = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "categories"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Brand(models.Model):
    name = models.CharField(max_length=80, unique=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Supplier(models.Model):
    """Optional. Nothing forces a product or a delivery to have a supplier."""

    name = models.CharField(max_length=120)
    contact_person = models.CharField(max_length=120, blank=True)
    phone = models.CharField(max_length=40, blank=True)
    email = models.EmailField(blank=True)
    address = models.CharField(max_length=250, blank=True)
    kra_pin = models.CharField("KRA PIN", max_length=20, blank=True)
    notes = models.TextField(blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Shelf(models.Model):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=80)
    notes = models.CharField(max_length=200, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        verbose_name_plural = "shelves"

    def __str__(self):
        return f"{self.code} {self.name}"

    def save(self, *args, **kwargs):
        self.code = codes.normalize(self.code)
        super().save(*args, **kwargs)


class CodeSequence(models.Model):
    """Running numbers for generated codes. Always advanced under a row lock."""

    name = models.CharField(max_length=30, primary_key=True)
    value = models.PositiveBigIntegerField(default=0)

    @classmethod
    def next(cls, name):
        with transaction.atomic():
            cls.objects.get_or_create(name=name)
            row = cls.objects.select_for_update().get(name=name)
            row.value = F("value") + 1
            row.save(update_fields=["value"])
            row.refresh_from_db()
            return row.value


class Product(models.Model):
    """A sellable item. Stock is held in BASE UNITS (the smallest unit sold)."""

    name = models.CharField(max_length=160, db_index=True)
    brand = models.ForeignKey(Brand, null=True, blank=True, on_delete=models.PROTECT, related_name="products")
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.PROTECT, related_name="products")
    description = models.TextField(blank=True)
    sku = models.CharField("Internal product code (SKU)", max_length=40, unique=True)
    base_unit = models.CharField(max_length=20, default="bottle",
                                 help_text="The smallest unit you sell, e.g. bottle, can, tot.")

    # Per base unit. buying_cost is the manager's current expected cost (it prefills deliveries).
    # avg_cost is maintained by stock receiving and is what profit uses; nobody edits it by hand.
    buying_cost = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal("0"),
                                      validators=[MinValueValidator(Decimal("0"))])
    avg_cost = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal("0"), editable=False)
    last_cost = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal("0"), editable=False)

    selling_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"),
                                        validators=[MinValueValidator(Decimal("0"))])
    wholesale_price = models.DecimalField(null=True, blank=True, **MONEY)
    min_selling_price = models.DecimalField(null=True, blank=True, **MONEY)

    # Never edited directly: only inventory.services changes it, always with a StockMovement.
    stock_qty = models.IntegerField(default=0, editable=False)
    reorder_level = models.PositiveIntegerField(default=0)

    tax_class = models.CharField(
        max_length=1, default="B",
        choices=[("A", "A - Exempt"), ("B", "B - Standard rate (VAT)"), ("C", "C - Zero-rated"),
                 ("D", "D - Non-VAT"), ("E", "E - Special rate (8%)")],
        help_text="Wines and spirits are normally B (standard rate). Confirm with your accountant.")
    etims_item_class_code = models.CharField(
        "eTIMS item class code", max_length=20, blank=True,
        help_text="KRA's item classification code for this product. Needed only when eTIMS is switched on.")

    supplier = models.ForeignKey(Supplier, null=True, blank=True, on_delete=models.SET_NULL, related_name="products")
    shelf = models.ForeignKey(Shelf, null=True, blank=True, on_delete=models.SET_NULL, related_name="products")
    image = models.ImageField(upload_to="products/", blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.CheckConstraint(condition=Q(selling_price__gte=0), name="product_price_not_negative"),
            models.CheckConstraint(
                condition=Q(min_selling_price__isnull=True) | Q(min_selling_price__lte=F("selling_price")),
                name="product_min_price_not_above_price"),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.sku = codes.normalize(self.sku)
        super().save(*args, **kwargs)

    @property
    def is_low(self):
        return self.reorder_level > 0 and self.stock_qty <= self.reorder_level

    def stock_text(self):
        pack = self.packagings.filter(active=True, units_per_pack__gt=1).order_by("-units_per_pack").first()
        return units.describe_stock(self.stock_qty, self.base_unit,
                                    pack.name if pack else None, pack.units_per_pack if pack else None)


class Packaging(models.Model):
    """A pack size for one product: Carton of 24, Crate of 12, Six-pack ... Each product has its own."""

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="packagings")
    name = models.CharField(max_length=40)
    units_per_pack = models.PositiveIntegerField(validators=[MinValueValidator(2)])
    selling_price = models.DecimalField(null=True, blank=True, **MONEY,
                                        help_text="Leave blank to use the unit price x units in the pack.")
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["units_per_pack"]
        constraints = [
            models.UniqueConstraint(fields=["product", "name"], name="uniq_packaging_name_per_product"),
            models.CheckConstraint(condition=Q(units_per_pack__gte=2), name="packaging_at_least_two_units"),
        ]

    def __str__(self):
        return f"{self.name} ({self.units_per_pack} x {self.product.base_unit})"

    @property
    def price(self):
        return units.pack_price(self.product.selling_price, self.units_per_pack, self.selling_price)


class ProductCode(models.Model):
    """Everything a scanner can read for a product. Unique across the whole shop."""

    KIND_BARCODE = "BARCODE"
    KIND_INTERNAL = "INTERNAL"
    KIND_MANUAL = "MANUAL"
    KIND_SKU = "SKU"
    KINDS = [(KIND_BARCODE, "Manufacturer barcode"), (KIND_INTERNAL, "Shop-generated code"),
             (KIND_MANUAL, "Manual code"), (KIND_SKU, "SKU")]

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="codes")
    packaging = models.ForeignKey(Packaging, null=True, blank=True, on_delete=models.CASCADE, related_name="codes",
                                  help_text="Set when this code identifies a whole pack (carton, crate).")
    code = models.CharField(max_length=40, unique=True)
    kind = models.CharField(max_length=10, choices=KINDS)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")

    class Meta:
        ordering = ["kind", "code"]

    def __str__(self):
        return self.code

    def save(self, *args, **kwargs):
        self.code = codes.normalize(self.code)
        super().save(*args, **kwargs)

    @property
    def units(self):
        return self.packaging.units_per_pack if self.packaging_id else 1


class ShelfTransfer(models.Model):
    """History of products moving between shelves (append-only; see core.db_hardening)."""

    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="shelf_transfers")
    from_shelf = models.ForeignKey(Shelf, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    to_shelf = models.ForeignKey(Shelf, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise PermissionError("Shelf transfer history cannot be modified.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("Shelf transfer history cannot be deleted.")
