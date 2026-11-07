"""Phase 3, second half: the sales screen pages, the approval hand-shake the screen relies on, and receipts as the screen uses them."""
import json
import re
from decimal import Decimal as D
from pathlib import Path

from django.test import Client
from django.urls import resolve

from core.models import ShopSettings
from tills.models import CashierShift

from . import rules
from .models import Sale
from .tests import SalesCase

STATIC = Path(__file__).parent / "static" / "sales"
TEMPLATES = Path(__file__).parent / "templates" / "sales"


def post(client, url, payload=None):
    return client.post(url, data=json.dumps(payload or {}), content_type="application/json")


class ScreenCase(SalesCase):
    def setUp(self):
        super().setUp()
        self.c = Client()
        self.c.force_login(self.cashier)
        session = self.c.session
        session["pos_till"] = 1
        session.save()

    def other_client(self, user, till=1):
        client = Client()
        client.force_login(user)
        session = client.session
        session["pos_till"] = till
        session.save()
        return client


class SellPageTests(ScreenCase):
    def test_needs_login_cashier_permission_and_open_shift(self):
        self.assertEqual(Client().get("/pos/sell/").status_code, 302)
        clerk = self.other_client(self.clerk)
        self.assertEqual(clerk.get("/pos/sell/").status_code, 302)
        CashierShift.objects.filter(pk=self.shift.pk).update(closed_at=self.shift.opened_at)
        self.assertEqual(self.c.get("/pos/sell/").status_code, 302)

    def test_page_shows_who_is_selling_and_the_keyboard_guide(self):
        html = self.c.get("/pos/sell/").content.decode()
        for text in ("Authorized by", self.supervisor.display_name, self.cashier.display_name, 'id="scan"',
                     "F9", "Complete the sale", "Supervisor authorization", "Shift summary"):
            self.assertIn(text, html)

    def test_manager_can_change_a_shortcut_and_a_clash_falls_back_to_defaults(self):
        ShopSettings.objects.filter(pk=1).update(pos_shortcuts={"hold": "Alt+H"})
        self.assertIn("Alt+H", self.c.get("/pos/sell/").content.decode())
        ShopSettings.objects.filter(pk=1).update(pos_shortcuts={"hold": "F9"})       # clashes with Complete
        html = self.c.get("/pos/sell/").content.decode()
        self.assertIn('"hold": "F5"', html)

    def test_settings_validation_rejects_clashes_and_reserved_keys(self):
        from django.core.exceptions import ValidationError
        shop = ShopSettings.load()
        for bad in ({"hold": "F9"}, {"hold": "Ctrl+W"}, {"hold": "A"}, {"nonsense": "F2"}, {"hold": "Escape"}):
            shop.pos_shortcuts = bad
            with self.assertRaises(ValidationError, msg=bad):
                shop.full_clean()
        shop.pos_shortcuts = {"hold": "alt+h"}
        shop.full_clean()

    def test_pos_login_hands_a_ready_cashier_to_the_sales_screen(self):
        self.assertIn("/pos/sell/", self.c.get("/app/pos/").content.decode())

    def test_every_element_and_endpoint_the_script_uses_exists(self):
        js = (STATIC / "pos.js").read_text()
        html = self.c.get("/pos/sell/").content.decode()
        for ident in set(re.findall(r'\$\("([\w-]+)"\)', js)):
            self.assertIn(f'id="{ident}"', html, f"pos.js uses #{ident} but sell.html has no such element")
        paths = set(re.findall(r'(?:act\(|API \+ )"([a-z/]+/)"(?!\s*\+)', js))
        self.assertTrue(paths)
        for path in paths:
            resolve("/api/sales/" + path)           # raises Resolver404 if the screen calls an endpoint that does not exist
        for static in ("pos.js", "pos.css", "print.js"):
            self.assertTrue((STATIC / static).exists())


