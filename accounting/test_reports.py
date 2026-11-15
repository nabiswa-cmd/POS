"""Phase 5B: profit and loss, by product, by day, and the report screen."""
import csv
from datetime import timedelta
from decimal import Decimal as D

from django.db.models import Sum
from django.test import Client, override_settings
from django.utils import timezone

from catalog.models import SellingUnit
from core.models import ShopSettings
from inventory import movement_rules as mr
from inventory import services as stock_services
from inventory.models import StockMovement
from sales import refunds, services
from sales.models import Sale
from sales.test_phase4a import CFG
from sales.tests import SalesCase
from tills import cashup

from . import reports, services as expenses
from .models import ExpenseCategory

# SalesCase: price 3500, cost 2400, 10 bottles received.


@override_settings(MPESA=CFG)
class ProfitCase(SalesCase):
    def setUp(self):
        super().setUp()
        self.today = timezone.localdate()
        self.cat = ExpenseCategory.objects.get(name="Rent")

    def sell(self, qty=2):
        self.scan(qty=qty)
        return services.checkout_cash(self.cashier, self.shift, str(3500 * qty))

    def f(self):
        return reports.figures(self.today, self.today)

    def refund(self, sale, qty=1, restock=True):
        item = sale.items.get()
        r = refunds.request_refund(self.cashier, self.shift, sale.pk,
                                   [{"item_id": item.pk, "quantity": qty, "restock": restock}], "Customer returned it", "CASH")
        refunds.approve_refund(self.cashier, r.pk, approval_token=self.approval("refund", refunds.ref(r.pk)))
        return r

    def partial_sale(self, *, volume="30", price="100", qty=2):
        self.product.bottle_capacity_ml = D("750")
        self.product.save(update_fields=["bottle_capacity_ml"])
        unit = SellingUnit.objects.create(product=self.product, name=f"Measure {volume}",
                                          volume_ml=D(volume), selling_price=D(price))
        sale, item = services.add_item(self.cashier, self.shift, selling_unit_id=unit.pk, quantity=qty)
        done = services.checkout_cash(self.cashier, self.shift, str(sale.total))
        return done, item

    def refund_item(self, sale, item, qty, restock=True):
        r = refunds.request_refund(self.cashier, self.shift, sale.pk,
                                   [{"item_id": item.pk, "quantity": qty, "restock": restock}],
                                   "Customer returned it", "CASH")
        refunds.approve_refund(self.cashier, r.pk, approval_token=self.approval("refund", refunds.ref(r.pk)))
        return r

    def spend(self, amount, **kw):
        args = dict(date=self.today, category_id=self.cat.pk, amount=amount, description="Shop rent", paid_from="BANK")
        args.update(kw)
        return expenses.record_expense(self.manager, **args)


