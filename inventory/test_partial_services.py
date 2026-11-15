from decimal import Decimal

from django.db import IntegrityError, transaction

from catalog.models import SellingUnit
from catalog.tests import CatalogCase

from . import movement_rules as mr
from . import services
from .models import Bottle, StockMovement


class PartialInventoryServiceTests(CatalogCase):
    def add_stock(self, quantity):
        services.adjust_stock(product_id=self.product.pk, movement_type=mr.OPENING, quantity=quantity,
                              user=self.manager, reason="Initial bottle count")

    def sell(self, volume, key, *, capacity="750"):
        return services.sell_volume(product_id=self.product.pk, volume_ml=Decimal(volume), user=self.cashier,
                                    operation_key=key, bottle_capacity_ml=Decimal(capacity))

    def test_two_tots_use_one_new_bottle_and_keep_total_volume(self):
        self.add_stock(10)
        tot = SellingUnit.objects.create(product=self.product, name="Tot", volume_ml=Decimal("30"),
                                         selling_price=Decimal("150"))

        movements = services.sell_measures(selling_unit_id=tot.pk, quantity=2, user=self.cashier,
                                           operation_key="sale-tots", bottle_capacity_ml=Decimal("750"))

        self.product.refresh_from_db()
        bottle = Bottle.objects.get(product=self.product)
        stock = services.partial_stock(self.product.pk, bottle_capacity_ml=Decimal("750"))
        self.assertEqual(self.product.stock_qty, 9)
        self.assertEqual((bottle.status, bottle.remaining_ml), (Bottle.OPENED, Decimal("690.00")))
        self.assertEqual(stock["total_volume_ml"], Decimal("7440.00"))
        self.assertEqual([m.movement_type for m in movements], [mr.BOTTLE_OPEN, mr.PARTIAL_SALE])
        self.assertEqual(StockMovement.objects.filter(operation_key="sale-tots").count(), 2)

    def test_half_bottle_sale(self):
        self.add_stock(1)

        self.sell("375", "sale-half")

        bottle = Bottle.objects.get(product=self.product)
        self.assertEqual(bottle.remaining_ml, Decimal("375.00"))
        self.assertEqual(bottle.status, Bottle.OPENED)

    def test_consecutive_sales_use_existing_open_bottle_before_sealed_stock(self):
        self.add_stock(2)
        bottle = services.open_bottle(product_id=self.product.pk, capacity_ml=Decimal("750"), user=self.cashier,
                                      operation_key="opening-1")

        self.sell("60", "sale-first")
        self.sell("30", "sale-second")

        self.product.refresh_from_db()
        bottle.refresh_from_db()
        self.assertEqual(self.product.stock_qty, 1)
        self.assertEqual(bottle.remaining_ml, Decimal("660.00"))
        self.assertEqual(Bottle.objects.filter(product=self.product).count(), 1)

    def test_opening_is_recorded_and_repeated_key_cannot_open_twice(self):
        self.add_stock(2)

        bottle = services.open_bottle(product_id=self.product.pk, capacity_ml=Decimal("750"), user=self.cashier,
                                      operation_key="open-once")
        with self.assertRaises(services.StockError):
            services.open_bottle(product_id=self.product.pk, capacity_ml=Decimal("750"), user=self.cashier,
                                 operation_key="open-once")

        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_qty, 1)
        self.assertEqual(StockMovement.objects.filter(operation_key="open-once", movement_type=mr.BOTTLE_OPEN).count(),
                         1)
        self.assertEqual(bottle.movements.get().volume_ml, Decimal("750.00"))

    def test_insufficient_volume_does_not_open_or_deduct_anything(self):
        self.add_stock(1)
        movement_count = StockMovement.objects.count()

        with self.assertRaises(services.StockError):
            self.sell("751", "sale-too-large")

        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_qty, 1)
        self.assertFalse(Bottle.objects.filter(product=self.product).exists())
        self.assertEqual(StockMovement.objects.count(), movement_count)

    def test_replayed_sale_operation_is_rejected_without_duplicate_movements(self):
        self.add_stock(2)
        self.sell("30", "sale-replay")
        movement_count = StockMovement.objects.count()

        with self.assertRaises(services.StockError):
            self.sell("30", "sale-replay")

        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_qty, 1)
        self.assertEqual(StockMovement.objects.count(), movement_count)
        self.assertEqual(Bottle.objects.get(product=self.product).remaining_ml, Decimal("720.00"))

    def test_operation_key_sequence_is_unique_in_database(self):
        self.add_stock(1)
        bottle = services.open_bottle(product_id=self.product.pk, capacity_ml=Decimal("750"), user=self.cashier,
                                      operation_key="unique-opening")

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                StockMovement.objects.create(
                    product=self.product, bottle=bottle, movement_type=mr.BOTTLE_OPEN, quantity=-1,
                    balance_after=0, volume_ml=Decimal("750"), bottle_remaining_after_ml=Decimal("750"),
                    user=self.cashier, operation_key="unique-opening", operation_sequence=0)

        self.assertEqual(StockMovement.objects.filter(operation_key="unique-opening").count(), 1)

    def test_spillage_records_wastage_and_empty_bottle(self):
        self.add_stock(1)
        bottle = services.open_bottle(product_id=self.product.pk, capacity_ml=Decimal("750"), user=self.cashier,
                                      operation_key="open-for-spill")

        services.record_spillage(product_id=self.product.pk, volume_ml=Decimal("750"), user=self.manager,
                                 operation_key="spill-all", reason="Bottle spilled")

        bottle.refresh_from_db()
        movement = StockMovement.objects.get(operation_key="spill-all")
        stock = services.partial_stock(self.product.pk)
        self.assertEqual(bottle.status, Bottle.EMPTY)
        self.assertEqual(movement.movement_type, mr.VOLUME_WASTAGE)
        self.assertEqual(movement.reason, "Bottle spilled")
        self.assertEqual(stock["empty_bottles"], 1)
        self.assertEqual(stock["total_volume_ml"], Decimal("0.00"))

    def test_wastage_cannot_consume_sealed_stock_implicitly(self):
        self.add_stock(1)

        with self.assertRaises(services.StockError):
            services.record_wastage(product_id=self.product.pk, volume_ml=Decimal("1"), user=self.manager,
                                    operation_key="waste-sealed", reason="Spill")

        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_qty, 1)
        self.assertFalse(Bottle.objects.filter(product=self.product).exists())

    def test_volume_adjustment_tracks_bottle_and_requires_reason(self):
        self.add_stock(1)
        bottle = services.open_bottle(product_id=self.product.pk, capacity_ml=Decimal("750"), user=self.cashier,
                                      operation_key="open-for-adjustment")
        services.adjust_bottle_volume(bottle_id=bottle.pk, volume_ml=Decimal("-10"), user=self.manager,
                                      operation_key="adjust-down", reason="Measured spillage")
        movement = services.adjust_bottle_volume(bottle_id=bottle.pk, volume_ml=Decimal("5"), user=self.manager,
                                                 operation_key="adjust-up", reason="Count correction")

        bottle.refresh_from_db()
        self.assertEqual(bottle.remaining_ml, Decimal("745.00"))
        self.assertEqual(movement.movement_type, mr.VOLUME_ADJUST)
        self.assertEqual(movement.bottle_remaining_after_ml, Decimal("745.00"))
        with self.assertRaises(services.StockError):
            services.adjust_bottle_volume(bottle_id=bottle.pk, volume_ml=Decimal("1"), user=self.manager,
                                          operation_key="no-reason", reason=" ")

    def test_manual_opening_requires_available_sealed_bottle(self):
        with self.assertRaises(services.StockError):
            services.open_bottle(product_id=self.product.pk, capacity_ml=Decimal("750"), user=self.cashier,
                                 operation_key="open-empty-stock")
        self.assertFalse(Bottle.objects.filter(product=self.product).exists())

