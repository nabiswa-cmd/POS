"""Manager's cash-up report: every closed shift with expected, counted and variance."""
from datetime import timedelta

from django.db.models import Sum
from django.shortcuts import render
from django.utils import timezone

from core.decorators import perm_required
from tills.models import CashierShift


@perm_required("shifts.view_all")
def cashup_report(request):
    try:
        days = max(1, min(int(request.GET.get("days", 7)), 366))
    except ValueError:
        days = 7
    shifts = (CashierShift.objects.filter(closed_at__gte=timezone.now() - timedelta(days=days))
              .select_related("till", "cashier", "authorized_by").order_by("-closed_at"))
    rows = list(shifts)
    tot = shifts.aggregate(e=Sum("expected_cash"), c=Sum("counted_cash"), v=Sum("variance"))
    return render(request, "sales/cashup_report.html", {
        "rows": rows, "days": days, "totals": tot, "off": sum(1 for r in rows if r.variance),
        "open_now": CashierShift.objects.filter(closed_at__isnull=True).count()})
