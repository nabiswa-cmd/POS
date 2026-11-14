"""Pure rules for stock movement types (no Django)."""

OPENING = "OPENING"
RECEIVE = "RECEIVE"
SALE = "SALE"
REFUND_RETURN = "REFUND_RETURN"
ADJUST = "ADJUST"
DAMAGED = "DAMAGED"
EXPIRED = "EXPIRED"
SUPPLIER_RETURN = "SUPPLIER_RETURN"

# Partial-quantity (open bottle) movements. They always point at one Bottle and carry a signed
# volume_ml. BOTTLE_OPEN is the only one that also moves whole units (one sealed bottle leaves
# stock_qty); the rest leave stock_qty alone because that bottle was already taken out of stock.
BOTTLE_OPEN = "BOTTLE_OPEN"
PARTIAL_SALE = "PARTIAL_SALE"
VOLUME_WASTAGE = "VOLUME_WASTAGE"
VOLUME_ADJUST = "VOLUME_ADJUST"
VOLUME_REFUND = "VOLUME_REFUND"

POSITIVE_ONLY = {OPENING, RECEIVE, REFUND_RETURN}
NEGATIVE_ONLY = {SALE, DAMAGED, EXPIRED, SUPPLIER_RETURN}
EITHER = {ADJUST}
BOTTLE_TYPES = {BOTTLE_OPEN, PARTIAL_SALE, VOLUME_WASTAGE, VOLUME_ADJUST, VOLUME_REFUND}
ALL_TYPES = POSITIVE_ONLY | NEGATIVE_ONLY | EITHER | BOTTLE_TYPES

# Types that set the weighted-average cost (they bring in stock at a real purchase cost).
COSTED_IN = {OPENING, RECEIVE}

# Manual adjustments a person can post from the stock screen (all need stock.adjust and approval).
MANUAL_TYPES = [OPENING, ADJUST, DAMAGED, EXPIRED, SUPPLIER_RETURN]

LABELS = {
    OPENING: "Opening stock", RECEIVE: "Stock received", SALE: "Sale", REFUND_RETURN: "Refund return",
    ADJUST: "Adjustment", DAMAGED: "Damaged", EXPIRED: "Expired", SUPPLIER_RETURN: "Returned to supplier",
    BOTTLE_OPEN: "Bottle opened", PARTIAL_SALE: "Partial sale", VOLUME_WASTAGE: "Volume wastage",
    VOLUME_ADJUST: "Volume adjustment", VOLUME_REFUND: "Partial refund return",
}


class MovementError(ValueError):
    pass


def check_quantity(movement_type, quantity):
    """Validate type and sign. Quantity is signed whole base units; zero is never a movement."""
    if movement_type not in ALL_TYPES:
        raise MovementError(f"Unknown movement type: {movement_type}")
    if movement_type in BOTTLE_TYPES:
        raise MovementError(f"{LABELS[movement_type]} is recorded against a bottle, not as whole units.")
    if int(quantity) != quantity or quantity == 0:
        raise MovementError("Quantity must be a non-zero whole number.")
    if movement_type in POSITIVE_ONLY and quantity < 0:
        raise MovementError(f"{LABELS[movement_type]} must add stock.")
    if movement_type in NEGATIVE_ONLY and quantity > 0:
        raise MovementError(f"{LABELS[movement_type]} must remove stock.")


def check_bottle_movement(movement_type, quantity, volume_ml):
    """Validate a bottle (partial quantity) movement. volume_ml is signed millilitres on one bottle."""
    if movement_type not in BOTTLE_TYPES:
        raise MovementError(f"{movement_type} is not a bottle movement.")
    if volume_ml is None or volume_ml == 0:
        raise MovementError("A bottle movement needs a non-zero volume.")
    if movement_type == BOTTLE_OPEN:
        if quantity != -1:
            raise MovementError("Opening a bottle takes exactly one sealed bottle out of stock.")
        if volume_ml < 0:
            raise MovementError("Opening a bottle must add volume to the open bottle.")
        return
    if quantity != 0:
        raise MovementError(f"{LABELS[movement_type]} does not change whole-unit stock.")
    if movement_type in (PARTIAL_SALE, VOLUME_WASTAGE) and volume_ml > 0:
        raise MovementError(f"{LABELS[movement_type]} must remove volume.")
    if movement_type == VOLUME_REFUND and volume_ml < 0:
        raise MovementError("Partial refund return must add volume.")
