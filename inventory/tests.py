from decimal import Decimal

from django.db import IntegrityError, transaction

from accounts import services as auth_services
from catalog.models import Packaging
from catalog.tests import CatalogCase, make_product
from core.models import AuditLog, ShopSettings

from . import movement_rules as mr
from . import services
from .models import Receiving, StockLot, StockMovement


def stock(product):
    product.refresh_from_db()
    return product.stock_qty


def receive(user, product, packs, cost_per_pack, packaging=None, **doc):
    r = services.new_receiving(user, **doc)
    services.add_receiving_item(r, user, product=product, packaging=packaging, qty_good=packs, qty_damaged=0,
                                qty_missing=0, cost_per_pack=Decimal(cost_per_pack))
    return r


class ReceivingTests(CatalogCase):
    def test_receiving_adds_stock_with_movement_and_lot(self):
        r = receive(self.clerk, self.product, 10, "2400", delivery_note="DN-1", invoice_ref="INV-9")
        self.assertEqual(stock(self.product), 0)          # nothing changes until the delivery is posted
        done = services.submit_receiving(r.pk, self.clerk)
        self.assertEqual(done.status, Receiving.POSTED)
        self.assertEqual(stock(self.product), 10)
        m = StockMovement.objects.get()
        self.assertEqual((m.movement_type, m.quantity, m.balance_after, m.reference), (mr.RECEIVE, 10, 10, r.number))
        self.assertEqual(StockLot.objects.get().remaining_qty, 10)
        self.assertEqual(m.user, self.clerk)

    def test_pack_conversion_carton_of_24(self):
        pack = Packaging.objects.create(product=self.product, name="Carton", units_per_pack=24)
        r = receive(self.clerk, self.product, 3, "57600", packaging=pack)   # 3 cartons at 57,600 each
        services.submit_receiving(r.pk, self.clerk)
        self.assertEqual(stock(self.product), 72)
        self.product.refresh_from_db()
        self.assertEqual(self.product.avg_cost, Decimal("2400.0000"))
        self.assertEqual(self.product.stock_text(), "3 Carton (72 bottle)")

    def test_damaged_and_missing_are_recorded_but_not_added(self):
        r = services.new_receiving(self.clerk)
        services.add_receiving_item(r, self.clerk, product=self.product, packaging=None, qty_good=8, qty_damaged=2,
                                    qty_missing=1, cost_per_pack=Decimal("2400"))
        services.submit_receiving(r.pk, self.clerk)
        self.assertEqual(stock(self.product), 8)
        item = r.items.get()
        self.assertEqual((item.qty_damaged, item.qty_missing), (2, 1))

    def test_weighted_average_cost_across_deliveries(self):
        services.submit_receiving(receive(self.clerk, self.product, 10, "100").pk, self.clerk)
        services.submit_receiving(receive(self.clerk, self.product, 10, "120").pk, self.clerk)
        self.product.refresh_from_db()
        self.assertEqual(self.product.avg_cost, Decimal("110.0000"))
        self.assertEqual(self.product.last_cost, Decimal("120.0000"))

    def test_blank_cost_uses_expected_cost(self):
        r = services.new_receiving(self.clerk)
        item = services.add_receiving_item(r, self.clerk, product=self.product, packaging=None, qty_good=1,
                                           qty_damaged=0, qty_missing=0, cost_per_pack=None)
        self.assertEqual(item.cost_per_pack, Decimal("2400.00"))

    def test_clerk_receiving_never_changes_selling_price(self):
        services.submit_receiving(receive(self.clerk, self.product, 5, "9999").pk, self.clerk)
        self.product.refresh_from_db()
        self.assertEqual(self.product.selling_price, Decimal("3500.00"))
        self.assertEqual(self.product.buying_cost, Decimal("2400.0000"))

    def test_cashier_cannot_receive(self):
        with self.assertRaises(services.StockError):
            services.new_receiving(self.cashier)

    def test_submitted_delivery_is_locked(self):
        r = receive(self.clerk, self.product, 1, "1")
        services.submit_receiving(r.pk, self.clerk)
        with self.assertRaises(services.StockError):
            services.add_receiving_item(r, self.clerk, product=self.product, packaging=None, qty_good=1, qty_damaged=0,
                                        qty_missing=0, cost_per_pack=Decimal("1"))
        with self.assertRaises(services.StockError):
            services.submit_receiving(r.pk, self.clerk)       # cannot post twice
        self.assertEqual(stock(self.product), 1)

    def test_empty_delivery_cannot_be_submitted(self):
        r = services.new_receiving(self.clerk)
        with self.assertRaises(services.StockError):
            services.submit_receiving(r.pk, self.clerk)

    def test_approval_workflow(self):
        shop = ShopSettings.load()
        shop.receiving_requires_approval = True
        shop.save()
        r = receive(self.clerk, self.product, 6, "100")
        pending = services.submit_receiving(r.pk, self.clerk)
        self.assertEqual(pending.status, Receiving.PENDING)
        self.assertEqual(stock(self.product), 0)
        with self.assertRaises(services.StockError):          # the clerk cannot approve their own delivery
            services.decide_receiving(r.pk, self.clerk, approve=True)
        services.decide_receiving(r.pk, self.supervisor, approve=True)
        self.assertEqual(stock(self.product), 6)
        m = StockMovement.objects.get()
        self.assertEqual((m.user, m.approved_by), (self.clerk, self.supervisor))

    def test_rejection_needs_reason_and_adds_nothing(self):
        shop = ShopSettings.load()
        shop.receiving_requires_approval = True
        shop.save()
        r = receive(self.clerk, self.product, 6, "100")
        services.submit_receiving(r.pk, self.clerk)
        with self.assertRaises(services.StockError):
            services.decide_receiving(r.pk, self.supervisor, approve=False, note="")
        services.decide_receiving(r.pk, self.supervisor, approve=False, note="Wrong brand delivered")
        self.assertEqual(stock(self.product), 0)
        self.assertEqual(Receiving.objects.get().status, Receiving.REJECTED)

    def test_scan_screen_adds_line_from_barcode(self):
        from catalog import services as cat
        cat.add_code(self.product, "6001000000017", self.manager)
        r = services.new_receiving(self.clerk)
        self.client.force_login(self.clerk)
        resp = self.client.post(f"/manage/receiving/{r.pk}/", {
            "action": "add_item", "code": "6001000000017", "qty_good": "4", "qty_damaged": "0", "qty_missing": "0",
            "cost_per_pack": "2400"})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(r.items.get().product, self.product)

    def test_clerk_cannot_open_price_or_product_editing(self):
        self.client.force_login(self.clerk)
        self.assertEqual(self.client.get("/manage/products/new/").status_code, 403)
        self.assertEqual(self.client.get(f"/manage/stock/{self.product.pk}/adjust/").status_code, 403)

    def test_cashier_cannot_open_receiving_documents(self):
        r = receive(self.clerk, self.product, 1, "1")
        self.client.force_login(self.cashier2)
        self.assertEqual(self.client.get(f"/manage/receiving/{r.pk}/").status_code, 403)


