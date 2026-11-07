from decimal import Decimal as D

from accounts import services as auth_services
from catalog import services as catalog_services
from catalog.models import Packaging
from catalog.tests import CatalogCase, make_product
from core.models import AuditLog, ReceiptSettings
from inventory import movement_rules as mr
from inventory import services as stock_services
from inventory.models import StockMovement
from inventory.tests import receive
from tills.models import CashierShift, Till

from . import receipt, rules, services
from .models import Payment, Sale, SaleItem


class SalesCase(CatalogCase):
    """Product 'Johnnie Walker Black 750ml': price 3500, cost 2400, 10 bottles in stock, till 1 open."""

    def setUp(self):
        super().setUp()
        stock_services.submit_receiving(receive(self.clerk, self.product, 10, "2400").pk, self.clerk)
        self.till1 = Till.objects.get(number=1)
        self.till2 = Till.objects.get(number=2)
        self.shift = CashierShift.objects.create(till=self.till1, cashier=self.cashier,
                                                 authorized_by=self.supervisor, opening_float=D("5000"))
        catalog_services.add_code(self.product, "6001111111111", self.manager)

    def approval(self, action, object_ref, requester=None, reason="Customer asked"):
        raw, _ = auth_services.grant_approval(
            action=action, approver_username="sup", secret="4321", reason=reason,
            requester=requester or self.cashier, object_ref=object_ref)
        return raw

    def scan(self, code="6001111111111", qty=1, user=None, shift=None):
        return services.add_item(user or self.cashier, shift or self.shift, code=code, quantity=qty)

    def stock(self):
        self.product.refresh_from_db()
        return self.product.stock_qty


class CartTests(SalesCase):
    def test_scan_adds_line_and_rescan_adds_to_it(self):
        sale, line = self.scan()
        self.scan()
        line.refresh_from_db()
        sale.refresh_from_db()
        self.assertEqual((line.quantity, line.line_total, sale.total), (2, D("7000.00"), D("7000.00")))
        self.assertEqual(SaleItem.objects.count(), 1)

    def test_unknown_code_is_refused(self):
        with self.assertRaises(services.SaleError):
            self.scan("0000000000000")

    def test_cannot_put_more_in_the_cart_than_is_in_stock(self):
        with self.assertRaises(services.SaleError) as ctx:
            self.scan(qty=11)
        self.assertIn("Only 10", str(ctx.exception))

    def test_internally_generated_code_scans(self):
        code = catalog_services.generate_internal_code(self.product, self.manager)
        sale, line = self.scan(code.code)
        self.assertEqual(line.product, self.product)

    def test_pack_scan_sells_a_whole_pack_of_base_units(self):
        pack = Packaging.objects.create(product=self.product, name="Six-pack", units_per_pack=6)
        catalog_services.add_code(self.product, "SIX-JW", self.manager, packaging=pack)
        sale, line = self.scan("SIX-JW")
        self.assertEqual((line.units_per_sale_unit, line.base_qty, line.unit_price), (6, 6, D("21000.00")))
        services.checkout_cash(self.cashier, self.shift, "21000")
        self.assertEqual(self.stock(), 4)

    def test_pack_cannot_exceed_stock(self):
        pack = Packaging.objects.create(product=self.product, name="Carton", units_per_pack=24)
        catalog_services.add_code(self.product, "CARTON-JW", self.manager, packaging=pack)
        with self.assertRaises(services.SaleError):
            self.scan("CARTON-JW")

    def test_product_without_a_price_cannot_be_sold(self):
        free = make_product("Free", "FREE-1", price="0")
        with self.assertRaises(services.SaleError):
            self.scan("FREE-1")

    def test_no_open_shift_no_sale(self):
        self.shift.closed_at = self.shift.opened_at
        self.shift.save()
        with self.assertRaises(services.SaleError):
            self.scan()

    def test_receiving_clerk_cannot_sell(self):
        clerk_shift = CashierShift.objects.create(till=self.till2, cashier=self.clerk,
                                                  authorized_by=self.supervisor, opening_float=D("0"))
        with self.assertRaises(services.NotAllowed):
            self.scan(user=self.clerk, shift=clerk_shift)


