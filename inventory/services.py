"""Stock rules. Every change to Product.stock_qty goes through post_movement()."""
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from accounts import services as auth_services
from accounts.approval_rules import ApprovalError
from catalog.models import CodeSequence, Packaging, Product, SellingUnit
from core import audit
from core.models import ShopSettings

from . import costing, movement_rules as mr
from .models import Bottle, Receiving, ReceivingItem, StockLot, StockMovement


class StockError(Exception):
    """A stock rule was broken; the message is safe to show the user."""


def _lock_product(product_id):
    # Row lock: two tills selling the last bottle at once are serialised here, so one of them
    # sees the reduced stock and is refused instead of both succeeding.
    return Product.objects.select_for_update().get(pk=product_id)


def _issue_cost(product, quantity, method):
    """Consume lots (oldest first) and return the cost basis of `quantity` units leaving stock.

    FIFO costs the units at the lots' own costs; weighted average costs them at product.avg_cost.
    Lots are consumed either way so they stay consistent. Units not covered by any lot (stock that
    pre-dates lot tracking) are costed at the average cost.
    """
    lots = list(StockLot.objects.select_for_update().filter(product=product, remaining_qty__gt=0)
                .order_by("received_at", "id"))
    try:
        allocations = costing.allocate_fifo([(l.pk, l.remaining_qty, l.unit_cost) for l in lots], quantity)
        uncovered = 0
    except costing.InsufficientLots as short:
        allocations, uncovered = short.allocations, short.shortfall
    by_id = {l.pk: l for l in lots}
    for lot_id, taken, _cost in allocations:
        lot = by_id[lot_id]
        lot.remaining_qty -= taken
        lot.save(update_fields=["remaining_qty"])
    if method == ShopSettings.COSTING_FIFO:
        total = costing.allocation_cost(allocations) + costing.q2(product.avg_cost * uncovered)
    else:
        total = costing.q2(product.avg_cost * quantity)
    return total


@transaction.atomic
def post_movement(*, product_id, movement_type, quantity, user, reference="", reason="", unit_cost=None,
                  approved_by=None, allow_negative=False):
    """Change one product's stock and record why. Returns the StockMovement.

    quantity is signed base units. Stock may not go below zero unless allow_negative is set
    (it never is for sales). For a sale of several products, call this once per product in
    ascending product_id order so two tills cannot deadlock each other.
    """
    try:
        mr.check_quantity(movement_type, quantity)
    except mr.MovementError as exc:
        raise StockError(str(exc)) from exc
    product = _lock_product(product_id)
    new_balance = product.stock_qty + quantity
    if new_balance < 0 and not allow_negative:
        raise StockError(f"Only {product.stock_qty} {product.base_unit} of '{product.name}' in stock.")

    shop = ShopSettings.load()
    if quantity > 0:
        cost = costing.q4(unit_cost if unit_cost is not None else product.avg_cost)
        total_cost = costing.q2(cost * quantity)
    else:
        total_cost = _issue_cost(product, -quantity, shop.costing_method)
        cost = costing.q4(total_cost / -quantity)

    movement = StockMovement.objects.create(
        product=product, movement_type=movement_type, quantity=quantity, balance_after=new_balance,
        unit_cost=cost, total_cost=total_cost, user=user, approved_by=approved_by,
        reference=reference[:60], reason=reason[:250])

    if quantity > 0:
        StockLot.objects.create(product=product, movement=movement, received_qty=quantity,
                                remaining_qty=quantity, unit_cost=cost)
        if movement_type in mr.COSTED_IN:
            product.avg_cost = costing.weighted_average(product.stock_qty, product.avg_cost, quantity, cost)
            product.last_cost = cost
    product.stock_qty = new_balance
    product.save(update_fields=["stock_qty", "avg_cost", "last_cost"])
    if movement_type == mr.SALE and not shop.costing_locked:
        shop.costing_locked = True  # the costing method can never change once a sale exists
        shop.save(update_fields=["costing_locked"])
    return movement


# --- Partial volume inventory -------------------------------------------------------------------

def _volume(value, *, positive=False, allow_zero=False):
    try:
        amount = Decimal(str(value))
        if not amount.is_finite():
            raise InvalidOperation
        rounded = amount.quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        raise StockError("Volume must be a valid amount in millilitres.") from None
    if amount != rounded:
        raise StockError("Volume can have at most two decimal places.")
    if (positive and amount <= 0) or (not allow_zero and not positive and amount == 0):
        raise StockError("Volume must be greater than zero.")
    return rounded


