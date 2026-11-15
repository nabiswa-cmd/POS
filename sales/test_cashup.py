"""Phase 5: cash-up and closing a shift."""
import json
from decimal import Decimal as D

from django.test import Client

from core.models import AuditLog
from . import cashup, services
from .models import Sale
from .tests import SalesCase


class CashUpTests(SalesCase):
    def sell_cash(self):
        self.scan()
        services.checkout_cash(self.cashier, self.shift, "3500")           # 5000 float + 3500 = 8500 expected

    def test_exact_count_closes_the_shift_and_stores_the_figures(self):
        self.sell_cash()
        shift, summary = cashup.close_shift(self.cashier, self.shift, "8500")
        self.assertEqual((shift.expected_cash, shift.counted_cash, shift.variance), (D("8500.00"), D("8500.00"), D("0.00")))
        self.assertIsNotNone(shift.closed_at)
        self.assertTrue(AuditLog.objects.filter(action="shift.closed").exists())

    def test_difference_needs_a_note_and_is_recorded(self):
        self.sell_cash()
        with self.assertRaises(services.SaleError) as ctx:
            cashup.close_shift(self.cashier, self.shift, "8400")
        self.assertIn("short by KES 100.00", str(ctx.exception))
        shift, _ = cashup.close_shift(self.cashier, self.shift, "8400", "gave wrong change")
        self.assertEqual((shift.variance, shift.close_note), (D("-100.00"), "gave wrong change"))

    def test_cannot_close_twice_nor_someone_elses_nor_with_a_sale_in_progress(self):
        self.scan()
        with self.assertRaises(services.SaleError):
            cashup.close_shift(self.cashier, self.shift, "5000")           # cart has items
        services.void_sale(self.cashier, self.shift, approval_token=self.approval("void_sale", f"sale:{Sale.objects.get(status='OPEN').pk}"), reason="x")
        with self.assertRaises(services.NotAllowed):
            cashup.close_shift(self.supervisor, self.shift, "5000")
        cashup.close_shift(self.cashier, self.shift, "5000")
        with self.assertRaises(services.SaleError):
            cashup.close_shift(self.cashier, self.shift, "5000")

    def test_mpesa_is_not_in_the_drawer_and_closed_shift_cannot_sell(self):
        self.sell_cash()
        s = services.shift_summary(self.shift)
        self.assertEqual((s["card_sales"], s["bank_sales"]), ("0.00", "0.00"))
        cashup.close_shift(self.cashier, self.shift, "8500")
        with self.assertRaises(services.SaleError):
            services.add_item(self.cashier, self.shift, code="6001111111111", quantity=1)

    def test_endpoint(self):
        self.sell_cash()
        c = Client(); c.force_login(self.cashier)
        s = c.session; s["pos_till"] = 1; s.save()
        bad = c.post("/api/sales/shift/close/", json.dumps({"counted": "abc"}), content_type="application/json")
        self.assertEqual(bad.status_code, 400)
        ok = c.post("/api/sales/shift/close/", json.dumps({"counted": "8500"}), content_type="application/json")
        self.assertEqual((ok.status_code, ok.json()["variance"]), (200, "0.00"))


class CashUpReportTests(SalesCase):
    def test_report_lists_closed_shifts_and_is_permission_gated(self):
        self.scan(); services.checkout_cash(self.cashier, self.shift, "3500")
        cashup.close_shift(self.cashier, self.shift, "8400", "short change")
        c = Client(); c.force_login(self.cashier)
        self.assertEqual(c.get("/manage/cash-up/").status_code, 403)
        c = Client(); c.force_login(self.supervisor)
        html = c.get("/manage/cash-up/?days=3").content.decode()
        self.assertIn("short change", html); self.assertIn("-100.00", html)
