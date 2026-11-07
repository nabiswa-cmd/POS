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


def normalize(code):
    """Scanners and typists vary in case and stray spaces; the stored form is upper-case, trimmed."""
    return (code or "").strip().upper()


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
