"""Phase 4: manager screens for bottles - sealed/opened counts, history, authorized wastage and adjustments."""
from decimal import Decimal as D

from accounts.models import ApprovalPolicy
from catalog import services as catalog_services
from catalog.models import Product, SellingUnit
from catalog.test_selling_units import make_role_user
from catalog.tests import CatalogCase, make_product
from core.models import AuditLog

from . import movement_rules as mr
from . import services
from .models import Bottle, StockMovement


class BottleAdminCase(CatalogCase):
    def setUp(self):
        super().setUp()
        services.adjust_stock(product_id=self.product.pk, movement_type=mr.OPENING, quantity=5, user=self.manager,
                              reason="Initial count")
        self.url = f"/manage/stock/{self.product.pk}/bottles/"

    def sell(self, volume, key):
        return services.sell_volume(product_id=self.product.pk, volume_ml=D(volume), user=self.cashier,
                                    operation_key=key, bottle_capacity_ml=D("750"))

    def controller(self, *codes):
        return make_role_user(self, "controller", "products.view", "stock.view", *codes)

    def bottle(self):
        return Bottle.objects.get(product=self.product)


class OverviewTests(BottleAdminCase):
    def test_overview_before_any_bottle_is_opened_has_no_sealed_volume_yet(self):
        o = services.bottle_overview(self.product.pk)
        self.assertEqual((o["sealed_bottles"], o["opened_bottles"], o["empty_bottles"]), (5, 0, 0))
        self.assertIsNone(o["sealed_volume_ml"])
        self.assertIsNone(o["total_volume_ml"])

    def test_overview_counts_sealed_opened_and_remaining_liquid(self):
        self.sell("60", "s1")
        o = services.bottle_overview(self.product.pk)
        self.assertEqual((o["sealed_bottles"], o["opened_bottles"], o["empty_bottles"]), (4, 1, 0))
        self.assertEqual(o["opened_volume_ml"], D("690.00"))
        self.assertEqual(o["sealed_volume_ml"], D("3000.00"))
        self.assertEqual(o["total_volume_ml"], D("3690.00"))

    def test_overview_counts_empty_bottles_and_matches_partial_stock(self):
        self.sell("750", "s1")
        o = services.bottle_overview(self.product.pk)
        self.assertEqual((o["opened_bottles"], o["empty_bottles"], o["opened_volume_ml"]), (0, 1, D("0.00")))
        p = services.partial_stock(self.product.pk)
        for key in ("sealed_bottles", "opened_bottles", "empty_bottles", "opened_volume_ml", "sealed_volume_ml",
                    "total_volume_ml"):
            self.assertEqual(o[key], p[key], key)

    def test_overview_with_no_stock_and_no_bottles_is_zero_not_unknown(self):
        empty = make_product("Gin", "GIN-1")
        o = services.bottle_overview(empty.pk)
        self.assertEqual((o["sealed_volume_ml"], o["total_volume_ml"]), (D("0.00"), D("0.00")))


