"""Find a waiting M-PESA payment by its code, and 'Get latest payments' on the till screen.

The rule that never changes (see mpesa_services): typing a code never creates or confirms a payment. It only picks
one Safaricom already confirmed, which is what makes several waiting payments of the same amount easy to tell apart."""
import json
from decimal import Decimal as D
from unittest import mock

from django.core.cache import cache
from django.test import override_settings

from . import mpesa_services as ms
from . import relay
from .models import MpesaTransaction, Payment
from .services import NotAllowed, SaleError
from .test_phase4a import CFG
from .test_relay import RCFG, make, row
from .tests import SalesCase


class FindByCodeTests(SalesCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        # three customers paid exactly the same amount, minutes apart
        self.a = make("SGL1AAA111", "3500.00", name="MARY")
        self.b = make("SGL1BBB222", "3500.00", name="JOHN")
        self.c = make("SGL1CCC333", "3500.00")

    def codes(self, raw):
        return [t.trans_id for t in ms.find_by_code(self.cashier, raw)]

    def test_full_code_picks_exactly_that_payment_among_same_amounts(self):
        self.assertEqual(self.codes("SGL1BBB222"), ["SGL1BBB222"])

    def test_last_characters_are_enough(self):
        self.assertEqual(self.codes("BBB222"), ["SGL1BBB222"])
        self.assertEqual(self.codes("2222"), [])             # only the real ending matches, not a part from the middle
        self.assertEqual(self.codes("B222"), ["SGL1BBB222"])

    def test_case_spaces_and_dashes_are_ignored(self):
        self.assertEqual(self.codes(" sgl1-bbb 222 "), ["SGL1BBB222"])

    def test_too_short_is_refused(self):
        with self.assertRaises(SaleError):
            ms.find_by_code(self.cashier, "B22")
        with self.assertRaises(SaleError):
            ms.find_by_code(self.cashier, "  ")

    def test_wildcard_characters_do_not_match_everything(self):
        for raw in ("%%%%", "_%_%", "____", "%%%%%%%%"):     # everything except letters and digits is dropped, leaving too little
            with self.assertRaises(SaleError):
                ms.find_by_code(self.cashier, raw)
        self.assertEqual(self.codes("BB%B2_22"), ["SGL1BBB222"])   # symbols are removed, never treated as wildcards

    def test_an_unknown_code_finds_nothing_and_creates_nothing(self):
        before = MpesaTransaction.objects.count()
        self.assertEqual(self.codes("ZZZZ9999"), [])
        self.assertEqual(MpesaTransaction.objects.count(), before)

    def test_only_people_who_may_accept_payments_can_search(self):
        with self.assertRaises(NotAllowed):
            ms.find_by_code(self.clerk, "BBB222")

    def test_found_payment_is_older_than_the_waiting_list_window(self):
        old = make("SGL1OLD999", "200.00", age_hours=30)
        self.assertNotIn(old.trans_id, [t.trans_id for t in ms.recent_unmatched(self.cashier, self.shift)])   # off the 90-minute list
        self.assertEqual(self.codes("OLD999"), ["SGL1OLD999"])

    def test_can_be_found_by_code_and_used_for_the_sale(self):
        self.scan()                                          # sale total 3500
        found = ms.find_by_code(self.cashier, "CCC333")[0]
        done = ms.checkout_mpesa(self.cashier, self.shift, found.pk)
        self.assertEqual(done.payments.get().reference, "SGL1CCC333")
        found.refresh_from_db()
        self.assertEqual(found.status, MpesaTransaction.MATCHED)


class FindEndpointTests(SalesCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.client.force_login(self.cashier)
        session = self.client.session
        session["pos_till"] = 1
        session.save()

    def get(self, code):
        return self.client.get("/api/sales/mpesa/find/", {"code": code})

    def post(self, url, payload):
        return self.client.post(url, data=json.dumps(payload), content_type="application/json")

    def test_returns_the_matching_waiting_payment_with_its_details(self):
        self.scan()
        make("SGL1BBB222", "3500.00", name="JOHN KAMAU")
        make("SGL1CCC333", "3500.00")
        data = self.get("bbb222").json()
        self.assertTrue(data["ok"])
        self.assertEqual([p["receipt"] for p in data["payments"]], ["SGL1BBB222"])
        self.assertEqual(data["payments"][0]["name"], "JOHN KAMAU")
        self.assertTrue(data["payments"][0]["matches"])      # equals the sale total

    def test_tells_why_a_payment_cannot_be_used(self):
        self.scan()
        used = make("SGL1USED001", "3500.00")
        ms.checkout_mpesa(self.cashier, self.shift, used.pk)
        aside = make("SGL1ASID002", "100.00")
        MpesaTransaction.objects.filter(pk=aside.pk).update(status=MpesaTransaction.IGNORED)
        data = self.get("USED001").json()
        self.assertEqual((data["payments"], data["used"][0]["receipt"]), ([], "SGL1USED001"))
        self.assertTrue(data["used"][0]["sale"])
        self.assertEqual(self.get("ASID002").json()["set_aside"][0]["receipt"], "SGL1ASID002")

    def test_short_code_is_a_clear_error(self):
        res = self.get("AB")
        self.assertEqual(res.status_code, 400)
        self.assertIn("at least 4", res.json()["error"])

    def test_someone_without_an_open_shift_on_this_till_is_refused(self):
        self.client.force_login(self.clerk)
        res = self.get("BBB222")
        self.assertIn(res.status_code, (400, 403))
        self.assertFalse(res.json()["ok"])

    @override_settings(MPESA=CFG)
    def test_not_found_and_no_server_connection_is_explained_not_faked(self):
        data = self.get("NOPE1234").json()
        self.assertEqual((data["ok"], data["payments"], data["relay"]), (True, [], False))
        self.assertFalse(MpesaTransaction.objects.filter(trans_id__endswith="NOPE1234").exists())

    @override_settings(MPESA=RCFG)
    def test_a_payment_this_pc_does_not_hold_yet_is_fetched_from_the_online_server_then_found(self):
        feed = {"ok": True, "rows": [row("SGL1LATE777", "3500.00", "ANN WANJIRU")], "last_id": 5, "more": False}
        with mock.patch.object(relay, "_fetch", return_value=feed) as fetched:
            data = self.get("LATE777").json()
        self.assertTrue(fetched.called)
        self.assertEqual([p["receipt"] for p in data["payments"]], ["SGL1LATE777"])
        self.assertEqual(MpesaTransaction.objects.get(trans_id="SGL1LATE777").amount, D("3500.00"))

    @override_settings(MPESA=RCFG)
    def test_server_trouble_is_reported_while_still_searching_what_we_hold(self):
        make("SGL1HELD555", "50.00")
        with mock.patch.object(relay, "_fetch", side_effect=relay.RelayError("Cannot reach the online server right now (no internet?).")):
            nothing = self.get("MISS0000").json()
            held = self.get("HELD555").json()
        self.assertIn("Cannot reach", nothing["pull_error"])
        self.assertEqual(held["payments"][0]["receipt"], "SGL1HELD555")
        self.assertEqual(held["pull_error"], "")             # found locally: the server was not even asked

    @override_settings(MPESA=RCFG)
    def test_a_wrong_amount_from_the_server_never_overwrites_what_we_hold(self):
        make("SGL1SAME888", "3500.00")
        feed = {"ok": True, "rows": [row("SGL1SAME888", "9999.00")], "last_id": 1, "more": False}
        with mock.patch.object(relay, "_fetch", return_value=feed):
            relay.pull_for_till(self.cashier, throttle=False)
        self.assertEqual(MpesaTransaction.objects.get(trans_id="SGL1SAME888").amount, D("3500.00"))


class PullEndpointTests(SalesCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.client.force_login(self.cashier)
        session = self.client.session
        session["pos_till"] = 1
        session.save()

    def pull(self, manual=False):
        return self.client.post("/api/sales/mpesa/pull/", data=json.dumps({"manual": manual}), content_type="application/json")

    @override_settings(MPESA=CFG)
    def test_not_connected_gives_a_plain_message(self):
        data = self.pull().json()
        self.assertFalse(data["ok"])
        self.assertIn("not connected to the online server", data["error"])

    @override_settings(MPESA=RCFG)
    def test_brings_in_new_payments_and_reports_the_count(self):
        feed = {"ok": True, "rows": [row("SGL1NEW0001"), row("SGL1NEW0002", "100.00")], "last_id": 2, "more": False}
        with mock.patch.object(relay, "_fetch", return_value=feed):
            data = self.pull(manual=True).json()
        self.assertEqual((data["ok"], data["checked"], data["new"]), (True, True, 2))
        self.assertEqual(MpesaTransaction.objects.filter(trans_id__startswith="SGL1NEW").count(), 2)

    @override_settings(MPESA=RCFG)
    def test_pulling_twice_adds_nothing_new(self):
        feed = {"ok": True, "rows": [row("SGL1TWICE01")], "last_id": 1, "more": False}
        with mock.patch.object(relay, "_fetch", return_value=feed):
            first = self.pull(manual=True).json()
            second = self.pull(manual=True).json()
        self.assertEqual((first["new"], second["new"]), (1, 0))
        self.assertEqual(MpesaTransaction.objects.filter(trans_id="SGL1TWICE01").count(), 1)

    @override_settings(MPESA=RCFG)
    def test_the_automatic_check_is_throttled_but_the_button_is_not(self):
        feed = {"ok": True, "rows": [], "last_id": 0, "more": False}
        with mock.patch.object(relay, "_fetch", return_value=feed) as fetched:
            self.assertTrue(self.pull().json()["checked"])
            self.assertFalse(self.pull().json()["checked"])  # asked a moment ago
            self.assertEqual(fetched.call_count, 1)
            self.assertTrue(self.pull(manual=True).json()["checked"])
            self.assertEqual(fetched.call_count, 2)

    @override_settings(MPESA=RCFG)
    def test_server_trouble_is_shown_not_hidden(self):
        with mock.patch.object(relay, "_fetch", side_effect=relay.RelayError("The online server refused the request.")):
            data = self.pull(manual=True).json()
        self.assertEqual((data["ok"], data["error"]), (False, "The online server refused the request."))

    @override_settings(MPESA=RCFG)
    def test_someone_without_an_open_shift_on_this_till_is_refused(self):
        self.client.force_login(self.clerk)
        res = self.pull()
        self.assertIn(res.status_code, (400, 403))
        self.assertFalse(res.json()["ok"])

    def test_the_till_screen_has_the_new_controls(self):
        page = self.client.get("/pos/sell/").content.decode()
        for marker in ('id=\\"mp-pull\\"', "id=\"mp-code\"", "mpesa/find/", "mpesa/pull/", "Get latest payments"):
            self.assertTrue(marker in page or marker.replace('\\"', '"') in page, marker)
