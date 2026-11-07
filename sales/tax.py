"""Kenyan VAT maths (pure Python, no Django).

Shelf prices in a Kenyan shop include VAT, so the tax on a line is *inside* its price:
    VAT = price x rate / (100 + rate)         taxable value = price - VAT
Each line is rounded half-up to the cent and the sale's VAT is the sum of its lines, which is how
KRA's eTIMS adds up an invoice (item by item). Exclusive pricing (VAT added on top) is not supported.

Tax classes use the letters of the KRA eTIMS specification. The rates for A, C, D and E are fixed
here; B (standard rate) comes from Shop settings, default 16. These letters and rates are what the
build was written against, but KRA's own documents are the authority: confirm the list with your
accountant before go-live (see README).
"""
import re
from decimal import Decimal

from .rules import RuleError, money

TAX_CLASSES = {
    "A": "A - Exempt",
    "B": "B - Standard rate (VAT)",
    "C": "C - Zero-rated",
    "D": "D - Non-VAT",
    "E": "E - Special rate (8%)",
}
DEFAULT_CLASS = "B"            # wines and spirits are standard-rated; confirm with your accountant
FIXED_RATES = {"A": Decimal("0"), "C": Decimal("0"), "D": Decimal("0"), "E": Decimal("8")}
DEFAULT_STANDARD_RATE = Decimal("16.00")


def rate_for(tax_class, standard_rate=DEFAULT_STANDARD_RATE):
    if tax_class not in TAX_CLASSES:
        raise RuleError(f"Unknown tax class '{tax_class}'.")
    if tax_class == "B":
        return Decimal(standard_rate)
    return FIXED_RATES[tax_class]


def vat_included(amount, rate):
    """The VAT contained in a VAT-inclusive amount."""
    rate = Decimal(rate)
    if rate <= 0:
        return Decimal("0.00")
    return money(Decimal(amount) * rate / (100 + rate))


def split_line(line_total, rate):
    """(taxable value, VAT) for one VAT-inclusive line total."""
    line_total = money(line_total)
    tax = vat_included(line_total, rate)
    return line_total - tax, tax


def summarize(lines):
    """lines: iterable of (tax_class, rate, line_total). Returns (by_class, taxable_total, vat_total).

    by_class maps each class used to {"rate", "gross", "taxable", "tax"}; every figure is the sum of
    the per-line figures, so the summary can never disagree with the lines printed above it.
    """
    by_class = {}
    taxable_total = vat_total = Decimal("0.00")
    for tax_class, rate, total in lines:
        taxable, tax = split_line(total, rate)
        row = by_class.setdefault(tax_class, {"rate": Decimal(rate), "gross": Decimal("0.00"),
                                              "taxable": Decimal("0.00"), "tax": Decimal("0.00")})
        row["gross"] += money(total)
        row["taxable"] += taxable
        row["tax"] += tax
        taxable_total += taxable
        vat_total += tax
    return dict(sorted(by_class.items())), taxable_total, vat_total


_PIN = re.compile(r"^[AP]\d{9}[A-Z]$")


def clean_kra_pin(raw):
    """A customer's KRA PIN: one letter (A or P), nine digits, one letter. Blank is fine."""
    value = "".join(str(raw or "").split()).upper()
    if not value:
        return ""
    if not _PIN.match(value):
        raise RuleError("A KRA PIN looks like A123456789Z (letter, 9 digits, letter).")
    return value
