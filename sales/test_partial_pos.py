"""Cashier POS integration for configured selling units and bottle-capacity inventory."""
from decimal import Decimal as D

from django.db.models import Sum

from catalog.models import SellingUnit
from inventory import movement_rules as mr
from inventory import services as stock_services
from inventory.models import Bottle, StockMovement

from . import rules, services
from .models import MpesaTransaction, Payment, Sale, SaleItem
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

    def test_insufficient_partial_sale_requires_approval_at_checkout(self):
        response = post(self.c, "/api/sales/cart/add/", {"selling_unit_id": self.tot.pk, "quantity": 251})
        self.assertEqual(response.status_code, 200)
        sale_id = response.json()["cart"]["id"]
        checkout = post(self.c, "/api/sales/checkout/cash/", {"tendered": "25100"})
        self.assertEqual(checkout.status_code, 409)
        request = checkout.json()["approval_required"]
        self.assertEqual((request["action"], request["object_ref"]), ("negative_stock_sale", rules.sale_ref(sale_id)))
        self.assertEqual(SaleItem.objects.count(), 1)
        self.assertEqual(Payment.objects.count(), 0)

        token = self.approval("negative_stock_sale", rules.sale_ref(sale_id), reason="Approved missing liquid")
        done = post(self.c, "/api/sales/checkout/cash/", {
            "tendered": "25100", "approval_token": token
        }).json()
        self.assertTrue(done["ok"], done)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_qty, 0)
        deficit = StockMovement.objects.get(movement_type=mr.PARTIAL_DEFICIT)
        self.assertEqual((deficit.volume_ml, deficit.approved_by), (D("-30.00"), self.supervisor))

    def test_future_receipt_offsets_partial_deficit_without_creating_extra_stock(self):
        response = post(self.c, "/api/sales/cart/add/", {"selling_unit_id": self.tot.pk, "quantity": 251})
        sale_id = response.json()["cart"]["id"]
        token = self.approval("negative_stock_sale", rules.sale_ref(sale_id), reason="Approved shortage")
        completed = post(self.c, "/api/sales/checkout/cash/", {
            "tendered": "25100", "approval_token": token
        }).json()
        self.assertTrue(completed["ok"], completed)

        stock_services.post_movement(product_id=self.product.pk, movement_type=mr.RECEIVE, quantity=1,
                                     user=self.clerk, unit_cost=D("2400"), reference="AFTER-DEFICIT")
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_qty, 0)
        self.assertEqual(Bottle.objects.get(product=self.product, status=Bottle.OPENED).remaining_ml, D("720.00"))
        self.assertEqual(StockMovement.objects.filter(movement_type=mr.PARTIAL_DEFICIT_OFFSET)
                         .aggregate(total=Sum("volume_ml"))["total"], D("30.00"))
        self.assertEqual(StockMovement.objects.filter(movement_type=mr.VOLUME_DEFICIT_OFFSET)
                         .aggregate(total=Sum("volume_ml"))["total"], D("-30.00"))

        stock_services.post_movement(product_id=self.product.pk, movement_type=mr.RECEIVE, quantity=1,
                                     user=self.clerk, unit_cost=D("2400"), reference="EXTRA-RECEIPT")
        self.product.refresh_from_db()
        self.assertEqual((self.product.stock_qty,
                          StockMovement.objects.filter(movement_type=mr.PARTIAL_DEFICIT_OFFSET).count()), (1, 1))

    def test_mpesa_checkout_keeps_confirmed_payment_unmatched_until_approved(self):
        sale, _line = services.add_item(self.cashier, self.shift, code="6001111111111", quantity=11)
        transaction = MpesaTransaction.objects.create(
            trans_id="NEGATIVE-STOCK-01", source=MpesaTransaction.C2B, amount=D("38500"))

        response = post(self.c, "/api/sales/checkout/", {"mpesa_ids": [transaction.pk]})
        self.assertEqual(response.status_code, 409)
        transaction.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual((transaction.status, Payment.objects.count(), self.product.stock_qty),
                         (MpesaTransaction.UNMATCHED, 0, 10))
        token = self.approval("negative_stock_sale", rules.sale_ref(sale.pk), reason="Approved M-PESA shortage")

        done = post(self.c, "/api/sales/checkout/", {
            "mpesa_ids": [transaction.pk], "approval_token": token
        }).json()
        self.assertTrue(done["ok"], done)
        transaction.refresh_from_db()
        self.assertEqual((transaction.status, transaction.sale_id), (MpesaTransaction.MATCHED, done["sale_id"]))

    def test_mixed_cart_volume_shortage_is_approved_atomically_at_checkout(self):
        full = post(self.c, "/api/sales/cart/add/", {"code": "6001111111111", "quantity": 9}).json()
        self.assertTrue(full["ok"], full)
        partial = post(self.c, "/api/sales/cart/add/", {"selling_unit_id": self.tot.pk, "quantity": 26}).json()
        self.assertTrue(partial["ok"], partial)
        sale_id = partial["cart"]["id"]
        response = post(self.c, "/api/sales/checkout/cash/", {"tendered": "34100"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["approval_required"]["action"], "negative_stock_sale")
        token = self.approval("negative_stock_sale", rules.sale_ref(sale_id), reason="Approved mixed-cart shortage")
        done = post(self.c, "/api/sales/checkout/cash/", {
            "tendered": "34100", "approval_token": token
        }).json()
        self.assertTrue(done["ok"], done)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_qty, 0)
        self.assertEqual(StockMovement.objects.get(movement_type=mr.PARTIAL_DEFICIT).volume_ml, D("-30.00"))

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

    def test_mixed_full_and_partial_cash_checkout_and_retry_post_once(self):
        full = post(self.c, "/api/sales/cart/add/", {"code": "6001111111111"})
        partial = post(self.c, "/api/sales/cart/add/", {"selling_unit_id": self.tot.pk, "quantity": 2})
        self.assertTrue(full.json()["ok"], full.json())
        self.assertTrue(partial.json()["ok"], partial.json())

        response = post(self.c, "/api/sales/checkout/cash/", {"tendered": "4000"})
        done = response.json()
        self.assertTrue(done["ok"], done)
        retry = post(self.c, "/api/sales/checkout/cash/", {"tendered": "4000"})
        self.assertEqual(retry.status_code, 400)

        self.product.refresh_from_db()
        bottle = Bottle.objects.get(product=self.product, status=Bottle.OPENED)
        payment = Payment.objects.get(sale_id=done["sale_id"])
        self.assertEqual((payment.amount, payment.tendered, payment.change_given),
                         (D("3700.00"), D("4000.00"), D("300.00")))
        self.assertEqual((self.product.stock_qty, bottle.remaining_ml), (8, D("690.00")))
        self.assertEqual(StockMovement.objects.filter(reference=done["number"], movement_type=mr.SALE).count(), 1)
        self.assertEqual(StockMovement.objects.filter(reference=done["number"], movement_type=mr.PARTIAL_SALE).count(), 1)
        self.assertEqual(Sale.objects.filter(status=Sale.COMPLETED).count(), 1)

    def test_mixed_mpesa_and_cash_checkout_reconciles_payment_and_inventory(self):
        post(self.c, "/api/sales/cart/add/", {"code": "6001111111111"})
        post(self.c, "/api/sales/cart/add/", {"selling_unit_id": self.tot.pk, "quantity": 2})
        txn = MpesaTransaction.objects.create(trans_id="MIXEDPARTIAL1", source=MpesaTransaction.C2B,
                                              amount=D("1000.00"))

        response = post(self.c, "/api/sales/checkout/", {"mpesa_ids": [txn.pk], "cash_tendered": "2700"})
        result = response.json()
        self.assertTrue(result["ok"], result)

        sale = Sale.objects.get(pk=result["sale_id"])
        self.assertEqual(sum(p.amount for p in sale.payments.all()), sale.total)
        self.assertEqual({p.method: p.amount for p in sale.payments.all()},
                         {Payment.MPESA: D("1000.00"), Payment.CASH: D("2700.00")})
        txn.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual((txn.status, txn.sale_id), (MpesaTransaction.MATCHED, sale.pk))
        self.assertEqual((self.product.stock_qty, Bottle.objects.get(product=self.product).remaining_ml),
                         (8, D("690.00")))
        self.assertEqual(StockMovement.objects.filter(reference=sale.number, movement_type=mr.PARTIAL_SALE).count(), 1)

    def test_stock_shortage_after_cart_creation_requires_approval_without_partial_commit(self):
        post(self.c, "/api/sales/cart/add/", {"code": "6001111111111"})
        post(self.c, "/api/sales/cart/add/", {"selling_unit_id": self.tot.pk, "quantity": 2})
        stock_services.post_movement(product_id=self.product.pk, movement_type=mr.DAMAGED, quantity=-9,
                                     user=self.manager, reason="Checkout rollback test")

        response = post(self.c, "/api/sales/checkout/cash/", {"tendered": "3700"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["approval_required"]["action"], "negative_stock_sale")
        sale = Sale.objects.get()
        self.product.refresh_from_db()
        self.assertEqual((sale.status, sale.number, self.product.stock_qty), (Sale.OPEN, None, 1))
        self.assertFalse(Payment.objects.exists())
        self.assertEqual(StockMovement.objects.filter(movement_type__in=(mr.SALE, mr.BOTTLE_OPEN,
                                                                         mr.PARTIAL_SALE, mr.PARTIAL_DEFICIT)).count(), 0)
        self.assertFalse(Bottle.objects.filter(product=self.product).exists())

    def test_cashier_screen_loads_configured_units_and_displays_volume(self):
        html = self.c.get("/pos/sell/").content.decode()
        self.assertIn("/api/catalog/products/", html)
        self.assertIn("selling_unit_id", html)
        self.assertIn("total_volume_ml", html)
        self.assertIn("<th class=\"num\">Volume</th>", html)