class GrossProfitTests(ProfitCase):
    def test_profit_uses_the_cost_of_what_was_sold_not_selling_price_alone(self):
        self.sell(2)
        f = self.f()
        self.assertEqual((f["net_sales"], f["cogs"], f["gross_profit"]), (D("7000.00"), D("4800.00"), D("2200.00")))
        self.assertEqual(f["gross_margin"], D("31.4"))

    def test_a_later_cost_change_does_not_rewrite_old_profit(self):
        self.sell(2)
        stock_services.post_movement(product_id=self.product.pk, movement_type=mr.RECEIVE, quantity=5, user=self.clerk,
                                     unit_cost=D("3000"))
        self.assertEqual(self.f()["gross_profit"], D("2200.00"))

    def test_stock_bought_is_shown_but_is_not_a_cost_of_sales(self):
        self.sell(1)
        stock_services.post_movement(product_id=self.product.pk, movement_type=mr.RECEIVE, quantity=5, user=self.clerk,
                                     unit_cost=D("3000"))
        f = self.f()
        self.assertEqual(f["stock_bought"], D("39000.00"))       # 10 x 2400 (setUp delivery) + 5 x 3000
        self.assertEqual(f["gross_profit"], D("1100.00"))

    def test_restocked_refund_reduces_sales_and_cost(self):
        sale = self.sell(2)
        self.refund(sale, 1)
        f = self.f()
        self.assertEqual((f["net_sales"], f["net_cogs"], f["gross_profit"]), (D("3500.00"), D("2400.00"), D("1100.00")))

    def test_refund_of_goods_not_fit_to_resell_loses_the_cost_too(self):
        sale = self.sell(2)
        self.refund(sale, 1, restock=False)
        f = self.f()
        self.assertEqual((f["net_sales"], f["net_cogs"], f["gross_profit"]), (D("3500.00"), D("4800.00"), D("-1300.00")))

    def test_a_requested_but_unapproved_refund_changes_nothing(self):
        sale = self.sell(2)
        item = sale.items.get()
        refunds.request_refund(self.cashier, self.shift, sale.pk, [{"item_id": item.pk, "quantity": 1}], "Customer returned it", "CASH")
        self.assertEqual(self.f()["gross_profit"], D("2200.00"))

    def test_vat_is_taken_out_of_net_sales(self):
        shop = ShopSettings.load()
        shop.vat_registered, shop.kra_pin = True, "P051234567X"
        shop.save()
        sale = self.sell(2)
        sale.refresh_from_db()
        f = self.f()
        self.assertGreater(sale.tax_total, 0)
        self.assertEqual((f["sales_vat"], f["net_sales"]), (sale.tax_total, sale.total - sale.tax_total))
        self.assertEqual(f["gross_profit"], sale.total - sale.tax_total - D("4800.00"))

    def test_partial_revenue_is_reported_net_of_its_saved_vat(self):
        shop = ShopSettings.load()
        shop.vat_registered, shop.kra_pin = True, "P051234567X"
        shop.save()
        sale, item = self.partial_sale(qty=2)
        item.refresh_from_db()
        f = self.f()

        self.assertEqual(f["partial_sales"], item.line_total - item.tax_amount)
        self.assertEqual(f["net_sales"], sale.total - sale.tax_total)

    def test_partial_tot_revenue_is_not_counted_as_a_full_bottle(self):
        sale, _item = self.partial_sale(qty=2)
        f = self.f()

        self.assertEqual((f["net_sales"], f["full_units_sold"], f["partial_units_sold"],
                          f["partial_volume_sold_ml"], f["full_sales"], f["partial_sales"]),
                         (sale.total, 0, 2, D("60.00"), D("0.00"), D("200.00")))

    def test_half_bottle_uses_its_saved_partial_price_and_volume(self):
        sale, _item = self.partial_sale(volume="375", price="1750", qty=1)
        f = self.f()

        self.assertEqual((f["net_sales"], f["full_units_sold"], f["partial_units_sold"],
                          f["partial_volume_sold_ml"], f["partial_sales"], f["cogs"]),
                         (sale.total, 0, 1, D("375.00"), D("1750.00"), D("1200.00")))

    def test_mixed_full_and_partial_sale_splits_revenue_units_and_keeps_cashup_amount(self):
        _sale, _full_item = self.scan()
        sale, _partial_item = self.partial_sale(qty=2)
        f = self.f()
        snapshot = cashup.snapshot(self.shift)

        self.assertEqual((f["net_sales"], f["full_units_sold"], f["partial_units_sold"],
                          f["partial_volume_sold_ml"], f["full_sales"], f["partial_sales"]),
                         (D("3700.00"), 1, 2, D("60.00"), D("3500.00"), D("200.00")))
        self.assertEqual((snapshot["sales_total"], snapshot["cash_sales"], snapshot["expected_cash"]),
                         (D("3700.00"), D("3700.00"), D("8700.00")))

    def test_partial_refund_reverses_revenue_volume_and_restocked_cost(self):
        sale, item = self.partial_sale(qty=2)
        self.refund_item(sale, item, 1)
        f = self.f()
        row = reports.by_product(self.today, self.today)[0]

        self.assertEqual((f["net_sales"], f["partial_refunds"], f["net_partial_units"],
                          f["net_partial_volume_ml"], f["cogs"], f["cogs_returned"], f["net_cogs"]),
                         (D("100.00"), D("100.00"), 1, D("30.00"),
                          D("192.00"), D("96.00"), D("96.00")))
        self.assertEqual((row["units"], row["partial_units"], row["partial_volume_ml"],
                          row["partial_units_refunded"], row["partial_volume_refunded_ml"],
                          row["net_partial_units"], row["net_partial_volume_ml"],
                          row["partial_sales"], row["sales"], row["cost"]),
                         (0, 2, D("60.00"), 1, D("30.00"), 1, D("30.00"),
                          D("100.00"), D("100.00"), D("96.00")))

    def test_partial_refund_without_restock_does_not_reverse_cost_of_goods(self):
        sale, item = self.partial_sale(qty=2)
        self.refund_item(sale, item, 1, restock=False)
        f = self.f()

        self.assertEqual((f["net_sales"], f["partial_volume_refunded_ml"],
                          f["cogs_returned"], f["net_cogs"]),
                         (D("100.00"), D("30.00"), D("0.00"), D("192.00")))

    def test_full_refund_reverses_full_bottle_revenue_and_returned_cost(self):
        sale = self.sell(1)
        self.refund(sale, 1)
        f = self.f()

        self.assertEqual((f["net_sales"], f["full_units_sold"], f["full_units_refunded"],
                          f["net_full_units"], f["cogs_returned"], f["net_cogs"]),
                         (D("0.00"), 1, 1, 0, D("2400.00"), D("0.00")))

    def test_full_refund_of_partial_line_restores_all_volume_and_cost(self):
        sale, item = self.partial_sale(qty=2)
        self.refund_item(sale, item, 2)
        f = self.f()

        self.assertEqual((f["net_sales"], f["partial_units_refunded"], f["net_partial_units"],
                          f["partial_volume_refunded_ml"], f["net_partial_volume_ml"],
                          f["cogs_returned"], f["net_cogs"]),
                         (D("0.00"), 2, 0, D("60.00"), D("0.00"), D("192.00"), D("0.00")))

    def test_partial_sale_volume_reconciles_to_stock_movements_without_double_counting_opening(self):
        sale, _item = self.partial_sale(qty=2)
        self.refund_item(sale, sale.items.get(), 1)
        f = self.f()
        volume_moves = StockMovement.objects.filter(
            movement_type__in=(mr.PARTIAL_SALE, mr.VOLUME_REFUND)).aggregate(t=Sum("volume_ml"))["t"]
        stock = stock_services.partial_stock(self.product.pk)

        self.assertEqual((volume_moves, f["net_partial_volume_ml"], stock["total_volume_ml"]),
                         (D("-30.00"), D("30.00"), D("7470.00")))

    def test_partial_sales_follow_sale_day_and_daily_report_breakdown(self):
        sale, _item = self.partial_sale(qty=2)
        yesterday = self.today - timedelta(days=1)
        Sale.objects.filter(pk=sale.pk).update(
            completed_at=timezone.now() - timedelta(days=1))

        self.assertEqual(self.f()["partial_sales"], D("0.00"))
        prior = reports.figures(yesterday, yesterday)
        self.assertEqual((prior["partial_sales"], prior["partial_volume_sold_ml"]),
                         (D("200.00"), D("60.00")))
        self.assertIn("200.00", reports.csv_days(yesterday, yesterday))


