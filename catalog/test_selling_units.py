"""Phase 4: configuring selling units (measures) - rules, validation, permissions and screens."""
from decimal import Decimal as D

from django.utils import timezone

from accounts.models import Role, RolePermission
from core.models import AuditLog
from inventory.models import Bottle

from . import services
from .models import SellingUnit
from .tests import CatalogCase, make_product


def make_role_user(testcase, username, *codes):
    """A user whose role holds exactly `codes`, so each permission can be tested on its own."""
    from accounts.tests import make_user

    role = Role.objects.create(name=f"role-{username}")
    for code in codes:
        RolePermission.objects.create(role=role, code=code)
    user = make_user(username)
    user.role = role
    user.save()
    return user


class SellingUnitCase(CatalogCase):
    def add(self, name="Tot", volume="30", price="150", **kw):
        return services.create_selling_unit(self.product, self.manager, name=name, volume_ml=D(volume),
                                            selling_price=D(price), **kw)

    def refused(self, fn, *args, **kwargs):
        with self.assertRaises(services.CatalogError) as ctx:
            fn(*args, **kwargs)
        return str(ctx.exception)

    def audit(self, action):
        return AuditLog.objects.filter(action=action)


class ConfigurationTests(SellingUnitCase):
    def test_the_standard_measures_can_all_be_configured(self):
        for name, volume, price in [("Bottle", "750", "3500"), ("Half Bottle", "375", "1800"), ("TOT", "30", "150"),
                                    ("Double TOT", "60", "280"), ("Custom Glass", "100", "450")]:
            self.add(name, volume, price)
        units = list(self.product.selling_units.all())
        self.assertEqual([u.volume_ml for u in units], [D("30"), D("60"), D("100"), D("375"), D("750")])
        self.assertTrue(all(u.active for u in units))

    def test_create_stores_values_and_writes_an_audit_entry(self):
        unit = self.add("Tot", "30", "150")
        unit.refresh_from_db()
        self.assertEqual((unit.name, unit.volume_ml, unit.selling_price, unit.active),
                         ("Tot", D("30.00"), D("150.00"), True))
        entry = self.audit("selling_unit.created").get()
        self.assertEqual(entry.actor, self.manager)
        self.assertEqual(entry.object_ref, str(self.product.pk))
        self.assertEqual(entry.new_value["volume_ml"], "30.00")

    def test_rename_changes_volume_and_price_and_audits_each_kind_of_change(self):
        unit = self.add()
        services.update_selling_unit(unit, self.manager, name="Single Tot", volume_ml=D("25"), selling_price=D("120"))
        unit.refresh_from_db()
        self.assertEqual((unit.name, unit.volume_ml, unit.selling_price), ("Single Tot", D("25.00"), D("120.00")))
        updated = self.audit("selling_unit.updated").get()
        self.assertEqual(updated.previous_value["name"], "Tot")
        self.assertEqual(updated.new_value["name"], "Single Tot")
        price = self.audit("selling_unit.price_changed").get()
        self.assertEqual((price.previous_value["selling_price"], price.new_value["selling_price"]), ("150.00", "120.00"))

    def test_price_only_change_is_audited_as_a_price_change_only(self):
        unit = self.add()
        services.update_selling_unit(unit, self.manager, selling_price=D("160"))
        self.assertEqual(self.audit("selling_unit.price_changed").count(), 1)
        self.assertEqual(self.audit("selling_unit.updated").count(), 0)

    def test_saving_without_changes_writes_nothing(self):
        unit = self.add()
        before = AuditLog.objects.count()
        services.update_selling_unit(unit, self.manager, name="Tot", volume_ml=D("30"), selling_price=D("150"))
        self.assertEqual(AuditLog.objects.count(), before)

    def test_disable_and_enable_are_audited(self):
        unit = self.add()
        services.set_selling_unit_active(unit, self.manager, False)
        unit.refresh_from_db()
        self.assertFalse(unit.active)
        self.assertEqual(self.audit("selling_unit.disabled").count(), 1)
        services.set_selling_unit_active(unit, self.manager, True)
        unit.refresh_from_db()
        self.assertTrue(unit.active)
        self.assertEqual(self.audit("selling_unit.enabled").count(), 1)

    def test_a_unit_can_be_created_switched_off(self):
        unit = self.add("Old Tot", "25", "100", active=False)
        self.assertFalse(unit.active)

    def test_units_of_different_products_are_independent(self):
        other = make_product("Vodka 750ml", "VOD-1")
        self.add("Tot", "30", "150")
        services.create_selling_unit(other, self.manager, name="Tot", volume_ml=D("30"), selling_price=D("100"))
        self.assertEqual(SellingUnit.objects.filter(name="Tot").count(), 2)


