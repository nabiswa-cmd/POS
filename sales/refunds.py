"""Refunds: request -> approve -> pay back. The rules in one place:

* A refund is ALWAYS against one completed sale and never edits it. Whole sold-unit quantities (including
  configured measures) only, and never more than was sold or paid (earlier refunds of the same line are counted).
* A cashier can ask, but cannot approve their own request. Someone else with refund authority approves,
  either by signing in on the screen (the usual approval window) or because they are signed in themselves.
  The shop owner is the one exception (as for every other approval).
* Approving is the moment stock goes back (only lines marked resaleable, at the cost they left stock at).
  Approving is NOT paying: cash is paid when the cashier hands it over; M-PESA is paid when someone other
  than the requester records the real M-PESA payback code. Both are separate, audited steps.
* Cash refunds come out of the cashier's drawer figure; M-PESA refunds never touch it.
"""
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.db.models import Q, Sum
from django.utils import timezone

from accounts import services as auth_services
from accounts.approval_rules import ApprovalError
from catalog.models import CodeSequence
from core import audit
from core.models import ReceiptSettings, ShopSettings
from inventory import costing
from inventory import movement_rules as mr
from inventory import services as stock_services

from . import etims, receipt_text, refund_rules as rr, rules, tax
from . import services as sales_services
from .models import Payment, Refund, RefundItem, Sale, SaleItem
from .services import ApprovalNeeded, NotAllowed, SaleError

LIVE = (Refund.REQUESTED, Refund.APPROVED, Refund.PAID)     # refunds that still count against the sale


def ref(refund_id):
    """What an approval token for a refund is bound to (client and server must agree)."""
    return f"refund:{int(refund_id)}"


def _rule(call, *args, **kwargs):
    try:
        return call(*args, **kwargs)
    except rules.RuleError as exc:
        raise SaleError(str(exc)) from exc


def can_approve(user):
    return auth_services.is_exempt(user) or user.can("approve.refund")


# --- looking at a sale ---------------------------------------------------------------------------

def _refunded(item):
    row = RefundItem.objects.filter(sale_item=item, refund__status__in=LIVE).aggregate(q=Sum("quantity"), a=Sum("amount"))
    return int(row["q"] or 0), rules.money(row["a"] or 0)


def sale_lines(sale):
    out = []
    for item in sale.items.all():
        qty, amount = _refunded(item)
        left = item.quantity - qty
        volume_per_unit = item.volume_per_unit_ml if item.is_partial else None
        out.append({"item_id": item.pk, "name": item.product_name, "pack": item.pack_name,
                    "selling_unit": item.selling_unit_name, "sold": item.quantity, "left": left,
                    "volume_per_unit_ml": str(volume_per_unit) if volume_per_unit is not None else None,
                    "sold_volume_ml": str(item.total_volume_ml) if item.is_partial else None,
                    "left_volume_ml": str(volume_per_unit * left) if volume_per_unit is not None else None,
                    "refunded_volume_ml": str(volume_per_unit * qty) if volume_per_unit is not None else None,
                    "unit_price": str(item.unit_price), "line_total": str(item.line_total),
                    "refunded": qty, "refunded_amount": str(amount)})
    return out


def find_sale(user, query):
    """A completed sale by its receipt number (full, or just the last digits). Cashiers see only their own."""
    sales_services._need(user, "refunds.request", "You are not allowed to start refunds.")
    q = str(query or "").strip().upper()
    if len(q) < 3:
        raise SaleError("Type at least the last 3 characters of the receipt number.")
    qs = Sale.objects.filter(status=Sale.COMPLETED, number__iendswith=q).select_related("till", "cashier")
    if not user.can("sales.view_all"):
        qs = qs.filter(cashier=user)
    found = list(qs.order_by("-completed_at")[:5])
    if not found:
        raise SaleError("No completed sale found with that receipt number.")
    if len(found) > 1:
        raise SaleError("More than one sale matches. Type more of the receipt number.")
    return found[0]


# --- requesting ----------------------------------------------------------------------------------

def _request_key(value):
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise SaleError("This refund request is not valid. Reopen the refund form and try again.")
    key = value.strip()
    if not key or len(key) > 80:
        raise SaleError("This refund request is not valid. Reopen the refund form and try again.")
    return key