class NetProfitTests(ProfitCase):
    def test_expenses_reduce_net_profit_and_voided_ones_do_not(self):
        self.sell(2)
        self.spend("500")
        gone = self.spend("9999", description="Wrong entry")
        expenses.void_expense(self.manager, gone.pk, "Entered twice by mistake")
        f = self.f()
        self.assertEqual((f["expenses"], f["gross_profit"], f["net_profit"]), (D("500.00"), D("2200.00"), D("1700.00")))

    def test_damaged_stock_is_a_loss_at_cost(self):
        self.sell(2)
        stock_services.post_movement(product_id=self.product.pk, movement_type=mr.DAMAGED, quantity=-1, user=self.manager)
        f = self.f()
        self.assertEqual((f["stock_losses"], f["net_profit"]), (D("2400.00"), D("-200.00")))

    def test_stock_found_in_an_adjustment_offsets_losses(self):
        stock_services.post_movement(product_id=self.product.pk, movement_type=mr.EXPIRED, quantity=-2, user=self.manager)
        stock_services.post_movement(product_id=self.product.pk, movement_type=mr.ADJUST, quantity=1, user=self.manager,
                                     unit_cost=D("2400"))
        self.assertEqual(self.f()["stock_losses"], D("2400.00"))     # 4800 lost - 2400 found

    def test_a_short_till_lowers_net_profit(self):
        self.sell(1)
        cashup.close_shift(self.cashier, self.shift.pk, {"1000": "8", "100": "3"}, "Gave wrong change twice")   # 8300 vs 8500
        f = self.f()
        self.assertEqual((f["till_difference"], f["gross_profit"], f["net_profit"]), (D("-200.00"), D("1100.00"), D("900.00")))

    def test_only_the_chosen_period_counts(self):
        self.sell(2)
        self.spend("300", date=self.today - timedelta(days=40), description="Old repair")
        self.assertEqual(self.f()["expenses"], D("0.00"))
        self.assertEqual(reports.figures(self.today - timedelta(days=60), self.today)["expenses"], D("300.00"))

    def test_nothing_sold_gives_zero_not_an_error(self):
        f = self.f()
        self.assertEqual((f["net_sales"], f["gross_profit"], f["net_profit"], f["gross_margin"]),
                         (D("0.00"), D("0.00"), D("0.00"), None))