class ValidationTests(SellingUnitCase):
    def test_name_is_required_trimmed_and_limited(self):
        self.refused(self.add, name="   ")
        self.refused(self.add, name="x" * 41)
        self.assertEqual(self.add(name="  Double   Tot ", volume="60").name, "Double Tot")

    def test_duplicate_name_is_refused_ignoring_case(self):
        self.add("Tot", "30")
        self.assertIn("already has", self.refused(self.add, name="TOT", volume="60"))

    def test_volume_must_be_a_positive_number_with_two_decimals_at_most(self):
        for bad in ["0", "-30", "abc", "", "NaN", "Infinity", "30.555"]:
            self.refused(services.create_selling_unit, self.product, self.manager, name=f"Bad {bad}",
                         volume_ml=bad, selling_price=D("100"))
        self.assertEqual(SellingUnit.objects.count(), 0)

    def test_price_must_be_positive(self):
        for bad in ["0", "-1", "abc", "NaN", "10.005"]:
            self.refused(services.create_selling_unit, self.product, self.manager, name=f"Bad {bad}",
                         volume_ml=D("30"), selling_price=bad)
        self.assertEqual(SellingUnit.objects.count(), 0)

    def test_two_available_units_cannot_share_a_volume(self):
        self.add("Tot", "30")
        self.assertIn("already an active", self.refused(self.add, name="Shot", volume="30"))

    def test_a_switched_off_unit_does_not_block_its_volume(self):
        first = self.add("Tot", "30")
        services.set_selling_unit_active(first, self.manager, False)
        second = self.add("Shot", "30")
        self.assertTrue(second.active)

    def test_turning_a_unit_back_on_is_refused_if_its_volume_is_taken(self):
        first = self.add("Tot", "30")
        services.set_selling_unit_active(first, self.manager, False)
        self.add("Shot", "30")
        self.refused(services.set_selling_unit_active, first, self.manager, True)
        first.refresh_from_db()
        self.assertFalse(first.active)

    def test_editing_to_a_clashing_name_or_volume_is_refused_and_changes_nothing(self):
        self.add("Tot", "30")
        other = self.add("Double", "60")
        self.refused(services.update_selling_unit, other, self.manager, name="tot")
        self.refused(services.update_selling_unit, other, self.manager, volume_ml=D("30"))
        other.refresh_from_db()
        self.assertEqual((other.name, other.volume_ml), ("Double", D("60.00")))

    def test_a_unit_cannot_be_bigger_than_the_largest_bottle_on_record(self):
        Bottle.objects.create(product=self.product, capacity_ml=D("750"), remaining_ml=D("750"),
                              status=Bottle.OPENED, opened_at=timezone.now())
        self.add("Full", "750")
        self.assertIn("largest bottle", self.refused(self.add, name="Magnum", volume="1500"))

    def test_no_bottle_on_record_means_no_size_limit_is_applied(self):
        self.assertEqual(self.add("Magnum", "1500").volume_ml, D("1500.00"))

    def test_units_cannot_be_added_to_an_inactive_product(self):
        self.product.active = False
        self.product.save()
        self.refused(self.add)

    def test_failed_validation_leaves_no_audit_entry(self):
        self.refused(self.add, name="")
        self.assertEqual(self.audit("selling_unit.created").count(), 0)


