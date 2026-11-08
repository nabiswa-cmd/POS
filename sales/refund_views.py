"""The manager's refunds screen: /manage/refunds/ (look only: approving and paying happen at the till with Alt+R)."""
from datetime import datetime, timedelta

from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone

from core.decorators import perm_required

from . import refund_report
from .models import Refund


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
    if request.GET.get("csv"):
        resp = HttpResponse(refund_report.csv_for(day_from, day_to, status), content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = f'attachment; filename="refunds-{day_from.isoformat()}-to-{day_to.isoformat()}.csv"'
        return resp
    return render(request, "sales/manage_refunds.html", {"r": refund_report.build(day_from, day_to, status), "today": today})
