"""Phase 3, second half: owner exemption, VAT, KRA eTIMS queue, the sales screen and manager screens."""
from decimal import Decimal as D
from unittest import mock

from django.test import override_settings

from accounts import services as auth_services
from accounts.tests import make_user
from catalog.models import Product
from core.models import AuditLog, ShopSettings
from tills.models import CashierShift, PosAuthorization

from . import etims, receipt, rules, services
from .models import EtimsSubmission, Sale
from .tests import SalesCase


class OwnerExemptionTests(SalesCase):
    """The shop owner (manager) is never stopped by a second-person approval, and everything is still audited."""

    def setUp(self):
        super().setUp()
        self.shift.closed_at = self.shift.opened_at
        self.shift.save()
        self.boss_shift = CashierShift.objects.create(till=self.till1, cashier=self.manager,
                                                      authorized_by=self.manager, opening_float=D("0"))

    def scan(self, qty=1):
        return services.add_item(self.manager, self.boss_shift, code="6001111111111", quantity=qty)

    def test_owner_removes_an_item_without_approval_and_it_is_audited(self):
        sale, line = self.scan(2)
        services.remove_item(self.manager, self.boss_shift, line.pk)
        self.assertEqual(sale.items.count(), 0)
        entry = AuditLog.objects.get(action="sale.item_removed")
        self.assertEqual((entry.actor, entry.approver), (self.manager, self.manager))

    def test_owner_lowers_quantity_voids_and_discounts_freely(self):
        sale, line = self.scan(4)
        services.set_quantity(self.manager, self.boss_shift, line.pk, 2)
        services.apply_discount(self.manager, self.boss_shift, line.pk, percent="40")   # far above the 5% limit
        line.refresh_from_db()
        self.assertEqual(line.discount, D("2800.00"))
        self.assertEqual(line.discount_approved_by, self.manager)
        voided = services.void_sale(self.manager, self.boss_shift)
        self.assertEqual((voided.status, voided.void_approved_by), (Sale.VOIDED, self.manager))

    def test_owner_changes_a_price_and_is_recorded_as_the_authority(self):
        sale, line = self.scan()
        services.change_price(self.manager, self.boss_shift, line.pk, "3000")
        line.refresh_from_db()
        self.assertEqual((line.unit_price, line.price_approved_by), (D("3000.00"), self.manager))

    def test_a_supervisor_is_not_exempt(self):
        sup_shift = self.boss_shift
        sup_shift.closed_at = sup_shift.opened_at
        sup_shift.save()
        shift = CashierShift.objects.create(till=self.till1, cashier=self.supervisor, authorized_by=self.manager,
                                            opening_float=D("0"))
        _, line = services.add_item(self.supervisor, shift, code="6001111111111")
        with self.assertRaises(services.ApprovalNeeded):
            services.remove_item(self.supervisor, shift, line.pk)

    def test_a_cashier_still_needs_approval(self):
        shift = CashierShift.objects.create(till=self.till2, cashier=self.cashier, authorized_by=self.supervisor,
                                            opening_float=D("0"))
        _, line = services.add_item(self.cashier, shift, code="6001111111111")
        with self.assertRaises(services.ApprovalNeeded) as ctx:
            services.remove_item(self.cashier, shift, line.pk)
        self.assertEqual(ctx.exception.action, "remove_item")

    def test_inactive_owner_is_not_exempt(self):
        self.manager.is_active = False
        self.assertFalse(auth_services.is_exempt(self.manager))

    def test_approval_endpoint_tells_the_owner_nothing_is_needed_and_the_cashier_it_is(self):
        self.client.force_login(self.manager)
        res = self.post("/api/approvals/request/", {"action": "void_sale"})
        self.assertEqual((res.status_code, res.json()["required"], res.json()["owner"]), (200, False, True))
        self.client.force_login(self.cashier)
        res = self.post("/api/approvals/request/", {"action": "void_sale"})
        self.assertEqual(res.status_code, 403)             # no approver named: refused

    def test_owner_receipt_does_not_say_authorized_by_themselves(self):
        self.scan()
        sale = services.checkout_cash(self.manager, self.boss_shift, "3500")
        text = "\n".join(receipt.render(receipt.get_completed(sale.pk))[1])
        self.assertIn("Cashier: boss", text)
        self.assertNotIn("Authorized by", text)