class PermissionTests(SellingUnitCase):
    def test_cashier_supervisor_and_clerk_cannot_configure_units(self):
        unit = self.add()
        for user in (self.cashier, self.supervisor, self.clerk):
            self.refused(services.create_selling_unit, self.product, user, name="Shot", volume_ml=D("25"),
                         selling_price=D("100"))
            self.refused(services.update_selling_unit, unit, user, name="Renamed")
            self.refused(services.update_selling_unit, unit, user, selling_price=D("1"))
            self.refused(services.set_selling_unit_active, unit, user, False)
        unit.refresh_from_db()
        self.assertEqual((unit.name, unit.selling_price, unit.active), ("Tot", D("150.00"), True))
        self.assertEqual(SellingUnit.objects.count(), 1)

    def test_manage_without_price_permission_cannot_create_or_reprice(self):
        editor = make_role_user(self, "editor", "products.view", "products.manage")
        self.refused(services.create_selling_unit, self.product, editor, name="Shot", volume_ml=D("25"),
                     selling_price=D("100"))
        unit = self.add()
        self.refused(services.update_selling_unit, unit, editor, selling_price=D("999"))
        unit.refresh_from_db()
        self.assertEqual(unit.selling_price, D("150.00"))

    def test_manage_without_price_permission_can_rename_and_switch_off(self):
        editor = make_role_user(self, "editor", "products.view", "products.manage")
        unit = self.add()
        services.update_selling_unit(unit, editor, name="Single", volume_ml=D("30"), selling_price=D("150"))
        services.set_selling_unit_active(unit, editor, False)
        unit.refresh_from_db()
        self.assertEqual((unit.name, unit.selling_price, unit.active), ("Single", D("150.00"), False))

    def test_price_permission_alone_is_not_enough(self):
        pricer = make_role_user(self, "pricer", "products.view", "products.change_price")
        self.refused(services.create_selling_unit, self.product, pricer, name="Shot", volume_ml=D("25"),
                     selling_price=D("100"))
        self.refused(services.update_selling_unit, self.add(), pricer, selling_price=D("1"))

    def test_an_inactive_manager_account_cannot_configure_units(self):
        self.manager.is_active = False
        self.manager.save()
        self.refused(self.add)


class CashierSelectionTests(SellingUnitCase):
    def test_cashiers_are_only_offered_active_units_of_an_active_product(self):
        tot = self.add("Tot", "30")
        self.add("Double", "60")
        services.set_selling_unit_active(tot, self.manager, False)
        self.assertEqual([u.name for u in services.sellable_units(self.product)], ["Double"])
        self.product.active = False
        self.product.save()
        self.assertEqual(list(services.sellable_units(self.product)), [])

    def test_a_disabled_unit_cannot_be_resolved_for_a_sale(self):
        unit = self.add()
        self.assertEqual(services.get_sellable_unit(unit.pk), unit)
        services.set_selling_unit_active(unit, self.manager, False)
        self.refused(services.get_sellable_unit, unit.pk)
        self.refused(services.get_sellable_unit, 999999)

    def test_a_unit_of_an_inactive_product_cannot_be_resolved_for_a_sale(self):
        unit = self.add()
        self.product.active = False
        self.product.save()
        self.refused(services.get_sellable_unit, unit.pk)

    def test_json_list_shows_active_units_with_stored_prices_to_a_cashier(self):
        self.add("Tot", "30", "150")
        off = self.add("Shot", "25", "120")
        services.set_selling_unit_active(off, self.manager, False)
        self.client.force_login(self.cashier)
        r = self.client.get(f"/api/catalog/products/{self.product.pk}/selling-units/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual([(u["name"], u["volume_ml"], u["price"]) for u in r.json()["units"]],
                         [("Tot", "30.00", "150.00")])

    def test_json_list_needs_a_login_and_a_permission(self):
        self.add()
        url = f"/api/catalog/products/{self.product.pk}/selling-units/"
        self.assertIn(self.client.get(url).status_code, (401, 403))
        outsider = make_role_user(self, "outsider", "reports.sales.view")
        self.client.force_login(outsider)
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_json_list_is_read_only(self):
        self.client.force_login(self.cashier)
        r = self.client.post(f"/api/catalog/products/{self.product.pk}/selling-units/", {"price": "1"})
        self.assertEqual(r.status_code, 405)


