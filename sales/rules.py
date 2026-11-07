"""Pure sales maths and checks (no Django), so they can be tested anywhere.

Money is Decimal with two places, rounded half-up (KES has no sub-cent values).
A "selling unit" is whatever the cashier sold: one bottle, or one carton. Stock always
leaves in base units: base_qty = quantity x units_per_sale_unit.
"""
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENT = Decimal("0.01")
MAX_QUANTITY = 9999
MAX_MONEY = Decimal("10000000")


class RuleError(ValueError):
    """A sales rule was broken; the message is safe to show the cashier."""


def money(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def parse_money(raw, *, field="Amount", allow_zero=False):
    try:
        value = Decimal(str(raw).strip().replace(",", ""))
    except (InvalidOperation, ValueError):
        raise RuleError(f"{field} is not a valid number.")
    if not value.is_finite():
        raise RuleError(f"{field} is not a valid number.")
    value = money(value)
    if value < 0 or (value == 0 and not allow_zero):
        raise RuleError(f"{field} must be more than zero." if not allow_zero else f"{field} cannot be negative.")
    if value > MAX_MONEY:
        raise RuleError(f"{field} is too large.")
    return value


def parse_quantity(raw):
    try:
        value = Decimal(str(raw).strip())
    except (InvalidOperation, ValueError):
        raise RuleError("Quantity must be a whole number.")
    if not value.is_finite() or value != value.to_integral_value():
        raise RuleError("Quantity must be a whole number.")
    value = int(value)
    if value < 1:
        raise RuleError("Quantity must be at least 1.")
    if value > MAX_QUANTITY:
        raise RuleError(f"Quantity cannot be more than {MAX_QUANTITY}.")
    return value


def line_gross(quantity, unit_price):
    return money(Decimal(unit_price) * int(quantity))


def line_total(quantity, unit_price, discount=Decimal("0")):
    """Gross minus the line discount. The discount can never exceed the gross."""
    gross = line_gross(quantity, unit_price)
    discount = money(discount)
    if discount < 0:
        raise RuleError("Discount cannot be negative.")
    if discount > gross:
        raise RuleError("Discount cannot be more than the line total.")
    return gross - discount


def discount_percent(discount, gross):
    gross = Decimal(gross)
    if gross <= 0:
        return Decimal("0")
    return (Decimal(discount) * 100 / gross).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def discount_from_percent(percent, gross):
    percent = Decimal(str(percent))
    if not percent.is_finite() or percent < 0 or percent > 100:
        raise RuleError("Discount percentage must be between 0 and 100.")
    return money(Decimal(gross) * percent / 100)


def discount_needs_approval(discount, gross, max_percent, *, quantity=1, min_unit_price=None):
    """Ordinary discounts stay within max_percent and never push the unit price below the minimum."""
    if Decimal(discount) <= 0:
        return False
    if Decimal(discount) * 100 > Decimal(gross) * Decimal(max_percent):   # exact, no rounding in our favour
        return True
    if min_unit_price is not None and quantity > 0:
        effective_unit = (Decimal(gross) - Decimal(discount)) / int(quantity)
        if effective_unit < Decimal(min_unit_price):
            return True
    return False


def totals(lines):
    """lines: iterable of (quantity, unit_price, discount). Returns (subtotal, discount_total, total)."""
    subtotal = discount_total = Decimal("0.00")
    for quantity, unit_price, discount in lines:
        subtotal += line_gross(quantity, unit_price)
        discount_total += money(discount)
    subtotal, discount_total = money(subtotal), money(discount_total)
    return subtotal, discount_total, subtotal - discount_total


def change_due(total, tendered):
    total, tendered = money(total), money(tendered)
    if tendered < total:
        raise RuleError(f"Cash received is less than the total ({total:,.2f}).")
    return tendered - total


def allocate_cost(total_cost, weights):
    """Split a cost across lines in proportion to weights; the pieces always add up to total_cost."""
    weights = [int(w) for w in weights]
    if not weights or any(w <= 0 for w in weights):
        raise RuleError("Nothing to allocate cost to.")
    total_cost, whole = money(total_cost), sum(weights)
    parts, running = [], Decimal("0.00")
    for w in weights[:-1]:
        share = money(total_cost * w / whole)
        parts.append(share)
        running += share
    parts.append(total_cost - running)
    return parts


def base_quantity(quantity, units_per_sale_unit):
    return int(quantity) * int(units_per_sale_unit)


def sale_number(till_number, day, sequence):
    """T1-261107-0042: till, date (yymmdd), running number for that till and day."""
    return f"T{int(till_number)}-{day:%y%m%d}-{int(sequence):04d}"


def item_ref(sale_id, item_id):
    """The text an approval token is bound to for one cart line (client and server must agree)."""
    return f"sale:{int(sale_id)}:item:{int(item_id)}"


def sale_ref(sale_id):
    return f"sale:{int(sale_id)}"
