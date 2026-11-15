"""Refund maths and checks with no Django in them, so they are easy to test and cannot touch the database.

A refund is always against ONE completed sale. It can be full or partial, by whole units of any line.
Money is Decimal with two places, rounded half-up, like the rest of sales.
"""
import re
from decimal import Decimal

from .rules import RuleError, money

CASH, MPESA = "CASH", "MPESA"
METHODS = (CASH, MPESA)


def line_refund(line_total, quantity, refunded_qty, refunded_amount, qty):
    """Amount to give back for `qty` units of one sale line.

    Pro-rata of the amount the customer actually paid for the line (after any discount). The last unit takes
    whatever is left, so the pieces of several partial refunds always add up to exactly the line total and
    never more."""
    line_total, refunded_amount = money(line_total), money(refunded_amount)
    try:
        qty = int(qty)
    except (TypeError, ValueError):
        raise RuleError("Quantity to refund must be a whole number.")
    if qty < 1:
        raise RuleError("Quantity to refund must be at least 1.")
    left = int(quantity) - int(refunded_qty)
    if left <= 0:
        raise RuleError("This line has already been refunded in full.")
    if qty > left:
        raise RuleError(f"Only {left} left to refund on this line.")
    room = line_total - refunded_amount
    amount = room if qty == left else min(money(line_total * qty / int(quantity)), room)
    if amount <= 0:
        raise RuleError("Nothing to refund on this line.")
    return amount


def check_reason(reason):
    reason = re.sub(r"\s+", " ", str(reason or "")).strip()
    if len(reason) < 3:
        raise RuleError("Give a reason for the refund.")
    return reason[:250]


def check_method(method):
    method = str(method or "").upper()
    if method not in METHODS:
        raise RuleError("Choose how the customer is paid back: cash or M-PESA.")
    return method


def check_mpesa_room(requested, mpesa_paid, mpesa_already_refunded):
    """Money can only go back by M-PESA up to what the customer paid by M-PESA on this sale."""
    room = money(mpesa_paid) - money(mpesa_already_refunded)
    if money(requested) > room:
        raise RuleError(f"Only KES {max(room, Decimal('0.00')):,.2f} can go back by M-PESA on this sale. "
                        "Pay the rest back in cash.")


def check_payout_reference(raw):
    """The M-PESA reversal / transaction code that proves the customer really was paid back."""
    ref = str(raw or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{8,14}", ref):
        raise RuleError("Enter the M-PESA code of the payback (8 to 14 letters and digits).")
    return ref


def refund_number(day, sequence):
    """RF-261108-0003: refund, date (yymmdd), running number for that day."""
    return f"RF-{day:%y%m%d}-{int(sequence):04d}"