class OwnerOpensThePosTests(SalesCase):
    def setUp(self):
        super().setUp()
        self.shift.closed_at = self.shift.opened_at
        self.shift.save()

    def test_owner_signs_in_directly_and_opens_a_shift(self):
        res = self.post("/api/pos/cashier-login/", {"username": "boss", "secret": "Str0ng-pass!", "till": 1})
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()["authorized_by"], self.manager.display_name)
        res = self.post("/api/pos/shift/open/", {"opening_float": "2000"})
        self.assertEqual(res.status_code, 200, res.content)
        shift = CashierShift.objects.get(cashier=self.manager, closed_at__isnull=True)
        self.assertEqual(shift.authorized_by, self.manager)
        self.assertTrue(PosAuthorization.objects.filter(cashier=self.manager).exists())

    def test_a_cashier_still_cannot_skip_authorization(self):
        res = self.post("/api/pos/cashier-login/", {"username": "cash1", "secret": "1111", "till": 1})
        self.assertEqual(res.status_code, 403)

    def test_owner_may_authorize_and_then_be_the_cashier(self):
        self.assertEqual(self.post("/api/pos/authorize/", {"till": 1, "username": "boss", "secret": "Str0ng-pass!"}).status_code, 200)
        self.assertEqual(self.post("/api/pos/cashier-login/", {"username": "boss", "secret": "Str0ng-pass!", "till": 1}).status_code, 200)

    def test_a_supervisor_still_cannot_authorize_and_be_the_cashier(self):
        self.supervisor.role.grants.create(code="sales.make") if not self.supervisor.can("sales.make") else None
        self.assertEqual(self.post("/api/pos/authorize/", {"till": 1, "username": "sup", "secret": "4321"}).status_code, 200)
        self.assertEqual(self.post("/api/pos/cashier-login/", {"username": "sup", "secret": "4321", "till": 1}).status_code, 403)

    def test_owner_cannot_take_over_a_till_someone_else_has_open(self):
        CashierShift.objects.create(till=self.till1, cashier=self.cashier, authorized_by=self.supervisor, opening_float=D("0"))
        res = self.post("/api/pos/cashier-login/", {"username": "boss", "secret": "Str0ng-pass!", "till": 1})
        self.assertEqual(res.status_code, 409)


class VatTests(SalesCase):
    def setUp(self):
        super().setUp()
        self.shop = ShopSettings.load()

    def sell(self, qty=1, tendered="10000"):
        self.scan(qty=qty)
        return services.checkout_cash(self.cashier, self.shift, tendered)

    def test_not_registered_means_no_vat_anywhere(self):
        sale = self.sell()
        sale.refresh_from_db()
        self.assertEqual((sale.vat_applied, sale.tax_total), (False, D("0.00")))
        text = "\n".join(receipt.render(receipt.get_completed(sale.pk))[1])
        self.assertNotIn("VAT", text)
        self.assertIn("RECEIPT", text)

    def test_registered_shop_records_vat_inside_the_price(self):
        self.shop.vat_registered = True
        self.shop.kra_pin = "P051234567X"
        self.shop.save()
        sale = self.sell(2)
        sale.refresh_from_db()
        line = sale.items.get()
        self.assertEqual((sale.total, sale.tax_total), (D("7000.00"), D("965.52")))   # 7000 x 16/116
        self.assertEqual((line.tax_class, line.tax_rate, line.taxable_amount, line.tax_amount),
                         ("B", D("16.00"), D("6034.48"), D("965.52")))
        self.assertEqual(sale.total, line.taxable_amount + line.tax_amount)

    def test_receipt_shows_vat_summary_and_kra_pin(self):
        self.shop.vat_registered = True
        self.shop.kra_pin = "P051234567X"
        self.shop.save()
        sale = self.sell()
        text = "\n".join(receipt.render(receipt.get_completed(sale.pk))[1])
        for needle in ("PRICES INCLUDE VAT", "16%", "3,017.24", "482.76", "TOTAL VAT", "KRA PIN: P051234567X", "TOTAL PAYABLE"):
            self.assertIn(needle, text)
        self.assertNotIn("TAX INVOICE", text)             # only a KRA-signed sale may say that

    def test_exempt_and_standard_lines_split_correctly(self):
        self.shop.vat_registered = True
        self.shop.save()
        water = Product.objects.create(name="Mineral water", sku="WTR-1", selling_price=D("100"), tax_class="A")
        from catalog import services as cs
        cs.add_code(water, "6002222222222", self.manager)
        from inventory.tests import receive
        stock_services_submit = services.stock_services.submit_receiving
        stock_services_submit(receive(self.clerk, water, 5, "60").pk, self.clerk)
        self.scan()
        services.add_item(self.cashier, self.shift, code="6002222222222", quantity=1)
        sale = services.checkout_cash(self.cashier, self.shift, "5000")
        data = receipt.build_receipt(receipt.get_completed(sale.pk))
        rows = {r["class"]: r for r in data["vat"]["rows"]}
        self.assertEqual((rows["A"]["tax"], rows["A"]["taxable"]), ("0.00", "100.00"))
        self.assertEqual(rows["B"]["tax"], "482.76")
        self.assertEqual(data["vat"]["tax_total"], "482.76")

    def test_changing_the_setting_later_never_rewrites_an_old_sale(self):
        sale = self.sell()
        self.shop.vat_registered = True
        self.shop.save()
        text = "\n".join(receipt.render(receipt.get_completed(sale.pk))[1])
        self.assertNotIn("VAT", text)

    def test_customer_kra_pin_is_validated_and_printed(self):
        with self.assertRaises(services.SaleError):
            services.set_customer(self.cashier, self.shift, "Bar Ltd", pin="12345")
        services.set_customer(self.cashier, self.shift, "Bar Ltd", pin=" p051234567x ")
        sale = self.sell()
        text = "\n".join(receipt.render(receipt.get_completed(sale.pk))[1])
        self.assertIn("Customer PIN: P051234567X", text)

    def test_cart_reports_vat_included_for_the_screen(self):
        self.shop.vat_registered = True
        self.shop.save()
        self.scan()
        from .views import cart_payload
        self.assertEqual(cart_payload(services.active_sale(self.cashier, self.shift))["vat_included"], "482.76")