def _operation_key(value):
    key = str(value or "").strip()
    if not key:
        raise StockError("An operation key is required to prevent duplicate stock movements.")
    if len(key) > 80:
        raise StockError("Operation keys cannot exceed 80 characters.")
    return key


def _ensure_operation_unused(operation_key):
    if StockMovement.objects.filter(operation_key=operation_key).exists():
        raise StockError("This inventory operation was already recorded.")


def _check_bottle_movement(movement_type, quantity, volume_ml):
    try:
        mr.check_bottle_movement(movement_type, quantity, volume_ml)
    except mr.MovementError as exc:
        raise StockError(str(exc)) from exc


def _record_bottle_movement(*, product, bottle, movement_type, quantity, volume_ml, user, operation_key,
                            operation_sequence, reference="", reason="", balance_after=None,
                            remaining_after=None, unit_cost=None, total_cost=None):
    _check_bottle_movement(movement_type, quantity, volume_ml)
    return StockMovement.objects.create(
        product=product, bottle=bottle, movement_type=movement_type, quantity=quantity,
        balance_after=product.stock_qty if balance_after is None else balance_after,
        volume_ml=volume_ml, bottle_remaining_after_ml=remaining_after, user=user,
        operation_key=operation_key, operation_sequence=operation_sequence,
        reference=(reference or "")[:60], reason=(reason or "")[:250],
        unit_cost=unit_cost, total_cost=total_cost)


def _open_bottle_locked(*, product, capacity_ml, user, operation_key, operation_sequence, reference="", reason=""):
    """Open one bottle while the caller holds the product row lock and transaction."""
    if product.stock_qty < 1:
        raise StockError(f"No sealed bottles of '{product.name}' are available to open.")
    new_balance = product.stock_qty - 1
    total_cost = _issue_cost(product, 1, ShopSettings.load().costing_method)
    bottle = Bottle.objects.create(
        product=product, capacity_ml=capacity_ml, remaining_ml=capacity_ml, status=Bottle.OPENED,
        opened_at=timezone.now(), opened_by=user)
    movement = _record_bottle_movement(
        product=product, bottle=bottle, movement_type=mr.BOTTLE_OPEN, quantity=-1, volume_ml=capacity_ml,
        user=user, operation_key=operation_key, operation_sequence=operation_sequence,
        reference=reference, reason=reason, balance_after=new_balance, remaining_after=capacity_ml,
        unit_cost=costing.q4(total_cost), total_cost=total_cost)
    product.stock_qty = new_balance
    product.save(update_fields=["stock_qty"])
    return bottle, movement


def _volume_cost(product, bottle, volume_ml):
    opening = bottle.movements.filter(movement_type=mr.BOTTLE_OPEN).order_by("id").first()
    bottle_cost = opening.total_cost if opening and opening.total_cost is not None else product.avg_cost
    unit_cost = costing.q4(Decimal(bottle_cost) / bottle.capacity_ml)
    return unit_cost, costing.q2(unit_cost * volume_ml)


def _change_bottle_volume(*, product, bottle, volume_ml, movement_type, user, operation_key,
                          operation_sequence, reference="", reason=""):
    new_remaining = bottle.remaining_ml + volume_ml
    if new_remaining < 0:
        raise StockError(f"Only {bottle.remaining_ml} ml remain in this bottle.")
    if new_remaining > bottle.capacity_ml:
        raise StockError(f"This adjustment would exceed the bottle's {bottle.capacity_ml} ml capacity.")
    if bottle.status == Bottle.SEALED:
        raise StockError("A sealed bottle must be opened before its volume can be changed.")
    new_status = Bottle.EMPTY if new_remaining == 0 else Bottle.OPENED
    unit_cost, total_cost = _volume_cost(product, bottle, abs(volume_ml))
    bottle.remaining_ml = new_remaining
    bottle.status = new_status
    bottle.save(update_fields=["remaining_ml", "status"])
    movement = _record_bottle_movement(
        product=product, bottle=bottle, movement_type=movement_type, quantity=0, volume_ml=volume_ml,
        user=user, operation_key=operation_key, operation_sequence=operation_sequence,
        reference=reference, reason=reason, remaining_after=new_remaining, unit_cost=unit_cost,
        total_cost=total_cost)
    return movement