class ViewScreenTests(BottleAdminCase):
    def test_stock_viewer_sees_counts_bottles_and_history(self):
        self.sell("60", "s1")
        self.client.force_login(self.controller())
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["overview"]["opened_bottles"], 1)
        self.assertEqual({m.movement_type for m in r.context["page"]}, {mr.BOTTLE_OPEN, mr.PARTIAL_SALE})
        self.assertContains(r, "690.00")

    def test_opening_history_can_be_filtered_and_excludes_whole_bottle_movements(self):
        self.sell("60", "s1")
        self.client.force_login(self.controller())
        r = self.client.get(self.url, {"type": mr.BOTTLE_OPEN})
        self.assertEqual([m.movement_type for m in r.context["page"]], [mr.BOTTLE_OPEN])
        r = self.client.get(self.url)
        self.assertNotIn(mr.OPENING, {m.movement_type for m in r.context["page"]})
        r = self.client.get(self.url, {"type": "NOT-A-TYPE"})
        self.assertEqual(r.context["kind"], "")

    def test_cashier_cannot_see_the_bottle_screens(self):
        bottle = self.sell("60", "s1") and self.bottle()
        self.client.force_login(self.cashier)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.get(self.url + "wastage/").status_code, 403)
        self.assertEqual(self.client.get(self.url + f"{bottle.pk}/adjust/").status_code, 403)

    def test_stock_viewer_cannot_open_the_wastage_or_adjust_forms(self):
        self.sell("60", "s1")
        self.client.force_login(self.controller())
        self.assertEqual(self.client.get(self.url + "wastage/").status_code, 403)
        self.assertEqual(self.client.post(self.url + "wastage/", {"volume_ml": "10", "reason": "x"}).status_code, 403)
        self.assertEqual(self.client.get(self.url + f"{self.bottle().pk}/adjust/").status_code, 403)

    def test_cost_is_hidden_from_people_who_may_not_see_it(self):
        self.sell("60", "s1")
        self.client.force_login(self.controller())
        self.assertFalse(self.client.get(self.url).context["show_cost"])
        self.client.force_login(self.manager)
        self.assertTrue(self.client.get(self.url).context["show_cost"])

    def test_the_other_products_bottle_cannot_be_adjusted_through_this_product(self):
        self.sell("60", "s1")
        other = make_product("Vodka", "VOD-1")
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(f"/manage/stock/{other.pk}/bottles/{self.bottle().pk}/adjust/").status_code, 404)

    def test_pages_render_for_a_product_that_has_never_been_opened(self):
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertEqual(self.client.get(self.url + "wastage/").status_code, 200)


class WastageTests(BottleAdminCase):
    def setUp(self):
        super().setUp()
        self.sell("60", "s1")          # one opened bottle with 690 ml
        self.wastage_url = self.url + "wastage/"

    def post(self, **extra):
        data = {"volume_ml": "40", "reason": "Dropped a glass", "operation_key": "waste-1"}
        data.update(extra)
        return self.client.post(self.wastage_url, data)

    def test_manager_records_wastage_and_it_is_logged(self):
        self.client.force_login(self.manager)
        r = self.post()
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.bottle().remaining_ml, D("650.00"))
        m = StockMovement.objects.get(operation_key="waste-1")
        self.assertEqual((m.movement_type, m.volume_ml, m.user, m.reason), (mr.VOLUME_WASTAGE, D("-40.00"), self.manager,
                                                                           "Dropped a glass"))
        self.assertEqual(AuditLog.objects.filter(action="stock.volume_wasted", actor=self.manager).count(), 1)

    def test_a_reason_is_required(self):
        self.client.force_login(self.manager)
        self.assertEqual(self.post(reason="").status_code, 200)
        self.assertEqual(self.bottle().remaining_ml, D("690.00"))

    def test_more_than_the_opened_liquid_is_refused_and_sealed_bottles_are_untouched(self):
        self.client.force_login(self.manager)
        r = self.post(volume_ml="700")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "opened bottles")
        self.assertEqual(self.bottle().remaining_ml, D("690.00"))
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_qty, 4)

    def test_zero_negative_and_three_decimal_volumes_are_refused(self):
        self.client.force_login(self.manager)
        for bad in ("0", "-5", "abc", "1.234"):
            self.assertEqual(self.post(volume_ml=bad, operation_key=f"k{bad}").status_code, 200, bad)
        self.assertEqual(self.bottle().remaining_ml, D("690.00"))

    def test_submitting_the_same_form_twice_records_it_once(self):
        self.client.force_login(self.manager)
        self.assertEqual(self.post().status_code, 302)
        self.assertEqual(self.post().status_code, 200)               # same operation key: refused
        self.assertEqual(self.bottle().remaining_ml, D("650.00"))
        self.assertEqual(StockMovement.objects.filter(movement_type=mr.VOLUME_WASTAGE).count(), 1)

    def test_a_non_manager_needs_an_approver_and_nothing_changes_without_one(self):
        user = self.controller("stock.adjust")
        self.client.force_login(user)
        r = self.post()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.bottle().remaining_ml, D("690.00"))
        r = self.post(approver_username="sup", approver_secret="4321")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.bottle().remaining_ml, D("650.00"))
        m = StockMovement.objects.get(operation_key="waste-1")
        self.assertEqual(m.user, user)
        entry = AuditLog.objects.get(action="stock.volume_wasted")
        self.assertEqual((entry.actor, entry.approver), (user, self.supervisor))

    def test_a_wrong_approver_secret_is_refused(self):
        self.client.force_login(self.controller("stock.adjust"))
        r = self.post(approver_username="sup", approver_secret="0000")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.bottle().remaining_ml, D("690.00"))

    def test_a_cashier_cannot_approve(self):
        self.client.force_login(self.controller("stock.adjust"))
        r = self.post(approver_username="cash1", approver_secret="1111")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.bottle().remaining_ml, D("690.00"))

    def test_nobody_can_approve_their_own_wastage(self):
        self.client.force_login(self.supervisor)           # no stock.adjust, so refused outright
        self.assertEqual(self.post(approver_username="sup", approver_secret="4321").status_code, 403)

    def test_approval_is_not_asked_when_the_policy_turns_it_off(self):
        ApprovalPolicy.objects.update_or_create(action="stock_adjust", defaults={"required": False})
        self.client.force_login(self.controller("stock.adjust"))
        self.assertEqual(self.post().status_code, 302)
        self.assertEqual(self.bottle().remaining_ml, D("650.00"))


