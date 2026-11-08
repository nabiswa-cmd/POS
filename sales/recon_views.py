"""The manager's reconciliation screen: /manage/reconciliation/"""
from datetime import date, datetime

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone

from core.decorators import perm_required
from core.http import client_ip

from . import reconcile, relay
from .services import SaleError


def _day(raw):
    try:
        return datetime.strptime(str(raw), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return timezone.localdate()


@perm_required("shifts.view_all", "payments.reconcile")
def reconciliation(request):
    day = _day(request.POST.get("day") or request.GET.get("day"))
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "set_aside":
                t = reconcile.set_aside(request.user, request.POST.get("txn_id"), request.POST.get("reason"), ip=client_ip(request))
                messages.success(request, f"Payment {t.trans_id} (KES {t.amount}) set aside.")
            elif action == "restore":
                t = reconcile.restore(request.user, request.POST.get("txn_id"), request.POST.get("reason"), ip=client_ip(request))
                messages.success(request, f"Payment {t.trans_id} is back on the cashiers' list.")
            elif action == "pull":
                try:
                    t = relay.pull_now(request.user)
                except relay.RelayError as exc:
                    messages.error(request, str(exc))
                else:
                    messages.success(request, f"Asked the online server: {t['new']} new, {t['known']} already had"
                                              + (f", {t['conflict']} disagreed with what we hold (see the audit log)" if t["conflict"] else "")
                                              + (f", {t['bad']} unreadable" if t["bad"] else "") + ".")
            else:
                messages.error(request, "Unknown action.")
        except (SaleError, PermissionError) as exc:
            messages.error(request, str(exc))
        return redirect(f"{request.path}?day={day.isoformat()}")
    if request.GET.get("csv"):
        resp = HttpResponse(reconcile.csv_for_day(day), content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = f'attachment; filename="mpesa-{day.isoformat()}.csv"'
        return resp
    report = reconcile.day_report(day)
    return render(request, "sales/reconciliation.html", {
        "r": report, "day": day, "is_today": day == timezone.localdate(),
        "can_act": request.user.can("payments.reconcile"), "today": timezone.localdate(),
        "relay": relay.status()})
