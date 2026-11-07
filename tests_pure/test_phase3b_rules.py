import unittest
from datetime import datetime
from decimal import Decimal as D

from sales import etims_rules, shortcuts, tax
from sales.rules import RuleError


class TaxTests(unittest.TestCase):
    def test_vat_is_inside_the_price(self):
        self.assertEqual(tax.split_line("3500", "16"), (D("3017.24"), D("482.76")))
        self.assertEqual(tax.split_line("116", "16"), (D("100.00"), D("16.00")))

    def test_zero_and_exempt_have_no_vat(self):
        self.assertEqual(tax.split_line("100", "0"), (D("100.00"), D("0.00")))

    def test_summary_adds_up_line_by_line(self):
        by, taxable, vat = tax.summarize([("B", "16", "3500"), ("B", "16", "99.99"), ("A", "0", "100")])
        self.assertEqual(vat, tax.vat_included("3500", 16) + tax.vat_included("99.99", 16))
        self.assertEqual(taxable + vat, D("3699.99"))
        self.assertEqual(list(by), ["A", "B"])

    def test_rates_and_unknown_class(self):
        self.assertEqual(tax.rate_for("B", D("16")), D("16"))
        self.assertEqual(tax.rate_for("E"), D("8"))
        with self.assertRaises(RuleError):
            tax.rate_for("Z")

    def test_kra_pin_format(self):
        self.assertEqual(tax.clean_kra_pin(" p051234567x "), "P051234567X")
        self.assertEqual(tax.clean_kra_pin(""), "")
        for bad in ("12345", "X051234567X", "P05123456XX"):
            with self.assertRaises(RuleError):
                tax.clean_kra_pin(bad)


class ShortcutTests(unittest.TestCase):
    def test_defaults_have_no_conflicts(self):
        self.assertEqual(shortcuts.merged({}), shortcuts.DEFAULTS)

    def test_rebinding_works(self):
        self.assertEqual(shortcuts.merged({"print": "Alt+P"})["print"], "Alt+P")

    def test_conflicts_and_reserved_keys_are_refused(self):
        for bad in ({"print": "F2"}, {"print": "Escape"}, {"print": "Enter"}, {"print": "Ctrl+P"}, {"nope": "F1"}):
            with self.assertRaises(shortcuts.ShortcutError):
                shortcuts.merged(bad)

    def test_swapping_two_keys_in_one_go_is_a_conflict_only_if_it_collides(self):
        out = shortcuts.merged({"search": "F3", "scan": "F2"})
        self.assertEqual((out["search"], out["scan"]), ("F3", "F2"))


class EtimsRuleTests(unittest.TestCase):
    ITEM = dict(name="JW Black", code="JW-BLK", class_code="50202300", quantity=2, unit_price=D("3500"), discount=D("0"),
                tax_class="B", tax_rate=D("16"), taxable=D("6034.48"), tax=D("965.52"), total=D("7000"))

    def payload(self, **kw):
        return etims_rules.build_sale_payload(tin="P051234567X", branch_id="00", invoice_no=5, sale_time=datetime(2026, 11, 7, 12, 30, 5),
                                              items=[self.ITEM], **kw)

    def test_payload_totals_and_dates(self):
        p = self.payload()
        self.assertEqual((p["cfmDt"], p["salesDt"], p["totAmt"], p["totTaxAmt"]), ("20261107123005", "20261107", 7000.0, 965.52))
        self.assertEqual((p["taxblAmtB"], p["taxAmtB"], p["taxAmtA"], p["taxRtB"]), (6034.48, 965.52, 0.0, 16.0))
        self.assertAlmostEqual(p["totTaxblAmt"] + p["totTaxAmt"], p["totAmt"], places=2)

    def test_item_line(self):
        line = self.payload()["itemList"][0]
        self.assertEqual((line["itemSeq"], line["qty"], line["prc"], line["splyAmt"], line["totAmt"], line["taxTyCd"]),
                         (1, 2, 3500.0, 7000.0, 7000.0, "B"))

    def test_response_needs_code_000_and_a_signature(self):
        good = {"resultCd": "000", "resultMsg": "ok", "data": {"rcptSign": "S", "rcptNo": 1}}
        self.assertTrue(etims_rules.parse_response(good)["ok"])
        self.assertFalse(etims_rules.parse_response({"resultCd": "000", "data": {"rcptNo": 1}})["ok"])
        self.assertFalse(etims_rules.parse_response({"resultCd": "910", "resultMsg": "bad", "data": None})["ok"])
        self.assertFalse(etims_rules.parse_response("garbage")["ok"])

    def test_no_qr_without_template_or_signature(self):
        self.assertEqual(etims_rules.qr_text("", "T", "00", "S"), "")
        self.assertEqual(etims_rules.qr_text("u?{tin}{bhf}{sign}", "T", "00", ""), "")
        self.assertEqual(etims_rules.qr_text("u?{tin}-{bhf}-{sign}", "T", "00", "S"), "u?T-00-S")


if __name__ == "__main__":
    unittest.main()
