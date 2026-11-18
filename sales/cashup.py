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
from tills import cashup as tills_cashup
from tills.cashup_rules import CashupError
from tills.models import CashierShift

from . import rules, services
from .models import Sale
from .services import SaleError


@transaction.atomic
def close_shift(user, shift, counted, note=""):
    """Compatibility wrapper for the older sales API.

    Cash-up figures are stored on tills.CashUp now; this adapter accepts the old
    total-only input and delegates the closing rules and persistence to tills.cashup.
    """
    try:
        counted = rules.parse_money(counted, field="Cash counted", allow_zero=True)
    except rules.RuleError as exc:
        raise SaleError(str(exc)) from exc

    if shift.cashier_id != user.pk:
        raise services.NotAllowed("Only the cashier of this shift can close it.")

    snap = tills_cashup.snapshot(shift)
    variance = counted - Decimal(snap["expected_cash"])
    if variance != 0 and len(" ".join(str(note or "").split())) < 3:
        direction = "over" if variance > 0 else "short"
        raise SaleError(
            f"The drawer is {direction} by KES {abs(variance):,.2f}. Write a note saying why."
        )

    whole = counted
    cents = (counted - counted.to_integral_value()) * 100
    if cents != 0:
        raise SaleError("The counted cash must be a whole amount of Kenyan shillings.")

    counts = {}
    remaining = int(whole)
    for denomination in (1000, 500, 200, 100, 50, 40, 20, 10, 5, 1):
        pieces, remaining = divmod(remaining, denomination)
        if pieces:
            counts[str(denomination)] = pieces
    if remaining:
        raise SaleError("The counted cash cannot be represented by Kenyan notes and coins.")

    try:
        cashup, _warnings = tills_cashup.close_shift(user, shift.pk, counts, str(note or "").strip())
    except (CashupError, PermissionError) as exc:
        message = str(exc)
        if isinstance(exc, PermissionError):
            raise services.NotAllowed(message) from exc
        raise SaleError(message) from exc

    locked = CashierShift.objects.select_related("till").get(pk=shift.pk)
    summary = services.shift_summary(locked)
    summary["card_sales"] = "0.00"
    summary["bank_sales"] = "0.00"

    # Preserve the sales.cashup compatibility attributes for older callers/tests.
    locked.expected_cash = cashup.expected_cash
    locked.counted_cash = cashup.counted_cash
    locked.variance = cashup.variance
    locked.close_note = cashup.reason
    return locked, summary
