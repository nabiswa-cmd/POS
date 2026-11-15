"""Pure packaging/unit maths (no Django).

All stock is stored as a whole number of BASE UNITS (the smallest thing the shop sells
from that product: a bottle, a can, a tot...). Packaging (carton of 24, crate, six-pack)
is only a multiplier. So selling one bottle takes 1 off; selling a carton of 24 takes 24 off,
and "carton equivalent" stock is just total / units_per_pack. Each product has its own
conversion rules; nothing assumes 24, 12 or any other number.
"""
from decimal import Decimal


class UnitError(ValueError):
    pass


def to_base_units(quantity, units_per_pack=1):
    """quantity packs -> base units. Both must be whole numbers; quantity >= 0."""
    if int(quantity) != quantity or int(units_per_pack) != units_per_pack:
        raise UnitError("Quantities must be whole numbers.")
    quantity, units_per_pack = int(quantity), int(units_per_pack)
    if quantity < 0:
        raise UnitError("Quantity cannot be negative.")
    if units_per_pack < 1:
        raise UnitError("A pack must contain at least one unit.")
    return quantity * units_per_pack


def split_stock(total_units, units_per_pack):
    """total base units -> (whole packs, loose units). Negative stock is shown as-is on the units side."""
    if units_per_pack < 1:
        raise UnitError("A pack must contain at least one unit.")
    if total_units < 0:
        return 0, int(total_units)
    return divmod(int(total_units), int(units_per_pack))


def carton_equivalent(total_units, units_per_pack):
    """Stock expressed in packs, e.g. 60 bottles in 24-packs = 2.5."""
    if units_per_pack < 1:
        raise UnitError("A pack must contain at least one unit.")
    return (Decimal(int(total_units)) / Decimal(int(units_per_pack))).quantize(Decimal("0.01"))


def describe_stock(total_units, base_unit="bottle", pack_name=None, units_per_pack=None):
    """Human text: '2 Carton + 5 bottle (53 bottle)'. Falls back to plain units."""
    plain = f"{int(total_units)} {base_unit}"
    if not pack_name or not units_per_pack or units_per_pack < 2 or total_units < units_per_pack:
        return plain
    packs, loose = split_stock(total_units, units_per_pack)
    text = f"{packs} {pack_name}"
    if loose:
        text += f" + {loose} {base_unit}"
    return f"{text} ({plain})"


def pack_price(unit_price, units_per_pack, explicit_price=None):
    """Price of a pack: the explicit pack price if the manager set one, else unit price x units."""
    if explicit_price is not None:
        return Decimal(explicit_price).quantize(Decimal("0.01"))
    return (Decimal(unit_price) * int(units_per_pack)).quantize(Decimal("0.01"))