class RemovalAndApprovalTests(SalesCase):
    def test_cashier_cannot_remove_a_scanned_item_alone(self):
        sale, line = self.scan()
        with self.assertRaises(services.SaleError):
            services.remove_item(self.cashier, self.shift, line.pk)
        with self.assertRaises(services.SaleError):
            services.remove_item(self.cashier, self.shift, line.pk, approval_token="forged-token")
        self.assertEqual(SaleItem.objects.count(), 1)

    def test_supervisor_approval_removes_item_and_is_audited(self):
        sale, line = self.scan(qty=2)
        token = self.approval("remove_item", rules.item_ref(sale.pk, line.pk), reason="Wrong product scanned")
        services.remove_item(self.cashier, self.shift, line.pk, approval_token=token)
        self.assertEqual(SaleItem.objects.count(), 0)
        entry = AuditLog.objects.get(action="sale.item_removed")
        self.assertEqual((entry.actor, entry.approver), (self.cashier, self.supervisor))
        self.assertEqual(entry.reason, "Wrong product scanned")
        self.assertEqual(entry.previous_value["quantity"], 2)

    def test_approval_is_single_use(self):
        sale, line = self.scan()
        self.scan("6001111111111")
        token = self.approval("remove_item", rules.item_ref(sale.pk, line.pk))
        services.set_quantity(self.cashier, self.shift, line.pk, 1, approval_token=token)
        with self.assertRaises(services.SaleError):
            services.remove_item(self.cashier, self.shift, line.pk, approval_token=token)

    def test_approval_for_one_line_does_not_work_for_another(self):
        other = make_product("Other", "OTH-1", price="500")
        catalog_services.add_code(other, "6002222222222", self.manager)
        stock_services.submit_receiving(receive(self.clerk, other, 5, "300").pk, self.clerk)
        sale, a = self.scan()
        _, b = self.scan("6002222222222")
        token = self.approval("remove_item", rules.item_ref(sale.pk, a.pk))
        with self.assertRaises(services.SaleError):
            services.remove_item(self.cashier, self.shift, b.pk, approval_token=token)

    def test_lowering_quantity_needs_approval_but_raising_does_not(self):
        sale, line = self.scan(qty=2)
        services.set_quantity(self.cashier, self.shift, line.pk, 3)
        with self.assertRaises(services.SaleError):
            services.set_quantity(self.cashier, self.shift, line.pk, 1)
        token = self.approval("remove_item", rules.item_ref(sale.pk, line.pk))
        services.set_quantity(self.cashier, self.shift, line.pk, 1, approval_token=token)
        line.refresh_from_db()
        self.assertEqual(line.quantity, 1)

    def test_cashier_cannot_approve_their_own_removal(self):
        sale, line = self.scan()
        with self.assertRaises(auth_services.AuthError):
            auth_services.grant_approval(action="remove_item", approver_username="cash1", secret="1111",
                                         reason="Mine", requester=self.cashier,
                                         object_ref=rules.item_ref(sale.pk, line.pk))

    def test_manager_can_waive_the_removal_policy(self):
        from accounts.models import ApprovalPolicy
        ApprovalPolicy.objects.filter(action="remove_item").update(required=False)
        sale, line = self.scan()
        with self.assertRaises(services.SaleError):               # still needs a written reason
            services.remove_item(self.cashier, self.shift, line.pk)
        services.remove_item(self.cashier, self.shift, line.pk, reason="Customer changed mind")
        self.assertEqual(SaleItem.objects.count(), 0)


