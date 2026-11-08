"""Cash-up screens.

* /pos/cashup/             the cashier counts the drawer and closes their own shift (blind: no expected figure shown)
* /manage/cashups/         the manager's list of shifts and their variances (CSV for the accountant)
* /manage/cashups/<id>/    one shift in full: close it for a cashier, reopen it, print the slip
"""
from datetime import datetime, timedelta

from django.contrib import messages
from django.contrib.auth import logout
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone

from core.decorators import perm_required
from core.http import client_ip

from . import cashup
from . import cashup_rules as rules
from .models import CashierShift, CashUp


def _day(raw, default):
    try:
        return datetime.strptime(str(raw), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return default


def _counts_from(post):
    return {str(d): post.get(f"c_{d}", "") for d in rules.DENOMINATIONS}


def _grid(entered=None):
    entered = entered or {}
    return [{"denom": d, "value": entered.get(str(d), "")} for d in rules.DENOMINATIONS]


# --- The cashier closes their own shift ------------------------------------------------------------------------

def pos_cashup(request):
    user = request.user
    if not user.is_authenticated:
        return redirect("/app/pos/")
    till_number = request.session.get("pos_till")
    shift = (CashierShift.objects.select_related("till", "cashier")
             .filter(till__number=till_number, cashier=user, closed_at__isnull=True).first()) if till_number else None
    if shift is None or not user.can("sales.make"):
        return redirect("/app/pos/")
    entered, error = {}, ""
    if request.method == "POST":
        entered = _counts_from(request.POST)
        try:
            cu, warnings = cashup.close_shift(user, shift.pk, entered, request.POST.get("reason", ""),
                                              ip=client_ip(request))
        except (rules.CashupError, PermissionError) as exc:
            error = str(exc)
        else:
            logout(request)       # the shift is over: nobody stays signed in at the till
            return render(request, "tills/cashup_slip.html", {"c": cu, "warnings": warnings, "done": True,
                                                              "lines": _slip_lines(cu)})
    return render(request, "tills/cashup_pos.html", {
        "shift": shift, "grid": _grid(entered), "error": error, "reason": request.POST.get("reason", ""),
        "min_reason": rules.MIN_REASON})


def _slip_lines(c):
    return [{"denom": d, "pieces": c.counts.get(str(d), 0), "value": rules.money(d * int(c.counts.get(str(d), 0)))}
            for d in rules.DENOMINATIONS if int(c.counts.get(str(d), 0))]


# --- The manager's screens ----------------------------------------------------------------------------------------

@perm_required("shifts.view_all")
def cashups(request):
    today = timezone.localdate()
    day_to = _day(request.GET.get("to"), today)
    day_from = _day(request.GET.get("from"), day_to - timedelta(days=29))
    if day_from > day_to:
        day_from = day_to
    if request.GET.get("csv"):
        resp = HttpResponse(cashup.csv_for(day_from, day_to), content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = f'attachment; filename="cashups-{day_from.isoformat()}-to-{day_to.isoformat()}.csv"'
        return resp
    return render(request, "tills/manage_cashups.html", {"r": cashup.report(day_from, day_to), "today": today})


@perm_required("shifts.view_all")
def cashup_detail(request, shift_id):
    shift = (CashierShift.objects.select_related("till", "cashier", "authorized_by").filter(pk=shift_id).first())
    if shift is None:
        raise Http404
    user = request.user
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "close":
                cu, warnings = cashup.close_shift(user, shift.pk, _counts_from(request.POST),
                                                  request.POST.get("reason", ""), ip=client_ip(request))
                messages.success(request, f"Shift closed. {rules.describe(cu.variance)}.")
                for w in warnings:
                    messages.warning(request, w)
            elif action == "reopen":
                cashup.reopen_shift(user, shift.pk, request.POST.get("reason", ""), ip=client_ip(request))
                messages.success(request, "Shift reopened. The earlier count stays on record and the shift must be counted again.")
            else:
                messages.error(request, "Unknown action.")
        except (rules.CashupError, PermissionError) as exc:
            messages.error(request, str(exc))
        return redirect(request.path)
    active = CashUp.objects.select_related("closed_by", "superseded_by").filter(shift=shift, superseded_at__isnull=True).first()
    history = CashUp.objects.select_related("closed_by", "superseded_by").filter(shift=shift, superseded_at__isnull=False)
    from accounting.models import Expense
    return render(request, "tills/manage_cashup_detail.html", {
        "shift": shift, "c": active, "history": history, "live": cashup.snapshot(shift) if active is None else None,
        "lines": _slip_lines(active) if active else [], "grid": _grid(),
        "can_close_any": user.can("shifts.close_any") or shift.cashier_id == user.pk,
        "can_reopen": user.can("approve.reopen_shift"), "min_reason": rules.MIN_REASON,
        "payouts": Expense.objects.select_related("category").filter(shift=shift, paid_from=Expense.CASH_TILL).order_by("id")})


@perm_required("shifts.view_all")
def cashup_slip(request, shift_id):
    cu = (CashUp.objects.select_related("shift__till", "shift__cashier", "closed_by")
          .filter(shift_id=shift_id, superseded_at__isnull=True).first())
    if cu is None:
        raise Http404
    return render(request, "tills/cashup_slip.html", {"c": cu, "warnings": [], "done": False, "lines": _slip_lines(cu)})