class AdjustmentTests(BottleAdminCase):
    def setUp(self):
        super().setUp()
        self.sell("60", "s1")
        self.bottle_pk = self.bottle().pk
        self.adjust_url = self.url + f"{self.bottle_pk}/adjust/"

    def post(self, **extra):
        data = {"volume_ml": "-25", "reason": "Stock take difference", "operation_key": "adj-1"}
        data.update(extra)
        return self.client.post(self.adjust_url, data)

    def test_manager_corrects_a_bottle_down_and_up(self):
        self.client.force_login(self.manager)
        self.assertEqual(self.post().status_code, 302)
        self.assertEqual(self.bottle().remaining_ml, D("665.00"))
        self.assertEqual(self.post(volume_ml="10", operation_key="adj-2").status_code, 302)
        self.assertEqual(self.bottle().remaining_ml, D("675.00"))
        m = StockMovement.objects.get(operation_key="adj-1")
        self.assertEqual((m.movement_type, m.volume_ml, m.bottle_remaining_after_ml), (mr.VOLUME_ADJUST, D("-25.00"),
                                                                                       D("665.00")))
        self.assertEqual(AuditLog.objects.filter(action="stock.volume_adjusted").count(), 2)

    def test_adjusting_above_capacity_or_below_zero_is_refused(self):
        self.client.force_login(self.manager)
        self.assertEqual(self.post(volume_ml="100").status_code, 200)       # 690 + 100 > 750
        self.assertEqual(self.post(volume_ml="-700", operation_key="adj-3").status_code, 200)
        self.assertEqual(self.bottle().remaining_ml, D("690.00"))

    def test_a_zero_adjustment_is_refused(self):
        self.client.force_login(self.manager)
        self.assertEqual(self.post(volume_ml="0").status_code, 200)
        self.assertEqual(StockMovement.objects.filter(movement_type=mr.VOLUME_ADJUST).count(), 0)

    def test_adjusting_to_empty_marks_the_bottle_empty(self):
        self.client.force_login(self.manager)
        self.assertEqual(self.post(volume_ml="-690").status_code, 302)
        bottle = self.bottle()
        self.assertEqual((bottle.status, bottle.remaining_ml), (Bottle.EMPTY, D("0.00")))

    def test_a_reason_is_required(self):
        self.client.force_login(self.manager)
        self.assertEqual(self.post(reason="").status_code, 200)
        self.assertEqual(self.bottle().remaining_ml, D("690.00"))

    def test_non_manager_needs_approval_and_is_recorded_with_the_approver(self):
        user = self.controller("stock.adjust")
        self.client.force_login(user)
        self.assertEqual(self.post().status_code, 200)
        self.assertEqual(self.bottle().remaining_ml, D("690.00"))
        self.assertEqual(self.post(approver_username="sup", approver_secret="4321").status_code, 302)
        self.assertEqual(self.bottle().remaining_ml, D("665.00"))
        entry = AuditLog.objects.get(action="stock.volume_adjusted")
        self.assertEqual((entry.actor, entry.approver), (user, self.supervisor))

    def test_cashier_cannot_change_inventory_even_by_calling_the_services_directly(self):
        for call in (
            lambda: services.record_wastage(product_id=self.product.pk, volume_ml=D("10"), user=self.cashier,
                                            operation_key="c1", reason="x"),
            lambda: services.adjust_bottle_volume(bottle_id=self.bottle_pk, volume_ml=D("-10"), user=self.cashier,
                                                  operation_key="c2", reason="x"),
            lambda: services.adjust_stock(product_id=self.product.pk, movement_type=mr.ADJUST, quantity=5,
                                          user=self.cashier, reason="x"),
        ):
            with self.assertRaises(services.StockError):
                call()
        self.assertEqual(self.bottle().remaining_ml, D("690.00"))
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_qty, 4)