def _same_request(refund, user, sale, reason, method, wanted):
    existing = {
        line.sale_item_id: (line.quantity, line.requested_restock)
        for line in refund.items.all()
    }
    return (refund.sale_id == sale.pk and refund.requested_by_id == user.pk
            and refund.reason == reason and refund.method == method
            and existing == wanted)


@transaction.atomic
def request_refund(user, shift, sale_id, lines, reason, method, restock=True, request_key=None):
    """lines: [{"item_id": 5, "quantity": 1, "restock": True?}]. Returns the new REQUESTED refund."""
    sales_services._need(user, "refunds.request", "You are not allowed to start refunds.")
    sales_services._shift_still_open(shift)
    sale = Sale.objects.select_for_update().filter(pk=sale_id, status=Sale.COMPLETED).first()
    if sale is None:
        raise SaleError("Sale not found.")
    if sale.cashier_id != user.pk and not user.can("sales.view_all"):
        raise NotAllowed("You can only refund your own sales.")
    reason = _rule(rr.check_reason, reason)
    method = _rule(rr.check_method, method)
    request_key = _request_key(request_key)
    wanted = {}
    seen = set()
    for ln in lines or []:
        try:
            raw_item_id = ln.get("item_id")
            raw_qty = ln.get("quantity")
            item_id_value = Decimal(str(raw_item_id))
            quantity_value = Decimal(str(raw_qty or 0))
            if (not item_id_value.is_finite() or item_id_value != item_id_value.to_integral_value()
                    or item_id_value < 1 or not quantity_value.is_finite()
                    or quantity_value != quantity_value.to_integral_value() or quantity_value < 0):
                raise InvalidOperation
            item_id, qty = int(item_id_value), int(quantity_value)
        except (InvalidOperation, TypeError, ValueError, AttributeError):
            raise SaleError("A refund line is not valid.")
        if item_id in seen:
            raise SaleError("The same line was chosen twice.")
        seen.add(item_id)
        if qty > 0:
            wanted[item_id] = (qty, bool(ln.get("restock", restock)))
    if not wanted:
        raise SaleError("Choose at least one item to refund.")
    items = {i.pk: i for i in SaleItem.objects.filter(sale=sale, pk__in=wanted)}
    if len(items) != len(wanted):
        raise SaleError("One of those items is not on this sale.")
    if request_key:
        existing = Refund.objects.select_for_update().filter(request_key=request_key).first()
        if existing:
            if _same_request(existing, user, sale, reason, method,
                             {item_id: (qty, back) for item_id, (qty, back) in wanted.items()}):
                return existing
            raise SaleError("This refund request key was already used for a different request.")

    parts, total, tax_total = [], Decimal("0.00"), Decimal("0.00")
    for item_id in sorted(wanted):
        item, (qty, back) = items[item_id], wanted[item_id]
        done_qty, done_amount = _refunded(item)
        amount = _rule(rr.line_refund, item.line_total, item.quantity, done_qty, done_amount, qty)
        taxable, vat = tax.split_line(amount, item.tax_rate) if item.tax_rate else (amount, Decimal("0.00"))
        volume = item.volume_per_unit_ml * qty if item.is_partial else None
        parts.append((item, qty, amount, taxable, vat, back, volume))
        total += amount
        tax_total += vat
    if method == Refund.MPESA:
        paid = Payment.objects.filter(sale=sale, method=Payment.MPESA).aggregate(t=Sum("amount"))["t"] or 0
        back = Refund.objects.filter(sale=sale, method=Refund.MPESA, status__in=LIVE).aggregate(t=Sum("total"))["t"] or 0
        _rule(rr.check_mpesa_room, total, paid, back)

    number = rr.refund_number(timezone.localdate(), CodeSequence.next(f"refund-{timezone.localdate():%Y%m%d}"))
    try:
        with transaction.atomic():
            refund = Refund.objects.create(
                number=number, sale=sale, method=method, request_key=request_key, reason=reason,
                total=total, tax_total=tax_total, till=shift.till, shift=shift, requested_by=user)
    except IntegrityError:
        if request_key:
            existing = Refund.objects.select_for_update().filter(request_key=request_key).first()
            if existing and _same_request(existing, user, sale, reason, method,
                                          {item_id: (qty, back) for item_id, (qty, back) in wanted.items()}):
                return existing
        raise SaleError("This refund request key is already in use.") from None
    for item, qty, amount, taxable, vat, back, volume in parts:
        RefundItem.objects.create(refund=refund, sale_item=item, product_name=item.product_name, quantity=qty, amount=amount,
                                  selling_unit_name=item.selling_unit_name if item.is_partial else "",
                                  requested_restock=back, volume_ml=volume, tax_class=item.tax_class,
                                  tax_rate=item.tax_rate, taxable_amount=taxable, tax_amount=vat, restock=back)
    audit.record("refund.requested", actor=user, object_type="refund", object_ref=number,
                 new={"sale": sale.number, "total": str(total), "method": method,
                      "lines": [{"sale_item": item.pk, "quantity": qty, "volume_ml": str(volume) if volume else None,
                                 "amount": str(amount), "restock": back}
                                for item, qty, amount, _, _, back, volume in parts]}, reason=reason,
                 till_number=shift.till.number)
    return refund


