"""The manager's refunds screen: /manage/refunds/."""
from datetime import datetime, timedelta
from urllib.parse import urlencode

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone

from accounts import services as auth_services
from core.decorators import perm_required

from . import refund_report, refunds as refund_services
from .models import Refund
from .services import SaleError


def _day(raw, default):
    try:
        return datetime.strptime(str(raw), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return default


@perm_required("shifts.view_all", "approve.refund")
def refunds(request):
    today = timezone.localdate()
    day_to = _day(request.GET.get("to"), today)
    day_from = _day(request.GET.get("from"), day_to - timedelta(days=29))
    if day_from > day_to:
        day_from = day_to
    status = request.GET.get("status") or None
    if status not in dict(Refund.STATUSES):
        status = None
    query = {"from": day_from.isoformat(), "to": day_to.isoformat()}
    if status:
        query["status"] = status
    return_to_report = f"{request.path}?{urlencode(query)}"
    if request.method == "POST":
        if not request.user.can("approve.refund") and not auth_services.is_exempt(request.user):
            messages.error(request, "You are not allowed to approve or reject refunds.")
            return redirect(return_to_report)
        try:
            refund_id = int(request.POST.get("refund_id", ""))
        except (TypeError, ValueError):
            messages.error(request, "Refund not found.")
            return redirect(return_to_report)
        action = request.POST.get("action")
        try:
            if action == "approve":
                refund = refund_services.approve_refund(request.user, refund_id)
                messages.success(request, f"Refund {refund.number} approved. The cashier can complete the payout from the POS.")
            elif action == "reject":
                refund = refund_services.reject_refund(request.user, refund_id, request.POST.get("reason", ""))
                messages.success(request, f"Refund {refund.number} rejected.")
            else:
                messages.error(request, "Unknown refund action.")
        except (SaleError, PermissionError) as exc:
            messages.error(request, str(exc))
        return redirect(return_to_report)
    if request.GET.get("csv"):
        resp = HttpResponse(refund_report.csv_for(day_from, day_to, status), content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = f'attachment; filename="refunds-{day_from.isoformat()}-to-{day_to.isoformat()}.csv"'
        return resp
    return render(request, "sales/manage_refunds.html", {
        "r": refund_report.build(day_from, day_to, status), "today": today,
        "can_approve_refunds": request.user.can("approve.refund") or auth_services.is_exempt(request.user),
        "can_approve_own_refunds": auth_services.is_exempt(request.user),
    })
