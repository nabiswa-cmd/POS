"""Catalogue business rules. Views call these; they enforce permissions and write the audit trail."""
from decimal import Decimal

from django.db import IntegrityError, transaction

from core import audit

from . import codes
from .models import CodeSequence, Product, ProductCode, Shelf, ShelfTransfer


class CatalogError(Exception):
    """A rule was broken; the message is safe to show the user."""


PRICE_FIELDS = ("selling_price", "wholesale_price", "min_selling_price")
COST_FIELDS = ("buying_cost",)


def can_see_cost(user):
    """Buying costs and profit figures are not for cashiers or receiving clerks."""
    return user.can("products.change_cost") or user.can("reports.financial.view")


def _snapshot(product, fields):
    """Field values as fixed-decimal text, so 3500 and 3500.00 compare equal and audit entries read the same."""
    places = {f: (4 if f in COST_FIELDS else 2) for f in fields}
    return {f: (f"{Decimal(getattr(product, f)):.{places[f]}f}" if getattr(product, f) is not None else None)
            for f in fields}


def next_sku():
    """Generated SKU, e.g. P000123. Used when the manager does not type one."""
    while True:
        sku = f"P{CodeSequence.next('sku'):06d}"
        if not ProductCode.objects.filter(code=sku).exists():
            return sku


@transaction.atomic
def create_product(form, user, ip=None):
    """Save a validated ProductForm as a new product, register its SKU as a scannable code."""
    if not user.can("products.manage"):
        raise CatalogError("You are not allowed to add products.")
    product = form.save(commit=False)
    if not product.sku:
        product.sku = next_sku()
    try:
        product.sku = codes.validate_text(product.sku)
        product.save()
        form.save_m2m()
        ProductCode.objects.create(product=product, code=product.sku, kind=ProductCode.KIND_SKU, created_by=user)
    except codes.CodeError as exc:
        raise CatalogError(str(exc)) from exc
    except IntegrityError as exc:
        raise CatalogError("That product code is already in use.") from exc
    audit.record("product.created", actor=user, object_type="product", object_ref=product.pk,
                 new={"name": product.name, "sku": product.sku}, ip=ip)
    return product


@transaction.atomic
def update_product(form, user, ip=None):
    """Save an edited ProductForm. Price and cost changes are permission-checked and audited."""
    if not user.can("products.manage"):
        raise CatalogError("You are not allowed to edit products.")
    old = Product.objects.get(pk=form.instance.pk)
    before_prices, before_costs = _snapshot(old, PRICE_FIELDS), _snapshot(old, COST_FIELDS)
    product = form.save()
    after_prices, after_costs = _snapshot(product, PRICE_FIELDS), _snapshot(product, COST_FIELDS)
    # Defence in depth: the form already hides these fields from people without the permission.
    if after_prices != before_prices and not user.can("products.change_price"):
        raise CatalogError("You are not allowed to change selling prices.")
    if after_costs != before_costs and not user.can("products.change_cost"):
        raise CatalogError("You are not allowed to change buying costs.")
    if after_prices != before_prices:
        audit.record("product.price_changed", actor=user, object_type="product", object_ref=product.pk,
                     previous=before_prices, new=after_prices, ip=ip)
    if after_costs != before_costs:
        audit.record("product.cost_changed", actor=user, object_type="product", object_ref=product.pk,
                     previous=before_costs, new=after_costs, ip=ip)
    if old.active != product.active:
        audit.record("product.activated" if product.active else "product.deactivated", actor=user,
                     object_type="product", object_ref=product.pk, ip=ip)
    audit.record("product.updated", actor=user, object_type="product", object_ref=product.pk, ip=ip,
                 previous={"name": old.name, "shelf": old.shelf_id}, new={"name": product.name, "shelf": product.shelf_id})
    return product


@transaction.atomic
def add_code(product, raw_code, user, *, kind=ProductCode.KIND_BARCODE, packaging=None, ip=None):
    """Attach a barcode or manual code. Duplicates are refused across the whole shop."""
    if not user.can("products.manage"):
        raise CatalogError("You are not allowed to add product codes.")
    if packaging is not None and packaging.product_id != product.pk:
        raise CatalogError("That pack size belongs to a different product.")
    try:
        code = codes.validate_text(raw_code)
    except codes.CodeError as exc:
        raise CatalogError(str(exc)) from exc
    existing = ProductCode.objects.select_related("product").filter(code=code).first()
    if existing:
        raise CatalogError(f"Code {code} already belongs to '{existing.product.name}'.")
    try:
        with transaction.atomic():
            pc = ProductCode.objects.create(product=product, packaging=packaging, code=code, kind=kind, created_by=user)
    except IntegrityError as exc:
        raise CatalogError(f"Code {code} is already in use.") from exc
    audit.record("product.code_added", actor=user, object_type="product", object_ref=product.pk,
                 new={"code": code, "kind": kind, "packaging": packaging.pk if packaging else None}, ip=ip)
    return pc


@transaction.atomic
def generate_internal_code(product, user, *, packaging=None, ip=None):
    """Create a shop-generated EAN-13 for a product that has no barcode (or needs a pack barcode)."""
    for _ in range(20):
        candidate = codes.internal_ean13(CodeSequence.next("internal"))
        if not ProductCode.objects.filter(code=candidate).exists():
            return add_code(product, candidate, user, kind=ProductCode.KIND_INTERNAL, packaging=packaging, ip=ip)
    raise CatalogError("Could not generate a unique code. Try again.")


def find_by_code(raw_code):
    """What the POS and receiving screens call after a scan. Returns ProductCode or None."""
    code = codes.normalize(raw_code)
    if not code:
        return None
    return (ProductCode.objects.select_related("product", "packaging")
            .filter(code=code, product__active=True).first())


def label_code_for(product):
    """The code printed on a label: shop-generated first, then manufacturer barcode, then SKU."""
    order = {ProductCode.KIND_INTERNAL: 0, ProductCode.KIND_BARCODE: 1, ProductCode.KIND_MANUAL: 2, ProductCode.KIND_SKU: 3}
    rows = [c for c in product.codes.all() if c.packaging_id is None]
    rows.sort(key=lambda c: order.get(c.kind, 9))
    return rows[0].code if rows else product.sku


@transaction.atomic
def move_products_to_shelf(products, shelf, user, ip=None):
    """Assign products to a shelf (or None to unassign) and record every move."""
    if not user.can("shelves.manage"):
        raise CatalogError("You are not allowed to move products between shelves.")
    moved = 0
    for product in Product.objects.select_for_update().filter(pk__in=[p.pk for p in products]):
        if product.shelf_id == (shelf.pk if shelf else None):
            continue
        ShelfTransfer.objects.create(product=product, from_shelf=product.shelf, to_shelf=shelf, user=user)
        product.shelf = shelf
        product.save(update_fields=["shelf"])
        audit.record("shelf.product_moved", actor=user, object_type="product", object_ref=product.pk,
                     new={"shelf": shelf.code if shelf else None}, ip=ip)
        moved += 1
    return moved
