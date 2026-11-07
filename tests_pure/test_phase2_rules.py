"""Phase 2 framework-free tests. Run from the project root:

    python3 -m unittest discover -s tests_pure -t .
"""
import unittest
from decimal import Decimal

from catalog import barcodes, codes, units
from inventory import costing, movement_rules as mr


def decode_code128(modules):
    """Independent decoder used to prove the encoder round-trips (set B only)."""
    # split into runs of widths
    runs, cur, n = [], modules[0], 0
    for m in modules:
        if m == cur:
            n += 1
        else:
            runs.append(n); cur, n = m, 1
    runs.append(n)
    assert modules[0] == "1"
    widths = "".join(str(r) for r in runs)
    symbols = [widths[i:i + 6] for i in range(0, len(widths) - 7, 6)]
    stop = widths[-7:]
    assert stop == barcodes._C128_STOP, stop
    vals = [barcodes._C128.index(s) for s in symbols]
    assert vals[0] == barcodes.START_B
    check = vals[-1]
    body = vals[1:-1]
    assert (barcodes.START_B + sum(v * i for i, v in enumerate(body, 1))) % 103 == check
    return "".join(chr(v + 32) for v in body)


def decode_ean13(modules):
    assert len(modules) == 95 and modules[:3] == "101" and modules[45:50] == "01010" and modules[-3:] == "101"
    left = [modules[3 + 7 * i: 10 + 7 * i] for i in range(6)]
    right = [modules[50 + 7 * i: 57 + 7 * i] for i in range(6)]
    digits, parity = "", ""
    for p in left:
        if p in barcodes._L:
            parity += "L"; digits += str(barcodes._L.index(p))
        else:
            parity += "G"; digits += str(barcodes._G.index(p))
    first = str(barcodes._PARITY.index(parity))
    digits2 = "".join(str(barcodes._R.index(p)) for p in right)
    return first + digits + digits2


class UnitTests(unittest.TestCase):
    def test_carton_of_24(self):
        self.assertEqual(units.to_base_units(3, 24), 72)
        self.assertEqual(units.split_stock(53, 24), (2, 5))
        self.assertEqual(units.carton_equivalent(60, 24), Decimal("2.50"))

    def test_products_use_their_own_conversion(self):
        self.assertEqual(units.to_base_units(2, 12), 24)
        self.assertEqual(units.to_base_units(2, 6), 12)
        self.assertEqual(units.describe_stock(53, "bottle", "Carton", 24), "2 Carton + 5 bottle (53 bottle)")
        self.assertEqual(units.describe_stock(10, "bottle", "Carton", 24), "10 bottle")

    def test_rejects_bad_input(self):
        for args in [(1.5, 24), (-1, 24), (1, 0)]:
            with self.assertRaises(units.UnitError):
                units.to_base_units(*args)

    def test_pack_price(self):
        self.assertEqual(units.pack_price("250", 24), Decimal("6000.00"))
        self.assertEqual(units.pack_price("250", 24, "5500"), Decimal("5500.00"))


class CostingTests(unittest.TestCase):
    def test_weighted_average(self):
        self.assertEqual(costing.weighted_average(10, "100", 10, "120"), Decimal("110.0000"))
        self.assertEqual(costing.weighted_average(0, "999", 5, "80"), Decimal("80.0000"))
        self.assertEqual(costing.weighted_average(-4, "999", 5, "80"), Decimal("80.0000"))  # negative stock has no value

    def test_unit_cost_from_pack(self):
        self.assertEqual(costing.unit_cost_from_pack("3600", 24), Decimal("150.0000"))
        self.assertEqual(costing.unit_cost_from_pack("100", 3), Decimal("33.3333"))

    def test_fifo_takes_oldest_first(self):
        lots = [(1, 5, "100"), (2, 10, "120")]
        alloc = costing.allocate_fifo(lots, 8)
        self.assertEqual([(a, b) for a, b, _ in alloc], [(1, 5), (2, 3)])
        self.assertEqual(costing.allocation_cost(alloc), Decimal("860.00"))

    def test_fifo_shortfall_reported(self):
        with self.assertRaises(costing.InsufficientLots) as ctx:
            costing.allocate_fifo([(1, 2, "100")], 5)
        self.assertEqual(ctx.exception.shortfall, 3)
        self.assertEqual(ctx.exception.allocations, [(1, 2, Decimal("100"))])


