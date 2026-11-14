"""Catalogue business rules. Views call these; they enforce permissions and write the audit trail."""
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction

from core import audit

from . import codes
from .models import CodeSequence, Product, ProductCode, SellingUnit, Shelf, ShelfTransfer


class CatalogError(Exception):
    """A rule was broken; the message is safe to show the user."""


PRICE_FIELDS = ("selling_price", "wholesale_price", "min_selling_price")
COST_FIELDS = ("buying_cost",)


def can_see_cost(user):
    """Buying costs and profit figures are not for cashiers or receiving clerks."""
    return user.can("products.change_cost") or user.can("reports.financial.view")


def _snapshot(product, fields):
    return {f: (str(getattr(product, f)) if getattr(product, f) is not None else None) for f in fields}


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
    product.refresh_from_db()          # read back the stored (rounded) values for the audit trail
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


# --- Selling units (measures sold from an open bottle) ----------------------------------------------
#
# Who may do what:
#   create a unit ............ products.manage AND products.change_price (a unit always has a price)
#   rename / change volume ... products.manage
#   change a unit's price .... products.change_price (and products.manage)
#   enable / disable ......... products.manage
#   see the configured units . products.view
# Cashiers hold none of the write permissions. They can only *select* an active unit (sellable_units /
# get_sellable_unit); the price they get is always the stored one.

UNIT_NAME_MAX = 40
UNIT_VOLUME_MAX = Decimal("99999999.99")      # SellingUnit.volume_ml is Decimal(10, 2)
UNIT_PRICE_MAX = Decimal("9999999999.99")     # SellingUnit.selling_price is Decimal(12, 2)
UNIT_FIELDS = ("name", "volume_ml", "selling_price", "active")


def _decimal_2dp(value, what):
    try:
        amount = Decimal(str(value).strip())
        if not amount.is_finite():
            raise InvalidOperation
    except (InvalidOperation, TypeError, ValueError, AttributeError):
        raise CatalogError(f"{what} must be a valid number.") from None
    if amount != amount.quantize(Decimal("0.01")):
        raise CatalogError(f"{what} can have at most two decimal places.")
    return amount.quantize(Decimal("0.01"))


def _clean_unit_name(product, name, exclude_pk=None):
    name = " ".join(str(name or "").split())
    if not name:
        raise CatalogError("The selling unit needs a name.")
    if len(name) > UNIT_NAME_MAX:
        raise CatalogError(f"The name can have at most {UNIT_NAME_MAX} characters.")
    clash = SellingUnit.objects.filter(product=product, name__iexact=name)
    if exclude_pk:
        clash = clash.exclude(pk=exclude_pk)
    if clash.exists():
        raise CatalogError(f"'{product.name}' already has a selling unit called '{name}'.")
    return name


def _largest_known_bottle_ml(product):
    from inventory.models import Bottle      # inventory imports catalog, so import lazily

    return (Bottle.objects.filter(product=product).order_by("-capacity_ml")
            .values_list("capacity_ml", flat=True).first())


def _clean_unit_volume(product, volume, exclude_pk=None, check_active_clash=True):
    volume = _decimal_2dp(volume, "Volume")
    if volume <= 0:
        raise CatalogError("Volume must be greater than zero millilitres.")
    if volume > UNIT_VOLUME_MAX:
        raise CatalogError("That volume is too large.")
    largest = _largest_known_bottle_ml(product)
    if largest is not None and volume > largest:
        raise CatalogError(f"A selling unit cannot be bigger than the largest bottle on record ({largest} ml).")
    if check_active_clash:
        clash = SellingUnit.objects.filter(product=product, volume_ml=volume, active=True)
        if exclude_pk:
            clash = clash.exclude(pk=exclude_pk)
        other = clash.first()
        if other:
            raise CatalogError(f"'{other.name}' is already an active {volume} ml unit for this product. "
                               "Turn it off first, or pick a different volume.")
    return volume


def _clean_unit_price(price):
    price = _decimal_2dp(price, "Selling price")
    if price <= 0:
        raise CatalogError("The selling price must be greater than zero.")
    if price > UNIT_PRICE_MAX:
        raise CatalogError("That selling price is too large.")
    return price


def _unit_snapshot(unit):
    return {"name": unit.name, "volume_ml": str(unit.volume_ml), "selling_price": str(unit.selling_price),
            "active": unit.active}


def _lock_unit(unit):
    return SellingUnit.objects.select_for_update().select_related("product").get(pk=unit.pk)