def _known_capacity(product_id):
    return (Bottle.objects.filter(product_id=product_id).order_by("-created_at", "-pk")
            .values_list("capacity_ml", flat=True).first())


@transaction.atomic
def open_bottle(*, product_id, capacity_ml, user, operation_key, reference="", reason=""):
    """Move one sealed bottle into tracked open stock and append its opening movement."""
    capacity = _volume(capacity_ml, positive=True)
    key = _operation_key(operation_key)
    product = _lock_product(product_id)
    _ensure_operation_unused(key)
    bottle, _movement = _open_bottle_locked(
        product=product, capacity_ml=capacity, user=user, operation_key=key, operation_sequence=0,
        reference=reference, reason=reason)
    return bottle


@transaction.atomic
def sell_measures(*, selling_unit_id, quantity, user, operation_key, bottle_capacity_ml=None, reference="",
                  reason=""):
    """Sell an integer quantity of an active SellingUnit using its saved millilitre volume."""
    try:
        measure_count = Decimal(str(quantity))
        if not measure_count.is_finite() or measure_count != measure_count.to_integral_value() or measure_count < 1:
            raise InvalidOperation
    except (InvalidOperation, TypeError, ValueError):
        raise StockError("Measure quantity must be a positive whole number.") from None
    selling_unit = SellingUnit.objects.select_for_update().select_related("product").get(pk=selling_unit_id)
    if not selling_unit.active or not selling_unit.product.active:
        raise StockError("That selling unit is inactive.")
    return sell_volume(
        product_id=selling_unit.product_id,
        volume_ml=selling_unit.volume_ml * int(measure_count),
        user=user,
        operation_key=operation_key,
        bottle_capacity_ml=bottle_capacity_ml,
        reference=reference,
        reason=reason)


@transaction.atomic
def sell_volume(*, product_id, volume_ml, user, operation_key, bottle_capacity_ml=None, reference="", reason=""):
    """Deduct liquid from oldest opened bottles first, opening sealed bottles as needed."""
    requested = _volume(volume_ml, positive=True)
    key = _operation_key(operation_key)
    product = _lock_product(product_id)
    _ensure_operation_unused(key)
    bottles = list(Bottle.objects.select_for_update().filter(
        product=product, status=Bottle.OPENED, remaining_ml__gt=0).order_by("opened_at", "pk"))
    available_open_ml = sum((bottle.remaining_ml for bottle in bottles), Decimal("0.00"))
    needed_ml = max(requested - available_open_ml, Decimal("0.00"))
    capacity = _volume(bottle_capacity_ml, positive=True) if bottle_capacity_ml is not None else _known_capacity(product.pk)
    if needed_ml and capacity is None:
        raise StockError("Bottle capacity is required before opening the first bottle.")
    sealed_needed = int((needed_ml / capacity).to_integral_value(rounding="ROUND_CEILING")) if needed_ml else 0
    if sealed_needed > product.stock_qty:
        total_available = available_open_ml + product.stock_qty * (capacity or Decimal("0.00"))
        raise StockError(f"Only {total_available} ml of '{product.name}' are available.")

    movements = []
    sequence = 0
    remaining_to_sell = requested
    for bottle in bottles:
        if remaining_to_sell <= 0:
            break
        amount = min(bottle.remaining_ml, remaining_to_sell)
        movements.append(_change_bottle_volume(
            product=product, bottle=bottle, volume_ml=-amount, movement_type=mr.PARTIAL_SALE,
            user=user, operation_key=key, operation_sequence=sequence, reference=reference, reason=reason))
        sequence += 1
        remaining_to_sell -= amount

    while remaining_to_sell > 0:
        bottle, opening_movement = _open_bottle_locked(
            product=product, capacity_ml=capacity, user=user, operation_key=key,
            operation_sequence=sequence, reference=reference, reason="Bottle opened for partial sale")
        movements.append(opening_movement)
        sequence += 1
        amount = min(bottle.remaining_ml, remaining_to_sell)
        movements.append(_change_bottle_volume(
            product=product, bottle=bottle, volume_ml=-amount, movement_type=mr.PARTIAL_SALE,
            user=user, operation_key=key, operation_sequence=sequence, reference=reference, reason=reason))
        sequence += 1
        remaining_to_sell -= amount

    shop = ShopSettings.load()
    if not shop.costing_locked:
        shop.costing_locked = True
        shop.save(update_fields=["costing_locked"])
    return movements


