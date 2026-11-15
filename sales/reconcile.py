"""M-PESA reconciliation, part 1: money Safaricom confirmed that no sale has used, and a daily check that the books agree.

Rules:
* A confirmed M-PESA payment is never edited or deleted. Reconciliation only changes its STATUS:
  waiting (UNMATCHED)  <->  set aside (IGNORED, with a reason, who and when).  A payment used on a sale (MATCHED) is final.
* Only someone holding `payments.reconcile` (the owner always does) can set aside or restore. Viewing is wider.
* Setting aside does NOT mean the money was handled. It only takes it off the cashier's list so nobody sells against it
  by mistake; the reason says why (customer paid twice and is being refunded, test payment, wrong till, ...).
* Every step is written to the audit log with the person, the receipt code, the amount and the reason.
(Assigning a payment to a sale after the fact, and pulling payments the shop missed while offline, are part 2.)
"""
import csv
import io
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Count, Sum
from django.utils import timezone

from core import audit

from .models import MpesaStkRequest, MpesaTransaction, Payment, Refund, Sale
from .services import NotAllowed, SaleError

OLD_AFTER_HOURS = 24          # a payment nobody has used for this long is flagged
STK_STUCK_MINUTES = 10        # a phone request still "waiting" after this long needs a look
MIN_REASON = 8
ZERO = Decimal("0.00")


def _need(user, action):
    if not (user.can("payments.reconcile") if user.is_active else False):
        raise NotAllowed(f"You are not allowed to {action}.")


def _reason(text):
    reason = " ".join(str(text or "").split())[:250]
    if len(reason) < MIN_REASON:
        raise SaleError(f"Give a reason of at least {MIN_REASON} characters, so the next person understands.")
    return reason


@transaction.atomic
def set_aside(user, txn_id, reason, ip=None):
    """Take a payment nobody used off the cashiers' list, with a reason."""
    _need(user, "set aside payments")
    reason = _reason(reason)
    txn = MpesaTransaction.objects.select_for_update().filter(pk=txn_id).first()
    if txn is None:
        raise SaleError("That payment was not found.")
    if txn.status == MpesaTransaction.MATCHED:
        raise SaleError("That payment was used on a sale. It cannot be set aside.")
    if txn.status == MpesaTransaction.IGNORED:
        raise SaleError("That payment is already set aside.")
    txn.status, txn.resolution_reason = MpesaTransaction.IGNORED, reason
    txn.resolved_by, txn.resolved_at = user, timezone.now()
    txn.save(update_fields=["status", "resolution_reason", "resolved_by", "resolved_at", "updated_at"])
    audit.record("mpesa.set_aside", actor=user, object_type="mpesa", object_ref=txn.trans_id,
                 previous={"status": MpesaTransaction.UNMATCHED},
                 new={"status": MpesaTransaction.IGNORED, "amount": str(txn.amount)}, reason=reason, ip=ip)
    return txn


@transaction.atomic
def restore(user, txn_id, reason, ip=None):
    """Put a set-aside payment back on the cashiers' list (it was set aside by mistake, or the situation changed)."""
    _need(user, "restore payments")
    reason = _reason(reason)
    txn = MpesaTransaction.objects.select_for_update().filter(pk=txn_id).first()
    if txn is None:
        raise SaleError("That payment was not found.")
    if txn.status != MpesaTransaction.IGNORED:
        raise SaleError("Only a payment that was set aside can be put back.")
    previous_reason = txn.resolution_reason
    txn.status = MpesaTransaction.UNMATCHED
    txn.resolution_reason, txn.resolved_by, txn.resolved_at = "", None, None
    txn.save(update_fields=["status", "resolution_reason", "resolved_by", "resolved_at", "updated_at"])
    audit.record("mpesa.restored", actor=user, object_type="mpesa", object_ref=txn.trans_id,
                 previous={"status": MpesaTransaction.IGNORED, "reason": previous_reason},
                 new={"status": MpesaTransaction.UNMATCHED, "amount": str(txn.amount)}, reason=reason, ip=ip)
    return txn


# --- the daily picture -----------------------------------------------------------------------------------------------

def day_bounds(day):
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(day, time.min), tz)
    return start, start + timedelta(days=1)


def _sum(qs, field="amount"):
    return qs.aggregate(t=Sum(field))["t"] or ZERO