class MovementRuleTests(unittest.TestCase):
    def test_signs(self):
        mr.check_quantity(mr.RECEIVE, 5)
        mr.check_quantity(mr.SALE, -1)
        mr.check_quantity(mr.ADJUST, -3)
        mr.check_quantity(mr.ADJUST, 3)
        for t, q in [(mr.RECEIVE, -1), (mr.SALE, 1), (mr.DAMAGED, 2), (mr.OPENING, -1), (mr.ADJUST, 0), ("NOPE", 1)]:
            with self.assertRaises(mr.MovementError):
                mr.check_quantity(t, q)


class CodeTests(unittest.TestCase):
    def test_known_ean13_check_digits(self):
        self.assertTrue(codes.is_valid_ean13("4006381333931"))
        self.assertTrue(codes.is_valid_ean13("5901234123457"))
        self.assertFalse(codes.is_valid_ean13("5901234123458"))

    def test_internal_codes_are_valid_unique_and_in_store_range(self):
        seen = set()
        for n in (1, 2, 3, 99, 12345):
            c = codes.internal_ean13(n)
            self.assertTrue(codes.is_valid_ean13(c) and codes.is_internal(c))
            self.assertTrue(c.startswith("20"))
            seen.add(c)
        self.assertEqual(len(seen), 5)
        self.assertFalse(codes.is_internal("5901234123457"))

    def test_normalize_and_validate(self):
        self.assertEqual(codes.normalize("  abc-12 "), "ABC-12")
        self.assertEqual(codes.validate_text("sku_001"), "SKU_001")
        for bad in ["", "a", "has space", "x" * 41, "semi;colon"]:
            with self.assertRaises(codes.CodeError):
                codes.validate_text(bad)


class BarcodeTests(unittest.TestCase):
    def test_c128_patterns_are_well_formed(self):
        self.assertEqual(len(barcodes._C128), 106)
        self.assertEqual(len(set(barcodes._C128)), 106)
        for p in barcodes._C128:
            self.assertEqual(len(p), 6)
            self.assertEqual(sum(map(int, p)), 11, p)
        self.assertEqual(sum(map(int, barcodes._C128_STOP)), 13)

    def test_code128_round_trips(self):
        for text in ["SKU-0001", "WHISKY.750", "A", "20000000000XYZ", "Hello World 123"]:
            self.assertEqual(decode_code128(barcodes.code128_modules(text)), text)

    def test_code128_rejects_non_ascii(self):
        with self.assertRaises(codes.CodeError):
            barcodes.code128_modules("caf\u00e9")

    def test_ean13_round_trips(self):
        for c in ["4006381333931", "5901234123457", codes.internal_ean13(7), "0012345678905"]:
            m = barcodes.ean13_modules(c)
            self.assertEqual(len(m), 95)
            self.assertEqual(decode_ean13(m), c)

    def test_ean13_rejects_bad_check_digit(self):
        with self.assertRaises(codes.CodeError):
            barcodes.ean13_modules("5901234123458")

    def test_auto_choice_and_svg(self):
        self.assertEqual(len(barcodes.modules_for("5901234123457")), 95)
        self.assertNotEqual(len(barcodes.modules_for("SKU-0001")), 95)
        svg = barcodes.barcode_svg("SKU-0001")
        self.assertTrue(svg.startswith("<svg") and "<rect" in svg)


if __name__ == "__main__":
    unittest.main()
