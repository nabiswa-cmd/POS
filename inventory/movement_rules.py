"""Pure rules for stock movement types (no Django)."""

OPENING = "OPENING"
RECEIVE = "RECEIVE"
SALE = "SALE"
REFUND_RETURN = "REFUND_RETURN"
ADJUST = "ADJUST"
DAMAGED = "DAMAGED"
EXPIRED = "EXPIRED"
SUPPLIER_RETURN = "SUPPLIER_RETURN"

POSITIVE_ONLY = {OPENING, RECEIVE, REFUND_RETURN}
NEGATIVE_ONLY = {SALE, DAMAGED, EXPIRED, SUPPLIER_RETURN}
EITHER = {ADJUST}
ALL_TYPES = POSITIVE_ONLY | NEGATIVE_ONLY | EITHER

# Types that set the weighted-average cost (they bring in stock at a real purchase cost).
COSTED_IN = {OPENING, RECEIVE}

# Manual adjustments a person can post from the stock screen (all need stock.adjust and approval).
MANUAL_TYPES = [OPENING, ADJUST, DAMAGED, EXPIRED, SUPPLIER_RETURN]

LABELS = {
    OPENING: "Opening stock", RECEIVE: "Stock received", SALE: "Sale", REFUND_RETURN: "Refund return",
    ADJUST: "Adjustment", DAMAGED: "Damaged", EXPIRED: "Expired", SUPPLIER_RETURN: "Returned to supplier",
}


class MovementError(ValueError):
    pass


def check_quantity(movement_type, quantity):
    """Validate type and sign. Quantity is signed whole base units; zero is never a movement."""
    if movement_type not in ALL_TYPES:
        raise MovementError(f"Unknown movement type: {movement_type}")
    if int(quantity) != quantity or quantity == 0:
        raise MovementError("Quantity must be a non-zero whole number.")
    if movement_type in POSITIVE_ONLY and quantity < 0:
        raise MovementError(f"{LABELS[movement_type]} must add stock.")
    if movement_type in NEGATIVE_ONLY and quantity > 0:
        raise MovementError(f"{LABELS[movement_type]} must remove stock.")
