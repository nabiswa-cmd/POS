"""Closing a till shift: the blind cash count, the comparison with the books, and reopening.

The figures come from the same books the shift summary (F11) reads, so they always agree. Cash is the only thing
that can be in the drawer: M-PESA, card and bank money are shown next to the count but never added to it.
"""
import csv
import io
from decimal import Decimal as D

from django.db import IntegrityError, transaction
from django.db.models import Count, Sum
from django.utils import timezone

from core import audit

from . import cashup_rules as rules
from .cashup_rules import CashupError
from .models import CashierShift, CashUp


def _sum(qs, field="amount"):
    return rules.money(qs.aggregate(t=Sum(field))["t"] or 0)


def snapshot(shift):
    """Every figure for one shift, as Decimals, worked out from the books right now."""
    from accounting.models import Expense
    from sales.models import Payment, Refund, Sale

    done = Sale.objects.filter(shift=shift, status=Sale.COMPLETED)

    def paid(method):
        return _sum(Payment.objects.filter(sale__in=done, method=method))

    cash_sales, mpesa_sales = paid(Payment.CASH), paid(Payment.MPESA)
    other_sales = paid(Payment.CARD) + paid(Payment.BANK)
    # Only cash really handed back from THIS drawer leaves it; an M-PESA payback never touched it.
    cash_refunds = _sum(Refund.objects.filter(paid_shift=shift, status=Refund.PAID, method=Refund.CASH), "total")
    mpesa_refunds_paid = _sum(Refund.objects.filter(shift=shift, status=Refund.PAID, method=Refund.MPESA), "total")
    refunds_waiting = _sum(Refund.objects.filter(shift=shift, status__in=(Refund.REQUESTED, Refund.APPROVED)), "total")
    cash_paid_out = _sum(Expense.objects.filter(shift=shift, paid_from=Expense.CASH_TILL, voided_at__isnull=True))
    opening = rules.money(shift.opening_float)
    return {
        "sales_count": done.count(), "sales_total": _sum(done, "total"),
        "cash_sales": cash_sales, "mpesa_sales": mpesa_sales, "other_sales": other_sales,
        "cash_refunds": cash_refunds, "mpesa_refunds_paid": mpesa_refunds_paid, "refunds_waiting": refunds_waiting,
        "cash_paid_out": cash_paid_out, "opening_float": opening,
        "expected_cash": rules.expected_cash(opening, cash_sales, cash_refunds, cash_paid_out),
    }


def _check_who(user, shift):
    if shift.cashier_id == user.pk:
        if not user.can("sales.make"):
            raise PermissionError("You are not allowed to close this shift.")
        return False
    if not user.can("shifts.close_any"):
        raise PermissionError("Only the cashier, or a supervisor allowed to close other shifts, can close this one.")
    return True


@transaction.atomic
def close_shift(user, shift_id, counts, reason="", *, ip=None):
    """Count the drawer and close the shift. Returns (cashup, warnings).

    Refused while a sale is in progress or on hold. A drawer that does not match needs a written reason.
    If someone other than the cashier closes it, they must say why.
    """
    shift = (CashierShift.objects.select_for_update().select_related("till", "cashier")
             .filter(pk=shift_id or 0).first())
    if shift is None:
        raise CashupError("Shift not found.")
    if shift.closed_at is not None:
        raise CashupError("This shift is already closed.")
    for_someone_else = _check_who(user, shift)

    from sales.models import Sale
    carts = Sale.objects.filter(shift=shift, status=Sale.OPEN)
    in_progress = carts.annotate(n=Count("items")).filter(n__gt=0).count()
    held = Sale.objects.filter(shift=shift, status=Sale.HELD).count()
    rules.check_can_close(open_carts=in_progress, held_sales=held)

    pieces, counted = rules.parse_counts(counts)
    snap = snapshot(shift)
    var = rules.variance(counted, snap["expected_cash"])
    why = rules.check_reason(var, reason)
    if for_someone_else and len((reason or "").strip()) < rules.MIN_REASON:
        raise CashupError(f"You are closing {shift.cashier.display_name}'s shift. Say why "
                          f"(at least {rules.MIN_REASON} characters).")

    # An empty cart the POS opened by itself is not a sale: tidy it away so nothing is left dangling.
    carts.annotate(n=Count("items")).filter(n=0).delete()

    cashup = CashUp.objects.create(
        shift=shift, closed_by=user, opening_float=snap["opening_float"], cash_sales=snap["cash_sales"],
        cash_refunds=snap["cash_refunds"], cash_paid_out=snap["cash_paid_out"], expected_cash=snap["expected_cash"],
        counted_cash=counted, variance=var, counts={str(d): n for d, n in pieces.items()}, reason=why,
        mpesa_sales=snap["mpesa_sales"], mpesa_refunds_paid=snap["mpesa_refunds_paid"],
        other_sales=snap["other_sales"], sales_count=snap["sales_count"], sales_total=snap["sales_total"])
    shift.closed_at = timezone.now()
    shift.save(update_fields=["closed_at"])
    audit.record("shift.closed", actor=user, object_type="shift", object_ref=shift.pk,
                 new={"expected": str(snap["expected_cash"]), "counted": str(counted), "variance": str(var),
                      "cashier": shift.cashier.display_name, "closed_for_cashier": for_someone_else},
                 reason=why, ip=ip, till_number=shift.till.number)
    warnings = []
    if snap["refunds_waiting"]:
        warnings.append(f"Refunds worth KES {snap['refunds_waiting']} are still waiting to be approved or paid back. "
                        "They are not in this cash-up.")
    return cashup, warnings


