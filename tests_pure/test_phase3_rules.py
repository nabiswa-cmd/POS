"""Framework-free tests for the Phase 3 sales maths and receipt layout.

    python3 -m unittest discover -s tests_pure -t .
"""
import unittest
from datetime import date
from decimal import Decimal as D

from sales import receipt_text, rules


class MoneyAndQuantityTests(unittest.TestCase):
    def test_parse_money_accepts_commas_and_rounds_half_up(self):
        self.assertEqual(rules.parse_money("1,250.505"), D("1250.51"))

    def test_parse_money_rejects_junk_negative_zero_and_huge(self):
        for bad in ("abc", "", "-5", "0", "NaN", "Infinity", "99999999999"):
            with self.assertRaises(rules.RuleError, msg=bad):
                rules.parse_money(bad)
        self.assertEqual(rules.parse_money("0", allow_zero=True), D("0.00"))

    def test_quantity_must_be_whole_positive_and_capped(self):
        self.assertEqual(rules.parse_quantity("3"), 3)
        self.assertEqual(rules.parse_quantity(" 12.0 "), 12)
        for bad in ("0", "-1", "1.5", "x", "", "10000"):
            with self.assertRaises(rules.RuleError, msg=bad):
                rules.parse_quantity(bad)


class TotalsTests(unittest.TestCase):
    def test_line_total_and_discount_cap(self):
        self.assertEqual(rules.line_total(3, "3500", "500"), D("10000.00"))
        with self.assertRaises(rules.RuleError):
            rules.line_total(1, "100", "100.01")
        with self.assertRaises(rules.RuleError):
            rules.line_total(1, "100", "-1")

    def test_sale_totals_keep_gross_discount_and_net_separate(self):
        sub, disc, total = rules.totals([(2, "100", "0"), (1, "50.55", "5.55")])
        self.assertEqual((sub, disc, total), (D("250.55"), D("5.55"), D("245.00")))

    def test_change_due(self):
        self.assertEqual(rules.change_due("7050", "7100"), D("50.00"))
        self.assertEqual(rules.change_due("7050", "7050"), D("0.00"))
        with self.assertRaises(rules.RuleError):
            rules.change_due("7050", "7049.99")

    def test_pack_sale_takes_whole_pack_off_stock(self):
        self.assertEqual(rules.base_quantity(2, 24), 48)      # 2 cartons of 24
        self.assertEqual(rules.base_quantity(5, 1), 5)        # 5 single bottles


class DiscountTests(unittest.TestCase):
    def test_percent_to_amount(self):
        self.assertEqual(rules.discount_from_percent("5", "3500"), D("175.00"))
        with self.assertRaises(rules.RuleError):
            rules.discount_from_percent("101", "3500")

    def test_ordinary_limit(self):
        self.assertFalse(rules.discount_needs_approval("175", "3500", 5))      # exactly 5%
        self.assertTrue(rules.discount_needs_approval("175.01", "3500", 5))    # just over
        self.assertFalse(rules.discount_needs_approval("0", "3500", 5))

    def test_minimum_price_forces_approval_even_for_small_discounts(self):
        # 2 bottles at 3500, minimum 3400 each: a 3% discount gives 3395 each, below the floor
        self.assertTrue(rules.discount_needs_approval("210", "7000", 5, quantity=2, min_unit_price="3400"))
        self.assertFalse(rules.discount_needs_approval("100", "7000", 5, quantity=2, min_unit_price="3400"))


class CostAllocationTests(unittest.TestCase):
    def test_pieces_always_add_up(self):
        parts = rules.allocate_cost("100.00", [1, 1, 1])
        self.assertEqual(sum(parts), D("100.00"))
        parts = rules.allocate_cost("2400.00", [24, 6])
        self.assertEqual(parts, [D("1920.00"), D("480.00")])

    def test_rejects_nothing_to_allocate(self):
        with self.assertRaises(rules.RuleError):
            rules.allocate_cost("10", [])
        with self.assertRaises(rules.RuleError):
            rules.allocate_cost("10", [0])


class ReferenceTests(unittest.TestCase):
    def test_sale_number(self):
        self.assertEqual(rules.sale_number(1, date(2026, 11, 7), 42), "T1-261107-0042")

    def test_approval_references_are_stable(self):
        self.assertEqual(rules.item_ref(10, 3), "sale:10:item:3")
        self.assertEqual(rules.sale_ref(10), "sale:10")


def _data(**over):
    base = dict(
        number="T1-261107-0001", date="2026-11-07 14:03", till="Till 1", customer="Wanjiku Mwangi",
        cashier="Peter", authorized_by="Sup A", header_lines=["Mama's Wines", "Nairobi"],
        items=[dict(name="Johnnie Walker Black Label 750ml", quantity=2, unit_price="3500", gross="7000", discount="200"),
               dict(name="Tusker Lager", quantity=1, unit_price="250", gross="250", discount="0")],
        subtotal="7250", discount="200", total="7050",
        payments=[dict(method="CASH", amount="7050", tendered="7100", change="50", reference="")],
        approval_notes=["Discount approved by Sup A"], footer="Thank you for shopping with us!")
    base.update(over)
    return base


class ReceiptTextTests(unittest.TestCase):
    def test_lines_never_exceed_paper_width(self):
        for mm, width in ((58, 32), (80, 48)):
            for line in receipt_text.format_receipt(_data(), mm):
                self.assertLessEqual(len(line), width, line)

    def test_original_has_cashier_authorizer_customer_and_totals(self):
        text = "\n".join(receipt_text.format_receipt(_data(), 80))
        for needle in ("T1-261107-0001", "Cashier: Peter", "Authorized by: Sup A", "Customer: Wanjiku Mwangi",
                       "TOTAL PAYABLE", "7,050.00", "Change", "50.00", "Thank you for shopping with us!"):
            self.assertIn(needle, text)
        self.assertNotIn("DUPLICATE", text)

    def test_duplicate_is_clearly_marked(self):
        text = "\n".join(receipt_text.format_receipt(_data(duplicate=True, reprint_number=2), 58))
        self.assertIn("*** DUPLICATE RECEIPT ***", text)
        self.assertIn("Reprint #2", text)

    def test_customer_and_authorizer_are_left_out_when_absent(self):
        text = "\n".join(receipt_text.format_receipt(_data(customer="", authorized_by=""), 80))
        self.assertNotIn("Customer:", text)
        self.assertNotIn("Authorized by", text)

    def test_mpesa_reference_prints_without_cash_lines(self):
        pay = dict(method="M-PESA", amount="7050", tendered=None, change="0.00", reference="SKX1234ABC")
        text = "\n".join(receipt_text.format_receipt(_data(payments=[pay]), 80))
        self.assertIn("Ref: SKX1234ABC", text)
        self.assertNotIn("Cash received", text)

    def test_very_long_names_wrap(self):
        name = "X" * 70
        lines = receipt_text.format_receipt(_data(items=[dict(name=name, quantity=1, unit_price="1", gross="1", discount="0")]), 58)
        self.assertTrue(all(len(l) <= 32 for l in lines))


if __name__ == "__main__":
    unittest.main()