class FakeUnit:
    """Stands in for the KRA unit in tests only. Production always uses etims.HttpUnit."""

    def __init__(self, answer=None, error=None):
        self.answer, self.error, self.sent = answer, error, []

    def post(self, endpoint, payload):
        self.sent.append((endpoint, payload))
        if self.error:
            raise etims.EtimsError(self.error)
        return self.answer


GOOD = {"resultCd": "000", "resultMsg": "Successful", "data": {
    "rcptNo": 7, "totRcptNo": 7, "intrlData": "INTERNALDATA123", "rcptSign": "SIGN1234ABCD", "sdcId": "KRACU0100000001",
    "vsdcRcptPbctDate": "20261107120000"}}


@override_settings(ETIMS_MODE="sandbox", ETIMS_BASE_URL="http://127.0.0.1:8088", ETIMS_BRANCH_ID="00",
                   ETIMS_QR_URL_TEMPLATE="https://example.test/q?p={tin}&b={bhf}&s={sign}")
class EtimsTests(SalesCase):
    def setUp(self):
        super().setUp()
        shop = ShopSettings.load()
        shop.vat_registered, shop.kra_pin = True, "P051234567X"
        shop.save()
        Product.objects.filter(pk=self.product.pk).update(etims_item_class_code="50202300")
        self.unit = FakeUnit(GOOD)
        patcher = mock.patch.object(etims, "unit_factory", lambda: self.unit)
        patcher.start()
        self.addCleanup(patcher.stop)

    def sell(self):
        self.scan()
        return services.checkout_cash(self.cashier, self.shift, "3500")

    def test_every_sale_gets_a_gapless_whole_number_invoice_no(self):
        a = self.sell()
        b = self.sell()
        self.assertEqual([a.etims.invoice_no, b.etims.invoice_no], [1, 2])

    def test_signed_sale_stores_what_kra_returned_and_receipt_becomes_a_tax_invoice(self):
        sale = self.sell()
        sub = etims.submit(sale.pk)
        self.assertEqual((sub.status, sub.signature, sub.scu_receipt_no), ("SUBMITTED", "SIGN1234ABCD", "7"))
        self.assertEqual(sub.qr_text, "https://example.test/q?p=P051234567X&b=00&s=SIGN1234ABCD")
        text = "\n".join(receipt.render(receipt.get_completed(sale.pk))[1])
        for needle in ("TAX INVOICE", "SIGN1234ABCD", "KRACU0100000001", "INTERNALDATA123"):
            self.assertIn(needle, text)

    def test_payload_has_kra_fields_and_adds_up(self):
        sale = self.sell()
        etims.submit(sale.pk)
        endpoint, payload = self.unit.sent[0]
        self.assertEqual(endpoint, "saveTrnsSalesOsdc")
        self.assertEqual((payload["tin"], payload["invcNo"], payload["rcptTyCd"], payload["salesTyCd"]), ("P051234567X", 1, "S", "N"))
        self.assertEqual((payload["totAmt"], payload["totTaxAmt"], payload["totTaxblAmt"]), (3500.0, 482.76, 3017.24))
        self.assertEqual((payload["taxblAmtB"], payload["taxAmtB"], payload["taxRtB"]), (3017.24, 482.76, 16.0))
        self.assertEqual(payload["itemList"][0]["itemClsCd"], "50202300")

    def test_when_the_unit_is_unreachable_the_sale_stands_and_waits(self):
        self.unit.error = "Cannot reach the eTIMS unit (offline or not running)."
        sale = self.sell()
        sub = etims.submit(sale.pk)
        self.assertEqual((sale.status, sub.status, sub.attempts), (Sale.COMPLETED, EtimsSubmission.PENDING, 1))
        text = "\n".join(receipt.render(receipt.get_completed(sale.pk))[1])
        self.assertIn("awaiting KRA signature", text)
        self.assertNotIn("TAX INVOICE", text)
        self.assertNotIn("SIGN1234ABCD", text)
        self.unit.error = None                                  # internet is back
        self.assertEqual(etims.submit_pending(), 1)
        sub.refresh_from_db()
        self.assertEqual(sub.status, EtimsSubmission.SUBMITTED)

    def test_a_signed_invoice_is_never_sent_twice_or_changed(self):
        sale = self.sell()
        etims.submit(sale.pk)
        etims.submit(sale.pk)
        self.assertEqual(len(self.unit.sent), 1)
        sub = EtimsSubmission.objects.get(sale=sale)
        sub.signature = "forged"
        with self.assertRaises(PermissionError):
            sub.save()
        with self.assertRaises(PermissionError):
            sub.delete()

    def test_a_reply_without_a_signature_is_not_accepted_as_signed(self):
        self.unit.answer = {"resultCd": "000", "resultMsg": "Successful", "data": {"rcptNo": 1}}
        sale = self.sell()
        sub = etims.submit(sale.pk)
        self.assertEqual(sub.status, EtimsSubmission.REJECTED)
        self.assertEqual(sub.signature, "")

    def test_kra_rejection_is_recorded_and_not_retried_automatically(self):
        self.unit.answer = {"resultCd": "910", "resultMsg": "Invalid item", "data": None}
        sale = self.sell()
        sub = etims.submit(sale.pk)
        self.assertEqual(sub.status, EtimsSubmission.REJECTED)
        self.assertIn("910", sub.last_error)
        self.assertEqual(etims.submit_pending(), 0)
        self.assertEqual(len(self.unit.sent), 1)
        self.assertTrue(AuditLog.objects.filter(action="etims.rejected").exists())

    def test_missing_item_class_code_is_reported_not_sent(self):
        Product.objects.filter(pk=self.product.pk).update(etims_item_class_code="")
        sale = self.sell()
        sub = etims.submit(sale.pk)
        self.assertEqual(sub.status, EtimsSubmission.PENDING)
        self.assertIn("item class code", sub.last_error)
        self.assertEqual(self.unit.sent, [])

    def test_missing_kra_pin_is_reported_not_sent(self):
        ShopSettings.objects.filter(pk=1).update(kra_pin="")
        sale = self.sell()
        sub = etims.submit(sale.pk)
        self.assertIn("KRA PIN", sub.last_error)
        self.assertEqual(self.unit.sent, [])

    @override_settings(ETIMS_QR_URL_TEMPLATE="")
    def test_no_qr_is_made_up_when_no_template_is_configured(self):
        sale = self.sell()
        self.assertEqual(etims.submit(sale.pk).qr_text, "")

    @override_settings(ETIMS_MODE="off")
    def test_when_off_nothing_is_created_or_sent(self):
        sale = self.sell()
        self.assertFalse(EtimsSubmission.objects.exists())
        self.assertIsNone(etims.submit(sale.pk))
        text = "\n".join(receipt.render(receipt.get_completed(sale.pk))[1])
        self.assertNotIn("eTIMS", text)
        self.assertEqual(self.unit.sent, [])


