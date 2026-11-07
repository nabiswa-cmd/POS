"""Sales rules for the POS cart and cash checkout. Views call these; they enforce permissions,
supervisor approvals, stock and the audit trail. Nothing here trusts the screen.

Approvals (accounts.services) are single-use tokens bound to an action, the requesting cashier and an
object reference. For cart lines the reference is rules.item_ref(sale_id, item_id); for a whole sale it is
rules.sale_ref(sale_id). The cashier's screen collects the token from the supervisor and sends it with the
request; the server consumes it inside the same database transaction as the change, so a failed change
does not burn the approval.
"""
from collections import defaultdict
from decimal import Decimal

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from accounts import services as auth_services
from accounts.approval_rules import ApprovalError
from catalog import services as catalog_services
from catalog.models import CodeSequence, Packaging, Product
from core import audit
from inventory import movement_rules as mr
from inventory import services as stock_services
from tills.models import CashierShift

from . import rules
from .models import Payment, Sale, SaleItem


class SaleError(Exception):
    """A rule was broken; the message is safe to show the cashier."""


class NotAllowed(SaleError):
    """The user lacks a permission (shown as 403 by the views)."""


# --- helpers ------------------------------------------------------------------------------------

def _need(user, code, message=None):
    if not user.can(code):
        raise NotAllowed(message or "You are not allowed to do that.")


def _rule(call, *args, **kwargs):
    try:
        return call(*args, **kwargs)
    except rules.RuleError as exc:
        raise SaleError(str(exc)) from exc


def open_shift_for(user, till_number):
    """The cashier's open shift on this till, or an error. The POS cannot sell without one."""
    shift = (CashierShift.objects.select_related("till", "authorized_by")
             .filter(till__number=till_number, cashier=user, closed_at__isnull=True).first()) if till_number else None
    if shift is None:
        raise SaleError("Open your shift on this till before selling.")
    return shift


def _shift_still_open(shift):
    if not CashierShift.objects.filter(pk=shift.pk, closed_at__isnull=True).exists():
        raise SaleError("Your shift is closed.")


def _approve(user, action, object_ref, token):
    """Consume a supervisor/manager approval for a restricted action. Returns the Approval or None
    when the manager's policy waives approval for this action."""
    try:
        return auth_services.require_approval(action, token, requester=user, object_ref=object_ref)
    except ApprovalError as exc:
        raise SaleError(str(exc)) from exc


def _locked_sale(user, shift, *, create=False):
    """The cashier's cart in progress, row-locked. Call inside transaction.atomic()."""
    sale = Sale.objects.filter(shift=shift, cashier=user, status=Sale.OPEN).first()
    if sale is None:
        if not create:
            raise SaleError("There is no sale in progress.")
        try:
            with transaction.atomic():
                sale = Sale.objects.create(till=shift.till, shift=shift, cashier=user, authorized_by=shift.authorized_by)
        except IntegrityError:
            sale = Sale.objects.get(shift=shift, status=Sale.OPEN)
    try:
        return Sale.objects.select_for_update().get(pk=sale.pk, status=Sale.OPEN, cashier=user)
    except Sale.DoesNotExist:
        raise SaleError("There is no sale in progress.")


def _item(sale, item_id):
    try:
        return sale.items.select_for_update().get(pk=int(item_id))
    except (SaleItem.DoesNotExist, TypeError, ValueError):
        raise SaleError("That item is not in the current sale.")


def _recalc(sale):
    items = list(sale.items.all())
    subtotal, discount, total = rules.totals((i.quantity, i.unit_price, i.discount) for i in items)
    sale.subtotal, sale.discount_total, sale.total = subtotal, discount, total
    sale.save(update_fields=["subtotal", "discount_total", "total"])
    return sale


def _check_stock(product, wanted_base):
    product.refresh_from_db(fields=["stock_qty", "active", "name", "base_unit"])
    if not product.active:
        raise SaleError(f"'{product.name}' is not on sale.")
    if wanted_base > product.stock_qty:
        raise SaleError(f"Only {max(product.stock_qty, 0)} {product.base_unit} of '{product.name}' in stock.")


