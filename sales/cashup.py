"""Cash-up (Phase 5): the cashier counts the drawer and closes the shift.

* Expected cash = opening float + cash sales - cash refunds handed out from this drawer (sales.services.shift_summary).
  M-PESA, card and bank never count: they are not in the drawer.
* The cashier types what they really counted. Variance = counted - expected. Any difference needs a written note.
* Closing is final: the figures are stored on the shift and a closed shift cannot be closed again.
* A sale still in progress must be finished, held or voided first (so no goods are left half sold).
"""
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from core import audit
from tills.models import CashierShift

from . import rules, services
from .models import Sale
from .services import SaleError


@transaction.atomic
def close_shift(user, shift, counted, note=""):
    services._need(user, "shifts.view_own", "You are not allowed to close a shift.")
    locked = CashierShift.objects.select_for_update().select_related("till").filter(pk=shift.pk).first()
    if locked is None or locked.closed_at is not None:
        raise SaleError("This shift is already closed.")
    if locked.cashier_id != user.pk:
        raise services.NotAllowed("Only the cashier of this shift can close it.")
    if Sale.objects.filter(shift=locked, status=Sale.OPEN, items__isnull=False).exists():
        raise SaleError("A sale is still in progress. Finish, hold or void it before closing the shift.")
    try:
        counted = rules.parse_money(counted, field="Cash counted", allow_zero=True)
    except rules.RuleError as exc:
        raise SaleError(str(exc)) from exc
    summary = services.shift_summary(locked)
    expected = Decimal(summary["cash_in_drawer_expected"])
    variance = counted - expected
    note = " ".join(str(note or "").split())[:250]
    if variance != 0 and len(note) < 3:
        raise SaleError(f"The drawer is {'over' if variance > 0 else 'short'} by KES {abs(variance):,.2f}. Write a note saying why.")
    locked.expected_cash, locked.counted_cash, locked.variance = expected, counted, variance
    locked.close_note, locked.closed_at = note, timezone.now()
    locked.save(update_fields=["expected_cash", "counted_cash", "variance", "close_note", "closed_at"])
    audit.record("shift.closed", actor=user, object_type="shift", object_ref=locked.pk,
                 new={"expected": str(expected), "counted": str(counted), "variance": str(variance),
                      "sales": summary["sales_count"], "cash_sales": summary["cash_sales"],
                      "mpesa_sales": summary["mpesa_sales"], "other_sales": summary["other_sales"],
                      "refunds_waiting": summary["refunds_waiting"]},
                 reason=note, till_number=locked.till.number)
    return locked, summary
