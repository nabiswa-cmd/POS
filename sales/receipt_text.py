"""Plain-text thermal receipt layout (no Django). 58 mm paper is 32 characters wide, 80 mm is 48.

The same function builds the original and the duplicate. A reprint is always headed
"*** DUPLICATE RECEIPT ***" so it cannot be passed off as the original.
"""
from decimal import Decimal

WIDTHS = {58: 32, 80: 48}


def _m(value):
    return f"{Decimal(value):,.2f}"


def _wrap(text, width):
    words, lines, current = str(text).split(), [], ""
    for word in words:
        while len(word) > width:                      # a single very long word
            if current:
                lines.append(current)
                current = ""
            lines.append(word[:width])
            word = word[width:]
        if not current:
            current = word
        elif len(current) + 1 + len(word) <= width:
            current += " " + word
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [""]


def _center(text, width):
    return [line.center(width).rstrip() for line in _wrap(text, width)]


def _pair(left, right, width):
    left, right = str(left), str(right)
    room = width - len(right) - 1
    if len(left) > room:
        left = left[:max(room, 0)]
    return left + " " * (width - len(left) - len(right)) + right


def format_receipt(data, paper_mm=80):
    """data comes from sales.receipt.build_receipt. Returns the receipt as a list of lines."""
    width = WIDTHS.get(int(paper_mm), 48)
    rule, out = "-" * width, []

    if data.get("duplicate"):
        out += _center("*** DUPLICATE RECEIPT ***", width)
        out += _center(f"Reprint #{data.get('reprint_number', 1)}", width)
        out.append(rule)
    for line in data.get("header_lines", []):
        out += _center(line, width)
    out.append(rule)
    out.append(_pair("Receipt:", data["number"], width))
    out.append(_pair("Date:", data["date"], width))
    out.append(_pair("Till:", data["till"], width))
    if data.get("customer"):
        out += _wrap(f"Customer: {data['customer']}", width)
    if data.get("cashier"):
        out += _wrap(f"Cashier: {data['cashier']}", width)
    if data.get("authorized_by"):
        out += _wrap(f"Authorized by: {data['authorized_by']}", width)
    out.append(rule)

    for item in data["items"]:
        out += _wrap(item["name"], width)
        out.append(_pair(f"  {item['quantity']} x {_m(item['unit_price'])}", _m(item["gross"]), width))
        if Decimal(item["discount"]) > 0:
            out.append(_pair("  Discount", "-" + _m(item["discount"]), width))
    out.append(rule)

    out.append(_pair("Subtotal", _m(data["subtotal"]), width))
    if Decimal(data["discount"]) > 0:
        out.append(_pair("Discount", "-" + _m(data["discount"]), width))
    out.append(_pair("TOTAL PAYABLE", _m(data["total"]), width))
    out.append(rule)

    for pay in data.get("payments", []):
        out.append(_pair(f"Paid by {pay['method']}", _m(pay["amount"]), width))
        if pay.get("reference"):
            out += _wrap(f"Ref: {pay['reference']}", width)
        if pay.get("tendered") is not None and Decimal(pay["tendered"]) > 0:
            out.append(_pair("Cash received", _m(pay["tendered"]), width))
            out.append(_pair("Change", _m(pay["change"]), width))
    for note in data.get("approval_notes", []):
        out += _wrap(note, width)
    out.append(rule)
    out += _center(data.get("footer") or "Thank you!", width)
    return out