class DiscountAndPriceTests(SalesCase):
    def test_cashier_cannot_discount_without_approval(self):
        sale, line = self.scan()
        with self.assertRaises(services.SaleError):
            services.apply_discount(self.cashier, self.shift, line.pk, amount="100")

    def test_approved_discount_is_applied_and_recorded(self):
        sale, line = self.scan()
        token = self.approval("exceptional_discount", rules.item_ref(sale.pk, line.pk))
        services.apply_discount(self.cashier, self.shift, line.pk, amount="500", approval_token=token)
        line.refresh_from_db()
        sale.refresh_from_db()
        self.assertEqual((line.discount, line.line_total, sale.total, sale.discount_total),
                         (D("500.00"), D("3000.00"), D("3000.00"), D("500.00")))
        self.assertEqual(line.discount_approved_by, self.supervisor)
        self.assertTrue(AuditLog.objects.filter(action="sale.discount_applied", approver=self.supervisor).exists())

    def test_owner_discount_needs_no_second_person_at_any_size(self):
        mgr_shift = CashierShift.objects.create(till=self.till2, cashier=self.manager,
                                                authorized_by=self.supervisor, opening_float=D("0"))
        sale, line = services.add_item(self.manager, mgr_shift, code="6001111111111")
        services.apply_discount(self.manager, mgr_shift, line.pk, percent="5")       # 5% = 175
        line.refresh_from_db()
        self.assertEqual(line.discount, D("175.00"))
        services.apply_discount(self.manager, mgr_shift, line.pk, percent="20")      # the owner has no limit
        line.refresh_from_db()
        self.assertEqual((line.discount, line.discount_approved_by), (D("700.00"), self.manager))

    def test_discount_cannot_exceed_line(self):
        sale, line = self.scan()
        token = self.approval("exceptional_discount", rules.item_ref(sale.pk, line.pk))
        with self.assertRaises(services.SaleError):
            services.apply_discount(self.cashier, self.shift, line.pk, amount="3500.01", approval_token=token)

    def test_price_change_needs_approval_and_keeps_list_price(self):
        sale, line = self.scan()
        with self.assertRaises(services.SaleError):
            services.change_price(self.cashier, self.shift, line.pk, "3000")
        token = self.approval("price_change", rules.item_ref(sale.pk, line.pk))
        services.change_price(self.cashier, self.shift, line.pk, "3000", approval_token=token)
        line.refresh_from_db()
        self.assertEqual((line.unit_price, line.list_price, line.price_approved_by),
                         (D("3000.00"), D("3500.00"), self.supervisor))
        self.product.refresh_from_db()
        self.assertEqual(self.product.selling_price, D("3500.00"))      # the catalogue is untouched