def _record_wastage(*, product_id, volume_ml, user, operation_key, reason, reference="", approval_token=None,
                    ip=None):
    requested = _volume(volume_ml, positive=True)
    key = _operation_key(operation_key)
    note = (reason or "").strip()
    if not note:
        raise StockError("A reason is required.")
    if not user.can("stock.adjust"):
        raise StockError("You are not allowed to record volume wastage.")
    with transaction.atomic():
        product = _lock_product(product_id)
        _ensure_operation_unused(key)
        approver = _approval_for(user, "stock_adjust", product.pk, approval_token)
        bottles = list(Bottle.objects.select_for_update().filter(
            product=product, status=Bottle.OPENED, remaining_ml__gt=0).order_by("opened_at", "pk"))
        available = sum((bottle.remaining_ml for bottle in bottles), Decimal("0.00"))
        if requested > available:
            raise StockError(f"Only {available} ml are in opened bottles; sealed bottles are not wasted automatically.")
        remaining_to_remove = requested
        movements = []
        for sequence, bottle in enumerate(bottles):
            if remaining_to_remove <= 0:
                break
            amount = min(bottle.remaining_ml, remaining_to_remove)
            movements.append(_change_bottle_volume(
                product=product, bottle=bottle, volume_ml=-amount, movement_type=mr.VOLUME_WASTAGE,
                user=user, operation_key=key, operation_sequence=sequence, reference=reference, reason=note))
            remaining_to_remove -= amount
        audit.record("stock.volume_wasted", actor=user, approver=approver, object_type="product",
                     object_ref=product.pk, previous={"opened_volume_ml": str(available)},
                     new={"wasted_volume_ml": str(requested)}, reason=note, ip=ip)
        return movements


def record_wastage(*, product_id, volume_ml, user, operation_key, reason, reference="", approval_token=None, ip=None):
    """Record lost liquid across opened bottles; never opens sealed bottles to cover wastage."""
    return _record_wastage(product_id=product_id, volume_ml=volume_ml, user=user, operation_key=operation_key,
                           reason=reason, reference=reference, approval_token=approval_token, ip=ip)


def record_spillage(*, product_id, volume_ml, user, operation_key, reason="Spillage", reference="",
                    approval_token=None, ip=None):
    """Record a spillage using the same audited volume-wastage ledger."""
    return record_wastage(product_id=product_id, volume_ml=volume_ml, user=user, operation_key=operation_key,
                          reason=reason, reference=reference, approval_token=approval_token, ip=ip)


def adjust_bottle_volume(*, bottle_id, volume_ml, user, operation_key, reason, reference="",
                         approval_token=None, ip=None):
    """Reconcile a tracked opened/empty bottle; positive adjustments cannot exceed its capacity."""
    adjustment = _volume(volume_ml, allow_zero=False)
    key = _operation_key(operation_key)
    note = (reason or "").strip()
    if not note:
        raise StockError("A reason is required.")
    if not user.can("stock.adjust"):
        raise StockError("You are not allowed to adjust stock.")
    with transaction.atomic():
        product_id = Bottle.objects.values_list("product_id", flat=True).get(pk=bottle_id)
        product = _lock_product(product_id)
        bottle = Bottle.objects.select_for_update().get(pk=bottle_id, product=product)
        _ensure_operation_unused(key)
        approver = _approval_for(user, "stock_adjust", product.pk, approval_token)
        previous = bottle.remaining_ml
        movement = _change_bottle_volume(
            product=product, bottle=bottle, volume_ml=adjustment, movement_type=mr.VOLUME_ADJUST,
            user=user, operation_key=key, operation_sequence=0, reference=reference, reason=note)
        audit.record("stock.volume_adjusted", actor=user, approver=approver, object_type="product",
                     object_ref=product.pk, previous={"bottle_remaining_ml": str(previous)},
                     new={"bottle_remaining_ml": str(bottle.remaining_ml)}, reason=note, ip=ip)
        return movement