def _in_cart_base(sale, product, exclude_item_id=None):
    total = 0
    for it in sale.items.filter(product=product):
        if it.pk != exclude_item_id:
            total += it.base_qty
    return total


# --- reading ------------------------------------------------------------------------------------

def active_sale(user, shift):
    return Sale.objects.filter(shift=shift, cashier=user, status=Sale.OPEN).first()


# --- building the cart ---------------------------------------------------------------------------

@transaction.atomic
def add_item(user, shift, *, code=None, product_id=None, packaging_id=None, quantity=1):
    """Scan or pick a product. Scanning the same product again adds to its line."""
    _need(user, "sales.make", "You are not allowed to use the POS.")
    _shift_still_open(shift)
    qty = _rule(rules.parse_quantity, quantity)

    if code:
        hit = catalog_services.find_by_code(code)
        if hit is None:
            raise SaleError("Code not found. Check the label or search by name.")
        product, packaging = hit.product, hit.packaging
    else:
        try:
            product = Product.objects.get(pk=int(product_id), active=True)
        except (Product.DoesNotExist, TypeError, ValueError):
            raise SaleError("Product not found.")
        packaging = None
        if packaging_id:
            packaging = Packaging.objects.filter(pk=packaging_id, product=product, active=True).first()
            if packaging is None:
                raise SaleError("That pack size is not available.")

    units = packaging.units_per_pack if packaging else 1
    price = packaging.price if packaging else product.selling_price
    if price <= 0:
        raise SaleError(f"'{product.name}' has no selling price. Ask the manager to set one.")

    sale = _locked_sale(user, shift, create=True)
    _check_stock(product, _in_cart_base(sale, product) + qty * units)

    line = (sale.items.select_for_update()
            .filter(product=product, packaging=packaging, discount=0, unit_price=price, list_price=price).first())
    if line:
        line.quantity += qty
        line.line_total = rules.line_total(line.quantity, line.unit_price, 0)
        line.save(update_fields=["quantity", "line_total"])
    else:
        line = SaleItem.objects.create(
            sale=sale, product=product, packaging=packaging, product_name=product.name,
            pack_name=packaging.name if packaging else "", units_per_sale_unit=units, quantity=qty,
            list_price=price, unit_price=price, discount=Decimal("0.00"),
            line_total=rules.line_total(qty, price, 0))
    _recalc(sale)
    return sale, line


@transaction.atomic
def set_quantity(user, shift, item_id, quantity, *, approval_token=None):
    """Raise a quantity freely. Lowering it removes scanned stock from the sale, so it needs approval."""
    _need(user, "sales.make")
    _shift_still_open(shift)
    sale = _locked_sale(user, shift)
    item = _item(sale, item_id)
    new_qty = _rule(rules.parse_quantity, quantity)
    old_qty = item.quantity
    if new_qty == old_qty:
        return sale, item
    approval = None
    if new_qty < old_qty:
        approval = _approve(user, "remove_item", rules.item_ref(sale.pk, item.pk), approval_token)
    else:
        _check_stock(item.product, _in_cart_base(sale, item.product, item.pk) + new_qty * item.units_per_sale_unit)
    item.quantity = new_qty
    item.line_total = _rule(rules.line_total, new_qty, item.unit_price, 0)
    item.discount = Decimal("0.00")          # a discount was worked out for the old quantity
    item.discount_approved_by = None
    item.save(update_fields=["quantity", "line_total", "discount", "discount_approved_by"])
    _recalc(sale)
    if approval:
        audit.record("sale.item_quantity_reduced", actor=user, approver=approval.approver, object_type="sale_item",
                     object_ref=item.pk, previous={"quantity": old_qty}, new={"quantity": new_qty},
                     reason=approval.reason, till_number=shift.till.number)
    return sale, item