class CheckoutTests(SalesCase):
    def test_cash_sale_reduces_stock_records_cost_and_payment(self):
        self.scan(qty=3)
        sale = services.checkout_cash(self.cashier, self.shift, "11000")
        sale.refresh_from_db()
        self.assertEqual(sale.status, Sale.COMPLETED)
        self.assertRegex(sale.number, r"^T1-\d{6}-0001$")
        self.assertEqual(self.stock(), 7)
        move = StockMovement.objects.get(movement_type=mr.SALE)
        self.assertEqual((move.quantity, move.reference, move.user), (-3, sale.number, self.cashier))
        item = sale.items.get()
        self.assertEqual(item.cost_total, D("7200.00"))                  # 3 x 2400, not today's cost
        pay = sale.payments.get()
        self.assertEqual((pay.method, pay.amount, pay.tendered, pay.change_given),
                         (Payment.CASH, D("10500.00"), D("11000.00"), D("500.00")))
        self.assertEqual((sale.cashier, sale.authorized_by), (self.cashier, self.supervisor))

    def test_underpayment_is_refused_and_nothing_changes(self):
        self.scan()
        with self.assertRaises(services.SaleError):
            services.checkout_cash(self.cashier, self.shift, "3000")
        self.assertEqual(Sale.objects.get().status, Sale.OPEN)
        self.assertEqual(self.stock(), 10)

    def test_empty_sale_cannot_be_completed(self):
        with self.assertRaises(services.SaleError):
            services.checkout_cash(self.cashier, self.shift, "100")

    def test_sale_numbers_are_sequential_per_till_and_day(self):
        self.scan()
        a = services.checkout_cash(self.cashier, self.shift, "3500")
        self.scan()
        b = services.checkout_cash(self.cashier, self.shift, "3500")
        self.assertTrue(a.number.endswith("-0001") and b.number.endswith("-0002"))

    def test_stock_that_vanished_after_scanning_blocks_checkout_and_rolls_back(self):
        self.scan(qty=10)
        stock_services.post_movement(product_id=self.product.pk, movement_type=mr.DAMAGED, quantity=-4,
                                     user=self.manager, reason="Dropped crate")
        with self.assertRaises(services.SaleError) as ctx:
            services.checkout_cash(self.cashier, self.shift, "35000")
        self.assertIn("Only 6", str(ctx.exception))
        self.assertEqual(self.stock(), 6)
        sale = Sale.objects.get()
        self.assertEqual(sale.status, Sale.OPEN)
        self.assertIsNone(sale.number)
        self.assertFalse(Payment.objects.exists())

    def test_two_tills_share_stock_and_the_last_bottle_sells_once(self):
        # stock is 10; leave exactly one bottle
        stock_services.post_movement(product_id=self.product.pk, movement_type=mr.DAMAGED, quantity=-9,
                                     user=self.manager, reason="Test setup")
        cashier2_shift = CashierShift.objects.create(till=self.till2, cashier=self.cashier2,
                                                     authorized_by=self.supervisor, opening_float=D("0"))
        self.scan()
        services.add_item(self.cashier2, cashier2_shift, code="6001111111111")   # both carts hold the last bottle
        services.checkout_cash(self.cashier, self.shift, "3500")                  # till 1 sells it
        with self.assertRaises(services.SaleError):
            services.checkout_cash(self.cashier2, cashier2_shift, "3500")         # till 2 is refused
        self.assertEqual(self.stock(), 0)
        self.assertEqual(Sale.objects.filter(status=Sale.COMPLETED).count(), 1)

    def test_same_product_on_two_lines_is_taken_together(self):
        sale, a = self.scan()
        token = self.approval("price_change", rules.item_ref(sale.pk, a.pk))
        services.change_price(self.cashier, self.shift, a.pk, "3000", approval_token=token)
        self.scan()                                                   # a second line at the catalogue price
        done = services.checkout_cash(self.cashier, self.shift, "6500")
        self.assertEqual(done.items.count(), 2)
        self.assertEqual(sum(i.cost_total for i in done.items.all()), D("4800.00"))
        self.assertEqual(StockMovement.objects.filter(movement_type=mr.SALE).count(), 1)

    def test_cashier_without_payment_permission_cannot_take_payment(self):
        from accounts.models import RolePermission
        RolePermission.objects.filter(role=self.cashier.role, code="payments.accept").delete()
        self.scan()
        with self.assertRaises(services.NotAllowed):
            services.checkout_cash(self.cashier, self.shift, "3500")


class HistoryProtectionTests(SalesCase):
    def setUp(self):
        super().setUp()
        self.scan()
        self.done = services.checkout_cash(self.cashier, self.shift, "3500")

    def test_completed_sale_cannot_be_edited_or_deleted(self):
        sale = Sale.objects.get(pk=self.done.pk)
        sale.customer_name = "Someone else"
        with self.assertRaises(PermissionError):
            sale.save()
        with self.assertRaises(PermissionError):
            sale.delete()

    def test_completed_sale_lines_cannot_be_changed_or_deleted_or_added(self):
        item = SaleItem.objects.get(sale=self.done)
        item.quantity = 99
        with self.assertRaises(PermissionError):
            item.save()
        with self.assertRaises(PermissionError):
            item.delete()
        with self.assertRaises(PermissionError):
            SaleItem.objects.create(sale=self.done, product=self.product, product_name="x", quantity=1,
                                    list_price=D("1"), unit_price=D("1"), line_total=D("1"))

    def test_a_cashier_cannot_remove_items_from_a_completed_sale(self):
        item = SaleItem.objects.get(sale=self.done)
        with self.assertRaises(services.SaleError):
            services.remove_item(self.cashier, self.shift, item.pk)