class SharedStockTests(CatalogCase):
    """One database, two tills: a sale on Till 1 is immediately visible to Till 2."""

    def setUp(self):
        super().setUp()
        services.submit_receiving(receive(self.clerk, self.product, 5, "2400").pk, self.clerk)

    def sell(self, qty, user):
        return services.post_movement(product_id=self.product.pk, movement_type=mr.SALE, quantity=-qty, user=user,
                                      reference="TXN")

    def test_second_till_sees_reduced_stock_and_cannot_oversell(self):
        self.sell(3, self.cashier)                              # Till 1
        self.assertEqual(stock(self.product), 2)                # Till 2 reads the same row
        with self.assertRaises(services.StockError):
            self.sell(3, self.cashier2)                         # Till 2 cannot sell 3 of the remaining 2
        self.assertEqual(stock(self.product), 2)
        self.sell(2, self.cashier2)
        self.assertEqual(stock(self.product), 0)
        with self.assertRaises(services.StockError):
            self.sell(1, self.cashier)

    def test_first_sale_locks_the_costing_method(self):
        self.assertFalse(ShopSettings.load().costing_locked)
        self.sell(1, self.cashier)
        self.assertTrue(ShopSettings.load().costing_locked)

    def test_sale_records_cost_basis(self):
        m = self.sell(2, self.cashier)
        self.assertEqual(m.total_cost, Decimal("4800.00"))
        self.assertEqual(m.unit_cost, Decimal("2400.0000"))

    def test_fifo_costs_oldest_lot_first(self):
        shop = ShopSettings.load()
        shop.costing_method = ShopSettings.COSTING_FIFO
        shop.save()
        services.submit_receiving(receive(self.clerk, self.product, 5, "3000").pk, self.clerk)
        m = self.sell(7, self.cashier)                          # 5 @ 2400 + 2 @ 3000
        self.assertEqual(m.total_cost, Decimal("18000.00"))
        self.assertEqual(sorted(StockLot.objects.values_list("remaining_qty", flat=True)), [0, 3])

    def test_movements_are_append_only(self):
        m = self.sell(1, self.cashier)
        with self.assertRaises(PermissionError):
            m.quantity = -9
            m.save()
        with self.assertRaises(PermissionError):
            m.delete()

    def test_zero_or_wrong_sign_movements_are_refused(self):
        for kind, qty in [(mr.SALE, 0), (mr.SALE, 2), (mr.RECEIVE, -2)]:
            with self.assertRaises(services.StockError):
                services.post_movement(product_id=self.product.pk, movement_type=kind, quantity=qty, user=self.cashier)


