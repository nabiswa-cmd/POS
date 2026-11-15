"""Pure expense rules (no Django), so they can be tested anywhere.

An expense is money the shop spent to run (rent, power, wages, licences ...). It is never edited or deleted:
a mistake is voided with a reason and entered again, so the books keep the full story.
"""
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENT = Decimal("0.01")
MAX_AMOUNT = Decimal("10000000")
MIN_REASON = 8
MAX_BACKDATE_DAYS = 400          # about a year and a bit: old enough for a late annual licence, not for nonsense

# Where the money came from. Only CASH_TILL touches a till drawer (and so the cash-up).
CASH_TILL, CASH_OTHER, MPESA, BANK = "CASH_TILL", "CASH_OTHER", "MPESA", "BANK"
PAID_FROM = [
    (CASH_TILL, "Cash taken from a till drawer"),
    (CASH_OTHER, "Cash from the safe or office"),
    (MPESA, "M-PESA (business account)"),
    (BANK, "Bank"),
]
PAID_FROM_CODES = {code for code, _ in PAID_FROM}

DEFAULT_CATEGORIES = [
    "Rent", "Electricity", "Water", "Wages and salaries", "Licences and permits (county, liquor)",
    "Transport and delivery", "Security", "Repairs and maintenance", "Packaging and bags",
    "Bank and M-PESA charges", "Marketing", "Cleaning", "Stationery and receipt rolls", "Other",
]


class ExpenseError(ValueError):
    """An expense rule was broken; the message is safe to show on screen."""


def parse_amount(raw):
    try:
        value = Decimal(str(raw).strip().replace(",", ""))
    except (InvalidOperation, ValueError):
        raise ExpenseError("Amount is not a valid number.")
    if not value.is_finite():
        raise ExpenseError("Amount is not a valid number.")
    value = value.quantize(CENT, rounding=ROUND_HALF_UP)
    if value <= 0:
        raise ExpenseError("Amount must be more than zero.")
    if value > MAX_AMOUNT:
        raise ExpenseError("Amount is too large. Check the figure.")
    return value


def check_date(value, today):
    if not isinstance(value, date):
        raise ExpenseError("Choose the date the money was spent.")
    if value > today:
        raise ExpenseError("The date cannot be in the future.")
    if value < today - timedelta(days=MAX_BACKDATE_DAYS):
        raise ExpenseError("That date is too long ago to record here. Ask the accountant.")
    return value


def check_description(text):
    text = (text or "").strip()
    if len(text) < 3:
        raise ExpenseError("Say what the money was for (at least 3 characters).")
    return text[:200]


def check_paid_from(code):
    if code not in PAID_FROM_CODES:
        raise ExpenseError("Choose where the money came from.")
    return code


def check_void_reason(text):
    text = (text or "").strip()
    if len(text) < MIN_REASON:
        raise ExpenseError(f"Give a reason of at least {MIN_REASON} characters.")
    return text[:250]