@transaction.atomic
def remove_item(user, shift, item_id, *, approval_token=None, reason=""):
    """Take a scanned item out of the sale. Needs a supervisor/manager approval (when the policy requires it)."""
    _need(user, "sales.make")
    _shift_still_open(shift)
    sale = _locked_sale(user, shift)
    item = _item(sale, item_id)
    approval = _approve(user, "remove_item", rules.item_ref(sale.pk, item.pk), approval_token)
    why = approval.reason if approval else (reason or "").strip()
    if not why:
        raise SaleError("A reason is required to remove an item.")
    snapshot = {"product": item.product_name, "product_id": item.product_id, "quantity": item.quantity,
                "unit_price": str(item.unit_price), "line_total": str(item.line_total)}
    item.delete()
    _recalc(sale)
    audit.record("sale.item_removed", actor=user, approver=approval.approver if approval else None,
                 object_type="sale", object_ref=sale.pk, previous=snapshot, reason=why, till_number=shift.till.number)
    return sale


@transaction.atomic
def apply_discount(user, shift, item_id, *, amount=None, percent=None, approval_token=None):
    """Set the discount on one line (replacing any earlier one). Small discounts by people allowed to give
    them need nothing more; anything above the ordinary limit, or below the minimum price, needs approval."""
    _need(user, "sales.make")
    _shift_still_open(shift)
    sale = _locked_sale(user, shift)
    item = _item(sale, item_id)
    gross = rules.line_gross(item.quantity, item.unit_price)
    if percent not in (None, ""):
        discount = _rule(rules.discount_from_percent, percent, gross)
    else:
        discount = _rule(rules.parse_money, amount, field="Discount", allow_zero=True)
    new_total = _rule(rules.line_total, item.quantity, item.unit_price, discount)   # also rejects discount > gross

    min_price = item.product.min_selling_price
    if item.packaging_id and min_price is not None:
        min_price = rules.money(min_price * item.units_per_sale_unit)
    needs = rules.discount_needs_approval(discount, gross, settings.ORDINARY_DISCOUNT_MAX_PERCENT,
                                          quantity=item.quantity, min_unit_price=min_price)
    approval = None
    if discount > 0 and (needs or not user.can("discount.apply")):
        if not user.can("discount.apply") and not auth_services.is_required("exceptional_discount"):
            raise NotAllowed("You are not allowed to give discounts.")
        approval = _approve(user, "exceptional_discount", rules.item_ref(sale.pk, item.pk), approval_token)
    previous = {"discount": str(item.discount)}
    item.discount, item.line_total = discount, new_total
    item.discount_approved_by = approval.approver if approval else None
    item.save(update_fields=["discount", "line_total", "discount_approved_by"])
    _recalc(sale)
    if discount != Decimal(previous["discount"]):
        audit.record("sale.discount_applied", actor=user, approver=approval.approver if approval else None,
                     object_type="sale_item", object_ref=item.pk, previous=previous,
                     new={"discount": str(discount), "percent": str(rules.discount_percent(discount, gross))},
                     reason=approval.reason if approval else "Within ordinary discount limit",
                     till_number=shift.till.number)
    return sale, item


@transaction.atomic
def change_price(user, shift, item_id, new_price, *, approval_token=None):
    """Change the unit price of one line during checkout. Always a supervisor/manager decision."""
    _need(user, "sales.make")
    _shift_still_open(shift)
    sale = _locked_sale(user, shift)
    item = _item(sale, item_id)
    price = _rule(rules.parse_money, new_price, field="Price")
    if not user.can("products.change_price") and not auth_services.is_required("price_change"):
        raise NotAllowed("You are not allowed to change prices.")
    approval = None
    if not user.can("products.change_price"):
        approval = _approve(user, "price_change", rules.item_ref(sale.pk, item.pk), approval_token)
    previous = {"unit_price": str(item.unit_price), "discount": str(item.discount)}
    item.unit_price, item.discount = price, Decimal("0.00")
    item.discount_approved_by = None
    item.line_total = rules.line_total(item.quantity, price, 0)
    item.price_approved_by = approval.approver if approval else None
    item.save(update_fields=["unit_price", "discount", "discount_approved_by", "line_total", "price_approved_by"])
    _recalc(sale)
    audit.record("sale.price_changed", actor=user, approver=approval.approver if approval else None,
                 object_type="sale_item", object_ref=item.pk, previous=previous, new={"unit_price": str(price)},
                 reason=approval.reason if approval else "Changed by a user with price permission",
                 till_number=shift.till.number)
    return sale, item


