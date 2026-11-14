from decimal import Decimal

from django.test import TestCase

from accounts.tests import BaseCase
from catalog import services
from catalog.models import Packaging, Product, ProductCode, Shelf, ShelfTransfer
from core.models import AuditLog


def make_product(name="Johnnie Walker Black 750ml", sku="JW-BLK", price="3500", cost="2400", **extra):
    p = Product.objects.create(name=name, sku=sku, selling_price=Decimal(price), buying_cost=Decimal(cost), **extra)
    ProductCode.objects.create(product=p, code=p.sku, kind=ProductCode.KIND_SKU)
    return p


class CatalogCase(BaseCase):
    def setUp(self):
        super().setUp()
        self.product = make_product()


class CodeTests(CatalogCase):
    def test_duplicate_codes_refused_across_products(self):
        other = make_product("Other", "OTH-1")
        services.add_code(self.product, "6001234567890", self.manager)
        with self.assertRaises(services.CatalogError):
            services.add_code(other, "6001234567890", self.manager)
        with self.assertRaises(services.CatalogError):   # SKUs share the same namespace
            services.add_code(other, "jw-blk", self.manager)

    def test_codes_are_case_and_space_insensitive(self):
        services.add_code(self.product, " abc-123 ", self.manager)
        self.assertEqual(services.find_by_code("ABC-123").product, self.product)
        self.assertEqual(services.find_by_code("abc-123 ").product, self.product)

    def test_internal_code_is_generated_unique_and_scannable(self):
        a = services.generate_internal_code(self.product, self.manager)
        b = services.generate_internal_code(self.product, self.manager)
        self.assertNotEqual(a.code, b.code)
        self.assertTrue(a.code.startswith("20") and len(a.code) == 13)
        self.assertEqual(services.find_by_code(a.code).product, self.product)

    def test_pack_barcode_resolves_to_pack(self):
        pack = Packaging.objects.create(product=self.product, name="Carton", units_per_pack=12)
        services.add_code(self.product, "CARTON-JW", self.manager, packaging=pack)
        hit = services.find_by_code("CARTON-JW")
        self.assertEqual(hit.packaging, pack)
        self.assertEqual(hit.units, 12)

    def test_inactive_products_are_not_found(self):
        self.product.active = False
        self.product.save()
        self.assertIsNone(services.find_by_code("JW-BLK"))

    def test_cashier_cannot_add_codes(self):
        with self.assertRaises(services.CatalogError):
            services.add_code(self.product, "NEWCODE1", self.cashier)

    def test_lookup_api_for_cashier(self):
        self.client.force_login(self.cashier)
        r = self.client.get("/api/catalog/lookup/", {"code": "jw-blk"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["item"]["name"], self.product.name)
        self.assertEqual(self.client.get("/api/catalog/lookup/", {"code": "nope"}).status_code, 404)

    def test_lookup_requires_login(self):
        self.assertEqual(self.client.get("/api/catalog/lookup/", {"code": "JW-BLK"}).status_code, 401)


class PackagingTests(CatalogCase):
    def test_pack_price_defaults_to_unit_times_units(self):
        pack = Packaging.objects.create(product=self.product, name="Carton", units_per_pack=12)
        self.assertEqual(pack.price, Decimal("42000.00"))
        pack.selling_price = Decimal("40000")
        self.assertEqual(pack.price, Decimal("40000.00"))

    def test_each_product_has_its_own_conversion(self):
        beer = make_product("Tusker 500ml", "TUSK", price="250", cost="160", base_unit="bottle")
        Packaging.objects.create(product=self.product, name="Carton", units_per_pack=12)
        Packaging.objects.create(product=beer, name="Crate", units_per_pack=24)
        self.assertEqual(self.product.packagings.get().units_per_pack, 12)
        self.assertEqual(beer.packagings.get().units_per_pack, 24)


class ProductPermissionTests(CatalogCase):
    def product_form(self, **extra):
        from catalog.forms import ProductForm

        data = {"name": self.product.name, "sku": self.product.sku, "base_unit": "bottle",
                "buying_cost": "2400", "selling_price": "3500", "reorder_level": 0, "active": "on"}
        data.update(extra)
        return ProductForm(data, instance=self.product, user=self.manager)

    def test_cashier_and_clerk_cannot_open_product_creation(self):
        for user in (self.cashier, self.clerk):
            self.client.force_login(user)
            self.assertEqual(self.client.get("/manage/products/new/").status_code, 403)

    def test_cashier_sees_products_but_not_costs(self):
        self.client.force_login(self.cashier)
        r = self.client.get("/manage/products/")
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "Avg cost")
        self.assertFalse(services.can_see_cost(self.cashier))
        self.assertFalse(services.can_see_cost(self.clerk))
        self.assertTrue(services.can_see_cost(self.manager))

    def test_manager_price_change_is_audited_with_old_and_new_value(self):
        from catalog.forms import ProductForm
        form = ProductForm({"name": self.product.name, "sku": self.product.sku, "base_unit": "bottle",
                            "buying_cost": "2400", "selling_price": "3800", "reorder_level": 0, "active": "on"},
                           instance=self.product, user=self.manager)
        self.assertTrue(form.is_valid(), form.errors)
        services.update_product(form, self.manager)
        entry = AuditLog.objects.get(action="product.price_changed")
        self.assertEqual(entry.previous_value["selling_price"], "3500.00")
        self.assertEqual(entry.new_value["selling_price"], "3800.00")
        self.assertEqual(entry.actor, self.manager)

    def test_manager_can_configure_capacity_and_change_is_audited(self):
        form = self.product_form(bottle_capacity_ml="750")
        self.assertTrue(form.is_valid(), form.errors)
        services.update_product(form, self.manager)
        self.product.refresh_from_db()
        self.assertEqual(self.product.bottle_capacity_ml, Decimal("750.00"))
        entry = AuditLog.objects.get(action="product.bottle_capacity_changed")
        self.assertEqual(entry.new_value["bottle_capacity_ml"], "750.00")

    def test_capacity_cannot_be_changed_while_sealed_stock_remains(self):
        form = self.product_form(bottle_capacity_ml="750")
        self.assertTrue(form.is_valid(), form.errors)
        services.update_product(form, self.manager)
        Product.objects.filter(pk=self.product.pk).update(stock_qty=10)

        changed = self.product_form(bottle_capacity_ml="1000")
        self.assertTrue(changed.is_valid(), changed.errors)
        with self.assertRaises(services.CatalogError):
            services.update_product(changed, self.manager)
        self.product.refresh_from_db()
        self.assertEqual(self.product.bottle_capacity_ml, Decimal("750.00"))

    def test_capacity_cannot_be_smaller_than_an_active_selling_unit(self):
        from catalog.models import SellingUnit

        SellingUnit.objects.create(product=self.product, name="Tot", volume_ml=Decimal("30"),
                                   selling_price=Decimal("150"))
        form = self.product_form(bottle_capacity_ml="25")
        self.assertFalse(form.is_valid())
        self.assertIn("bottle_capacity_ml", form.errors)

    def test_capacity_cannot_be_cleared_while_selling_units_are_active(self):
        from catalog.models import SellingUnit

        self.product.bottle_capacity_ml = Decimal("750")
        self.product.save(update_fields=["bottle_capacity_ml"])
        SellingUnit.objects.create(product=self.product, name="Tot", volume_ml=Decimal("30"),
                                   selling_price=Decimal("150"))
        form = self.product_form(bottle_capacity_ml="")
        self.assertFalse(form.is_valid())
        self.assertIn("bottle_capacity_ml", form.errors)

    def test_user_without_price_permission_cannot_even_submit_price(self):
        from accounts.models import Role, RolePermission
        role = Role.objects.create(name="Catalogue editor")
        RolePermission.objects.create(role=role, code="products.manage")
        editor = self.cashier2
        editor.role = role
        editor.save()
        from catalog.forms import ProductForm
        form = ProductForm({"name": "Renamed", "sku": self.product.sku, "base_unit": "bottle", "selling_price": "1",
                            "buying_cost": "1", "reorder_level": 0, "active": "on"}, instance=self.product, user=editor)
        self.assertNotIn("selling_price", form.fields)
        self.assertNotIn("buying_cost", form.fields)
        self.assertTrue(form.is_valid(), form.errors)
        services.update_product(form, editor)
        self.product.refresh_from_db()
        self.assertEqual(self.product.selling_price, Decimal("3500.00"))
        self.assertEqual(self.product.buying_cost, Decimal("2400.0000"))

    def test_stock_quantity_is_not_editable_through_the_form(self):
        from catalog.forms import ProductForm
        self.assertNotIn("stock_qty", ProductForm(user=self.manager).fields)


