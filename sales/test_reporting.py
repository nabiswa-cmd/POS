"""Management reports for partial sales, refunds, inventory and audited activity."""
from datetime import datetime, time, timedelta
from decimal import Decimal as D

from django.test import Client
from django.utils import timezone

from catalog.models import SellingUnit
from core import audit
from inventory import movement_rules as mr
from inventory import services as stock_services
from inventory.models import Bottle
from . import refunds, reporting, services
from .models import Sale, SaleItem
from .tests import SalesCase


class SalesReportingTests(SalesCase):
    def setUp(self):
        super().setUp()
        self.product.bottle_capacity_ml = D("750")
        self.product.save(update_fields=["bottle_capacity_ml"])
        self.tot = SellingUnit.objects.create(product=self.product, name="TOT", volume_ml=D("30"),
                                              selling_price=D("100"))
        self.today = timezone.localdate()

    def sell_partial(self, quantity=2):
        sale, _ = services.add_item(self.cashier, self.shift, selling_unit_id=self.tot.pk, quantity=quantity)
        return services.checkout_cash(self.cashier, self.shift, str(sale.total))

    def approved_partial_refund(self, sale, quantity=1):
        item = sale.items.get()
        refund = refunds.request_refund(
            self.cashier, self.shift, sale.pk,
            [{"item_id": item.pk, "quantity": quantity}], "Customer returned measure", "CASH")
        token = self.approval("refund", refunds.ref(refund.pk))
        return refunds.approve_refund(self.cashier, refund.pk, approval_token=token)

    def test_unit_revenue_refund_and_volume_use_frozen_sale_snapshots(self):
        self.scan()
        full_sale = services.checkout_cash(self.cashier, self.shift, "3500")
        partial_sale = self.sell_partial()
        self.approved_partial_refund(partial_sale)
        full_item = full_sale.items.get()
        full_refund = refunds.request_refund(
            self.cashier, self.shift, full_sale.pk,
            [{"item_id": full_item.pk, "quantity": 1}], "Full bottle returned", "CASH")
        refunds.approve_refund(
            self.cashier, full_refund.pk,
            approval_token=self.approval("refund", refunds.ref(full_refund.pk)))
        SellingUnit.objects.filter(pk=self.tot.pk).update(
            name="Renamed TOT", volume_ml=D("60"), selling_price=D("999"))

        report = reporting.report(self.today, self.today)
        rows = report["selling_units"]
        full = next(row for row in rows if not row["is_partial"])
        partial = next(row for row in rows if row["is_partial"])

        self.assertEqual((full["quantity_sold"], full["quantity_refunded"], full["full_bottles_sold"],
                          full["full_bottles_refunded"], full["net_revenue"],
                          full["volume_sold_ml"], full["volume_refunded_ml"]),
                         (1, 1, 1, 1, D("0.00"), D("750.00"), D("750.00")))
        self.assertEqual((partial["selling_unit"], partial["quantity_sold"], partial["quantity_refunded"],
                          partial["volume_sold_ml"], partial["volume_refunded_ml"],
                          partial["sales_gross"], partial["refunds_gross"], partial["net_revenue"]),
                         ("TOT (30.00 ml)", 2, 1, D("60.00"), D("30.00"),
                          D("200.00"), D("100.00"), D("100.00")))
        self.assertEqual((report["summary"]["full_revenue"], report["summary"]["partial_revenue"],
                          report["summary"]["refunds_gross"]),
                         (D("0.00"), D("100.00"), full_sale.total + D("100.00")))
        self.assertEqual((report["summary"]["total_liquid_volume_sold_ml"],
                          report["summary"]["total_liquid_volume_refunded_ml"],
                          report["summary"]["net_liquid_volume_ml"]),
                         (D("810.00"), D("780.00"), D("30.00")))
        refund_row = report["refunds_by_day"][0]
        self.assertEqual((refund_row["requested_by"], refund_row["approved_by"], refund_row["refund_count"],
                          refund_row["full_bottles"], refund_row["partial_measures"],
                          refund_row["full_volume_ml"], refund_row["partial_volume_ml"], refund_row["gross"]),
                         (self.cashier.display_name, self.supervisor.display_name, 2, 1, 1,
                          D("750.00"), D("30.00"), full_sale.total + D("100.00")))

    def test_daily_cashier_report_separates_full_and_partial_sales(self):
        self.scan()
        services.checkout_cash(self.cashier, self.shift, "3500")
        self.sell_partial()

        rows = reporting.sales_by_day_cashier(self.today, self.today)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual((row["day"], row["cashier"], row["authorized_by"], row["sale_count"],
                          row["full_bottles"], row["partial_measures"], row["partial_volume_ml"],
                          row["full_volume_ml"], row["total_volume_ml"],
                          row["full_revenue"], row["partial_revenue"], row["sales_revenue"]),
                         (self.today, self.cashier.display_name, self.supervisor.display_name, 2, 1, 2,
                          D("60.00"), D("750.00"), D("810.00"),
                          D("3500.00"), D("200.00"), D("3700.00")))

    def test_mixed_cart_and_legacy_full_sales_report_volume_without_guessing(self):
        sale, _ = self.scan()
        sale, _ = services.add_item(self.cashier, self.shift, selling_unit_id=self.tot.pk, quantity=2)
        mixed = services.checkout_cash(self.cashier, self.shift, str(sale.total))
        mixed_report = reporting.report(self.today, self.today)
        self.assertEqual((mixed_report["summary"]["full_bottles_sold"],
                          mixed_report["summary"]["partial_measures_sold"],
                          mixed_report["summary"]["total_liquid_volume_sold_ml"]),
                         (1, 2, D("810.00")))
        self.assertEqual((mixed_report["sales_by_cashier"][0]["sale_count"],
                          mixed_report["sales_by_cashier"][0]["total_volume_ml"]),
                         (1, D("810.00")))

        SaleItem.objects.filter(sale=mixed, total_volume_ml__isnull=True).update(
            full_volume_per_sale_unit_ml=None)
        legacy_report = reporting.report(self.today, self.today)
        self.assertIsNone(legacy_report["summary"]["total_liquid_volume_sold_ml"])
        self.assertEqual(legacy_report["summary"]["known_volume_sold_ml"], D("60.00"))
        self.assertEqual(legacy_report["summary"]["unknown_full_units_sold"], 1)

    def test_daily_weekly_monthly_and_arbitrary_range_dates_filter_sales(self):
        start_week = self.today - timedelta(days=self.today.weekday())
        first_month = self.today.replace(day=1)
        sale_days = [self.today, start_week, first_month]
        for day in sale_days:
            sale, _ = self.scan()
            done = services.checkout_cash(self.cashier, self.shift, "3500")
            local_noon = timezone.make_aware(datetime.combine(day, time(12, 0)))
            Sale.objects.filter(pk=done.pk).update(completed_at=local_noon)

        count = lambda start, end: sum(start <= day <= end for day in sale_days)
        self.assertEqual(sum(r["sale_count"] for r in reporting.sales_by_day_cashier(self.today, self.today)),
                         count(self.today, self.today))
        self.assertEqual(sum(r["sale_count"] for r in reporting.sales_by_day_cashier(start_week, self.today)),
                         count(start_week, self.today))
        self.assertEqual(sum(r["sale_count"] for r in reporting.sales_by_day_cashier(first_month, self.today)),
                         count(first_month, self.today))
        self.assertEqual(sum(r["sale_count"] for r in reporting.sales_by_day_cashier(
            self.today - timedelta(days=40), self.today - timedelta(days=10))),
            count(self.today - timedelta(days=40), self.today - timedelta(days=10)))
        manager = Client()
        manager.force_login(self.manager)
        page = manager.get(f"/manage/sales-report/?from={first_month.isoformat()}&to={self.today.isoformat()}")
        self.assertEqual((page.status_code, page.context["day_from"], page.context["day_to"]),
                         (200, first_month, self.today))

    def test_stock_movements_and_current_inventory_reconcile_without_double_counting_openings(self):
        first = stock_services.open_bottle(product_id=self.product.pk, capacity_ml=D("750"),
                                           user=self.manager, operation_key="report-open-1")
        stock_services.record_wastage(product_id=self.product.pk, volume_ml=D("40"), user=self.manager,
                                      operation_key="report-waste", reason="Spillage in the service area")
        stock_services.adjust_bottle_volume(bottle_id=first.pk, volume_ml=D("10"), user=self.manager,
                                            operation_key="report-adjust-open", reason="Measured reconciliation")
        second = stock_services.open_bottle(product_id=self.product.pk, capacity_ml=D("750"),
                                            user=self.manager, operation_key="report-open-2")
        stock_services.adjust_bottle_volume(bottle_id=second.pk, volume_ml=D("-750"), user=self.manager,
                                            operation_key="report-adjust-empty", reason="Bottle confirmed empty")

        position = reporting.inventory_position()
        row = next(item for item in position["products"] if item["product"].pk == self.product.pk)
        movement_rows = {item["movement_type"]: item for item in reporting.movement_summary(self.today, self.today)}
        movement_details = reporting.stock_movements(self.today, self.today).select_related("user")
        wastage = movement_details.get(movement_type=mr.VOLUME_WASTAGE)

        self.assertEqual((row["sealed_bottles"], row["opened_bottles"], row["empty_bottles"],
                          row["opened_volume_ml"], row["sealed_volume_ml"], row["total_volume_ml"]),
                         (8, 1, 1, D("720.00"), D("6000.00"), D("6720.00")))
        self.assertEqual((position["unit_mismatch_count"], position["bottle_mismatch_count"]), (0, 0))
        self.assertEqual((movement_rows[mr.BOTTLE_OPEN]["events"], movement_rows[mr.BOTTLE_OPEN]["volume_ml"],
                          movement_rows[mr.VOLUME_WASTAGE]["volume_ml"],
                          movement_rows[mr.VOLUME_ADJUST]["volume_ml"]),
                         (2, D("1500.00"), D("-40.00"), D("-740.00")))
        self.assertEqual((wastage.reason, wastage.user), ("Spillage in the service area", self.manager))

        Bottle.objects.filter(pk=first.pk).update(remaining_ml=D("719.00"))
        reconciled = reporting.inventory_position()
        self.assertEqual((reconciled["bottle_mismatch_count"], reconciled["bottle_mismatch_volume_ml"]),
                         (1, D("1.00")))

    def test_report_page_permissions_and_audited_manager_supervisor_activity(self):
        audit.record("sale.discount_applied", actor=self.cashier, approver=self.supervisor,
                     object_type="sale", object_ref="REPORT-TEST", reason="Approved test discount")
        manager = Client()
        manager.force_login(self.manager)
        supervisor = Client()
        supervisor.force_login(self.supervisor)
        cashier = Client()
        cashier.force_login(self.cashier)

        self.assertEqual(Client().get("/manage/sales-report/").status_code, 302)
        self.assertEqual(cashier.get("/manage/sales-report/").status_code, 403)
        manager_page = manager.get("/manage/sales-report/")
        supervisor_page = supervisor.get("/manage/sales-report/")
        self.assertEqual((manager_page.status_code, supervisor_page.status_code), (200, 200))
        self.assertContains(manager_page, "Manager and supervisor activity")
        self.assertContains(manager_page, "Approved test discount")
        self.assertContains(manager_page, "Sales reports")
        self.assertContains(supervisor_page, "Sales reports")
        self.assertNotContains(supervisor_page, "Manager and supervisor activity")
        self.assertContains(supervisor_page, "Current inventory and ledger reconciliation")
        self.assertContains(manager_page, "This week")
        self.assertContains(manager_page, "Last month")
