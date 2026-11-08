"""Cash only, M-PESA only, or both on one sale (one checkout for every way of paying)."""
import json
from decimal import Decimal as D

from django.test import Client, override_settings

from . import mpesa_services as ms
from . import receipt, rules, services
from .models import MpesaTransaction, Payment, Sale
from .test_phase4a import CFG
from .tests import SalesCase


class SplitTenderRuleTests(SalesCase):
    """The pure maths (SalesCase only because it is the project's test base; no database use here)."""

    def test_cash_only_is_the_whole_total_with_change(self):
        r = rules.split_tender("3500", [], "4000")
        self.assertEqual((r["mpesa_total"], r["cash_due"], r["change"]), (D("0.00"), D("3500.00"), D("500.00")))

    def test_mpesa_covering_everything_needs_no_cash(self):
        r = rules.split_tender("3500", ["3500"], None)
        self.assertEqual((r["cash_due"], r["tendered"], r["change"]), (D("0.00"), D("0.00"), D("0.00")))

    def test_mix_makes_cash_the_balance(self):
        r = rules.split_tender("3500", ["1000.00"], "2500")
        self.assertEqual((r["cash_due"], r["change"]), (D("2500.00"), D("0.00")))
        r = rules.split_tender("3500", ["1000.00"], "3000")
        self.assertEqual((r["cash_due"], r["change"]), (D("2500.00"), D("500.00")))

    def test_short_missing_and_surplus_are_refused(self):
        for args in (("3500", ["1000"], "2000"), ("3500", ["1000"], None), ("3500", [], "3000"),
                     ("3500", ["3600"], None), ("3500", ["3500"], "100"), ("3500", ["2000", "1600"], None)):
            with self.assertRaises(rules.RuleError, msg=str(args)):
                rules.split_tender(*args)


@override_settings(MPESA=CFG)
class SplitCheckoutTests(SalesCase):
    def paid(self, amount, trans_id, name=""):
        return MpesaTransaction.objects.create(trans_id=trans_id, source="C2B", amount=D(amount), customer_name=name)

    def test_cash_only_still_works_through_the_one_checkout(self):
        self.scan()
        sale, plan = ms.checkout_split(self.cashier, self.shift, [], "4000")
        pay = sale.payments.get()
        self.assertEqual((pay.method, pay.amount, pay.tendered, pay.change_given), (Payment.CASH, D("3500.00"), D("4000.00"), D("500.00")))
        self.assertEqual(self.stock(), 9)

    def test_mpesa_only_exact(self):
        self.scan()
        txn = self.paid("3500.00", "ONLYMPESA1", "JOHN KAMAU")
        sale, _ = ms.checkout_split(self.cashier, self.shift, [txn.pk])
        self.assertEqual([(p.method, p.amount) for p in sale.payments.all()], [(Payment.MPESA, D("3500.00"))])
        self.assertEqual(sale.customer_name, "JOHN KAMAU")

    def test_part_mpesa_plus_cash_records_two_payments_that_add_up(self):
        self.scan()
        txn = self.paid("1000.00", "PARTMPESA1", "JANE WANJIKU")
        sale, plan = ms.checkout_split(self.cashier, self.shift, [txn.pk], "2500")
        rows = {p.method: p for p in sale.payments.all()}
        self.assertEqual((rows["MPESA"].amount, rows["CASH"].amount, rows["CASH"].change_given), (D("1000.00"), D("2500.00"), D("0.00")))
        self.assertEqual(sum(p.amount for p in sale.payments.all()), sale.total)
        txn.refresh_from_db()
        self.assertEqual((txn.status, txn.sale_id), (MpesaTransaction.MATCHED, sale.pk))
        self.assertEqual((self.stock(), sale.customer_name), (9, "JANE WANJIKU"))

    def test_change_only_comes_from_the_cash_part(self):
        self.scan()
        txn = self.paid("1000.00", "CHANGEMPES1")
        sale, plan = ms.checkout_split(self.cashier, self.shift, [txn.pk], "3000")
        cash = sale.payments.get(method=Payment.CASH)
        self.assertEqual((cash.amount, cash.tendered, cash.change_given), (D("2500.00"), D("3000.00"), D("500.00")))

    def test_two_mpesa_payments_plus_cash(self):
        self.scan()
        a, b = self.paid("1000.00", "TWOMPESA01"), self.paid("500.00", "TWOMPESA02")
        sale, _ = ms.checkout_split(self.cashier, self.shift, [a.pk, b.pk], "2000")
        self.assertEqual(sale.payments.count(), 3)
        self.assertEqual(sum(p.amount for p in sale.payments.all()), D("3500.00"))

    def test_cash_up_keeps_cash_and_mpesa_apart(self):
        self.scan()
        ms.checkout_split(self.cashier, self.shift, [self.paid("1000.00", "SUMMARY001").pk], "3000")   # 500 change
        s = services.shift_summary(self.shift)
        self.assertEqual((s["cash_sales"], s["mpesa_sales"], s["sales_total"]), ("2500.00", "1000.00", "3500.00"))
        self.assertEqual(s["cash_in_drawer_expected"], "7500.00")          # float 5000 + only the cash part

    def test_receipt_lists_each_part(self):
        self.scan()
        sale, _ = ms.checkout_split(self.cashier, self.shift, [self.paid("1000.00", "RECEIPT001").pk], "2500")
        text = "\n".join(receipt.render(receipt.get_completed(sale.pk))[1])
        self.assertIn("Paid by M-PESA", text)
        self.assertIn("Paid by CASH", text)
        self.assertIn("RECEIPT001", text)

    def test_mpesa_part_without_the_cash_balance_sells_nothing(self):
        self.scan()
        txn = self.paid("1000.00", "NOBALANCE01")
        with self.assertRaises(ms.SaleError):
            ms.checkout_split(self.cashier, self.shift, [txn.pk], None)
        with self.assertRaises(ms.SaleError):
            ms.checkout_split(self.cashier, self.shift, [txn.pk], "2000")          # 500 short
        txn.refresh_from_db()
        self.assertEqual((txn.status, self.stock(), Payment.objects.count()), (MpesaTransaction.UNMATCHED, 10, 0))

    def test_mpesa_larger_than_the_sale_is_refused(self):
        self.scan()
        txn = self.paid("4000.00", "TOOMUCH0001")
        with self.assertRaises(ms.SaleError):
            ms.checkout_split(self.cashier, self.shift, [txn.pk])
        self.assertEqual((self.stock(), Payment.objects.count()), (10, 0))

    def test_a_used_payment_or_a_repeated_id_is_refused_and_nothing_else_is_used_up(self):
        self.scan()
        good, used = self.paid("1000.00", "GOODONE0001"), self.paid("500.00", "USEDONE0001")
        used.status = MpesaTransaction.MATCHED
        used.save(update_fields=["status"])
        with self.assertRaises(ms.SaleError):
            ms.checkout_split(self.cashier, self.shift, [good.pk, used.pk], "2000")
        with self.assertRaises(ms.SaleError):
            ms.checkout_split(self.cashier, self.shift, [good.pk, good.pk], "2000")
        good.refresh_from_db()
        self.assertEqual((good.status, Payment.objects.count()), (MpesaTransaction.UNMATCHED, 0))

    def test_unknown_payment_id_is_refused(self):
        self.scan()
        with self.assertRaises(ms.SaleError):
            ms.checkout_split(self.cashier, self.shift, [999999], "3500")

    def test_part_stk_request_amount_is_limited_to_the_sale(self):
        self.scan()
        with self.assertRaises(ms.SaleError):
            ms.start_stk(self.cashier, self.shift, "0712345678", amount="5000")
        with self.assertRaises(ms.SaleError):
            ms.start_stk(self.cashier, self.shift, "0712345678", amount="1000.50")       # STK takes whole shillings

    def test_old_single_method_paths_are_untouched(self):
        self.scan()
        sale = services.checkout_cash(self.cashier, self.shift, "3500")
        self.assertEqual(sale.payments.get().amount, D("3500.00"))
        self.scan()
        sale = ms.checkout_mpesa(self.cashier, self.shift, self.paid("3500.00", "OLDPATH0001").pk)
        self.assertEqual(sale.payments.get().method, Payment.MPESA)


