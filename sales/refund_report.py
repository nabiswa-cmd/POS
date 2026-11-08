"""What the manager needs to see about refunds: what is waiting, what is owed to customers, what was paid and how."""
import csv
import io
from datetime import timedelta
from decimal import Decimal as D

from django.utils import timezone

from .models import Refund

OWED_WARN_HOURS = 24          # approved but the customer still has not been paid back after this long


def _money(x):
    return D(x).quantize(D("0.01"))


def refunds_in(day_from, day_to, status=None):
    qs = (Refund.objects.select_related("sale", "requested_by", "approved_by", "paid_by", "rejected_by")
          .filter(requested_at__date__gte=day_from, requested_at__date__lte=day_to))
    if status:
        qs = qs.filter(status=status)
    return qs.order_by("-requested_at", "-id")


def build(day_from, day_to, status=None):
    """Figures for the screen. Totals always cover the whole period; `status` only narrows the table."""
    everything = list(refunds_in(day_from, day_to))
    now = timezone.now()
    by_status = {code: {"count": 0, "total": D("0.00")} for code, _label in Refund.STATUSES}
    for r in everything:
        by_status[r.status]["count"] += 1
        by_status[r.status]["total"] += r.total
    paid = [r for r in everything if r.status == Refund.PAID]
    owed = [r for r in everything if r.status == Refund.APPROVED]
    late = [r for r in owed if r.approved_at and now - r.approved_at > timedelta(hours=OWED_WARN_HOURS)]
    rows = [r for r in everything if not status or r.status == status]
    return {
        "day_from": day_from, "day_to": day_to, "status": status or "", "rows": rows,
        "by_status": {dict(Refund.STATUSES)[k]: v for k, v in by_status.items()},
        "waiting": by_status[Refund.REQUESTED],
        "owed": {"count": len(owed), "total": _money(sum((r.total for r in owed), D("0")))},
        "owed_mpesa": {"count": sum(1 for r in owed if r.method == Refund.MPESA),
                       "total": _money(sum((r.total for r in owed if r.method == Refund.MPESA), D("0")))},
        "late": {"count": len(late), "total": _money(sum((r.total for r in late), D("0"))), "hours": OWED_WARN_HOURS},
        "paid_cash": _money(sum((r.total for r in paid if r.method == Refund.CASH), D("0"))),
        "paid_mpesa": _money(sum((r.total for r in paid if r.method == Refund.MPESA), D("0"))),
        "late_ids": {r.pk for r in late},
        "statuses": Refund.STATUSES,
    }


def csv_for(day_from, day_to, status=None):
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Refund", "Requested", "Sale", "Method", "KES", "Status", "Reason", "Requested by", "Approved by",
                "Paid by", "Paid at", "M-PESA payback code"])
    for r in refunds_in(day_from, day_to, status):
        w.writerow([r.number, timezone.localtime(r.requested_at).strftime("%Y-%m-%d %H:%M"), r.sale.number,
                    r.get_method_display(), r.total, r.get_status_display(), r.reason, r.requested_by.display_name,
                    r.approved_by.display_name if r.approved_by_id else "",
                    r.paid_by.display_name if r.paid_by_id else "",
                    timezone.localtime(r.paid_at).strftime("%Y-%m-%d %H:%M") if r.paid_at else "", r.pay_reference])
    return out.getvalue()