class SellingUnitAndStockTests(BottleAdminCase):
    def test_a_disabled_unit_cannot_be_sold_even_through_the_stock_service(self):
        unit = catalog_services.create_selling_unit(self.product, self.manager, name="Tot", volume_ml=D("30"),
                                                    selling_price=D("150"))
        catalog_services.set_selling_unit_active(unit, self.manager, False)
        with self.assertRaises(services.StockError):
            services.sell_measures(selling_unit_id=unit.pk, quantity=1, user=self.cashier, operation_key="k1",
                                   bottle_capacity_ml=D("750"))
        self.assertEqual(Bottle.objects.count(), 0)

    def test_a_unit_of_an_inactive_product_cannot_be_sold(self):
        unit = catalog_services.create_selling_unit(self.product, self.manager, name="Tot", volume_ml=D("30"),
                                                    selling_price=D("150"))
        Product.objects.filter(pk=self.product.pk).update(active=False)      # not save(): self.product is stale
        with self.assertRaises(services.StockError) as ctx:
            services.sell_measures(selling_unit_id=unit.pk, quantity=1, user=self.cashier, operation_key="k1",
                                   bottle_capacity_ml=D("750"))
        self.assertIn("inactive", str(ctx.exception))
        self.assertEqual(Bottle.objects.count(), 0)

    def test_changing_a_unit_does_not_change_stock_or_bottles(self):
        unit = catalog_services.create_selling_unit(self.product, self.manager, name="Tot", volume_ml=D("30"),
                                                    selling_price=D("150"))
        services.sell_measures(selling_unit_id=unit.pk, quantity=2, user=self.cashier, operation_key="k1",
                               bottle_capacity_ml=D("750"))
        before = (self.bottle().remaining_ml, StockMovement.objects.count())
        catalog_services.update_selling_unit(unit, self.manager, name="Single", volume_ml=D("25"),
                                             selling_price=D("120"))
        catalog_services.set_selling_unit_active(unit, self.manager, False)
        self.assertEqual((self.bottle().remaining_ml, StockMovement.objects.count()), before)
        self.assertEqual(SellingUnit.objects.get().volume_ml, D("25.00"))
