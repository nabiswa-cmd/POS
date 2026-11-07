"""Stock rules. Every change to Product.stock_qty goes through post_movement()."""
from django.db import transaction
from django.utils import timezone

from accounts import services as auth_services
from accounts.approval_rules import ApprovalError
from catalog.models import CodeSequence, Packaging, Product
from core import audit
from core.models import ShopSettings

from . import costing, movement_rules as mr
from .models import Receiving, ReceivingItem, StockLot, StockMovement


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


# --- Manual adjustments (restricted: stock_adjust) ----------------------------------------------

def _approval_for(user, action, object_ref, token):
    """Return the approver (User) for a restricted action, or None if none is needed.

    The shop owner (manager) is the top authority, so a manager acting alone does not need a second
    person; that is recorded in the audit log. Everyone else needs a valid single-use token.
    """
    if user.is_manager or not auth_services.is_required(action):
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

def _fresh(receiving):
    return Receiving.objects.get(pk=receiving.pk)


def new_receiving(user, **fields):
    if not user.can("stock.receive"):
        raise StockError("You are not allowed to receive stock.")
    number = f"GRN-{CodeSequence.next('grn'):06d}"
    return Receiving.objects.create(number=number, created_by=user, **fields)


def add_receiving_item(receiving, user, *, product, packaging, qty_good, qty_damaged, qty_missing, cost_per_pack):
    if not user.can("stock.receive"):
        raise StockError("You are not allowed to receive stock.")
    if not _fresh(receiving).editable:   # the caller's copy may be stale: re-read the status from the database
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
    if not _fresh(receiving).editable or item.receiving_id != receiving.pk:
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