@transaction.atomic
def set_customer(user, shift, name):
    _need(user, "sales.make")
    sale = _locked_sale(user, shift, create=True)
    cleaned = " ".join("".join(ch for ch in str(name or "") if ch.isprintable()).split())[:120]
    sale.customer_name = cleaned
    sale.save(update_fields=["customer_name"])
    return sale


# --- hold, resume, void --------------------------------------------------------------------------

@transaction.atomic
def hold_sale(user, shift, label=""):
    _need(user, "sales.hold", "You are not allowed to hold sales.")
    _shift_still_open(shift)
    sale = _locked_sale(user, shift)
    if not sale.items.exists():
        raise SaleError("There is nothing to hold.")
    sale.status, sale.held_at = Sale.HELD, timezone.now()
    sale.hold_label = " ".join(str(label or "").split())[:60]
    sale.save(update_fields=["status", "held_at", "hold_label"])
    audit.record("sale.held", actor=user, object_type="sale", object_ref=sale.pk, till_number=shift.till.number)
    return sale


def held_sales(user, shift):
    _need(user, "sales.hold", "You are not allowed to retrieve held sales.")
    return Sale.objects.filter(till=shift.till, status=Sale.HELD).select_related("cashier").prefetch_related("items")


@transaction.atomic
def resume_held(user, shift, sale_id):
    """Bring a held sale back. Prices stay as they were when the items were scanned; stock is checked at checkout."""
    _need(user, "sales.hold", "You are not allowed to retrieve held sales.")
    _shift_still_open(shift)
    try:
        held = Sale.objects.select_for_update().get(pk=int(sale_id), status=Sale.HELD, till=shift.till)
    except (Sale.DoesNotExist, TypeError, ValueError):
        raise SaleError("That held sale is not available.")
    if held.cashier_id != user.pk and not user.can("sales.view_all"):
        raise NotAllowed("That sale was held by another cashier.")
    current = Sale.objects.select_for_update().filter(shift=shift, status=Sale.OPEN).first()
    if current is not None:
        if current.items.exists():
            raise SaleError("Hold or finish the current sale first.")
        current.delete()                          # an empty cart has no history worth keeping
    held.status, held.cashier, held.shift = Sale.OPEN, user, shift
    held.authorized_by = shift.authorized_by
    held.save(update_fields=["status", "cashier", "shift", "authorized_by"])
    audit.record("sale.resumed", actor=user, object_type="sale", object_ref=held.pk, till_number=shift.till.number)
    return held


@transaction.atomic
def void_sale(user, shift, *, approval_token=None, reason=""):
    """Cancel the sale in progress before payment. Needs approval when it already has items."""
    _need(user, "sales.make")
    sale = _locked_sale(user, shift)
    if not sale.items.exists():
        return None                               # nothing to void
    approval = _approve(user, "void_sale", rules.sale_ref(sale.pk), approval_token)
    why = approval.reason if approval else (reason or "").strip()
    if not why:
        raise SaleError("A reason is required to void a sale.")
    summary = [{"product": i.product_name, "quantity": i.quantity, "line_total": str(i.line_total)}
               for i in sale.items.all()]
    sale.status, sale.voided_at, sale.voided_by = Sale.VOIDED, timezone.now(), user
    sale.void_approved_by = approval.approver if approval else None
    sale.void_reason = why[:250]
    sale.save(update_fields=["status", "voided_at", "voided_by", "void_approved_by", "void_reason"])
    audit.record("sale.voided", actor=user, approver=approval.approver if approval else None, object_type="sale",
                 object_ref=sale.pk, previous={"items": summary, "total": str(sale.total)}, reason=why,
                 till_number=shift.till.number)
    return sale


# --- checkout ------------------------------------------------------------------------------------