@transaction.atomic
def partial_stock(product_id, *, bottle_capacity_ml=None):
    """Return sealed, opened and empty bottle counts plus remaining liquid in millilitres."""
    product = _lock_product(product_id)
    capacity = _volume(bottle_capacity_ml, positive=True) if bottle_capacity_ml is not None else _known_capacity(product.pk)
    opened = Bottle.objects.filter(product=product, status=Bottle.OPENED)
    opened_count = opened.count()
    opened_volume = sum(opened.values_list("remaining_ml", flat=True), Decimal("0.00"))
    empty_count = Bottle.objects.filter(product=product, status=Bottle.EMPTY).count()
    if product.stock_qty and capacity is None:
        raise StockError("Bottle capacity is required to calculate sealed-stock volume.")
    sealed_volume = product.stock_qty * capacity if capacity is not None else Decimal("0.00")
    return {
        "sealed_bottles": product.stock_qty,
        "opened_bottles": opened_count,
        "empty_bottles": empty_count,
        "opened_volume_ml": opened_volume,
        "sealed_volume_ml": sealed_volume,
        "total_volume_ml": sealed_volume + opened_volume,
    }


def bottle_overview(product_id):
    """Read-only counts for the manager screen: sealed, opened and empty bottles and the liquid left.

    Unlike partial_stock this takes no row lock and never raises: when no bottle has ever been opened the
    bottle size is unknown, so the sealed volume is reported as None instead of guessed.
    """
    product = Product.objects.get(pk=product_id)
    capacity = _known_capacity(product.pk)
    opened = Bottle.objects.filter(product=product, status=Bottle.OPENED)
    opened_volume = sum(opened.values_list("remaining_ml", flat=True), Decimal("0.00"))
    sealed_volume = product.stock_qty * capacity if capacity is not None else (None if product.stock_qty else Decimal("0.00"))
    total = None if sealed_volume is None else sealed_volume + opened_volume
    return {
        "sealed_bottles": product.stock_qty,
        "opened_bottles": opened.count(),
        "empty_bottles": Bottle.objects.filter(product=product, status=Bottle.EMPTY).count(),
        "opened_volume_ml": opened_volume,
        "bottle_capacity_ml": capacity,
        "sealed_volume_ml": sealed_volume,
        "total_volume_ml": total,
    }


# --- Manual adjustments (restricted: stock_adjust) ----------------------------------------------

def _approval_for(user, action, object_ref, token):
    """Return the approver (User) for a restricted action, or None if none is needed.

    The shop owner (manager) is the top authority, so a manager acting alone does not need a second
    person; that is recorded in the audit log. Everyone else needs a valid single-use token.
    """
    if not auth_services.needs_approval(user, action):
        return None
    try:
        return auth_services.consume_approval(token, action=action, requester=user,
                                              object_ref=str(object_ref)).approver
    except ApprovalError as exc:
        raise StockError(str(exc)) from exc


def adjust_stock(*, product_id, movement_type, quantity, user, reason, approval_token=None, reference="", ip=None):
    """Opening stock, adjustments, damaged, expired and supplier returns. Needs stock.adjust + approval."""
    if movement_type not in mr.MANUAL_TYPES:
        raise StockError("That movement type cannot be posted by hand.")
    if not user.can("stock.adjust"):
        raise StockError("You are not allowed to adjust stock.")
    if not (reason or "").strip():
        raise StockError("A reason is required.")
    with transaction.atomic():
        product = _lock_product(product_id)
        if movement_type == mr.OPENING and StockMovement.objects.filter(product=product).exists():
            raise StockError("Opening stock can only be recorded before any other stock movement.")
        approver = _approval_for(user, "stock_adjust", product.pk, approval_token)
        previous = product.stock_qty
        unit_cost = product.buying_cost if movement_type == mr.OPENING else None
        movement = post_movement(
            product_id=product.pk, movement_type=movement_type, quantity=quantity, user=user,
            reference=reference, reason=reason.strip(), unit_cost=unit_cost, approved_by=approver)
        audit.record("stock.adjusted", actor=user, approver=approver, object_type="product", object_ref=product.pk,
                     previous={"stock": previous}, new={"stock": movement.balance_after, "type": movement_type},
                     reason=reason.strip(), ip=ip)
    return movement


# --- Receiving ----------------------------------------------------------------------------------

def new_receiving(user, **fields):
    if not user.can("stock.receive"):
        raise StockError("You are not allowed to receive stock.")
    number = f"GRN-{CodeSequence.next('grn'):06d}"
    return Receiving.objects.create(number=number, created_by=user, **fields)