class ApprovalHandshakeTests(ScreenCase):
    """What the screen does when a supervisor must approve: ask, get a token, send the same request again."""

    def test_refusal_tells_the_screen_what_to_ask_for_and_the_retry_works(self):
        post(self.c, "/api/sales/cart/add/", {"code": "6001111111111", "quantity": 2})
        cart = self.c.get("/api/sales/cart/").json()["cart"]
        item = cart["items"][0]
        res = post(self.c, "/api/sales/cart/remove/", {"item_id": item["id"], "reason": "Removed at the till"})
        body = res.json()
        self.assertFalse(body["ok"])
        need = body["approval_required"]
        self.assertEqual((need["action"], need["object_ref"]), ("remove_item", item["ref"]))
        self.assertEqual(need["label"], "Remove scanned item from sale")
        self.assertEqual(len(self.c.get("/api/sales/cart/").json()["cart"]["items"]), 1)    # nothing was removed

        ask = post(self.c, "/api/approvals/request/", {"action": need["action"], "approver_username": "sup",
                                                       "secret": "4321", "reason": "Wrong item", "object_ref": need["object_ref"]})
        self.assertTrue(ask.json()["ok"])
        res = post(self.c, "/api/sales/cart/remove/", {"item_id": item["id"], "approval_token": ask.json()["token"],
                                                       "reason": "Wrong item"})
        self.assertTrue(res.json()["ok"])
        self.assertEqual(res.json()["cart"]["items"], [])

    def test_wrong_pin_or_token_is_a_plain_error_not_another_approval_request(self):
        post(self.c, "/api/sales/cart/add/", {"code": "6001111111111"})
        item = self.c.get("/api/sales/cart/").json()["cart"]["items"][0]
        res = post(self.c, "/api/sales/cart/remove/", {"item_id": item["id"], "approval_token": "forged", "reason": "x"})
        self.assertFalse(res.json()["ok"])
        self.assertNotIn("approval_required", res.json())

    def test_discount_price_and_void_ask_for_the_right_approval(self):
        post(self.c, "/api/sales/cart/add/", {"code": "6001111111111"})
        cart = self.c.get("/api/sales/cart/").json()["cart"]
        item = cart["items"][0]
        got = post(self.c, "/api/sales/cart/discount/", {"item_id": item["id"], "percent": "10"}).json()
        self.assertEqual(got["approval_required"]["action"], "exceptional_discount")
        got = post(self.c, "/api/sales/cart/price/", {"item_id": item["id"], "price": "3000"}).json()
        self.assertEqual(got["approval_required"]["action"], "price_change")
        got = post(self.c, "/api/sales/cart/void/", {"reason": "Customer left"}).json()
        self.assertEqual((got["approval_required"]["action"], got["approval_required"]["object_ref"]),
                         ("void_sale", cart["ref"]))