class HoldVoidTests(SalesCase):
    def test_hold_then_resume_keeps_lines(self):
        self.scan(qty=2)
        held = services.hold_sale(self.cashier, self.shift, "Man in red shirt")
        self.assertEqual(held.status, Sale.HELD)
        self.scan()                                                      # a new customer
        with self.assertRaises(services.SaleError):                      # cannot resume over a cart with items
            services.resume_held(self.cashier, self.shift, held.pk)
        services.checkout_cash(self.cashier, self.shift, "3500")
        resumed = services.resume_held(self.cashier, self.shift, held.pk)
        self.assertEqual((resumed.pk, resumed.status, resumed.items.get().quantity), (held.pk, Sale.OPEN, 2))

    def test_empty_cart_cannot_be_held(self):
        with self.assertRaises(services.SaleError):
            services.hold_sale(self.cashier, self.shift)

    def test_another_cashier_cannot_resume_your_held_sale(self):
        self.scan()
        held = services.hold_sale(self.cashier, self.shift)
        self.shift.closed_at = self.shift.opened_at          # one open shift per till: close the first
        self.shift.save()
        other = CashierShift.objects.create(till=self.till1, cashier=self.cashier2,
                                            authorized_by=self.supervisor, opening_float=D("0"))
        with self.assertRaises(services.NotAllowed):
            services.resume_held(self.cashier2, other, held.pk)

    def test_void_needs_approval_and_keeps_the_record(self):
        sale, line = self.scan(qty=2)
        with self.assertRaises(services.SaleError):
            services.void_sale(self.cashier, self.shift)
        token = self.approval("void_sale", rules.sale_ref(sale.pk), reason="Customer left")
        voided = services.void_sale(self.cashier, self.shift, approval_token=token)
        self.assertEqual((voided.status, voided.void_approved_by), (Sale.VOIDED, self.supervisor))
        self.assertTrue(Sale.objects.filter(pk=sale.pk).exists())
        self.assertEqual(self.stock(), 10)
        with self.assertRaises(PermissionError):
            voided.delete()


class ReceiptTests(SalesCase):
    def setUp(self):
        super().setUp()
        services.set_customer(self.cashier, self.shift, "Wanjiku Mwangi")
        self.scan()
        self.done = services.checkout_cash(self.cashier, self.shift, "4000")

    def test_receipt_names_cashier_authorizer_customer_and_change(self):
        data, lines = receipt.render(receipt.get_completed(self.done.pk))
        text = "\n".join(lines)
        for needle in (self.done.number, "Wanjiku Mwangi", "Cashier: cash1", "Authorized by: sup",
                       "Johnnie Walker Black 750ml", "3,500.00", "Change", "500.00"):
            self.assertIn(needle, text)
        self.assertNotIn("DUPLICATE", text)

    def test_receipt_settings_can_hide_names(self):
        cfg = ReceiptSettings.load()
        cfg.show_authorized_by = False
        cfg.save()
        text = "\n".join(receipt.render(receipt.get_completed(self.done.pk))[1])
        self.assertNotIn("Authorized by", text)

    def test_original_prints_once_then_only_duplicates(self):
        services.mark_original_printed(self.cashier, self.done.pk)
        with self.assertRaises(services.SaleError):
            services.mark_original_printed(self.cashier, self.done.pk)

    def test_only_the_selling_cashier_prints_the_original(self):
        with self.assertRaises(services.NotAllowed):
            services.mark_original_printed(self.cashier2, self.done.pk)

    def test_cashier_cannot_reprint_but_supervisor_can_and_it_says_duplicate(self):
        with self.assertRaises(services.NotAllowed):
            services.mark_reprint(self.cashier, self.done.pk)
        sale = services.mark_reprint(self.supervisor, self.done.pk)
        self.assertEqual(sale.reprint_count, 1)
        text = "\n".join(receipt.render(receipt.get_completed(sale.pk), duplicate=True)[1])
        self.assertIn("*** DUPLICATE RECEIPT ***", text)
        self.assertTrue(AuditLog.objects.filter(action="receipt.reprinted", actor=self.supervisor).exists())

    def test_reprint_counter_is_the_only_thing_that_can_change(self):
        services.mark_reprint(self.supervisor, self.done.pk)
        self.done.refresh_from_db()
        self.assertEqual(self.done.total, D("3500.00"))