class BreakdownTests(ProfitCase):
    def test_product_rows_add_up_to_the_totals_including_refunds(self):
        sale = self.sell(3)
        self.refund(sale, 1)
        rows, f = reports.by_product(self.today, self.today), self.f()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["units"], rows[0]["sales"], rows[0]["cost"], rows[0]["profit"]),
                         (3, f["net_sales"], f["net_cogs"], f["gross_profit"]))
        self.assertEqual(rows[0]["profit"], D("2200.00"))             # 2 bottles kept: 7000 - 4800

    def test_day_table_has_the_days_that_had_activity(self):
        self.sell(1)
        self.spend("100")
        days = reports.by_day(self.today - timedelta(days=3), self.today)
        self.assertEqual([d["day_from"] for d in days], [self.today])
        self.assertEqual(days[0]["net_profit"], D("1000.00"))

    def test_collections_keep_cash_and_mpesa_apart(self):
        self.sell(1)
        c = reports.collections(self.today, self.today)
        self.assertEqual((c["cash"], c["mpesa"], c["card_bank"]), (D("3500.00"), D("0.00"), D("0.00")))


class ScreenTests(ProfitCase):
    def login(self, user):
        c = Client()
        c.force_login(user)
        return c

    def test_only_people_with_the_financial_permission_see_costs(self):
        self.assertEqual(Client().get("/manage/reports/").status_code, 302)
        for u in (self.cashier, self.supervisor, self.clerk):
            self.assertEqual(self.login(u).get("/manage/reports/").status_code, 403)
        self.assertEqual(self.login(self.manager).get("/manage/reports/").status_code, 200)

    def test_screen_and_downloads_show_the_figures(self):
        self.sell(2)
        self.spend("500")
        m = self.login(self.manager)
        page = m.get("/manage/reports/")
        self.assertContains(page, "2200.00")
        self.assertContains(page, "1700.00")
        self.assertIn("Net profit,1700.00", m.get("/manage/reports/?csv=summary").content.decode())
        self.assertIn("Gross profit", m.get("/manage/reports/?csv=products").content.decode())
        self.assertIn("Net profit", m.get("/manage/reports/?csv=days").content.decode())

    def test_stock_value_includes_remaining_liquid_in_open_bottles(self):
        self.partial_sale(qty=2)
        manager = self.login(self.manager)
        response = manager.get("/manage/stock/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["value"], D("23808.00"))
        self.assertEqual(response.context["page"][0].value, D("23808.00"))

    def test_partial_sales_are_distinguished_in_report_page_and_csv_exports(self):
        self.partial_sale(qty=2)
        manager = self.login(self.manager)
        page = manager.get("/manage/reports/")
        products = manager.get("/manage/reports/?csv=products").content.decode()
        summary = manager.get("/manage/reports/?csv=summary").content.decode()
        days = manager.get("/manage/reports/?csv=days").content.decode()
        summary_rows = {row[0]: row[1] for row in csv.reader(summary.splitlines()) if len(row) == 2}

        self.assertContains(page, "Partial measures sold / refunded / net")
        self.assertContains(page, "Partial volume sold / refunded / net (ml)")
        self.assertIn("Partial measures sold", products)
        self.assertIn("60.00", products)
        self.assertEqual(summary_rows["Partial sales revenue (excl. VAT, net of refunds)"], "200.00")
        self.assertIn("Partial sales (excl. VAT)", days)
        self.assertIn(",200.00,60.00,", days)