@transaction.atomic
def reopen_shift(user, shift_id, reason, *, ip=None):
    """Undo a close (for a count made in error). Needs `approve.reopen_shift`. The old cash-up stays on record,
    marked superseded; the shift is open again and must be counted again."""
    if not user.can("approve.reopen_shift"):
        raise PermissionError("Only a supervisor or the owner can reopen a closed shift.")
    text = (reason or "").strip()
    if len(text) < rules.MIN_REASON:
        raise CashupError(f"Give a reason of at least {rules.MIN_REASON} characters.")
    shift = (CashierShift.objects.select_for_update().select_related("till", "cashier")
             .filter(pk=shift_id or 0).first())
    if shift is None or shift.closed_at is None:
        raise CashupError("That shift is not closed.")
    cashup = CashUp.objects.select_for_update().filter(shift=shift, superseded_at__isnull=True).first()
    if cashup is None:
        raise CashupError("There is no cash-up to reopen.")
    if CashierShift.objects.filter(till=shift.till, closed_at__isnull=True).exists():
        raise CashupError(f"{shift.till} already has an open shift. Close that one first.")
    try:
        with transaction.atomic():
            cashup.superseded_at = timezone.now()
            cashup.superseded_by = user
            cashup.supersede_reason = text[:250]
            cashup.save(update_fields=["superseded_at", "superseded_by", "supersede_reason"])
            shift.closed_at = None
            shift.save(update_fields=["closed_at"])
    except IntegrityError:
        raise CashupError(f"{shift.till} already has an open shift. Close that one first.")
    audit.record("shift.reopened", actor=user, approver=user, object_type="shift", object_ref=shift.pk,
                 previous={"expected": str(cashup.expected_cash), "counted": str(cashup.counted_cash),
                           "variance": str(cashup.variance)},
                 reason=text, ip=ip, till_number=shift.till.number)
    return shift


# --- Reports -------------------------------------------------------------------------------------------------

def shifts_in(day_from, day_to):
    """Shifts opened within the period (both ends included), newest first, with their active cash-up."""
    return (CashierShift.objects.select_related("till", "cashier", "authorized_by")
            .filter(opened_at__date__gte=day_from, opened_at__date__lte=day_to).order_by("-opened_at", "-id"))


def report(day_from, day_to):
    rows, over, short, balanced = [], D("0.00"), D("0.00"), 0
    active = {c.shift_id: c for c in CashUp.objects.select_related("closed_by").filter(
        superseded_at__isnull=True, shift__opened_at__date__gte=day_from, shift__opened_at__date__lte=day_to)}
    for shift in shifts_in(day_from, day_to):
        cu = active.get(shift.pk)
        rows.append({"shift": shift, "cashup": cu})
        if cu:
            if cu.variance > 0:
                over += cu.variance
            elif cu.variance < 0:
                short += -cu.variance
            else:
                balanced += 1
    return {"day_from": day_from, "day_to": day_to, "rows": rows, "over": over, "short": short,
            "balanced": balanced, "open_count": sum(1 for r in rows if r["cashup"] is None),
            "counted_count": sum(1 for r in rows if r["cashup"] is not None)}


def csv_for(day_from, day_to):
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Till", "Cashier", "Opened", "Closed", "Opening float", "Cash sales", "Cash refunds", "Cash paid out",
                "Expected cash", "Counted cash", "Variance", "M-PESA sales", "M-PESA refunds paid", "Card/bank sales",
                "Closed by", "Reason"])
    for r in report(day_from, day_to)["rows"]:
        s, c = r["shift"], r["cashup"]
        base = [s.till.name, s.cashier.display_name, timezone.localtime(s.opened_at).strftime("%Y-%m-%d %H:%M"),
                timezone.localtime(s.closed_at).strftime("%Y-%m-%d %H:%M") if s.closed_at else "OPEN"]
        if c:
            w.writerow(base + [c.opening_float, c.cash_sales, c.cash_refunds, c.cash_paid_out, c.expected_cash,
                               c.counted_cash, c.variance, c.mpesa_sales, c.mpesa_refunds_paid, c.other_sales,
                               c.closed_by.display_name, c.reason])
        else:
            w.writerow(base + [s.opening_float] + [""] * 10)
    return out.getvalue()