@transaction.atomic
def create_selling_unit(product, user, *, name, volume_ml, selling_price, active=True, ip=None):
    """Add a measure (e.g. Tot 30 ml) to a product. Needs products.manage and products.change_price."""
    if not user.can("products.manage"):
        raise CatalogError("You are not allowed to configure selling units.")
    if not user.can("products.change_price"):
        raise CatalogError("You are not allowed to set selling prices.")
    product = Product.objects.select_for_update().get(pk=product.pk)
    if not product.active:
        raise CatalogError("Selling units cannot be added to an inactive product.")
    name = _clean_unit_name(product, name)
    volume = _clean_unit_volume(product, volume_ml, check_active_clash=bool(active))
    price = _clean_unit_price(selling_price)
    try:
        with transaction.atomic():
            unit = SellingUnit.objects.create(product=product, name=name, volume_ml=volume, selling_price=price,
                                              active=bool(active))
    except IntegrityError as exc:
        raise CatalogError("That name or volume is already used by another selling unit.") from exc
    audit.record("selling_unit.created", actor=user, object_type="product", object_ref=product.pk,
                 new={"unit_id": unit.pk, **_unit_snapshot(unit)}, ip=ip)
    return unit


@transaction.atomic
def update_selling_unit(unit, user, *, name=None, volume_ml=None, selling_price=None, ip=None):
    """Edit a unit's name, volume or price. Arguments left as None are not changed.

    Past sales are unaffected: sale lines keep their own snapshot of the name, volume and price.
    """
    if not user.can("products.manage"):
        raise CatalogError("You are not allowed to configure selling units.")
    unit = _lock_unit(unit)
    before = _unit_snapshot(unit)
    if selling_price is not None and _decimal_2dp(selling_price, "Selling price") != unit.selling_price:
        if not user.can("products.change_price"):
            raise CatalogError("You are not allowed to change selling prices.")
        unit.selling_price = _clean_unit_price(selling_price)
    if name is not None:
        unit.name = _clean_unit_name(unit.product, name, exclude_pk=unit.pk)
    if volume_ml is not None and _decimal_2dp(volume_ml, "Volume") != unit.volume_ml:
        unit.volume_ml = _clean_unit_volume(unit.product, volume_ml, exclude_pk=unit.pk,
                                            check_active_clash=unit.active)
    after = _unit_snapshot(unit)
    if after == before:
        return unit
    try:
        with transaction.atomic():
            unit.save(update_fields=["name", "volume_ml", "selling_price"])
    except IntegrityError as exc:
        raise CatalogError("That name or volume is already used by another selling unit.") from exc
    changed = {k for k in UNIT_FIELDS if before[k] != after[k]}
    if changed & {"name", "volume_ml"}:
        audit.record("selling_unit.updated", actor=user, object_type="product", object_ref=unit.product_id,
                     previous={"unit_id": unit.pk, **before}, new={"unit_id": unit.pk, **after}, ip=ip)
    if "selling_price" in changed:
        audit.record("selling_unit.price_changed", actor=user, object_type="product", object_ref=unit.product_id,
                     previous={"unit_id": unit.pk, "selling_price": before["selling_price"]},
                     new={"unit_id": unit.pk, "selling_price": after["selling_price"]}, ip=ip)
    return unit


@transaction.atomic
def set_selling_unit_active(unit, user, active, *, ip=None):
    """Enable or disable a unit. Disabled units stay on record but cannot be selected for a sale."""
    if not user.can("products.manage"):
        raise CatalogError("You are not allowed to configure selling units.")
    unit = _lock_unit(unit)
    active = bool(active)
    if unit.active == active:
        return unit
    if active:
        if not unit.product.active:
            raise CatalogError("Turn the product on before enabling its selling units.")
        _clean_unit_volume(unit.product, unit.volume_ml, exclude_pk=unit.pk)    # no second active unit of this size
    unit.active = active
    try:
        with transaction.atomic():
            unit.save(update_fields=["active"])
    except IntegrityError as exc:
        raise CatalogError("Another active selling unit already uses this volume.") from exc
    audit.record("selling_unit.enabled" if active else "selling_unit.disabled", actor=user,
                 object_type="product", object_ref=unit.product_id,
                 new={"unit_id": unit.pk, "name": unit.name, "volume_ml": str(unit.volume_ml)}, ip=ip)
    return unit


def sellable_units(product):
    """The units a cashier may choose from: active units of an active product, smallest first."""
    if not product.active:
        return SellingUnit.objects.none()
    return SellingUnit.objects.filter(product=product, active=True).order_by("volume_ml", "id")


def get_sellable_unit(unit_id):
    """The one way the POS should resolve a chosen unit. Disabled units and inactive products are refused.

    The returned unit carries the stored price; a client-supplied price is never trusted.
    """
    unit = SellingUnit.objects.select_related("product").filter(pk=unit_id).first()
    if unit is None or not unit.active or not unit.product.active:
        raise CatalogError("That selling unit is not available.")
    return unit
