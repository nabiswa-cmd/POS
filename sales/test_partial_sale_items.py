"""SaleItem columns for partial quantity selling (data model only; checkout is a later phase)."""
from decimal import Decimal as D

from django.db import IntegrityError, transaction

from catalog.models import Packaging, SellingUnit

from .models import SaleItem
from .tests import SalesCase


class PartialSaleItemTests(SalesCase):
    def setUp(self):
        super().setUp()
        self.sale, self.full = self.scan()                      # an ordinary full-bottle line
        self.unit = SellingUnit.objects.create(product=self.product, name="Tot", volume_ml=D("25"),
                                               selling_price=D("150"))

    def line(self, **kw):
        d = dict(sale=self.sale, product=self.product, product_name=self.product.name, quantity=4,
                 list_price=D("150"), unit_price=D("150"), line_total=D("600"), selling_unit=self.unit,
                 selling_unit_name="Tot", volume_per_unit_ml=D("25"), total_volume_ml=D("100"))
        d.update(kw)
        return SaleItem.objects.create(**d)

    def fails(self, **kw):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.line(**kw)

    def test_full_bottle_line_is_unchanged(self):
        self.assertFalse(self.full.is_partial)
        self.assertIsNone(self.full.selling_unit_id)
        self.assertIsNone(self.full.volume_per_unit_ml)
        self.assertIsNone(self.full.total_volume_ml)
        self.assertEqual(self.full.selling_unit_name, "")
        self.assertEqual(self.full.base_qty, self.full.quantity * self.full.units_per_sale_unit)

    def test_partial_line_stores_snapshot_and_takes_no_whole_bottles(self):
        it = self.line()
        it.refresh_from_db()
        self.assertTrue(it.is_partial)
        self.assertEqual((it.selling_unit_name, it.volume_per_unit_ml, it.total_volume_ml, it.unit_price),
                         ("Tot", D("25.00"), D("100.00"), D("150.00")))
        self.assertEqual(it.base_qty, 0)

    def test_snapshot_survives_later_changes_to_the_selling_unit(self):
        it = self.line()
        self.unit.name, self.unit.volume_ml, self.unit.selling_price = "Shot", D("30"), D("200")
        self.unit.save()
        it.refresh_from_db()
        self.assertEqual((it.selling_unit_name, it.volume_per_unit_ml, it.unit_price), ("Tot", D("25.00"), D("150.00")))

    def test_partial_fields_must_be_complete_and_consistent(self):
        self.fails(total_volume_ml=D("99"))                      # 4 x 25 is 100
        self.fails(total_volume_ml=None)
        self.fails(volume_per_unit_ml=None)
        self.fails(volume_per_unit_ml=D("0"), total_volume_ml=D("0"))
        self.fails(selling_unit_name="")
        self.fails(selling_unit=None, selling_unit_name="", volume_per_unit_ml=None, total_volume_ml=D("100"))
        self.fails(selling_unit=self.unit, selling_unit_name="", volume_per_unit_ml=None, total_volume_ml=None)

    def test_a_partial_line_cannot_also_be_a_pack(self):
        pack = Packaging.objects.create(product=self.product, name="Carton", units_per_pack=12)
        self.fails(packaging=pack)

    def test_selling_unit_in_use_cannot_be_deleted(self):
        from django.db.models import ProtectedError
        self.line()
        with self.assertRaises(ProtectedError):
            self.unit.delete()
