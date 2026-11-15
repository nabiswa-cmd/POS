"""Pure costing maths (no Django). Money is Decimal; unit costs keep 4 decimal places."""
from decimal import ROUND_HALF_UP, Decimal

FOUR = Decimal("0.0001")
TWO = Decimal("0.01")


class InsufficientLots(Exception):
    """FIFO was asked for more than the lots hold (callers decide whether that is an error)."""

    def __init__(self, allocations, shortfall):
        super().__init__(f"Lots short by {shortfall}")
        self.allocations = allocations
        self.shortfall = shortfall


def q4(value):
    return Decimal(value).quantize(FOUR, rounding=ROUND_HALF_UP)


def q2(value):
    return Decimal(value).quantize(TWO, rounding=ROUND_HALF_UP)


def unit_cost_from_pack(pack_cost, units_per_pack):
    if units_per_pack < 1:
        raise ValueError("A pack must contain at least one unit.")
    return q4(Decimal(pack_cost) / int(units_per_pack))


def weighted_average(old_qty, old_cost, add_qty, add_cost):
    """New average unit cost after adding stock. Negative or zero old stock carries no value."""
    if add_qty <= 0:
        raise ValueError("Added quantity must be positive.")
    old_qty = max(int(old_qty), 0)
    total_qty = old_qty + int(add_qty)
    total_value = Decimal(old_cost) * old_qty + Decimal(add_cost) * int(add_qty)
    return q4(total_value / total_qty)


def allocate_fifo(lots, quantity):
    """Take `quantity` units from lots, oldest first.

    lots: iterable of (lot_id, remaining_qty, unit_cost), already ordered oldest first.
    Returns [(lot_id, taken, unit_cost), ...]. Raises InsufficientLots (carrying what could be
    allocated plus the shortfall) if the lots do not cover the quantity.
    """
    if quantity <= 0:
        raise ValueError("Quantity must be positive.")
    needed = int(quantity)
    allocations = []
    for lot_id, remaining, cost in lots:
        if needed == 0:
            break
        if remaining <= 0:
            continue
        take = min(int(remaining), needed)
        allocations.append((lot_id, take, Decimal(cost)))
        needed -= take
    if needed:
        raise InsufficientLots(allocations, needed)
    return allocations


def allocation_cost(allocations):
    return q2(sum((Decimal(cost) * taken for _id, taken, cost in allocations), Decimal("0")))