class ReceiptAsTheScreenUsesItTests(ScreenCase):
    def sell(self, tendered="8000"):
        post(self.c, "/api/sales/cart/add/", {"code": "6001111111111", "quantity": 2})
        return post(self.c, "/api/sales/checkout/cash/", {"tendered": tendered}).json()

    def test_checkout_answer_has_what_the_done_box_shows(self):
        done = self.sell()
        self.assertTrue(done["ok"])
        self.assertEqual((done["total"], done["change"]), ("7000.00", "1000.00"))
        self.assertTrue(done["number"].startswith("T1-"))
        self.assertIsNone(done["cart"]["id"])

    def test_original_prints_once_then_only_a_supervisor_can_reprint_and_it_is_marked(self):
        done = self.sell()
        first = self.c.get(f"/api/sales/receipt/{done['sale_id']}/").json()
        self.assertTrue(first["ok"])
        text = "\n".join(first["lines"])
        for expected in (done["number"], self.cashier.display_name, "Authorized by: " + self.supervisor.display_name,
                         "7,000.00", "Change", "1,000.00"):
            self.assertIn(expected, text)
        self.assertNotIn("DUPLICATE", text)
        self.assertNotIn("2,400", text)                                   # buying cost is never printed
        again = self.c.get(f"/api/sales/receipt/{done['sale_id']}/").json()
        self.assertFalse(again["ok"])
        self.assertIn("already printed", again["error"])
        self.assertEqual(post(self.c, f"/api/sales/receipt/{done['sale_id']}/reprint/").status_code, 403)
        CashierShift.objects.create(till=self.till2, cashier=self.supervisor, authorized_by=self.manager)
        sup = self.other_client(self.supervisor, till=2)
        dup = post(sup, f"/api/sales/receipt/{done['sale_id']}/reprint/").json()
        self.assertTrue(dup["ok"])
        self.assertIn("*** DUPLICATE RECEIPT ***", "\n".join(dup["lines"]))
        self.assertEqual(Sale.objects.get(pk=done["sale_id"]).reprint_count, 1)

    def test_paper_width_changes_the_line_length_the_printer_gets(self):
        from core.models import ReceiptSettings
        ReceiptSettings.objects.filter(pk=1).update(paper_width_mm=58)
        done = self.sell()
        res = self.c.get(f"/api/sales/receipt/{done['sale_id']}/").json()
        self.assertEqual(res["paper_mm"], 58)
        self.assertTrue(all(len(line) <= 32 for line in res["lines"]))

    def test_held_sale_round_trip_through_the_endpoints_the_screen_calls(self):
        post(self.c, "/api/sales/cart/add/", {"code": "6001111111111", "quantity": 2})
        post(self.c, "/api/sales/cart/customer/", {"name": "Mr Otieno"})
        self.assertTrue(post(self.c, "/api/sales/cart/hold/", {"label": "Otieno"}).json()["ok"])
        self.assertIsNone(self.c.get("/api/sales/cart/").json()["cart"]["id"])
        held = self.c.get("/api/sales/held/").json()["held"]
        self.assertEqual((held[0]["label"], held[0]["items"], held[0]["total"]), ("Otieno", 2, "7000.00"))
        back = post(self.c, "/api/sales/held/resume/", {"sale_id": held[0]["id"]}).json()
        self.assertEqual((back["cart"]["count"], back["cart"]["customer"]), (2, "Mr Otieno"))

    def test_shift_summary_fields_the_screen_shows(self):
        self.sell()
        s = self.c.get("/api/sales/shift/summary/").json()["shift"]
        self.assertEqual((s["sales_count"], s["sales_total"], s["cash_sales"], s["opening_float"], s["cash_in_drawer_expected"]),
                         (1, "7000.00", "7000.00", "5000.00", "12000.00"))


class ManagerScreenTests(ScreenCase):
    def make_sale(self):
        post(self.c, "/api/sales/cart/add/", {"code": "6001111111111"})
        return post(self.c, "/api/sales/cart/customer/", {"name": "Zawadi"}) and post(
            self.c, "/api/sales/checkout/cash/", {"tendered": "3500"}).json()

    def test_pages_are_permission_gated(self):
        done = self.make_sale()
        urls = ["/manage/sales/", "/manage/tills/", f"/manage/sales/{done['sale_id']}/"]
        for url in urls:
            self.assertEqual(self.c.get(url).status_code, 403, url)          # a cashier may not browse every sale
            mgr = self.other_client(self.manager)
            self.assertEqual(mgr.get(url).status_code, 200, url)

    def test_sales_search_detail_and_tills_content(self):
        done = self.make_sale()
        mgr = self.other_client(self.manager)
        self.assertIn(done["number"], mgr.get("/manage/sales/?q=Zawadi").content.decode())
        self.assertNotIn(done["number"], mgr.get("/manage/sales/?q=Nobody").content.decode())
        detail = mgr.get(f"/manage/sales/{done['sale_id']}/").content.decode()
        for text in (done["number"], "Johnnie Walker Black 750ml", "3500.00", "Reprint receipt"):
            self.assertIn(text, detail)
        tills = mgr.get("/manage/tills/").content.decode()
        self.assertIn(self.cashier.display_name, tills)
        self.assertIn(self.supervisor.display_name, tills)

    def test_open_carts_are_not_listed_as_sales(self):
        post(self.c, "/api/sales/cart/add/", {"code": "6001111111111"})
        mgr = self.other_client(self.manager)
        self.assertNotIn("In progress", mgr.get("/manage/sales/").content.decode())
        pk = Sale.objects.get().pk
        self.assertEqual(mgr.get(f"/manage/sales/{pk}/").status_code, 404)