# --- approving / rejecting / cancelling ----------------------------------------------------------

def _locked(refund_id, *statuses):
    refund = Refund.objects.select_for_update().select_related("sale", "requested_by").filter(pk=refund_id).first()
    if refund is None:
        raise SaleError("Refund not found.")
    if statuses and refund.status not in statuses:
        raise SaleError(f"This refund is already {refund.get_status_display().lower()}.")
    return refund


@transaction.atomic
def approve_refund(user, refund_id, approval_token=None, restock=None):
    """restock: optional {sale_item_id: bool} so the approver decides what is resaleable. Returns the refund."""
    refund = _locked(refund_id, Refund.REQUESTED)
    object_ref = ref(refund.pk)
    if auth_services.is_exempt(user):
        approver, approval = user, None
    elif user.pk != refund.requested_by_id and user.can("approve.refund"):
        approver, approval = user, None                       # a supervisor/manager signed in approves directly
    else:
        if not approval_token:
            raise ApprovalNeeded("refund", object_ref, "A supervisor or manager must approve this refund.")
        try:
            approval = auth_services.consume_approval(approval_token, action="refund", requester=user, object_ref=object_ref)
        except ApprovalError as exc:
            raise SaleError(str(exc)) from exc
        approver = approval.approver
    if approver.pk == refund.requested_by_id and not auth_services.is_exempt(approver):
        raise SaleError("The person who asked for a refund cannot approve it.")

    items = list(refund.items.select_related("sale_item").order_by("sale_item__product_id", "id"))
    overrides = {int(k): bool(v) for k, v in (restock or {}).items()}
    for it in items:
        if it.sale_item_id in overrides:
            it.restock = overrides[it.sale_item_id]
        if it.restock:
            si = it.sale_item
            if si.is_partial:
                expected_volume = si.volume_per_unit_ml * it.quantity
                if it.volume_ml != expected_volume or it.selling_unit_name != si.selling_unit_name:
                    raise SaleError("The partial refund snapshot does not match the original sale line.")
                try:
                    stock_services.restore_refunded_volume(
                        refund_item_id=it.pk, user=user, approved_by=approver)
                except stock_services.StockError as exc:
                    raise SaleError(str(exc)) from exc
            else:
                if it.volume_ml is not None or it.selling_unit_name:
                    raise SaleError("A full-unit refund cannot contain a partial volume snapshot.")
                base = it.quantity * si.units_per_sale_unit
                unit_cost = costing.q4(si.cost_total / si.base_qty) if si.cost_total and si.base_qty else None
                try:
                    stock_services.post_movement(
                        product_id=si.product_id, movement_type=mr.REFUND_RETURN, quantity=base,
                        user=user, reference=refund.number, reason=f"Refund of {refund.sale.number}",
                        unit_cost=unit_cost, approved_by=approver)
                except stock_services.StockError as exc:
                    raise SaleError(str(exc)) from exc
            it.restocked = True
        it.save(update_fields=["restock", "restocked"])

    refund.status, refund.approved_by, refund.approved_at = Refund.APPROVED, approver, timezone.now()
    refund.approval_reason = approval.reason if approval else "Approved on screen"
    refund.save(update_fields=["status", "approved_by", "approved_at", "approval_reason"])
    audit.record("refund.approved", actor=user, approver=approver, object_type="refund", object_ref=refund.number,
                 new={"sale": refund.sale.number, "total": str(refund.total), "method": refund.method,
                      "restocked": [{"product": i.product_name, "selling_unit": i.selling_unit_name,
                                     "quantity": i.quantity, "volume_ml": str(i.volume_ml) if i.volume_ml else None,
                                     "amount": str(i.amount)}
                                    for i in items if i.restocked]},
                 reason=refund.approval_reason, till_number=refund.till.number)
    # The tax paperwork: a credit note for the invoice KRA signed. Created here so its number is gapless with the
    # approval; it is sent (and retried while offline) right after, never holding up the refund itself.
    if etims.create_credit_note(refund) is None and etims.enabled():
        audit.record("etims.credit_note_skipped", object_type="refund", object_ref=refund.number,
                     reason="The sale was never put through eTIMS, so there is no KRA invoice to credit.",
                     till_number=refund.till.number)
    return refund


