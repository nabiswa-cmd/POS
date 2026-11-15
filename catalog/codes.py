"""Pure product-code rules (no Django).

One namespace for everything a scanner can read: manufacturer barcodes, codes the shop
generates itself, manual codes and the SKU. QR labels simply encode the same text, so a
scanned QR and a scanned barcode find the product through the same lookup.

Internal codes are EAN-13 numbers starting with "20". GS1 reserves 20-29 for in-store use,
so they never clash with a real manufacturer barcode.
"""
import re

INTERNAL_PREFIX = "20"
CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9._\-]{1,39}$")


class CodeError(ValueError):
    pass


_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_AIM_ID_RE = re.compile(r"^\][A-Za-z][0-9A-Za-z]")


def normalize(code):
    """Scanners and typists vary in case, stray spaces and invisible control characters (a scanner may end
    a read with Enter/Tab or wrap it in STX/ETX). The stored form is upper-case, with those removed."""
    return _CONTROL_RE.sub("", code or "").strip().upper()


def from_scanner(raw):
    """Clean text that came from a hardware scanner (or a typist) into the code to look up.

    Some scanners can be set to send an AIM symbology identifier in front of the code (for example ']E0'
    for EAN-13). A stored code can never start with ']', so removing it is always safe."""
    code = normalize(raw)
    if _AIM_ID_RE.match(code) and len(code) > 3:
        code = code[3:].strip()
    return code


def lookup_candidates(raw):
    """Every stored form a scanned code may be filed under, best match first.

    A UPC-A label (12 digits) is the same product as the EAN-13 that starts with 0, and some scanners
    report one while the shop filed the other. Nothing else is guessed."""
    code = from_scanner(raw)
    if not code:
        return []
    found = [code]
    if code.isdigit():
        if len(code) == 12:
            found.append("0" + code)
        elif len(code) == 13 and code.startswith("0"):
            found.append(code[1:])
    return found


def validate_text(code):
    code = normalize(code)
    if not CODE_RE.match(code):
        raise CodeError("Codes use letters, digits, '.', '_' or '-', 2 to 40 characters.")
    return code


def ean13_check_digit(first12):
    if len(first12) != 12 or not first12.isdigit():
        raise CodeError("EAN-13 needs 12 digits before the check digit.")
    total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(first12))
    return str((10 - total % 10) % 10)


def is_valid_ean13(code):
    return len(code) == 13 and code.isdigit() and ean13_check_digit(code[:12]) == code[12]


def internal_ean13(sequence):
    """Build the shop's own EAN-13 from a running number (1, 2, 3 ...)."""
    sequence = int(sequence)
    if not 1 <= sequence <= 9_999_999_999:
        raise CodeError("Internal code sequence out of range.")
    body = INTERNAL_PREFIX + f"{sequence:010d}"
    return body + ean13_check_digit(body)


def is_internal(code):
    return is_valid_ean13(code) and code.startswith(INTERNAL_PREFIX)