def add_receiving_item(receiving, user, *, product, packaging, qty_good, qty_damaged, qty_missing, cost_per_pack):
    if not user.can("stock.receive"):
        raise StockError("You are not allowed to receive stock.")
    receiving.refresh_from_db(fields=["status"])     # another screen may have submitted it meanwhile
    if not receiving.editable:
        raise StockError("This delivery is already submitted and can no longer be changed.")
    if packaging is not None and packaging.product_id != product.pk:
        raise StockError("That pack size belongs to a different product.")
    if qty_good + qty_damaged + qty_missing == 0:
        raise StockError("Enter a quantity received, damaged or missing.")
    pack_size = packaging.units_per_pack if packaging else 1
    if cost_per_pack is None:  # clerk left cost blank: use the manager's expected cost for this product
        cost_per_pack = costing.q2(product.buying_cost * pack_size)
    return ReceivingItem.objects.create(
        receiving=receiving, product=product, packaging=packaging, pack_size=pack_size, qty_good=qty_good,
        qty_damaged=qty_damaged, qty_missing=qty_missing, cost_per_pack=cost_per_pack)


def remove_receiving_item(receiving, item, user):
    if not user.can("stock.receive"):
        raise StockError("You are not allowed to receive stock.")
    if not receiving.editable or item.receiving_id != receiving.pk:
        raise StockError("This delivery can no longer be changed.")
    item.delete()


@transaction.atomic
def _post(receiving, poster):
    items = list(receiving.items.select_related("product").order_by("product_id", "id"))  # stable lock order
    for item in items:
        if item.good_units > 0:
            unit_cost = costing.unit_cost_from_pack(item.cost_per_pack, item.pack_size)
            post_movement(product_id=item.product_id, movement_type=mr.RECEIVE, quantity=item.good_units,
                          user=receiving.created_by, approved_by=poster if poster.pk != receiving.created_by_id else None,
                          reference=receiving.number, unit_cost=unit_cost, reason=receiving.delivery_note or "")
    receiving.status = Receiving.POSTED
    receiving.decided_by = poster
    receiving.decided_at = timezone.now()
    receiving.save(update_fields=["status", "decided_by", "decided_at"])
    return receiving


def submit_receiving(receiving_id, user, ip=None):
    """Clerk finishes the delivery. Posts straight away, or waits for approval if the shop requires it."""
    with transaction.atomic():
        receiving = Receiving.objects.select_for_update().get(pk=receiving_id)
        if not user.can("stock.receive"):
            raise StockError("You are not allowed to receive stock.")
        if receiving.status != Receiving.DRAFT:
            raise StockError("This delivery was already submitted.")
        if not receiving.items.exists():
            raise StockError("Add at least one product before submitting.")
        receiving.submitted_at = timezone.now()
        shop = ShopSettings.load()
        can_self_approve = user.can("stock.approve_receiving")
        if shop.receiving_requires_approval and not can_self_approve:
            receiving.status = Receiving.PENDING
            receiving.save(update_fields=["status", "submitted_at"])
            audit.record("receiving.submitted", actor=user, object_type="receiving", object_ref=receiving.number, ip=ip)
            return receiving
        receiving.save(update_fields=["submitted_at"])
        _post(receiving, user)
        audit.record("receiving.posted", actor=user, object_type="receiving", object_ref=receiving.number, ip=ip)
        return receiving


def decide_receiving(receiving_id, approver, *, approve, note="", ip=None):
    """Approve (posts to stock) or reject a delivery that is waiting."""
    with transaction.atomic():
        receiving = Receiving.objects.select_for_update().get(pk=receiving_id)
        if not approver.can("stock.approve_receiving"):
            raise StockError("You are not allowed to approve deliveries.")
        if receiving.status != Receiving.PENDING:
            raise StockError("This delivery is not waiting for approval.")
        if receiving.created_by_id == approver.pk and not approver.is_manager:
            raise StockError("You cannot approve a delivery you recorded yourself.")
        if approve:
            _post(receiving, approver)
            audit.record("receiving.approved", actor=receiving.created_by, approver=approver, object_type="receiving",
                         object_ref=receiving.number, ip=ip)
        else:
            if not note.strip():
                raise StockError("Say why the delivery is rejected.")
            receiving.status = Receiving.REJECTED
            receiving.decided_by, receiving.decided_at, receiving.decision_note = approver, timezone.now(), note.strip()[:250]
            receiving.save(update_fields=["status", "decided_by", "decided_at", "decision_note"])
            audit.record("receiving.rejected", actor=receiving.created_by, approver=approver, object_type="receiving",
                         object_ref=receiving.number, reason=note.strip(), ip=ip)
        return receiving