@transaction.atomic
def reject_refund(user, refund_id, reason):
    refund = _locked(refund_id, Refund.REQUESTED)
    if not can_approve(user) or (user.pk == refund.requested_by_id and not auth_services.is_exempt(user)):
        raise NotAllowed("Only a supervisor or manager other than the requester can reject a refund.")
    reason = _rule(rr.check_reason, reason)
    refund.status, refund.rejected_by, refund.rejected_at, refund.reject_reason = Refund.REJECTED, user, timezone.now(), reason
    refund.save(update_fields=["status", "rejected_by", "rejected_at", "reject_reason"])
    audit.record("refund.rejected", actor=user, object_type="refund", object_ref=refund.number, reason=reason,
                 till_number=refund.till.number)
    return refund


@transaction.atomic
def cancel_refund(user, refund_id):
    """The cashier who asked changes their mind before anyone approved."""
    refund = _locked(refund_id, Refund.REQUESTED)
    if refund.requested_by_id != user.pk:
        raise NotAllowed("Only the person who asked for the refund can cancel it.")
    refund.status = Refund.CANCELLED
    refund.save(update_fields=["status"])
    audit.record("refund.cancelled", actor=user, object_type="refund", object_ref=refund.number, till_number=refund.till.number)
    return refund


# --- paying back ---------------------------------------------------------------------------------

@transaction.atomic
def pay_cash(user, shift, refund_id):
    """The cashier hands the money over. It leaves THIS shift's drawer figure (and only if the drawer can cover it)."""
    sales_services._need(user, "payments.accept", "You are not allowed to hand out refunds.")
    sales_services._shift_still_open(shift)
    refund = _locked(refund_id, Refund.APPROVED)
    if refund.method != Refund.CASH:
        raise SaleError("This refund goes back by M-PESA, not cash.")
    if refund.requested_by_id != user.pk and not can_approve(user):
        raise NotAllowed("Only the cashier who asked for the refund, or a supervisor, can hand over the cash.")
    expected = Decimal(sales_services.shift_summary(shift)["cash_in_drawer_expected"])
    if expected < refund.total:
        raise SaleError(f"There is only KES {expected:,.2f} cash expected in the drawer; the refund is KES {refund.total:,.2f}.")
    refund.status, refund.paid_by, refund.paid_at, refund.paid_shift = Refund.PAID, user, timezone.now(), shift
    refund.save(update_fields=["status", "paid_by", "paid_at", "paid_shift"])
    audit.record("refund.paid_cash", actor=user, object_type="refund", object_ref=refund.number,
                 new={"total": str(refund.total)}, till_number=shift.till.number)
    return refund


@transaction.atomic
def mark_mpesa_paid(user, refund_id, reference):
    """Someone other than the requester records the M-PESA code that proves the customer really was paid back."""
    refund = _locked(refund_id, Refund.APPROVED)
    if refund.method != Refund.MPESA:
        raise SaleError("This refund goes back in cash, not by M-PESA.")
    if not can_approve(user):
        raise NotAllowed("Only a supervisor or manager can record an M-PESA payback.")
    if user.pk == refund.requested_by_id and not auth_services.is_exempt(user):
        raise SaleError("Someone other than the person who asked for the refund must record the payback.")
    code = _rule(rr.check_payout_reference, reference)
    try:
        with transaction.atomic():
            refund.status, refund.paid_by, refund.paid_at, refund.pay_reference = Refund.PAID, user, timezone.now(), code
            refund.save(update_fields=["status", "paid_by", "paid_at", "pay_reference"])
    except IntegrityError:
        raise SaleError("That M-PESA code was already used for another refund.")
    audit.record("refund.paid_mpesa", actor=user, object_type="refund", object_ref=refund.number,
                 new={"total": str(refund.total), "code": code}, till_number=refund.till.number)
    return refund