class AdjustmentTests(CatalogCase):
    def setUp(self):
        super().setUp()
        services.submit_receiving(receive(self.clerk, self.product, 10, "100").pk, self.clerk)
        self.grant = lambda requester, approver="sup", secret="4321", reason="Bottle dropped": auth_services.grant_approval(
            action="stock_adjust", approver_username=approver, secret=secret, reason=reason,
            requester=requester, object_ref=str(self.product.pk))[0]

    def test_cashier_and_clerk_cannot_adjust(self):
        for user in (self.cashier, self.clerk):
            with self.assertRaises(services.StockError):
                services.adjust_stock(product_id=self.product.pk, movement_type=mr.DAMAGED, quantity=-1, user=user,
                                      reason="x")

    def test_manager_can_adjust_alone_and_it_is_audited(self):
        services.adjust_stock(product_id=self.product.pk, movement_type=mr.DAMAGED, quantity=-2, user=self.manager,
                              reason="Cracked bottles")
        self.assertEqual(stock(self.product), 8)
        entry = AuditLog.objects.get(action="stock.adjusted")
        self.assertEqual((entry.previous_value, entry.reason), ({"stock": 10}, "Cracked bottles"))

    def test_non_manager_needs_a_valid_single_use_approval(self):
        from accounts.models import Role, RolePermission
        role = Role.objects.create(name="Stock controller")
        RolePermission.objects.create(role=role, code="stock.adjust")
        controller = self.cashier2
        controller.role = role
        controller.save()
        with self.assertRaises(services.StockError):
            services.adjust_stock(product_id=self.product.pk, movement_type=mr.EXPIRED, quantity=-1, user=controller,
                                  reason="Expired", approval_token=None)
        token = self.grant(controller)
        m = services.adjust_stock(product_id=self.product.pk, movement_type=mr.EXPIRED, quantity=-1, user=controller,
                                  reason="Expired", approval_token=token)
        self.assertEqual(m.approved_by, self.supervisor)
        with self.assertRaises(services.StockError):             # token is single-use
            services.adjust_stock(product_id=self.product.pk, movement_type=mr.EXPIRED, quantity=-1, user=controller,
                                  reason="Expired", approval_token=token)
        self.assertEqual(stock(self.product), 9)

    def test_adjustment_cannot_take_stock_below_zero(self):
        with self.assertRaises(services.StockError):
            services.adjust_stock(product_id=self.product.pk, movement_type=mr.DAMAGED, quantity=-11, user=self.manager,
                                  reason="oops")

    def test_reason_required(self):
        with self.assertRaises(services.StockError):
            services.adjust_stock(product_id=self.product.pk, movement_type=mr.ADJUST, quantity=1, user=self.manager,
                                  reason="  ")

    def test_opening_stock_only_before_other_movements(self):
        fresh = make_product("Fresh", "FRESH-1", cost="50")
        m = services.adjust_stock(product_id=fresh.pk, movement_type=mr.OPENING, quantity=24, user=self.manager,
                                  reason="Opening count")
        fresh.refresh_from_db()
        self.assertEqual((fresh.stock_qty, fresh.avg_cost), (24, Decimal("50.0000")))
        with self.assertRaises(services.StockError):
            services.adjust_stock(product_id=fresh.pk, movement_type=mr.OPENING, quantity=5, user=self.manager,
                                  reason="again")
        self.assertEqual(m.movement_type, mr.OPENING)

    def test_adjust_screen_demands_approver_from_non_manager(self):
        from accounts.models import Role, RolePermission
        role = Role.objects.create(name="Stock controller")
        RolePermission.objects.create(role=role, code="stock.adjust")
        RolePermission.objects.create(role=role, code="stock.view")
        controller = self.cashier2
        controller.role = role
        controller.save()
        self.client.force_login(controller)
        url = f"/manage/stock/{self.product.pk}/adjust/"
        r = self.client.post(url, {"movement_type": mr.DAMAGED, "quantity": "2", "reason": "Dropped"})
        self.assertEqual(r.status_code, 200)                    # re-shown with an error, nothing changed
        self.assertEqual(stock(self.product), 10)
        r = self.client.post(url, {"movement_type": mr.DAMAGED, "quantity": "2", "reason": "Dropped",
                                   "approver_username": "sup", "approver_secret": "4321"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(stock(self.product), 8)