@transaction.atomic
def checkout_cash(user, shift, tendered):
    """Finish the sale as a cash sale. Stock, cost basis, payment and the sale number are written in ONE
    transaction: if any product is short, nothing is saved and the cart is left as it was."""
    _need(user, "sales.make")
    _need(user, "payments.accept", "You are not allowed to accept payments.")
    _shift_still_open(shift)
    sale = _locked_sale(user, shift)
    items = list(sale.items.select_related("product"))
    if not items:
        raise SaleError("The sale is empty.")
    subtotal, discount, total = rules.totals((i.quantity, i.unit_price, i.discount) for i in items)
    if total <= 0:
        raise SaleError("The total must be more than zero.")
    tendered = _rule(rules.parse_money, tendered, field="Cash received")
    change = _rule(rules.change_due, total, tendered)

    number_seq = CodeSequence.next(f"sale-{shift.till.number}-{timezone.localdate():%Y%m%d}")
    number = rules.sale_number(shift.till.number, timezone.localdate(), number_seq)

    # One stock movement per product, in ascending product id (so two tills cannot deadlock). Several
    # lines of the same product are taken together, so the last bottle can only be sold once.
    by_product = defaultdict(list)
    for item in items:
        by_product[item.product_id].append(item)
    for product_id in sorted(by_product):
        lines = by_product[product_id]
        base_total = sum(i.base_qty for i in lines)
        try:
            movement = stock_services.post_movement(
                product_id=product_id, movement_type=mr.SALE, quantity=-base_total, user=user, reference=number)
        except stock_services.StockError as exc:
            raise SaleError(str(exc)) from exc
        for line, cost in zip(lines, rules.allocate_cost(movement.total_cost, [i.base_qty for i in lines])):
            line.cost_total = cost
            line.save(update_fields=["cost_total"])      # the sale is still OPEN here, so this is allowed

    now = timezone.now()
    Payment.objects.create(sale=sale, method=Payment.CASH, status=Payment.CONFIRMED, amount=total,
                           tendered=tendered, change_given=change, received_by=user)
    sale.number, sale.status, sale.completed_at = number, Sale.COMPLETED, now
    sale.subtotal, sale.discount_total, sale.total = subtotal, discount, total
    sale.save(update_fields=["number", "status", "completed_at", "subtotal", "discount_total", "total"])
    audit.record("sale.completed", actor=user, object_type="sale", object_ref=number,
                 new={"total": str(total), "method": "CASH", "items": len(items)}, till_number=shift.till.number)
    return sale


# --- receipts ------------------------------------------------------------------------------------

@transaction.atomic
def mark_original_printed(user, sale_id):
    """First print of a receipt: only the cashier who made the sale, once. Later prints are duplicates."""
    sale = Sale.objects.select_for_update().filter(pk=sale_id, status=Sale.COMPLETED).first()
    if sale is None:
        raise SaleError("Sale not found.")
    if sale.cashier_id != user.pk or not user.can("sales.view_own"):
        raise NotAllowed("You can only print the receipt of your own sale.")
    if sale.printed_at is not None:
        raise SaleError("The original receipt was already printed. Ask a supervisor for a reprint.")
    sale.printed_at = timezone.now()
    sale.save(update_fields=["printed_at"])
    return sale


@transaction.atomic
def mark_reprint(user, sale_id, ip=None):
    """A reprint is always a marked duplicate and always audited."""
    _need(user, "receipts.reprint", "You are not allowed to reprint receipts.")
    sale = Sale.objects.select_for_update().filter(pk=sale_id, status=Sale.COMPLETED).first()
    if sale is None:
        raise SaleError("Sale not found.")
    sale.reprint_count += 1
    sale.save(update_fields=["reprint_count"])
    audit.record("receipt.reprinted", actor=user, object_type="sale", object_ref=sale.number,
                 new={"reprint_number": sale.reprint_count}, ip=ip, till_number=sale.till.number)
    return sale


# --- shift summary (F11) -------------------------------------------------------------------------

def shift_summary(shift):
    """A cashier's running view of their shift. Cash-up and closing arrive in Phase 5."""
    done = Sale.objects.filter(shift=shift, status=Sale.COMPLETED)
    cash = Payment.objects.filter(sale__in=done, method=Payment.CASH).aggregate(t=Sum("amount"))["t"] or Decimal("0.00")
    return {"sales_count": done.count(), "sales_total": str(done.aggregate(t=Sum("total"))["t"] or Decimal("0.00")),
            "cash_sales": str(cash), "opening_float": str(shift.opening_float),
            "cash_in_drawer_expected": str(shift.opening_float + cash)}