class SalesScreenTests(SalesCase):
    def setUp(self):
        super().setUp()
        self.shift.closed_at = self.shift.opened_at
        self.shift.save()
        self.post("/api/pos/authorize/", {"till": 1, "username": "sup", "secret": "4321"})
        self.post("/api/pos/cashier-login/", {"username": "cash1", "secret": "1111", "till": 1})
        self.post("/api/pos/shift/open/", {"opening_float": "500"})

    def test_screen_needs_login_and_an_open_shift(self):
        self.client.logout()
        res = self.client.get("/pos/sell/")
        self.assertEqual((res.status_code, res["Location"]), (302, "/app/pos/"))

    def test_screen_opens_for_the_cashier_with_a_shift(self):
        res = self.client.get("/pos/sell/")
        self.assertEqual(res.status_code, 200)
        for needle in (b'id="scan"', b"Supervisor authorization", b"/api/sales/config/"):
            self.assertIn(needle, res.content)

    def test_config_gives_the_screen_its_shortcuts_and_who_is_who(self):
        data = self.client.get("/api/sales/config/").json()
        self.assertEqual((data["shortcuts"]["help"], data["shortcuts"]["complete"], data["owner"]), ("F1", "F9", False))
        self.assertEqual(data["authorized_by"], self.supervisor.display_name)
        self.assertTrue(data["payment_methods"][1]["enabled"])            # M-PESA: Phase 4A
        self.assertFalse(data["payment_methods"][2]["enabled"])           # card: next update, not faked

    def test_a_bad_saved_shortcut_setting_never_locks_the_till(self):
        ShopSettings.objects.filter(pk=1).update(pos_shortcuts={"print": "F2"})   # clashes with search
        data = self.client.get("/api/sales/config/").json()
        self.assertEqual(data["shortcuts"]["print"], "F10")

    def test_discount_over_the_limit_asks_for_approval_then_succeeds_with_a_token(self):
        item = self.post("/api/sales/cart/add/", {"code": "6001111111111"}).json()["cart"]["items"][0]
        res = self.post("/api/sales/cart/discount/", {"item_id": item["id"], "percent": "20"})
        self.assertEqual((res.status_code, res.json()["approval_required"]), (409, "exceptional_discount"))
        token = self.post("/api/approvals/request/", {"action": "exceptional_discount", "approver_username": "sup", "secret": "4321",
                                                      "reason": "Regular customer", "object_ref": res.json()["object_ref"]}).json()["token"]
        res = self.post("/api/sales/cart/discount/", {"item_id": item["id"], "percent": "20", "approval_token": token})
        self.assertEqual((res.status_code, res.json()["cart"]["total"]), (200, "2800.00"))

    def test_owner_gets_no_approval_prompt_on_the_same_screen_calls(self):
        self.client.logout()
        self.post("/api/pos/cashier-login/", {"username": "boss", "secret": "Str0ng-pass!", "till": 1})
        # the supervisor's shift on till 1 is open in setUp, so use the owner's own till
        self.client.logout()
        CashierShift.objects.filter(closed_at__isnull=True).update(closed_at=self.shift.opened_at)
        self.assertEqual(self.post("/api/pos/cashier-login/", {"username": "boss", "secret": "Str0ng-pass!", "till": 1}).status_code, 200)
        self.post("/api/pos/shift/open/", {"opening_float": "0"})
        item = self.post("/api/sales/cart/add/", {"code": "6001111111111", "quantity": 2}).json()["cart"]["items"][0]
        self.assertTrue(self.client.get("/api/sales/config/").json()["owner"])
        res = self.post("/api/sales/cart/remove/", {"item_id": item["id"]})
        self.assertEqual((res.status_code, res.json()["cart"]["items"]), (200, []))

    def test_my_sales_lists_only_this_cashiers_completed_sales(self):
        self.post("/api/sales/cart/add/", {"code": "6001111111111"})
        self.post("/api/sales/checkout/cash/", {"tendered": "3500"})
        rows = self.client.get("/api/sales/mine/").json()["sales"]
        self.assertEqual((len(rows), rows[0]["total"], rows[0]["printed"]), (1, "3500.00", False))

    def test_checkout_response_reports_etims_state_when_on(self):
        self.post("/api/sales/cart/add/", {"code": "6001111111111"})
        res = self.post("/api/sales/checkout/cash/", {"tendered": "3500"}).json()
        self.assertIsNone(res["etims"])                                    # off by default


class ManagerScreenTests(SalesCase):
    def test_supervisor_sees_tills_and_can_reprint_as_a_marked_duplicate(self):
        self.scan()
        sale = services.checkout_cash(self.cashier, self.shift, "3500")
        self.client.force_login(self.supervisor)
        page = self.client.get("/manage/tills/")
        self.assertEqual(page.status_code, 200)
        self.assertIn(sale.number.encode(), page.content)
        res = self.client.post(f"/manage/sales/{sale.pk}/reprint/")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"DUPLICATE RECEIPT", res.content)
        sale.refresh_from_db()
        self.assertEqual(sale.reprint_count, 1)

    def test_cashier_cannot_open_the_manager_screens(self):
        self.client.force_login(self.cashier)
        self.assertEqual(self.client.get("/manage/tills/").status_code, 403)
        self.assertEqual(self.client.post("/manage/sales/1/reprint/").status_code, 403)

    def test_reprint_needs_post(self):
        self.client.force_login(self.supervisor)
        self.assertEqual(self.client.get("/manage/sales/1/reprint/").status_code, 405)
