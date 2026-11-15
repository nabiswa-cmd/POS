"""Phase 5A pure rules: cash-up maths and expense checks. No Django needed:
python3 -m unittest discover -s tests_pure -t ."""
import unittest
from datetime import date, timedelta
from decimal import Decimal as D

from accounting import rules as ar
from tills import cashup_rules as cr


class CountTests(unittest.TestCase):
    def test_total_is_the_sum_of_pieces_times_value(self):
        pieces, total = cr.parse_counts({"1000": "2", "500": 1, "50": "3", "1": 7})
        self.assertEqual(total, D("2657.00"))
        self.assertEqual(pieces[1000], 2)
        self.assertEqual(set(pieces), set(cr.DENOMINATIONS))     # every denomination is present, missing = 0
        self.assertEqual(pieces[200], 0)

    def test_empty_and_blank_fields_count_as_zero(self):
        self.assertEqual(cr.parse_counts({})[1], D("0.00"))
        self.assertEqual(cr.parse_counts(None)[1], D("0.00"))
        self.assertEqual(cr.parse_counts({"1000": "", "500": None})[1], D("0.00"))

    def test_the_forty_shilling_coin_is_counted(self):
        self.assertEqual(cr.parse_counts({"40": "5"})[1], D("200.00"))

    def test_bad_counts_are_refused(self):
        for bad in ({"1000": "-1"}, {"1000": "1.5"}, {"1000": "abc"}, {"1000": " "}, {"25": "1"}, {"x": "1"},
                    {"1": str(cr.MAX_PIECES + 1)}, "nope", [1, 2]):
            with self.assertRaises(cr.CashupError, msg=str(bad)):
                cr.parse_counts(bad)


class ExpectedCashTests(unittest.TestCase):
    def test_only_cash_and_float_make_up_the_drawer(self):
        # float 5000 + cash sales 7000 - cash refunds 500 - paid out 200
        self.assertEqual(cr.expected_cash("5000", "7000", "500", "200"), D("11300.00"))

    def test_variance_sign_and_labels(self):
        self.assertEqual(cr.variance("11000", "11300"), D("-300.00"))
        self.assertEqual(cr.classify("-300"), cr.SHORT)
        self.assertEqual(cr.classify("20"), cr.OVER)
        self.assertEqual(cr.classify("0.00"), cr.BALANCED)
        self.assertEqual(cr.describe("-300"), "Short by KES 300.00")
        self.assertEqual(cr.describe("0"), "Balanced")

    def test_a_mismatch_needs_a_real_reason_but_a_match_does_not(self):
        self.assertEqual(cr.check_reason("0", ""), "")
        with self.assertRaises(cr.CashupError):
            cr.check_reason("-5", "")
        with self.assertRaises(cr.CashupError):
            cr.check_reason("-5", "short")
        self.assertEqual(cr.check_reason("-5", "  gave wrong change twice  "), "gave wrong change twice")

    def test_cannot_close_with_sales_in_progress_or_on_hold(self):
        cr.check_can_close(open_carts=0, held_sales=0)
        with self.assertRaises(cr.CashupError):
            cr.check_can_close(open_carts=1, held_sales=0)
        with self.assertRaises(cr.CashupError):
            cr.check_can_close(open_carts=0, held_sales=2)


class ExpenseRuleTests(unittest.TestCase):
    today = date(2026, 11, 8)

    def test_amounts(self):
        self.assertEqual(ar.parse_amount("1,250.5"), D("1250.50"))
        for bad in ("0", "-5", "abc", "", "NaN", "Infinity", "99999999"):
            with self.assertRaises(ar.ExpenseError, msg=bad):
                ar.parse_amount(bad)

    def test_dates(self):
        self.assertEqual(ar.check_date(self.today, self.today), self.today)
        with self.assertRaises(ar.ExpenseError):
            ar.check_date(self.today + timedelta(days=1), self.today)
        with self.assertRaises(ar.ExpenseError):
            ar.check_date(self.today - timedelta(days=ar.MAX_BACKDATE_DAYS + 1), self.today)
        with self.assertRaises(ar.ExpenseError):
            ar.check_date(None, self.today)

    def test_description_source_and_void_reason(self):
        self.assertEqual(ar.check_description("  KPLC token  "), "KPLC token")
        with self.assertRaises(ar.ExpenseError):
            ar.check_description("ab")
        with self.assertRaises(ar.ExpenseError):
            ar.check_paid_from("PETTY")
        self.assertEqual(ar.check_paid_from("CASH_TILL"), "CASH_TILL")
        with self.assertRaises(ar.ExpenseError):
            ar.check_void_reason("oops")

    def test_default_categories_are_unique(self):
        self.assertEqual(len(ar.DEFAULT_CATEGORIES), len(set(ar.DEFAULT_CATEGORIES)))



class ProfitRuleTests(unittest.TestCase):
    """5B maths, pure."""

    def test_net_sales_removes_vat_and_refunds(self):
        from accounting import profit_rules as pr
        self.assertEqual(pr.net_sales("11600", "1600", "1160", "160"), D("9000.00"))

    def test_profit_and_loss_lines(self):
        from accounting import profit_rules as pr
        r = pr.profit_and_loss(net_sales="9000", cogs="6000", losses="100", expenses="1000", till_difference="-200")
        self.assertEqual((r["gross_profit"], r["net_profit"]), (D("3000.00"), D("1700.00")))
        self.assertEqual((r["gross_margin"], r["net_margin"]), (D("33.3"), D("18.9")))

    def test_an_overage_raises_and_a_shortage_lowers_net_profit(self):
        from accounting import profit_rules as pr
        base = dict(net_sales="1000", cogs="600", losses="0", expenses="0")
        self.assertEqual(pr.profit_and_loss(till_difference="50", **base)["net_profit"], D("450.00"))
        self.assertEqual(pr.profit_and_loss(till_difference="-50", **base)["net_profit"], D("350.00"))

    def test_no_sales_means_no_margin_not_a_division_error(self):
        from accounting import profit_rules as pr
        self.assertIsNone(pr.margin_pct("-500", "0"))
        r = pr.profit_and_loss(net_sales="0", cogs="0", losses="0", expenses="500", till_difference="0")
        self.assertEqual((r["net_profit"], r["net_margin"]), (D("-500.00"), None))

    def test_stock_losses_net_of_stock_found(self):
        from accounting import profit_rules as pr
        self.assertEqual(pr.stock_losses("100", "50", "30", "20"), D("160.00"))


if __name__ == "__main__":
    unittest.main()