class ShelfTests(CatalogCase):
    def test_moves_are_recorded_and_unchanged_moves_are_ignored(self):
        a, b = Shelf.objects.create(code="a1", name="Whisky"), Shelf.objects.create(code="B2", name="Beer")
        self.assertEqual(a.code, "A1")
        self.assertEqual(services.move_products_to_shelf([self.product], a, self.manager), 1)
        self.assertEqual(services.move_products_to_shelf([self.product], a, self.manager), 0)
        self.assertEqual(services.move_products_to_shelf([self.product], b, self.manager), 1)
        self.product.refresh_from_db()
        self.assertEqual(self.product.shelf, b)
        self.assertEqual(ShelfTransfer.objects.count(), 2)
        t = ShelfTransfer.objects.first()
        self.assertEqual((t.from_shelf, t.to_shelf), (a, b))

    def test_cashier_cannot_move_products(self):
        shelf = Shelf.objects.create(code="C3", name="x")
        with self.assertRaises(services.CatalogError):
            services.move_products_to_shelf([self.product], shelf, self.cashier)

    def test_history_cannot_be_edited_or_deleted(self):
        shelf = Shelf.objects.create(code="D4", name="x")
        services.move_products_to_shelf([self.product], shelf, self.manager)
        t = ShelfTransfer.objects.get()
        with self.assertRaises(PermissionError):
            t.save()
        with self.assertRaises(PermissionError):
            t.delete()


class LabelTests(CatalogCase):
    def test_label_sheet_shows_name_price_code_and_barcode(self):
        services.generate_internal_code(self.product, self.manager)
        self.client.force_login(self.manager)
        r = self.client.get("/manage/labels/print/", {"codes": "JW-BLK", "price": "1", "barcode": "1", "code": "1", "copies": "2"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, self.product.name)
        self.assertContains(r, "KES 3500.00")
        self.assertContains(r, "<svg", count=2)
        self.assertEqual(r.content.decode().count('class="label"'), 2)
        self.assertTrue(AuditLog.objects.filter(action="labels.printed").exists())

    def test_whole_shelf_labels_and_logged_in_cashier_denied(self):
        shelf = Shelf.objects.create(code="E5", name="x")
        services.move_products_to_shelf([self.product], shelf, self.manager)
        self.client.force_login(self.manager)
        r = self.client.get("/manage/labels/print/", {"shelf": shelf.pk, "price": "1", "barcode": "1"})
        self.assertContains(r, self.product.name)
        self.client.force_login(self.cashier)
        self.assertEqual(self.client.get("/manage/labels/print/", {"shelf": shelf.pk}).status_code, 403)

    def test_unauthenticated_users_are_sent_to_login(self):
        r = self.client.get("/manage/products/")
        self.assertEqual(r.status_code, 302)
