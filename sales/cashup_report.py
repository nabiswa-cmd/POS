"""Manager's cash-up report: every closed shift with expected, counted and variance."""
from datetime import timedelta

from django.shortcuts import render
from django.utils import timezone

from core.decorators import perm_required
from tills.models import CashierShift, CashUp


@perm_required("shifts.view_all")
def cashup_report(request):
    try:
        days = max(1, min(int(request.GET.get("days", 7)), 366))
    except ValueError:
        days = 7
    since = timezone.now() - timedelta(days=days)
    shifts = (CashierShift.objects.filter(closed_at__gte=since)
              .select_related("till", "cashier", "authorized_by").order_by("-closed_at"))
    active = {c.shift_id: c for c in CashUp.objects.filter(
        superseded_at__isnull=True, shift__closed_at__gte=since).select_related("shift")}
    rows = list(shifts)
    cashups = list(active.values())
    total_expected = sum((c.expected_cash for c in cashups), 0)
    total_counted = sum((c.counted_cash for c in cashups), 0)
    total_variance = sum((c.variance for c in cashups), 0)
    return render(request, "sales/cashup_report.html", {
        "rows": [{"shift": s, "cashup": active.get(s.pk)} for s in rows],
        "days": days,
        "totals": {"e": total_expected, "c": total_counted, "v": total_variance},
        "off": sum(1 for c in cashups if c.variance),
        "open_now": CashierShift.objects.filter(closed_at__isnull=True).count()})
