"""Reconciliation part 2: the relay (online server feed + shop pull), idempotency, conflicts, offline."""
import json
from datetime import timedelta
from decimal import Decimal as D
from unittest import mock

from django.test import Client, override_settings
from django.utils import timezone

from core.models import AuditLog

from . import mpesa_services as ms
from . import relay
from .models import MpesaStkRequest, MpesaTransaction, Sale
from .services import NotAllowed
from .test_phase4a import CFG, SECRET, stk_ok
from .tests import SalesCase

TOKEN = "relay-token-long-random"
RCFG = {**CFG, "RELAY_URL": "https://pos.example.com", "RELAY_TOKEN": TOKEN}


def make(code, amount="3500.00", name="", source="C2B", raw=None, age_hours=0):
    t = MpesaTransaction.objects.create(trans_id=code, source=source, amount=D(amount), customer_name=name, raw=raw or {})
    if age_hours:
        MpesaTransaction.objects.filter(pk=t.pk).update(created_at=timezone.now() - timedelta(hours=age_hours))
    return t


def row(code="RELAY00001", amount="3500.00", name="JOHN KAMAU", source="C2B", raw=None):
    return {"trans_id": code, "source": source, "amount": amount, "customer_name": name, "payer_ref": "f" * 64,
            "shortcode": "123456", "bill_ref": "", "trans_time": "20261107201500", "raw": raw or {}}


@override_settings(MPESA=RCFG)
class FeedTests(SalesCase):
    URL = f"/api/pay/relay/{SECRET}/"

    def get(self, query="", token=TOKEN, secret=SECRET):
        headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token is not None else {}
        return Client().get(f"/api/pay/relay/{secret}/{query}", **headers)

    def test_needs_the_secret_and_the_token(self):
        make("FEED000001")
        self.assertEqual(self.get(secret="wrong").status_code, 404)
        self.assertEqual(self.get(token="wrong").status_code, 404)
        self.assertEqual(self.get(token=None).status_code, 404)
        self.assertEqual(self.get().status_code, 200)

    @override_settings(MPESA={**RCFG, "RELAY_TOKEN": ""})
    def test_does_not_exist_unless_a_token_is_configured(self):
        self.assertEqual(self.get(token="").status_code, 404)
        self.assertEqual(Client().get(self.URL, HTTP_AUTHORIZATION="Bearer ").status_code, 404)

    def test_post_is_not_allowed(self):
        self.assertEqual(Client().post(self.URL, HTTP_AUTHORIZATION=f"Bearer {TOKEN}").status_code, 405)

    def test_lists_payments_oldest_first_and_is_not_cached(self):
        make("FEED000001", name="A B")
        make("FEED000002", "100.00")
        r = self.get()
        body = r.json()
        self.assertEqual([x["trans_id"] for x in body["rows"]], ["FEED000001", "FEED000002"])
        self.assertEqual((body["ok"], body["more"], body["rows"][0]["amount"], body["rows"][0]["customer_name"]),
                         (True, False, "3500.00", "A B"))
        self.assertIn("no-store", r["Cache-Control"])

    def test_paging(self):
        for i in range(5):
            make(f"FEEDPAGE0{i}")
        first = self.get("?limit=2").json()
        self.assertEqual((len(first["rows"]), first["more"]), (2, True))
        second = self.get(f"?limit=2&after={first['last_id']}").json()
        self.assertEqual((len(second["rows"]), second["more"]), (2, True))
        third = self.get(f"?limit=2&after={second['last_id']}").json()
        self.assertEqual((len(third["rows"]), third["more"]), (1, False))

    def test_only_recent_payments(self):
        make("FEEDOLD001", age_hours=100)
        make("FEEDNEW001")
        self.assertEqual([x["trans_id"] for x in self.get().json()["rows"]], ["FEEDNEW001"])
        self.assertEqual(len(self.get("?hours=200").json()["rows"]), 2)

    def test_an_unmatched_stk_payment_reaches_the_feed(self):
        Client().post(f"/api/pay/stk/{SECRET}/", json.dumps(stk_ok(checkout="shop-asked")), content_type="application/json")
        (r,) = self.get().json()["rows"]
        self.assertEqual((r["trans_id"], r["source"]), ("SGL1ABC23D", "STK"))
        self.assertIn("stkCallback", json.dumps(r["raw"]))