class ScreenTests(SellingUnitCase):
    def url(self, suffix=""):
        return f"/manage/products/{self.product.pk}/selling-units/{suffix}"

    def test_manager_adds_edits_and_toggles_through_the_screens(self):
        self.client.force_login(self.manager)
        r = self.client.post(self.url("new/"), {"name": "Tot", "volume_ml": "30", "selling_price": "150", "active": "on"})
        self.assertEqual(r.status_code, 302)
        unit = SellingUnit.objects.get()
        self.assertEqual(self.client.get(self.url()).status_code, 200)
        self.assertContains(self.client.get(self.url()), "Tot")
        self.assertEqual(self.client.get(self.url(f"{unit.pk}/edit/")).status_code, 200)
        r = self.client.post(self.url(f"{unit.pk}/edit/"), {"name": "Single Tot", "volume_ml": "25", "selling_price": "140"})
        self.assertEqual(r.status_code, 302)
        unit.refresh_from_db()
        self.assertEqual((unit.name, unit.volume_ml, unit.selling_price), ("Single Tot", D("25.00"), D("140.00")))
        self.client.post(self.url(f"{unit.pk}/toggle/"))
        unit.refresh_from_db()
        self.assertFalse(unit.active)

    def test_an_invalid_edit_is_shown_again_with_the_reason_and_changes_nothing(self):
        unit = self.add()
        self.client.force_login(self.manager)
        r = self.client.post(self.url(f"{unit.pk}/edit/"), {"name": "Tot", "volume_ml": "-5", "selling_price": "150"})
        self.assertEqual(r.status_code, 200)
        unit.refresh_from_db()
        self.assertEqual(unit.volume_ml, D("30.00"))

    def test_service_errors_are_shown_on_the_edit_form(self):
        self.add("Tot", "30")
        other = self.add("Double", "60")
        self.client.force_login(self.manager)
        r = self.client.post(self.url(f"{other.pk}/edit/"), {"name": "tot", "volume_ml": "60", "selling_price": "280"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "already has")

    def test_cashier_cannot_open_or_post_to_any_configuration_screen(self):
        unit = self.add()
        self.client.force_login(self.cashier)
        self.assertEqual(self.client.get(self.url(f"{unit.pk}/edit/")).status_code, 403)
        self.assertEqual(self.client.post(self.url("new/"), {"name": "Shot", "volume_ml": "25",
                                                             "selling_price": "1"}).status_code, 403)
        self.assertEqual(self.client.post(self.url(f"{unit.pk}/edit/"), {"name": "x", "volume_ml": "30",
                                                                         "selling_price": "1"}).status_code, 403)
        self.assertEqual(self.client.post(self.url(f"{unit.pk}/toggle/")).status_code, 403)
        unit.refresh_from_db()
        self.assertEqual((unit.name, unit.selling_price, unit.active), ("Tot", D("150.00"), True))
        self.assertEqual(SellingUnit.objects.count(), 1)

    def test_cashier_can_see_the_list_but_gets_no_forms_or_buttons(self):
        self.add()
        self.client.force_login(self.cashier)
        r = self.client.get(self.url())
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "Add selling unit")
        self.assertNotContains(r, "Turn off")

    def test_anonymous_visitors_are_sent_to_sign_in(self):
        self.assertEqual(self.client.get(self.url()).status_code, 302)
        self.assertEqual(self.client.post(self.url("new/"), {}).status_code, 302)

    def test_the_price_field_is_not_offered_without_the_price_permission(self):
        editor = make_role_user(self, "editor", "products.view", "products.manage")
        unit = self.add()
        self.client.force_login(editor)
        r = self.client.get(self.url(f"{unit.pk}/edit/"))
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, 'name="selling_price"')
        self.client.post(self.url(f"{unit.pk}/edit/"), {"name": "Renamed", "volume_ml": "30", "selling_price": "1"})
        unit.refresh_from_db()
        self.assertEqual((unit.name, unit.selling_price), ("Renamed", D("150.00")))

    def test_a_forged_price_in_the_form_post_cannot_create_a_unit_without_permission(self):
        editor = make_role_user(self, "editor", "products.view", "products.manage")
        self.client.force_login(editor)
        self.client.post(self.url("new/"), {"name": "Shot", "volume_ml": "25", "selling_price": "1", "active": "on"})
        self.assertEqual(SellingUnit.objects.count(), 0)

    def test_a_unit_cannot_be_edited_through_another_products_url(self):
        other = make_product("Vodka 750ml", "VOD-1")
        unit = self.add()
        self.client.force_login(self.manager)
        r = self.client.get(f"/manage/products/{other.pk}/selling-units/{unit.pk}/edit/")
        self.assertEqual(r.status_code, 404)