# --- lists the screen shows ----------------------------------------------------------------------

def visible_refunds(user, shift):
    """Approvers see everything waiting; a cashier sees their own open ones and this shift's finished ones."""
    qs = Refund.objects.select_related("sale", "requested_by").prefetch_related("items")
    waiting = Q(status__in=(Refund.REQUESTED, Refund.APPROVED))
    if can_approve(user):
        qs = qs.filter(waiting | Q(shift=shift) | Q(paid_shift=shift))
    else:
        qs = qs.filter(Q(requested_by=user) & (waiting | Q(shift=shift)))
    return list(qs.distinct().order_by("-requested_at")[:25])


def etims_note(refund):
    """The refund's eTIMS credit note, or None (eTIMS off, or the sale was never on eTIMS)."""
    try:
        return refund.etims
    except Refund.etims.RelatedObjectDoesNotExist:
        return None


def refund_json(refund, user):
    approver = can_approve(user)
    mine = refund.requested_by_id == user.pk
    owner = auth_services.is_exempt(user)
    return {
        "id": refund.pk, "ref": ref(refund.pk), "number": refund.number, "sale": refund.sale.number,
        "status": refund.status, "status_label": refund.get_status_display(), "method": refund.method,
        "total": str(refund.total), "reason": refund.reason, "by": refund.requested_by.display_name,
        "etims": getattr(etims_note(refund), "status", ""),
        "time": timezone.localtime(refund.requested_at).strftime("%H:%M"),
        "items": [{"name": (f"{i.product_name} ({i.selling_unit_name})" if i.selling_unit_name else i.product_name),
                   "quantity": i.quantity, "volume_ml": str(i.volume_ml) if i.volume_ml is not None else None,
                   "amount": str(i.amount), "restock": i.restock}
                  for i in refund.items.all()],
        "can": {
            "approve": refund.status == Refund.REQUESTED,                         # anyone may open the approval window
            "reject": refund.status == Refund.REQUESTED and approver and (not mine or owner),
            "cancel": refund.status == Refund.REQUESTED and mine,
            "pay_cash": refund.status == Refund.APPROVED and refund.method == Refund.CASH and (mine or approver),
            "mark_mpesa": refund.status == Refund.APPROVED and refund.method == Refund.MPESA and approver and (not mine or owner),
        },
    }


# --- the refund receipt --------------------------------------------------------------------------

def _etims_block(refund):
    note = etims_note(refund)
    if note is None:
        return None
    return {"status": note.status, "invoice_no": note.invoice_no, "original_invoice_no": note.original_invoice_no,
            "scu_id": note.scu_id, "scu_receipt_no": note.scu_receipt_no, "scu_datetime": note.scu_datetime,
            "internal_data": note.internal_data, "signature": note.signature, "qr_text": note.qr_text}


def build_receipt(refund, *, duplicate=False):
    shop, cfg = ShopSettings.load(), ReceiptSettings.load()
    header = [shop.shop_name] + [x for x in (cfg.header_text, shop.address, shop.phone,
                                             f"KRA PIN: {shop.kra_pin}" if shop.kra_pin else "") if x]
    return {
        "number": refund.number, "sale": refund.sale.number, "status": refund.status, "method": refund.method,
        "date": timezone.localtime(refund.requested_at).strftime("%Y-%m-%d %H:%M"), "till": refund.till.name,
        "customer": refund.sale.customer_name, "reason": refund.reason, "header_lines": header,
        "requested_by": refund.requested_by.display_name,
        "approved_by": refund.approved_by.display_name if refund.approved_by_id else "",
        "pay_reference": refund.pay_reference,
        "etims": _etims_block(refund),
        "items": [{"name": (f"{i.product_name} ({i.selling_unit_name})"
                            if i.selling_unit_name else i.product_name),
                   "quantity": i.quantity, "volume_ml": str(i.volume_ml) if i.volume_ml is not None else None,
                   "amount": str(i.amount)} for i in refund.items.all()],
        "total": str(refund.total), "tax_total": str(refund.tax_total), "vat": refund.sale.vat_applied,
        "duplicate": duplicate, "copy_number": refund.print_count, "footer": cfg.footer_text, "paper_mm": cfg.paper_width_mm,
    }