@override_settings(MPESA=CFG)
class SplitEndpointTests(SalesCase):
    def setUp(self):
        super().setUp()
        self.c = Client()
        self.c.force_login(self.cashier)
        session = self.c.session
        session["pos_till"] = 1
        session.save()

    def post(self, body):
        return self.c.post("/api/sales/checkout/", json.dumps(body), content_type="application/json")

    def test_endpoint_completes_a_mixed_sale(self):
        self.scan()
        txn = MpesaTransaction.objects.create(trans_id="ENDPOINT001", source="C2B", amount=D("1000.00"))
        res = self.post({"mpesa_ids": [txn.pk], "cash_tendered": "3000"}).json()
        self.assertTrue(res["ok"], res)
        self.assertEqual((res["mpesa"], res["cash"], res["change"], res["references"]), ("1000.00", "2500.00", "500.00", ["ENDPOINT001"]))
        self.assertEqual(Sale.objects.filter(status="COMPLETED").count(), 1)

    def test_endpoint_errors_are_clean_json_and_sell_nothing(self):
        self.scan()
        res = self.post({"mpesa_ids": ["abc"]})
        self.assertEqual((res.status_code, res.json()["ok"]), (400, False))
        res = self.post({"mpesa_ids": [], "cash_tendered": "100"})
        self.assertEqual((res.status_code, res.json()["ok"]), (400, False))
        self.assertEqual(Sale.objects.filter(status="COMPLETED").count(), 0)

    def test_stk_status_tells_the_screen_the_amount_and_receipt_to_apply(self):
        from .models import MpesaStkRequest
        sale, _ = self.scan()
        txn = MpesaTransaction.objects.create(trans_id="STKSTATUS01", source="STK", amount=D("1000.00"))
        stk = MpesaStkRequest.objects.create(sale=sale, cashier=self.cashier, shift=self.shift, phone="254712345678",
                                             amount=D("1000"), checkout_request_id="ws_CO_9", status="CONFIRMED", transaction=txn)
        res = self.c.get(f"/api/sales/mpesa/stk/{stk.pk}/").json()
        self.assertEqual((res["status"], res["transaction_id"], res["amount"], res["receipt"]),
                         ("CONFIRMED", txn.pk, "1000.00", "STKSTATUS01"))

    def test_screen_uses_the_one_checkout_endpoint(self):
        html = self.c.get("/pos/sell/").content.decode() if False else open("sales/templates/sales/pos.html", encoding="utf-8").read()
        self.assertIn("/api/sales/checkout/", html)
        self.assertNotIn("/api/sales/checkout/cash/", html)