@override_settings(MPESA=RCFG)
class ApplyTests(SalesCase):
    def test_new_payment_is_stored_once_and_audited(self):
        self.assertEqual(relay.apply_row(row()), "new")
        self.assertEqual(relay.apply_row(row()), "known")
        t = MpesaTransaction.objects.get()
        self.assertEqual((t.status, t.amount, t.customer_name), (MpesaTransaction.UNMATCHED, D("3500.00"), "JOHN KAMAU"))
        self.assertEqual(AuditLog.objects.filter(action="mpesa.payment_relayed").count(), 1)

    def test_a_name_that_arrived_later_fills_a_blank_but_never_overwrites(self):
        make("RELAY00001", name="")
        self.assertEqual(relay.apply_row(row(name="JOHN KAMAU")), "known")
        self.assertEqual(MpesaTransaction.objects.get().customer_name, "JOHN KAMAU")
        relay.apply_row(row(name="SOMEONE ELSE"))
        self.assertEqual(MpesaTransaction.objects.get().customer_name, "JOHN KAMAU")

    def test_a_different_amount_for_a_known_code_is_a_conflict_and_changes_nothing(self):
        make("RELAY00001", "3500.00")
        self.assertEqual(relay.apply_row(row(amount="35.00")), "conflict")
        self.assertEqual(MpesaTransaction.objects.get().amount, D("3500.00"))
        self.assertEqual(AuditLog.objects.filter(action="mpesa.relay_conflict", object_ref="RELAY00001").count(), 1)

    def test_a_used_payment_is_never_reopened_by_the_relay(self):
        self.scan()
        txn = make("RELAY00001")
        ms.checkout_mpesa(self.cashier, self.shift, txn.pk)
        self.assertEqual(relay.apply_row(row()), "known")
        txn.refresh_from_db()
        self.assertEqual(txn.status, MpesaTransaction.MATCHED)

    def test_malformed_rows_raise_and_store_nothing(self):
        for bad in (row(code="x"), row(amount="0"), row(source="CARD"), "junk"):
            with self.assertRaises(relay.rules.MpesaError):
                relay.apply_row(bad)
        self.assertEqual(MpesaTransaction.objects.count(), 0)

    def test_stk_row_confirms_the_shops_own_pending_phone_request(self):
        self.scan()
        stk = MpesaStkRequest.objects.create(sale=Sale.objects.get(status="OPEN"), cashier=self.cashier, shift=self.shift,
                                             phone="254712345678", amount=D("3500"), checkout_request_id="ws_CO_1",
                                             status=MpesaStkRequest.PROCESSING)
        self.assertEqual(relay.apply_row(row(code="SGL1ABC23D", source="STK", name="", raw=stk_ok())), "new")
        stk.refresh_from_db()
        t = MpesaTransaction.objects.get()
        self.assertEqual((stk.status, stk.transaction_id, t.source), (MpesaStkRequest.CONFIRMED, t.pk, "STK"))

    def test_stk_row_that_disagrees_with_its_own_message_is_refused(self):
        with self.assertRaises(relay.rules.MpesaError):
            relay.apply_row(row(code="OTHERCODE01", source="STK", raw=stk_ok()))
        with self.assertRaises(relay.rules.MpesaError):
            relay.apply_row(row(code="SGL1ABC23D", source="STK", amount="100.00", raw=stk_ok()))
        self.assertEqual(MpesaTransaction.objects.count(), 0)

    def test_stk_row_arriving_after_the_till_message_adds_nothing_twice(self):
        relay.apply_row(row(code="SGL1ABC23D", source="C2B"))
        self.assertEqual(relay.apply_row(row(code="SGL1ABC23D", source="STK", name="", raw=stk_ok())), "known")
        self.assertEqual(MpesaTransaction.objects.count(), 1)


