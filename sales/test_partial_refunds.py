"""Partial-volume refunds preserve the original sale snapshot and restore tracked bottle liquid."""
from decimal import Decimal as D

from catalog.models import SellingUnit
from core.models import AuditLog
from inventory import movement_rules as mr
from inventory import services as stock_services
from inventory.models import Bottle, StockMovement

from . import refunds, rules, services
from .models import Refund, RefundItem
from .tests import SalesCase


class PartialRefundTests(SalesCase):
    def setUp(self):
        super().setUp()
        self.product.bottle_capacity_ml = D("750")
        self.product.save(update_fields=["bottle_capacity_ml"])
        self.tot = SellingUnit.objects.create(
            product=self.product, name="TOT", volume_ml=D("30"), selling_price=D("100"))
        self.half = SellingUnit.objects.create(
            product=self.product, name="Half Bottle", volume_ml=D("375"), selling_price=D("800"))

    def sell(self, unit=None, quantity=1):
        sale, _ = services.add_item(
            self.cashier, self.shift, selling_unit_id=(unit or self.tot).pk, quantity=quantity)
        return services.checkout_cash(self.cashier, self.shift, str(sale.total))

    def ask(self, sale, quantity, *, request_key=None, restock=True):
        item = sale.items.get()
        return refunds.request_refund(
            self.cashier, self.shift, sale.pk,
            [{"item_id": item.pk, "quantity": quantity, "restock": restock}],
            "Customer returned measure", "CASH", request_key=request_key)

    def approve(self, refund):
        return refunds.approve_refund(
            self.cashier, refund.pk,
            approval_token=self.approval("refund", refunds.ref(refund.pk)))

    def test_tot_refund_uses_sale_price_snapshot_and_restores_same_bottle(self):
        sale = self.sell(self.tot, 2)
        item = sale.items.get()
        opening = StockMovement.objects.get(movement_type=mr.BOTTLE_OPEN, reference=sale.number)
        sold = StockMovement.objects.get(movement_type=mr.PARTIAL_SALE, reference=sale.number)
        self.tot.selling_price = D("999")
        self.tot.save(update_fields=["selling_price"])

        refund = self.ask(sale, 1)
        refund_line = refund.items.get()
        self.assertEqual((refund.total, refund_line.amount, refund_line.volume_ml,
                          refund_line.selling_unit_name), (D("100.00"), D("100.00"), D("30.00"), "TOT"))
        self.assertEqual(item.unit_price, D("100.00"))

        self.approve(refund)
        bottle = Bottle.objects.get(pk=opening.bottle_id)
        returned = StockMovement.objects.get(movement_type=mr.VOLUME_REFUND)
        refund_line.refresh_from_db()
        self.assertEqual((bottle.remaining_ml, bottle.status), (D("720.00"), Bottle.OPENED))
        self.assertEqual((returned.volume_ml, returned.bottle_id, returned.source_movement_id,
                          returned.refund_item_id, returned.user_id, returned.approved_by_id),
                         (D("30.00"), opening.bottle_id, sold.pk, refund_line.pk,
                          self.cashier.pk, self.supervisor.pk))
        self.assertEqual(returned.reference, refund.number)

    def test_half_bottle_refund_restores_375_ml_and_original_amount(self):
        sale = self.sell(self.half, 2)
        refund = self.ask(sale, 1)
        line = refund.items.get()
        self.assertEqual((refund.total, line.amount, line.volume_ml), (D("800.00"), D("800.00"), D("375.00")))

        self.approve(refund)
        bottle = Bottle.objects.get(product=self.product)
        self.assertEqual((bottle.remaining_ml, bottle.status), (D("375.00"), Bottle.OPENED))

    def test_multiple_partial_refunds_are_capped_by_units_sold(self):
        sale = self.sell(self.tot, 2)
        first = self.ask(sale, 1)
        self.approve(first)
        second = self.ask(sale, 1)
        self.approve(second)

        bottle = Bottle.objects.get(product=self.product)
        item = sale.items.get()
        self.assertEqual((bottle.remaining_ml, bottle.status), (D("750.00"), Bottle.OPENED))
        self.assertEqual(sum(StockMovement.objects.filter(movement_type=mr.VOLUME_REFUND)
                             .values_list("volume_ml", flat=True)), D("60.00"))
        self.assertEqual(sum(refunds._refunded(item)[1:]), D("200.00"))
        with self.assertRaises(services.SaleError):
            self.ask(sale, 1)

    def test_over_refund_is_rejected_before_any_inventory_change(self):
        sale = self.sell(self.tot, 1)
        with self.assertRaises(services.SaleError):
            self.ask(sale, 2)
        self.assertFalse(Refund.objects.exists())
        self.assertEqual(StockMovement.objects.filter(movement_type=mr.VOLUME_REFUND).count(), 0)

    def test_same_request_key_and_repeated_approval_restore_only_once(self):
        sale = self.sell(self.tot, 2)
        key = "refund-attempt-1"
        first = self.ask(sale, 1, request_key=key)
        retry = self.ask(sale, 1, request_key=key)
        self.assertEqual(first.pk, retry.pk)
        with self.assertRaises(services.SaleError):
            self.ask(sale, 1, request_key=key, restock=False)

        self.approve(first)
        with self.assertRaises(services.SaleError):
            refunds.approve_refund(self.manager, first.pk)
        self.assertEqual(StockMovement.objects.filter(movement_type=mr.VOLUME_REFUND).count(), 1)
        retry_after_approval = self.ask(sale, 1, request_key=key)
        self.assertEqual((retry_after_approval.pk, retry_after_approval.status), (first.pk, Refund.APPROVED))
        self.assertEqual(StockMovement.objects.filter(movement_type=mr.VOLUME_REFUND).count(), 1)

    def test_partial_refund_payout_is_reconciled_and_cannot_be_paid_twice(self):
        sale = self.sell(self.tot, 2)
        refund = self.ask(sale, 1)
        self.approve(refund)
        refunds.pay_cash(self.cashier, self.shift, refund.pk)

        with self.assertRaises(services.SaleError):
            refunds.pay_cash(self.cashier, self.shift, refund.pk)

        summary = services.shift_summary(self.shift)
        self.assertEqual((refund.total, summary["cash_sales"], summary["cash_refunds"],
                          summary["cash_in_drawer_expected"]),
                         (D("100.00"), "200.00", "100.00", "5100.00"))
        self.assertEqual(StockMovement.objects.filter(movement_type=mr.VOLUME_REFUND).count(), 1)

    def test_non_resalable_partial_refund_does_not_restore_liquid(self):
        sale = self.sell(self.tot, 1)
        refund = self.ask(sale, 1, restock=False)
        self.approve(refund)
        self.assertFalse(StockMovement.objects.filter(movement_type=mr.VOLUME_REFUND).exists())
        self.assertEqual(Bottle.objects.get(product=self.product).remaining_ml, D("720.00"))

    def test_partial_refund_requires_existing_approval_and_records_audit(self):
        sale = self.sell(self.tot, 1)
        refund = self.ask(sale, 1)
        with self.assertRaises(services.ApprovalNeeded):
            refunds.approve_refund(self.cashier, refund.pk)
        self.assertEqual(Refund.objects.get(pk=refund.pk).status, Refund.REQUESTED)

        self.approve(refund)
        approval_event = AuditLog.objects.get(action="refund.approved", object_ref=refund.number)
        movement = StockMovement.objects.get(movement_type=mr.VOLUME_REFUND)
        self.assertEqual((movement.user, movement.approved_by), (self.cashier, self.supervisor))
        self.assertEqual((approval_event.actor, approval_event.approver), (self.cashier, self.supervisor))
        self.assertEqual(approval_event.new_value["sale"], sale.number)
        self.assertEqual(approval_event.new_value["restocked"][0]["volume_ml"], "30.00")

    def test_restoration_error_rolls_back_all_bottle_and_ledger_changes(self):
        sale = self.sell(self.tot, 26)  # 780 ml spans two original bottles.
        refund = self.ask(sale, 26)
        bottles = list(Bottle.objects.filter(product=self.product).order_by("id"))
        self.assertEqual([b.remaining_ml for b in bottles], [D("0.00"), D("720.00")])
        stock_services.adjust_bottle_volume(
            bottle_id=bottles[1].pk, volume_ml=D("30.00"), user=self.manager,
            operation_key="pre-refund-reconciliation", reason="Recorded reconciliation")
        movement_count = StockMovement.objects.count()

        with self.assertRaises(services.SaleError):
            self.approve(refund)

        bottles[0].refresh_from_db()
        bottles[1].refresh_from_db()
        refund.refresh_from_db()
        self.assertEqual([b.remaining_ml for b in bottles], [D("0.00"), D("750.00")])
        self.assertEqual(StockMovement.objects.count(), movement_count)
        self.assertEqual(refund.status, Refund.REQUESTED)
        self.assertFalse(RefundItem.objects.get(refund=refund).restocked)

    def test_rejected_refund_does_not_restore_liquid(self):
        sale = self.sell(self.tot, 1)
        refund = self.ask(sale, 1)
        refunds.reject_refund(self.supervisor, refund.pk, "Not resalable")
        self.assertFalse(StockMovement.objects.filter(movement_type=mr.VOLUME_REFUND).exists())
        self.assertEqual(Bottle.objects.get(product=self.product).remaining_ml, D("720.00"))

    def test_refund_of_negative_partial_sale_offsets_outstanding_volume_debt(self):
        sale, _line = services.add_item(self.cashier, self.shift, selling_unit_id=self.tot.pk, quantity=251)
        token = self.approval("negative_stock_sale", rules.sale_ref(sale.pk), reason="Approved volume shortage")
        sale = services.checkout_cash(self.cashier, self.shift, "25100", approval_token=token)
        refund = self.ask(sale, 251)
        self.approve(refund)

        source = StockMovement.objects.get(movement_type=mr.PARTIAL_DEFICIT, reference=sale.number)
        returned = StockMovement.objects.get(
            movement_type=mr.PARTIAL_DEFICIT_OFFSET, refund_item__isnull=False)
        self.assertEqual((source.volume_ml, returned.volume_ml, returned.source_movement_id),
                         (D("-30.00"), D("30.00"), source.pk))
        self.assertEqual(Bottle.objects.filter(product=self.product, status=Bottle.OPENED).count(), 10)

    def test_refund_after_receipt_offset_restores_liquid_to_the_received_bottle(self):
        sale, _line = services.add_item(self.cashier, self.shift, selling_unit_id=self.tot.pk, quantity=251)
        token = self.approval("negative_stock_sale", rules.sale_ref(sale.pk), reason="Approved volume shortage")
        sale = services.checkout_cash(self.cashier, self.shift, "25100", approval_token=token)
        stock_services.post_movement(product_id=self.product.pk, movement_type=mr.RECEIVE, quantity=1,
                                     user=self.clerk, unit_cost=D("2400"), reference="REFUND-RESTOCK")

        refund = self.ask(sale, 251)
        self.approve(refund)
        offset = StockMovement.objects.get(movement_type=mr.VOLUME_DEFICIT_OFFSET)
        bottle = Bottle.objects.get(pk=offset.bottle_id)
        returned = StockMovement.objects.get(
            movement_type=mr.VOLUME_REFUND, source_movement__movement_type=mr.PARTIAL_DEFICIT)
        self.assertEqual((bottle.remaining_ml, returned.volume_ml, returned.total_cost),
                         (D("750.00"), D("30.00"), D("96.00")))

