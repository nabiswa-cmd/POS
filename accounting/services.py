"""Recording and voiding expenses, and the totals the reports (Phase 5B) will read."""
import csv
import io
from datetime import date as date_type
from decimal import Decimal as D

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from core import audit

from . import rules
from .models import Expense, ExpenseCategory
from .rules import ExpenseError


def _need(user, code="expenses.manage"):
    if not user.can(code):
        raise PermissionError("You are not allowed to record or void expenses.")


def ensure_default_categories():
    """Create the standard categories once. Safe to call again; never renames or re-enables anything."""
    for name in rules.DEFAULT_CATEGORIES:
        ExpenseCategory.objects.get_or_create(name=name)


def _snapshot(e):
    return {"date": e.date.isoformat(), "category": e.category.name, "amount": str(e.amount),
            "description": e.description, "payee": e.payee, "reference": e.reference,
            "paid_from": e.paid_from, "shift": e.shift_id}


@transaction.atomic
def record_expense(user, *, date, category_id, amount, description, paid_from, payee="", reference="",
                   shift_id=None, ip=None):
    """Write one expense. Cash taken from a till drawer must name an OPEN shift and cannot be more than the
    drawer should hold (you cannot take out cash that is not there)."""
    _need(user)
    today = timezone.localdate()
    amount = rules.parse_amount(amount)
    description = rules.check_description(description)
    paid_from = rules.check_paid_from(paid_from)
    category = ExpenseCategory.objects.filter(pk=category_id or 0, active=True).first()
    if category is None:
        raise ExpenseError("Choose a category.")
    shift = None
    if paid_from == rules.CASH_TILL:
        from tills import cashup
        from tills.models import CashierShift
        shift = CashierShift.objects.select_for_update().filter(pk=shift_id or 0).first()
        if shift is None or shift.closed_at is not None:
            raise ExpenseError("Cash can only come out of a till whose shift is open. Choose an open shift.")
        date = today      # cash leaves the drawer today, whatever the form says
        if amount > cashup.snapshot(shift)["expected_cash"]:
            raise ExpenseError("That is more cash than the drawer should hold right now.")
    else:
        if shift_id:
            raise ExpenseError("Only cash taken from a till drawer is linked to a shift.")
        if not isinstance(date, date_type):
            date = None
        date = rules.check_date(date, today)
    expense = Expense.objects.create(
        date=date, category=category, amount=amount, description=description, payee=(payee or "").strip()[:120],
        reference=(reference or "").strip()[:60], paid_from=paid_from, shift=shift, recorded_by=user)
    audit.record("expense.recorded", actor=user, object_type="expense", object_ref=expense.pk,
                 new=_snapshot(expense), ip=ip, till_number=shift.till.number if shift else None)
    return expense


@transaction.atomic
def void_expense(user, expense_id, reason, *, ip=None):
    _need(user)
    reason = rules.check_void_reason(reason)
    expense = (Expense.objects.select_for_update().select_related("category", "shift__till")
               .filter(pk=expense_id or 0).first())
    if expense is None:
        raise ExpenseError("Expense not found.")
    if expense.is_void:
        raise ExpenseError("That expense is already voided.")
    if expense.shift_id and expense.shift.closed_at is not None:
        raise ExpenseError("This cash came out of a drawer that has already been counted. "
                           "Reopen that shift first (needs approval), then void it.")
    expense.voided_at = timezone.now()
    expense.voided_by = user
    expense.void_reason = reason
    expense.save(update_fields=["voided_at", "voided_by", "void_reason"])
    audit.record("expense.voided", actor=user, object_type="expense", object_ref=expense.pk,
                 previous=_snapshot(expense), reason=reason, ip=ip,
                 till_number=expense.shift.till.number if expense.shift_id else None)
    return expense


# --- Figures other screens and reports read ----------------------------------------------------------------

def live(day_from, day_to):
    """Expenses that count (not voided) dated within the period, both ends included."""
    return Expense.objects.filter(date__gte=day_from, date__lte=day_to, voided_at__isnull=True)


def total(day_from, day_to):
    return (live(day_from, day_to).aggregate(t=Sum("amount"))["t"] or D("0.00")).quantize(D("0.01"))


def by_category(day_from, day_to):
    rows = (live(day_from, day_to).values("category__name").annotate(t=Sum("amount")).order_by("-t"))
    return [(r["category__name"], r["t"].quantize(D("0.01"))) for r in rows]


def by_paid_from(day_from, day_to):
    labels = dict(rules.PAID_FROM)
    rows = live(day_from, day_to).values("paid_from").annotate(t=Sum("amount")).order_by("paid_from")
    return [(labels[r["paid_from"]], r["t"].quantize(D("0.01"))) for r in rows]


def listing(day_from, day_to, *, include_void=True):
    qs = Expense.objects.select_related("category", "recorded_by", "voided_by", "shift__till").filter(
        date__gte=day_from, date__lte=day_to)
    if not include_void:
        qs = qs.filter(voided_at__isnull=True)
    return qs.order_by("-date", "-id")


def csv_for(day_from, day_to):
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Date", "Category", "Description", "Payee", "Reference", "Paid from", "KES", "Status",
                "Recorded by", "Void reason"])
    labels = dict(rules.PAID_FROM)
    for e in listing(day_from, day_to).order_by("date", "id"):
        w.writerow([e.date.isoformat(), e.category.name, e.description, e.payee, e.reference,
                    labels[e.paid_from], e.amount, "VOIDED" if e.is_void else "Counted",
                    e.recorded_by.display_name, e.void_reason])
    return out.getvalue()