@override_settings(MPESA=RCFG)
class PullTests(SalesCase):
    def pages(self, *pages):
        """Replace the network call with prepared pages: each is (rows, last_id, more)."""
        it = iter(pages)
        calls = []

        def fake(after, limit, hours):
            calls.append((after, hours))
            rows, last, more = next(it)
            return {"ok": True, "rows": rows, "last_id": last, "more": more}
        return mock.patch.object(relay, "_fetch", fake), calls

    def test_pulls_every_page_and_counts(self):
        make("RELAY00002")
        patch, calls = self.pages(([row("RELAY00001"), row("RELAY00002")], 2, True), ([row("RELAY00003")], 3, False))
        with patch:
            totals = relay.pull()
        self.assertEqual(totals, {"new": 2, "known": 1, "conflict": 0, "bad": 0})
        self.assertEqual([c[0] for c in calls], [0, 2])
        self.assertEqual(MpesaTransaction.objects.count(), 3)

    def test_pulling_again_changes_nothing(self):
        for _ in range(2):
            patch, _ = self.pages(([row("RELAY00001")], 1, False))
            with patch:
                relay.pull()
        self.assertEqual(MpesaTransaction.objects.count(), 1)

    def test_one_bad_row_does_not_stop_the_good_ones(self):
        patch, _ = self.pages(([row("x"), row("RELAY00001")], 2, False))
        with patch:
            self.assertEqual(relay.pull(), {"new": 1, "known": 0, "conflict": 0, "bad": 1})

    def test_a_pulled_payment_can_be_used_at_the_till(self):
        self.scan()
        patch, _ = self.pages(([row("RELAY00001", "3500.00")], 1, False))
        with patch:
            relay.pull()
        sale = ms.checkout_mpesa(self.cashier, self.shift, MpesaTransaction.objects.get().pk)
        self.assertEqual((sale.payments.get().reference, sale.customer_name), ("RELAY00001", "JOHN KAMAU"))

    def test_offline_is_a_clean_error_and_nothing_is_lost(self):
        down = mock.patch.object(relay, "_fetch", side_effect=relay.RelayError("Cannot reach the online server right now (no internet?)."))
        with down:
            for _ in range(2):
                with self.assertRaises(relay.RelayError):
                    relay.pull()
        self.assertEqual(MpesaTransaction.objects.count(), 0)
        self.assertEqual(AuditLog.objects.filter(action="mpesa.relay_failed").count(), 1)      # not one line per attempt
        st = relay.status()
        self.assertEqual((st["enabled"], st["failing"]), (True, True))
        self.assertIn("no internet", st["error"])

    def test_a_garbage_reply_is_an_error(self):
        with mock.patch.object(relay, "_fetch", return_value={"ok": False}):
            with self.assertRaises(relay.RelayError):
                relay.pull()

    def test_success_after_failure_clears_the_warning(self):
        with mock.patch.object(relay, "_fetch", side_effect=relay.RelayError("down")):
            with self.assertRaises(relay.RelayError):
                relay.pull()
        patch, _ = self.pages(([], 0, False))
        with patch:
            relay.pull()
        st = relay.status()
        self.assertEqual((st["failing"], st["error"]), (False, ""))
        self.assertIsNotNone(st["last_ok"])

    @override_settings(MPESA={**CFG})
    def test_not_set_up_is_a_clear_error(self):
        self.assertFalse(relay.enabled())
        with self.assertRaises(relay.RelayError):
            relay.pull()

    def test_only_those_who_may_reconcile_can_pull_now(self):
        patch, _ = self.pages(([], 0, False))
        with patch:
            for user in (self.cashier, self.supervisor):
                with self.assertRaises(NotAllowed):
                    relay.pull_now(user)
            self.assertEqual(relay.pull_now(self.manager)["new"], 0)


@override_settings(MPESA=RCFG)
class ScreenTests(SalesCase):
    URL = "/manage/reconciliation/"

    def test_screen_shows_relay_state_and_pull_button(self):
        self.client.force_login(self.manager)
        page = self.client.get(self.URL).content.decode()
        self.assertIn("Online relay", page)
        self.assertIn("never pulled", page.lower())
        self.assertIn('value="pull"', page)

    def test_pull_button_stores_payments_and_says_so(self):
        self.client.force_login(self.manager)
        feed = {"ok": True, "rows": [row("RELAY00001")], "last_id": 1, "more": False}
        with mock.patch.object(relay, "_fetch", return_value=feed):
            r = self.client.post(self.URL, {"action": "pull"}, follow=True)
        self.assertEqual(MpesaTransaction.objects.count(), 1)
        self.assertIn("1 new", r.content.decode())

    def test_pull_button_reports_offline_without_a_crash(self):
        self.client.force_login(self.manager)
        with mock.patch.object(relay, "_fetch", side_effect=relay.RelayError("Cannot reach the online server right now (no internet?).")):
            r = self.client.post(self.URL, {"action": "pull"}, follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertIn("Cannot reach the online server", r.content.decode())

    def test_supervisor_does_not_get_the_button(self):
        self.client.force_login(self.supervisor)
        self.assertNotIn('value="pull"', self.client.get(self.URL).content.decode())

    @override_settings(MPESA={**CFG})
    def test_screen_says_when_the_relay_is_not_set_up(self):
        self.client.force_login(self.manager)
        page = self.client.get(self.URL).content.decode()
        self.assertIn("not set up", page)
        self.assertNotIn('value="pull"', page)
