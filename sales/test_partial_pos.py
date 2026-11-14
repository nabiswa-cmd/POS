"""Cashier POS integration for configured selling units and bottle-capacity inventory."""
from decimal import Decimal as D

from catalog.models import SellingUnit
from inventory import movement_rules as mr
from inventory.models import Bottle, StockMovement

from . import rules
from .models import SaleItem
from .tests_screen import ScreenCase, post


class PartialSellingPosTests(ScreenCase):
    def setUp(self):
        super().setUp()
        self.product.bottle_capacity_ml = D("750")
        self.product.save(update_fields=["bottle_capacity_ml"])
        self.tot = SellingUnit.objects.create(product=self.product, name="TOT", volume_ml=D("30"),
                                              selling_price=D("100"))
        self.double = SellingUnit.objects.create(product=self.product, name="Double TOT", volume_ml=D("60"),
                                                 selling_price=D("200"))

    def test_selected_units_use_backend_price_and_volume_not_browser_values(self):
        units = self.c.get(f"/api/catalog/products/{self.product.pk}/selling-units/").json()["units"]
        self.assertEqual([(x["name"], x["volume_ml"], x["price"]) for x in units],
                         [("TOT", "30.00", "100.00"), ("Double TOT", "60.00", "200.00")])

        response = post(self.c, "/api/sales/cart/add/", {
            "selling_unit_id": self.tot.pk, "quantity": 2, "unit_price": "0.01", "volume_ml": "9000"
        }).json()
        self.assertTrue(response["ok"], response)
        line = response["cart"]["items"][0]
        self.assertEqual((line["selling_unit"], line["quantity"], line["unit_price"], line["total_volume_ml"]),
                         ("TOT", 2, "100.00", "60.00"))

    def test_multiple_units_quantity_edits_and_removal_keep_cart_volume_accurate(self):
        post(self.c, "/api/sales/cart/add/", {"selling_unit_id": self.tot.pk, "quantity": 2})
        second = post(self.c, "/api/sales/cart/add/", {"selling_unit_id": self.double.pk, "quantity": 1}).json()
        self.assertEqual(len(second["cart"]["items"]), 2)
        tot_line = next(i for i in second["cart"]["items"] if i["selling_unit"] == "TOT")
        double_line = next(i for i in second["cart"]["items"] if i["selling_unit"] == "Double TOT")

        raised = post(self.c, "/api/sales/cart/quantity/", {"item_id": tot_line["id"], "quantity": 4}).json()
        self.assertTrue(raised["ok"], raised)
        tot_line = next(i for i in raised["cart"]["items"] if i["selling_unit"] == "TOT")
        self.assertEqual((tot_line["quantity"], tot_line["total_volume_ml"]), (4, "120.00"))

        sale_id = raised["cart"]["id"]
        token = self.approval("remove_item", rules.item_ref(sale_id, tot_line["id"]))
        lowered = post(self.c, "/api/sales/cart/quantity/", {
            "item_id": tot_line["id"], "quantity": 3, "approval_token": token
        }).json()
        self.assertTrue(lowered["ok"], lowered)
        tot_line = next(i for i in lowered["cart"]["items"] if i["selling_unit"] == "TOT")
        self.assertEqual((tot_line["quantity"], tot_line["total_volume_ml"]), (3, "90.00"))

        token = self.approval("remove_item", rules.item_ref(sale_id, double_line["id"]))
        removed = post(self.c, "/api/sales/cart/remove/", {
            "item_id": double_line["id"], "approval_token": token
        }).json()
        self.assertTrue(removed["ok"], removed)
        self.assertEqual([(i["selling_unit"], i["total_volume_ml"]) for i in removed["cart"]["items"]],
                         [("TOT", "90.00")])

    def test_insufficient_partial_stock_is_rejected(self):
        response = post(self.c, "/api/sales/cart/add/", {"selling_unit_id": self.tot.pk, "quantity": 251})
        self.assertEqual(response.status_code, 400)
        self.assertIn("Only", response.json()["error"])
        self.assertEqual(SaleItem.objects.count(), 0)

    def test_partial_and_full_cart_reservations_cannot_oversell_combined_volume(self):
        full = post(self.c, "/api/sales/cart/add/", {"code": "6001111111111", "quantity": 9}).json()
        self.assertTrue(full["ok"], full)
        partial = post(self.c, "/api/sales/cart/add/", {"selling_unit_id": self.tot.pk, "quantity": 25}).json()
        self.assertTrue(partial["ok"], partial)
        extra_measure = post(self.c, "/api/sales/cart/add/", {"selling_unit_id": self.tot.pk, "quantity": 1})
        self.assertEqual(extra_measure.status_code, 400)

        full_after_partial = post(self.c, "/api/sales/cart/add/", {"code": "6001111111111"}).json()
        self.assertFalse(full_after_partial["ok"])
        self.assertIn("ml remain", full_after_partial["error"])

    def test_partial_checkout_opens_a_bottle_records_volume_and_allocates_cost(self):
        added = post(self.c, "/api/sales/cart/add/", {"selling_unit_id": self.tot.pk, "quantity": 2}).json()
        self.assertTrue(added["ok"], added)
        done = post(self.c, "/api/sales/checkout/cash/", {"tendered": "200"}).json()
        self.assertTrue(done["ok"], done)

        self.product.refresh_from_db()
        bottle = Bottle.objects.get(product=self.product, status=Bottle.OPENED)
        line = SaleItem.objects.get(sale_id=done["sale_id"])
        self.assertEqual((self.product.stock_qty, bottle.capacity_ml, bottle.remaining_ml), (9, D("750.00"), D("690.00")))
        self.assertEqual((line.total_volume_ml, line.cost_total), (D("60.00"), D("192.00")))
        self.assertEqual(list(StockMovement.objects.filter(reference=done["number"])
                              .order_by("operation_sequence").values_list("movement_type", flat=True)),
                         [mr.BOTTLE_OPEN, mr.PARTIAL_SALE])

        receipt = self.c.get(f"/api/sales/receipt/{done['sale_id']}/").json()
        self.assertIn("TOT", " ".join(receipt["lines"]))

    def test_ordinary_full_bottle_sale_keeps_existing_stock_behavior(self):
        added = post(self.c, "/api/sales/cart/add/", {"code": "6001111111111"}).json()
        self.assertTrue(added["ok"], added)
        done = post(self.c, "/api/sales/checkout/cash/", {"tendered": "3500"}).json()
        self.assertTrue(done["ok"], done)

        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_qty, 9)
        self.assertFalse(Bottle.objects.filter(product=self.product).exists())
        self.assertEqual(StockMovement.objects.filter(reference=done["number"], movement_type=mr.SALE).count(), 1)

    def test_cashier_screen_loads_configured_units_and_displays_volume(self):
        html = self.c.get("/pos/sell/").content.decode()
        self.assertIn("/api/catalog/products/", html)
        self.assertIn("selling_unit_id", html)
        self.assertIn("total_volume_ml", html)
        self.assertIn("<th class=\"num\">Volume</th>", html)