def day_report(day):
    """Everything the reconciliation screen shows for one local day, plus the integrity checks."""
    start, end = day_bounds(day)
    now = timezone.now()
    received = MpesaTransaction.objects.filter(created_at__gte=start, created_at__lt=end)
    by_status = {s: {"count": 0, "total": ZERO} for s, _ in MpesaTransaction.STATUSES}
    for row in received.values("status").annotate(n=Count("id"), t=Sum("amount")):
        by_status[row["status"]] = {"count": row["n"], "total": row["t"]}

    sales_today = Sale.objects.filter(status=Sale.COMPLETED, completed_at__gte=start, completed_at__lt=end)
    pay_today = Payment.objects.filter(sale__in=sales_today, method=Payment.MPESA)
    used_today = _sum(pay_today)

    waiting = MpesaTransaction.objects.filter(status=MpesaTransaction.UNMATCHED)
    old_cut = now - timedelta(hours=OLD_AFTER_HOURS)
    old_waiting = waiting.filter(created_at__lt=old_cut)
    ignored = MpesaTransaction.objects.filter(status=MpesaTransaction.IGNORED)
    refunds_paid = Refund.objects.filter(status=Refund.PAID, method=Refund.MPESA, paid_at__gte=start, paid_at__lt=end)

    checks, problems = 0, []

    # 1. Each sale's M-PESA payment rows must equal the Safaricom payments marked as used on it.
    for sale in sales_today.filter(payments__method=Payment.MPESA).distinct():
        checks += 1
        paid = _sum(sale.payments.filter(method=Payment.MPESA))
        used = _sum(sale.mpesa_transactions.filter(status=MpesaTransaction.MATCHED))
        if paid != used:
            problems.append(f"Sale {sale.number}: M-PESA on the receipt is KES {paid:,.2f} but the Safaricom payments used on it add up to KES {used:,.2f}.")
    # 2. An M-PESA payment row must point at a real Safaricom payment.
    checks += 1
    for p in pay_today.filter(mpesa_transaction__isnull=True).select_related("sale"):
        problems.append(f"Sale {p.sale.number}: an M-PESA payment of KES {p.amount:,.2f} has no Safaricom confirmation behind it.")
    # 3. A payment marked used must belong to a completed sale (all time: this is cheap and important).
    checks += 1
    for t in MpesaTransaction.objects.filter(status=MpesaTransaction.MATCHED).exclude(sale__status=Sale.COMPLETED).select_related("sale")[:25]:
        problems.append(f"Payment {t.trans_id} (KES {t.amount:,.2f}) is marked used but its sale is not completed.")
    # 4. Used payments must have a sale; waiting/set-aside ones must not.
    checks += 1
    for t in MpesaTransaction.objects.filter(status=MpesaTransaction.MATCHED, sale__isnull=True)[:25]:
        problems.append(f"Payment {t.trans_id} is marked used but is not linked to any sale.")
    for t in MpesaTransaction.objects.filter(status__in=[MpesaTransaction.UNMATCHED, MpesaTransaction.IGNORED], sale__isnull=False)[:25]:
        problems.append(f"Payment {t.trans_id} is marked {t.get_status_display().lower()} but is linked to a sale.")
    # 5. Today's received money must be fully accounted for by status.
    checks += 1
    if sum(v["total"] for v in by_status.values()) != _sum(received):
        problems.append("Today's payments by status do not add up to the total received. Tell the developer.")
    # 6. Phone requests that were paid must have produced a payment record.
    checks += 1
    for r in MpesaStkRequest.objects.filter(status=MpesaStkRequest.CONFIRMED, transaction__isnull=True)[:25]:
        problems.append(f"Phone request {r.pk} says paid but has no Safaricom payment record.")
    stuck = MpesaStkRequest.objects.filter(status__in=MpesaStkRequest.OPEN_STATES,
                                           created_at__lt=now - timedelta(minutes=STK_STUCK_MINUTES))
    checks += 1
    for r in stuck.select_related("sale")[:25]:
        problems.append(f"Phone request for KES {r.amount:,.2f} sent {timezone.localtime(r.created_at):%d %b %H:%M} never got an answer. "
                        "If the customer says they paid, look for it under Waiting payments.")
    # 7. Set-aside payments must say why.
    checks += 1
    for t in ignored.filter(resolution_reason="")[:25]:
        problems.append(f"Payment {t.trans_id} is set aside without a reason.")

    return {
        "day": day, "received": {"count": received.count(), "total": _sum(received)}, "by_status": by_status,
        "used_today": used_today,
        "waiting": {"count": waiting.count(), "total": _sum(waiting), "rows": list(waiting.order_by("created_at")[:100])},
        "old_waiting": {"count": old_waiting.count(), "total": _sum(old_waiting)},
        "ignored": {"count": ignored.count(), "total": _sum(ignored),
                    "rows": list(ignored.select_related("resolved_by").order_by("-resolved_at")[:50])},
        "refunds_paid": {"count": refunds_paid.count(), "total": _sum(refunds_paid, "total")},
        "checks": checks, "problems": problems, "old_hours": OLD_AFTER_HOURS, "now": now,
    }


def csv_for_day(day):
    """The day's received M-PESA payments as a spreadsheet for the accountant."""
    start, end = day_bounds(day)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Received (local)", "M-PESA code", "Source", "Amount KES", "Customer name (from Safaricom)", "Payer ref",
                "Status", "Sale", "Set aside by", "Reason"])
    rows = (MpesaTransaction.objects.filter(created_at__gte=start, created_at__lt=end)
            .select_related("sale", "resolved_by").order_by("created_at"))
    for t in rows:
        w.writerow([timezone.localtime(t.created_at).strftime("%Y-%m-%d %H:%M:%S"), t.trans_id, t.get_source_display(), t.amount,
                    t.customer_name, t.payer_ref, t.get_status_display(), t.sale.number if t.sale_id and t.sale.number else "",
                    t.resolved_by.display_name if t.resolved_by_id else "", t.resolution_reason])
    return out.getvalue()
