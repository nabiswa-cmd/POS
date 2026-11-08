"""Pure till cash-up rules (no Django), so they can be tested anywhere.

The drawer should hold:  opening float + cash sales - cash refunds handed back - cash paid out of the drawer.
M-PESA, card and bank money never enter this sum: it is not in the drawer.

The cash-up page never shows the expected figure: the cashier counts and types what is there, then the system compares.
"""
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")

# Kenyan notes and coins, largest first. (The 40 shilling coin is in circulation, so it is counted too.)
DENOMINATIONS = (1000, 500, 200, 100, 50, 40, 20, 10, 5, 1)
MAX_PIECES = 100000            # per denomination: anything above this is a typing slip, not a real count
MAX_COUNTED = Decimal("50000000")
MIN_REASON = 8                 # same minimum as the M-PESA reconciliation reasons

BALANCED, OVER, SHORT = "BALANCED", "OVER", "SHORT"


class CashupError(ValueError):
    """A cash-up rule was broken; the message is safe to show on screen."""


def money(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def parse_counts(raw):
    """Turn {"1000": "3", "50": 2, ...} into (clean {denomination: pieces}, total).

    Empty or missing means zero pieces. Anything that is not a whole number of pieces, or is negative, is refused.
    Unknown denominations are refused too (so a typo in a field name cannot silently drop money).
    """
    if raw in (None, ""):
        raw = {}
    if not isinstance(raw, dict):
        raise CashupError("The cash count is not in the expected form.")
    clean = {}
    for key, value in raw.items():
        try:
            denom = int(str(key).strip())
        except ValueError:
            raise CashupError(f"'{key}' is not a note or coin we count.")
        if denom not in DENOMINATIONS:
            raise CashupError(f"{denom} is not a Kenyan note or coin we count.")
        text = "0" if value in (None, "") else str(value).strip()
        if not text.isdigit():
            raise CashupError(f"Pieces of {denom} must be a whole number, 0 or more.")
        pieces = int(text)
        if pieces > MAX_PIECES:
            raise CashupError(f"{pieces} pieces of {denom} is not believable. Check what you typed.")
        clean[denom] = pieces
    total = sum((Decimal(d) * n for d, n in clean.items()), Decimal("0"))
    if total > MAX_COUNTED:
        raise CashupError("The counted total is too large.")
    return {d: clean.get(d, 0) for d in DENOMINATIONS}, money(total)


def expected_cash(opening_float, cash_sales, cash_refunds_paid, cash_paid_out):
    """What the drawer should hold. Cash handed back to customers and cash taken out leave the drawer."""
    return money(Decimal(opening_float) + Decimal(cash_sales) - Decimal(cash_refunds_paid) - Decimal(cash_paid_out))


def variance(counted, expected):
    """counted - expected. Positive = more cash than expected (over); negative = short."""
    return money(Decimal(counted) - Decimal(expected))


def classify(var):
    var = money(var)
    if var == 0:
        return BALANCED
    return OVER if var > 0 else SHORT


def check_reason(var, reason):
    """A drawer that does not balance can only be closed with a reason in words."""
    text = (reason or "").strip()
    if money(var) != 0 and len(text) < MIN_REASON:
        raise CashupError(f"The count is off by KES {abs(money(var))}. Recount, or type a reason of at least "
                          f"{MIN_REASON} characters.")
    return text


def check_can_close(*, open_carts, held_sales):
    """A shift cannot close while a sale is still in progress or on hold (money and stock are not settled)."""
    if open_carts:
        raise CashupError("A sale is still in progress on this shift. Complete it or void it first.")
    if held_sales:
        word = "sale is" if held_sales == 1 else "sales are"
        raise CashupError(f"{held_sales} {word} on hold on this shift. Complete or void them first.")


def describe(var):
    var = money(var)
    kind = classify(var)
    if kind == BALANCED:
        return "Balanced"
    return f"{'Over' if kind == OVER else 'Short'} by KES {abs(var)}"
