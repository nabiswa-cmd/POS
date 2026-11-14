"""Database foundation for partial quantity selling: SellingUnit, Bottle and bottle movements."""
from decimal import Decimal as D

from django.db import IntegrityError, transaction
from django.utils import timezone

from catalog.models import SellingUnit
from catalog.tests import CatalogCase, make_product

from . import movement_rules as mr
from . import services
from .models import Bottle, StockMovement


def fails(testcase, fn):
    with testcase.assertRaises(IntegrityError):
        with transaction.atomic():
            fn()


class MovementRuleTests(CatalogCase):
    def test_bottle_types_are_known_and_labelled(self):
        for t in mr.BOTTLE_TYPES:
            self.assertIn(t, mr.ALL_TYPES)
            self.assertIn(t, mr.LABELS)
        self.assertLessEqual(max(len(t) for t in mr.ALL_TYPES), 16)       # StockMovement.movement_type max_length

    def test_whole_unit_posting_refuses_bottle_types(self):
        for t in mr.BOTTLE_TYPES:
            with self.assertRaises(mr.MovementError):
                mr.check_quantity(t, -1)
        with self.assertRaises(services.StockError):
            services.post_movement(product_id=self.product.pk, movement_type=mr.PARTIAL_SALE, quantity=-1,
                                   user=self.manager)

    def test_existing_rules_unchanged(self):
        mr.check_quantity(mr.SALE, -1)
        mr.check_quantity(mr.RECEIVE, 5)
        for bad in ((mr.SALE, 1), (mr.RECEIVE, -1), (mr.ADJUST, 0)):
            with self.assertRaises(mr.MovementError):
                mr.check_quantity(*bad)
        self.assertFalse(mr.BOTTLE_TYPES & set(mr.MANUAL_TYPES))          # not offered on the manual stock form

    def test_bottle_movement_rules(self):
        mr.check_bottle_movement(mr.BOTTLE_OPEN, -1, D("750"))
        mr.check_bottle_movement(mr.PARTIAL_SALE, 0, D("-25"))
        mr.check_bottle_movement(mr.VOLUME_WASTAGE, 0, D("-10"))
        mr.check_bottle_movement(mr.VOLUME_ADJUST, 0, D("5"))
        mr.check_bottle_movement(mr.VOLUME_ADJUST, 0, D("-5"))
        bad = [(mr.BOTTLE_OPEN, -2, D("750")), (mr.BOTTLE_OPEN, 0, D("750")), (mr.BOTTLE_OPEN, -1, D("-750")),
               (mr.PARTIAL_SALE, 0, D("25")), (mr.PARTIAL_SALE, -1, D("-25")), (mr.PARTIAL_SALE, 0, D("0")),
               (mr.VOLUME_WASTAGE, 0, D("10")), (mr.VOLUME_ADJUST, 0, None), (mr.SALE, 0, D("-5"))]
        for args in bad:
            with self.assertRaises(mr.MovementError, msg=str(args)):
                mr.check_bottle_movement(*args)


class SellingUnitTests(CatalogCase):
    def make(self, **kw):
        d = dict(product=self.product, name="Tot", volume_ml=D("25"), selling_price=D("150"))
        d.update(kw)
        return SellingUnit.objects.create(**d)

    def test_create_and_defaults(self):
        u = self.make()
        u.refresh_from_db()
        self.assertTrue(u.active)
        self.assertEqual(self.product.selling_units.get(), u)
        self.assertEqual(str(u), "Tot (25.00 ml)")

    def test_name_unique_per_product_but_not_across_products(self):
        self.make()
        fails(self, lambda: self.make(volume_ml=D("50")))
        other = make_product("Other", "OTH-1")
        self.make(product=other)

    def test_one_active_unit_per_volume_but_inactive_duplicates_allowed(self):
        self.make()
        fails(self, lambda: self.make(name="Shot", volume_ml=D("25")))
        self.make(name="Old tot", volume_ml=D("25"), active=False)

    def test_volume_must_be_positive_and_price_not_negative(self):
        fails(self, lambda: self.make(volume_ml=D("0")))
        fails(self, lambda: self.make(volume_ml=D("-5")))
        fails(self, lambda: self.make(selling_price=D("-1")))
        self.make(selling_price=D("0"))

    def test_decimal_volume_kept_exactly(self):
        self.assertEqual(self.make(volume_ml=D("37.50")).volume_ml, D("37.50"))
        self.assertIsInstance(SellingUnit._meta.get_field("volume_ml").to_python("1.5"), D)