def format_receipt(data):
    """The refund slip. It says CREDIT NOTE only when KRA's unit has really signed one; until then it is a plain
    refund slip, so it is never passed off as a tax document."""
    width = receipt_text.WIDTHS.get(int(data["paper_mm"]), 48)
    rule, out = "-" * width, []
    c, p, m, w = receipt_text._center, receipt_text._pair, receipt_text._m, receipt_text._wrap
    if data["duplicate"]:
        out += c("*** DUPLICATE ***", width) + c(f"COPY - Reprint #{data['copy_number']}", width) + [rule]
    for line in data["header_lines"]:
        out += c(line, width)
    et = data.get("etims")
    signed = bool(et and et["status"] == "SUBMITTED")
    out += [rule] + c("CREDIT NOTE" if signed else "REFUND SLIP", width) + [rule]
    out += [p("Refund:", data["number"], width), p("Sale:", data["sale"], width), p("Date:", data["date"], width),
            p("Till:", data["till"], width)]
    if data["customer"]:
        out += w(f"Customer: {data['customer']}", width)
    out += w(f"Reason: {data['reason']}", width) + [rule]
    for it in data["items"]:
        out += w(it["name"], width)
        returned = f"  {it['quantity']} returned"
        if it["volume_ml"] is not None:
            returned += f" ({Decimal(it['volume_ml']):g} ml)"
        out.append(p(returned, m(it["amount"]), width))
    out += [rule, p("TOTAL REFUND", m(data["total"]), width)]
    if data["vat"] and Decimal(data["tax_total"]) > 0:
        out.append(p("  of which VAT", m(data["tax_total"]), width))
    out.append(p("Paid back by", "CASH" if data["method"] == Refund.CASH else "M-PESA", width))
    if data["pay_reference"]:
        out += w(f"M-PESA ref: {data['pay_reference']}", width)
    out.append(rule)
    out += w(f"Requested by: {data['requested_by']}", width)
    if data["approved_by"]:
        out += w(f"Approved by: {data['approved_by']}", width)
    if data["status"] == Refund.APPROVED:
        out += c("APPROVED - NOT YET PAID BACK" if data["method"] == Refund.MPESA else "APPROVED - CASH TO BE HANDED OVER", width)
    out.append(rule)
    if signed:
        out += c("KRA eTIMS", width)
        out += [p("CU invoice no:", et["scu_receipt_no"], width), p("Credit note ref:", et["invoice_no"], width),
                p("Original invoice:", et["original_invoice_no"], width)]
        if et["scu_id"]:
            out += w(f"SCU ID: {et['scu_id']}", width)
        if et["scu_datetime"]:
            out += w(f"SCU time: {et['scu_datetime']}", width)
        out += w(f"Receipt signature: {et['signature']}", width)
        if et["internal_data"]:
            out += w(f"Internal data: {et['internal_data']}", width)
    elif et:
        out += c("eTIMS: awaiting KRA signature", width) + c("Not yet a valid credit note", width)
    else:
        out += c("Keep this slip. Not a tax document.", width)
    out += [rule] + c(data.get("footer") or "Thank you", width)
    return out


@transaction.atomic
def mark_printed(user, refund_id):
    """First print is the original; every later print is a marked duplicate. Returns (refund, duplicate)."""
    sales_services._need(user, "refunds.request", "You are not allowed to print refunds.")
    refund = Refund.objects.select_for_update(of=("self",)).select_related("sale", "till", "requested_by", "approved_by").filter(pk=refund_id).first()
    if refund is None:
        raise SaleError("Refund not found.")
    if refund.status in (Refund.REQUESTED, Refund.REJECTED, Refund.CANCELLED):
        raise SaleError("Only an approved refund has a slip to print.")
    duplicate = refund.print_count > 0
    if duplicate and not (can_approve(user) or user.can("receipts.reprint")):
        raise NotAllowed("The slip was already printed. Ask a supervisor for a reprint.")
    refund.print_count += 1
    refund.save(update_fields=["print_count"])
    audit.record("refund.slip_printed", actor=user, object_type="refund", object_ref=refund.number,
                 new={"copy": refund.print_count}, till_number=refund.till.number)
    return refund, duplicate