class ApiTests(SalesCase):
    """The same flow through the JSON endpoints the sales screen will use."""

    def setUp(self):
        super().setUp()
        self.shift.closed_at = self.shift.opened_at          # use the real opening flow below
        self.shift.save()
        self.assertEqual(self.post("/api/pos/authorize/", {"till": 1, "username": "sup", "secret": "4321"}).status_code, 200)
        self.assertEqual(self.post("/api/pos/cashier-login/", {"username": "cash1", "secret": "1111", "till": 1}).status_code, 200)
        self.assertEqual(self.post("/api/pos/shift/open/", {"opening_float": "1000"}).status_code, 200)

    def test_scan_remove_with_supervisor_then_checkout_and_receipt(self):
        res = self.post("/api/sales/cart/add/", {"code": "6001111111111", "quantity": 2})
        self.assertEqual(res.status_code, 200, res.content)
        cart = res.json()["cart"]
        self.assertEqual((cart["count"], cart["total"]), (2, "7000.00"))
        item = cart["items"][0]

        res = self.post("/api/sales/cart/remove/", {"item_id": item["id"]})        # no approval
        self.assertEqual(res.status_code, 409)                                      # "a supervisor must approve"
        self.assertEqual(res.json()["approval_required"], "remove_item")
        self.assertEqual(res.json()["object_ref"], item["ref"])

        res = self.post("/api/approvals/request/", {"action": "remove_item", "approver_username": "sup",
                                                    "secret": "4321", "reason": "Customer changed mind",
                                                    "object_ref": item["ref"]})
        token = res.json()["token"]
        res = self.post("/api/sales/cart/remove/", {"item_id": item["id"], "approval_token": token})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["cart"]["items"], [])

        self.post("/api/sales/cart/add/", {"code": "6001111111111"})
        res = self.post("/api/sales/checkout/cash/", {"tendered": "5000"})
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()["change"], "1500.00")
        sale_id = res.json()["sale_id"]

        res = self.client.get(f"/api/sales/receipt/{sale_id}/")
        self.assertEqual(res.status_code, 200)
        self.assertIn("TOTAL PAYABLE", "\n".join(res.json()["lines"]))
        self.assertEqual(self.client.get(f"/api/sales/receipt/{sale_id}/").status_code, 400)   # original only once
        self.assertEqual(self.post(f"/api/sales/receipt/{sale_id}/reprint/", {}).status_code, 403)

    def test_endpoints_need_a_signed_in_cashier(self):
        self.client.logout()
        self.assertEqual(self.client.get("/api/sales/cart/").status_code, 401)
        self.assertEqual(self.post("/api/sales/cart/add/", {"code": "6001111111111"}).status_code, 401)

    def test_unknown_code_is_a_clean_error(self):
        res = self.post("/api/sales/cart/add/", {"code": "nope"})
        self.assertEqual(res.status_code, 400)
        self.assertFalse(res.json()["ok"])