class BottleTests(CatalogCase):
    def make(self, **kw):
        d = dict(product=self.product, capacity_ml=D("750"), remaining_ml=D("750"), status=Bottle.SEALED)
        d.update(kw)
        return Bottle.objects.create(**d)

    def opened(self, remaining="500", **kw):
        return self.make(remaining_ml=D(remaining), status=Bottle.OPENED, opened_at=timezone.now(),
                         opened_by=self.manager, **kw)

    def test_valid_states(self):
        self.make()
        self.opened()
        self.make(remaining_ml=D("0"), status=Bottle.EMPTY, opened_at=timezone.now())
        self.assertEqual(Bottle.objects.count(), 3)

    def test_capacity_and_remaining_limits(self):
        fails(self, lambda: self.make(capacity_ml=D("0"), remaining_ml=D("0"), status=Bottle.EMPTY))
        fails(self, lambda: self.opened("751"))
        fails(self, lambda: self.opened("-1"))

    def test_status_must_match_volume(self):
        fails(self, lambda: self.make(remaining_ml=D("700")))                                  # sealed but not full
        fails(self, lambda: self.make(opened_at=timezone.now()))                               # sealed with opening time
        fails(self, lambda: self.make(status=Bottle.OPENED, remaining_ml=D("500")))            # opened, no timestamp
        fails(self, lambda: self.opened("0"))                                                  # opened but nothing left
        fails(self, lambda: self.make(status=Bottle.EMPTY, remaining_ml=D("10"), opened_at=timezone.now()))
        fails(self, lambda: self.make(status="BROKEN", remaining_ml=D("750")))

    def test_oldest_opened_bottle_comes_first(self):
        a = self.opened("100")
        b = self.opened("200")
        self.assertEqual(list(Bottle.objects.filter(status=Bottle.OPENED)), [a, b])


class BottleMovementTests(CatalogCase):
    def setUp(self):
        super().setUp()
        self.bottle = Bottle.objects.create(product=self.product, capacity_ml=D("750"), remaining_ml=D("725"),
                                            status=Bottle.OPENED, opened_at=timezone.now(), opened_by=self.manager)

    def move(self, mtype, qty, vol, **kw):
        d = dict(product=self.product, movement_type=mtype, quantity=qty, balance_after=0, user=self.manager,
                 bottle=self.bottle, volume_ml=vol, bottle_remaining_after_ml=D("700"))
        d.update(kw)
        return StockMovement.objects.create(**d)

    def test_partial_sale_movement_is_volume_only(self):
        m = self.move(mr.PARTIAL_SALE, 0, D("-25"))
        self.assertEqual((m.quantity, m.volume_ml), (0, D("-25")))
        self.assertEqual(self.bottle.movements.get(), m)
        self.assertEqual(m.get_movement_type_display(), "Partial sale")

    def test_opening_wastage_and_adjustment_movements(self):
        self.move(mr.BOTTLE_OPEN, -1, D("750"))
        self.move(mr.VOLUME_WASTAGE, 0, D("-20"))
        self.move(mr.VOLUME_ADJUST, 0, D("5"))
        self.assertEqual(StockMovement.objects.filter(movement_type__in=mr.BOTTLE_TYPES).count(), 3)

    def test_zero_quantity_still_refused_without_volume(self):
        fails(self, lambda: self.move(mr.ADJUST, 0, None, bottle=None, bottle_remaining_after_ml=None))
        fails(self, lambda: self.move(mr.PARTIAL_SALE, 0, D("0")))

    def test_volume_requires_a_bottle(self):
        fails(self, lambda: self.move(mr.PARTIAL_SALE, 0, D("-25"), bottle=None))

    def test_ordinary_movements_unchanged_and_still_append_only(self):
        m = StockMovement.objects.create(product=self.product, movement_type=mr.RECEIVE, quantity=3, balance_after=3,
                                         user=self.manager)
        self.assertIsNone(m.bottle_id)
        self.assertIsNone(m.volume_ml)
        with self.assertRaises(PermissionError):
            m.save()
        p = self.move(mr.PARTIAL_SALE, 0, D("-25"))
        with self.assertRaises(PermissionError):
            p.delete()

    def test_bottle_with_movements_cannot_be_deleted(self):
        from django.db.models import ProtectedError
        self.move(mr.PARTIAL_SALE, 0, D("-25"))
        with self.assertRaises(ProtectedError):
            self.bottle.delete()
