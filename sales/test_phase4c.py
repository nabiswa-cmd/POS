"""Phase 4C: callback protection (address allow-list), the offline relay, database protection, the refunds screen."""
import json
from datetime import timedelta
from decimal import Decimal as D
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import Client, override_settings
from django.utils import timezone

from core import db_hardening
from core.models import AuditLog

from . import mpesa_relay as relay
from . import mpesa_rules as rules
from . import mpesa_services as ms
from . import refund_report
from .models import MpesaCallbackLog, MpesaRelayCursor, MpesaStkRequest, MpesaTransaction, Refund, Sale
from .test_phase4a import CFG, SECRET, c2b, stk_ok
from .tests import SalesCase

TOKEN = "relay-token-xyz"


def post(kind, payload, secret=SECRET, **extra):
    return Client().post(f"/api/pay/{kind}/{secret}/", json.dumps(payload), content_type="application/json", **extra)


def rec(i, kind="c2b", payload=None):
    return {"id": i, "kind": kind, "payload": payload if payload is not None else c2b(f"RLY{i:07d}"), "received_at": "2026-11-08T10:00:00+03:00"}


class FakeOnlineServer:
    """Stands in for the VPS: hands out records after a given position, and counts how often it was asked."""

    def __init__(self, records):
        self.records, self.asked = records, []

    def __call__(self, after_id):
        self.asked.append(after_id)
        return {"ok": True, "records": [r for r in self.records if r["id"] > after_id]}


@override_settings(MPESA={**CFG, "ALLOWED_IPS": "196.201.214.200, 10.1.0.0/16"})
class AllowListTests(SalesCase):
    def test_a_caller_outside_the_list_gets_404_and_nothing_is_recorded(self):
        self.assertEqual(post("c2b/confirm", c2b()).status_code, 404)           # the test client calls from 127.0.0.1
        self.assertEqual((MpesaTransaction.objects.count(), MpesaCallbackLog.objects.count()), (0, 0))
        self.assertTrue(AuditLog.objects.filter(action="mpesa.callback_blocked").exists())

    def test_addresses_and_ranges_on_the_list_are_accepted(self):
        self.assertEqual(post("c2b/confirm", c2b("RLYALLOW01"), REMOTE_ADDR="196.201.214.200").status_code, 200)
        self.assertEqual(post("c2b/confirm", c2b("RLYALLOW02"), REMOTE_ADDR="10.1.77.8").status_code, 200)
        self.assertEqual(MpesaTransaction.objects.count(), 2)

    def test_validation_endpoint_is_protected_too(self):
        self.assertEqual(post("c2b/validate", c2b()).status_code, 404)
        self.assertEqual(post("c2b/validate", c2b(), REMOTE_ADDR="196.201.214.200").status_code, 200)

    def test_the_secret_is_still_required_from_an_allowed_address(self):
        self.assertEqual(post("c2b/confirm", c2b(), secret="nope", REMOTE_ADDR="196.201.214.200").status_code, 404)

    def test_forwarded_header_is_ignored_unless_the_proxy_is_trusted(self):
        r = post("c2b/confirm", c2b(), HTTP_X_FORWARDED_FOR="196.201.214.200")
        self.assertEqual(r.status_code, 404)

    def test_behind_our_proxy_the_last_forwarded_address_counts(self):
        with override_settings(MPESA={**CFG, "ALLOWED_IPS": "196.201.214.200", "TRUST_PROXY": True}):
            ok = post("c2b/confirm", c2b("RLYPROXY01"), HTTP_X_FORWARDED_FOR="9.9.9.9, 196.201.214.200")
            forged = post("c2b/confirm", c2b("RLYPROXY02"), HTTP_X_FORWARDED_FOR="196.201.214.200, 6.6.6.6")
        self.assertEqual((ok.status_code, forged.status_code), (200, 404))
        self.assertEqual(list(MpesaTransaction.objects.values_list("trans_id", flat=True)), ["RLYPROXY01"])

    def test_a_typo_in_the_list_fails_loudly_instead_of_allowing_everyone(self):
        with override_settings(MPESA={**CFG, "ALLOWED_IPS": "196.201.214.2OO"}):
            with self.assertRaises(rules.MpesaError):
                post("c2b/confirm", c2b(), REMOTE_ADDR="196.201.214.200")
        self.assertEqual(MpesaTransaction.objects.count(), 0)


@override_settings(MPESA={**CFG, "RELAY_TOKEN": TOKEN})
class CallbackLogAndExportTests(SalesCase):
    def export(self, token=TOKEN, after=0):
        headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token is not None else {}
        return Client().get(f"/api/pay/relay/export/?after={after}", **headers)

    def test_every_good_callback_is_kept_exactly_as_received(self):
        post("c2b/confirm", c2b("RLYLOG0001"))
        log = MpesaCallbackLog.objects.get()
        self.assertEqual((log.kind, log.outcome, log.payload["TransID"]), ("c2b", "ok", "RLYLOG0001"))

    def test_garbage_is_not_kept_or_exported(self):
        self.assertEqual(post("c2b/confirm", {"TransID": ""}).status_code, 400)
        self.assertEqual(MpesaCallbackLog.objects.count(), 0)

    def test_the_log_cannot_be_edited_or_deleted(self):
        post("c2b/confirm", c2b())
        log = MpesaCallbackLog.objects.get()
        log.outcome = "changed"
        with self.assertRaises(PermissionError):
            log.save()
        with self.assertRaises(PermissionError):
            log.delete()

    def test_export_needs_the_token(self):
        post("c2b/confirm", c2b())
        self.assertEqual(self.export(token=None).status_code, 404)
        self.assertEqual(self.export(token="wrong").status_code, 404)
        self.assertEqual(self.export().status_code, 200)

    def test_export_is_off_when_no_token_is_configured(self):
        with override_settings(MPESA={**CFG, "RELAY_TOKEN": ""}):
            self.assertEqual(Client().get("/api/pay/relay/export/", HTTP_AUTHORIZATION="Bearer ").status_code, 404)

    def test_export_returns_accepted_messages_in_order_after_the_position(self):
        post("c2b/confirm", c2b("RLYEXP0001"))
        post("c2b/confirm", c2b("RLYEXP0002"))
        relay.log_callback("c2b", c2b("RLYEXP0003"), None, "error")           # a message that failed here: never handed out
        data = self.export().json()
        ids = [r["id"] for r in data["records"]]
        self.assertEqual([r["payload"]["TransID"] for r in data["records"]], ["RLYEXP0001", "RLYEXP0002"])
        self.assertEqual(data["last_id"], ids[-1])
        self.assertEqual(self.export(after=ids[0]).json()["records"][0]["payload"]["TransID"], "RLYEXP0002")
        self.assertEqual(self.export(after=ids[-1]).json()["records"], [])


@override_settings(MPESA={**CFG, "RELAY_TOKEN": TOKEN})
class PullTests(SalesCase):
    def test_a_payment_made_while_the_shop_was_offline_arrives_and_can_be_used(self):
        server = FakeOnlineServer([rec(1, payload=c2b("RLYPULL001", amount="3500.00"))])
        result = relay.pull(server)
        self.assertEqual((result["applied"], result["position"]), (1, 1))
        txn = MpesaTransaction.objects.get()
        self.assertEqual((txn.trans_id, txn.status, txn.customer_name), ("RLYPULL001", MpesaTransaction.UNMATCHED, "JOHN KAMAU"))
        self.scan()
        sale = ms.checkout_mpesa(self.cashier, self.shift, txn.pk)
        self.assertEqual((sale.status, sale.payments.get().reference), (Sale.COMPLETED, "RLYPULL001"))

    def test_running_it_again_changes_nothing(self):
        server = FakeOnlineServer([rec(1), rec(2)])
        relay.pull(server)
        again = relay.pull(server)
        self.assertEqual((again["applied"], again["position"], MpesaTransaction.objects.count()), (0, 2, 2))
        self.assertEqual(server.asked[-1], 2)                                   # it asks only for what is new

    def test_a_message_the_shop_already_received_directly_is_not_recorded_twice(self):
        post("c2b/confirm", c2b("RLYBOTH001"))                                  # Safaricom reached the shop itself
        relay.pull(FakeOnlineServer([rec(1, payload=c2b("RLYBOTH001"))]))       # and the VPS also had it
        self.assertEqual(MpesaTransaction.objects.count(), 1)

    def test_an_stk_result_confirms_the_request_the_shop_sent(self):
        self.scan()
        MpesaStkRequest.objects.create(sale=Sale.objects.get(status="OPEN"), cashier=self.cashier, shift=self.shift,
                                       phone="254712345678", amount=D("3500"), checkout_request_id="ws_CO_9",
                                       status=MpesaStkRequest.PROCESSING)
        relay.pull(FakeOnlineServer([rec(1, "stk", stk_ok(checkout="ws_CO_9", receipt="RLYSTK0001"))]))
        stk = MpesaStkRequest.objects.get()
        self.assertEqual((stk.status, stk.transaction.trans_id), (MpesaStkRequest.CONFIRMED, "RLYSTK0001"))

    def test_nothing_unusable_blocks_the_rest(self):
        bad = rec(1, payload={"TransID": ""})                                    # not a real payment
        junk = {"id": 2, "kind": "other", "payload": {}}
        result = relay.pull(FakeOnlineServer([bad, junk, rec(3)]))
        self.assertEqual((result["applied"], result["skipped"], result["position"]), (1, 2, 3))
        self.assertTrue(AuditLog.objects.filter(action="mpesa.relay_skipped").exists())

    def test_a_failure_part_way_keeps_what_was_done_and_retries_the_rest(self):
        server = FakeOnlineServer([rec(1), rec(2), rec(3)])
        real = ms.record_c2b
        calls = {"n": 0}

        def flaky(payload):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("database went away")
            return real(payload)

        with mock.patch.object(ms, "record_c2b", flaky):
            with self.assertRaises(RuntimeError):
                relay.pull(server)
        self.assertEqual((MpesaRelayCursor.load().last_id, MpesaTransaction.objects.count()), (1, 1))
        result = relay.pull(server)                                              # the next run finishes the job
        self.assertEqual((result["applied"], result["position"], MpesaTransaction.objects.count()), (2, 3, 3))

    def test_a_lost_connection_changes_nothing(self):
        def down(after_id):
            raise CommandError("Could not read the online server.")
        with self.assertRaises(CommandError):
            relay.pull(down)
        self.assertEqual(MpesaRelayCursor.load().last_id, 0)

    def test_the_position_never_moves_backwards(self):
        relay.pull(FakeOnlineServer([rec(5)]))
        relay.pull(FakeOnlineServer([rec(2)]))
        self.assertEqual(MpesaRelayCursor.load().last_id, 5)


class PullCommandTests(SalesCase):
    def test_refuses_without_settings(self):
        with override_settings(MPESA={**CFG, "RELAY_URL": "", "RELAY_TOKEN": ""}):
            with self.assertRaises(CommandError):
                call_command("mpesa_pull")

    def test_refuses_plain_http_to_a_remote_server(self):
        with override_settings(MPESA={**CFG, "RELAY_URL": "http://pay.example.com", "RELAY_TOKEN": TOKEN}):
            with self.assertRaises(CommandError) as ctx:
                call_command("mpesa_pull")
        self.assertIn("https", str(ctx.exception))


class DatabaseProtectionTests(SalesCase):
    def test_the_hardening_covers_the_mpesa_tables(self):
        self.assertIn("sales_mpesacallbacklog", db_hardening.APPEND_ONLY_TABLES)
        sql = db_hardening.MPESA_SQL
        for needle in ("cannot be deleted", "trans_id", "amount", "MATCHED", "sale_id"):
            self.assertIn(needle, sql)

    def test_apply_does_nothing_off_postgresql(self):
        if connection.vendor != "postgresql":
            self.assertEqual(db_hardening.apply(connection), [])

    def test_the_model_already_refuses_to_change_or_delete_a_payment(self):
        txn = MpesaTransaction.objects.create(trans_id="RLYPROT001", source="C2B", amount=D("100.00"))
        txn.amount = D("1.00")
        with self.assertRaises(PermissionError):
            txn.save()
        with self.assertRaises(PermissionError):
            txn.delete()


class RefundScreenTests(SalesCase):
    def setUp(self):
        super().setUp()
        self.scan(qty=2)
        self.sale = self.sale_done()
        self.n = 0

    def sale_done(self):
        from . import services
        return services.checkout_cash(self.cashier, self.shift, "7000")

    def refund(self, status=Refund.REQUESTED, method=Refund.CASH, total="3500.00", **kw):
        self.n += 1
        return Refund.objects.create(number=f"R-TEST-{self.n:03d}", sale=self.sale, status=status, method=method,
                                     reason="Customer returned a bottle", total=D(total), till=self.till1,
                                     shift=self.shift, requested_by=self.cashier, **kw)

    def test_owner_and_supervisor_can_open_it_and_a_cashier_cannot(self):
        self.refund()
        for user, code in ((self.manager, 200), (self.supervisor, 200), (self.cashier, 403)):
            c = Client()
            c.force_login(user)
            self.assertEqual(c.get("/manage/refunds/").status_code, code, user.username)
        self.assertEqual(Client().get("/manage/refunds/").status_code, 302)

    def test_it_shows_who_asked_who_approved_and_who_paid(self):
        self.refund(Refund.PAID, Refund.MPESA, approved_by=self.supervisor, approved_at=timezone.now(),
                    paid_by=self.manager, paid_at=timezone.now(), pay_reference="QWE12345TY")
        c = Client()
        c.force_login(self.manager)
        body = c.get("/manage/refunds/").content.decode()
        for needle in ("R-TEST-001", "QWE12345TY", self.cashier.display_name, self.supervisor.display_name, "Paid back"):
            self.assertIn(needle, body)

    def test_late_unpaid_refunds_are_flagged(self):
        r = self.refund(Refund.APPROVED, Refund.MPESA, approved_by=self.supervisor, approved_at=timezone.now())
        Refund.objects.filter(pk=r.pk).update(approved_at=timezone.now() - timedelta(hours=30))
        c = Client()
        c.force_login(self.manager)
        self.assertIn("overdue", c.get("/manage/refunds/").content.decode())
        report = refund_report.build(timezone.localdate() - timedelta(days=1), timezone.localdate())
        self.assertEqual((report["owed"]["count"], report["late"]["count"], report["owed_mpesa"]["total"]), (1, 1, D("3500.00")))

    def test_cash_and_mpesa_payouts_are_kept_apart(self):
        now = timezone.now()
        self.refund(Refund.PAID, Refund.CASH, total="1000.00", approved_by=self.supervisor, approved_at=now, paid_by=self.supervisor, paid_at=now)
        self.refund(Refund.PAID, Refund.MPESA, total="2500.00", approved_by=self.supervisor, approved_at=now, paid_by=self.manager,
                    paid_at=now, pay_reference="ABC12345DE")
        report = refund_report.build(timezone.localdate(), timezone.localdate())
        self.assertEqual((report["paid_cash"], report["paid_mpesa"]), (D("1000.00"), D("2500.00")))

    def test_status_filter_and_csv(self):
        self.refund(Refund.REQUESTED)
        self.refund(Refund.REJECTED, rejected_by=self.supervisor, rejected_at=timezone.now(), reject_reason="Not our product")
        c = Client()
        c.force_login(self.manager)
        only = c.get("/manage/refunds/?status=REJECTED").content.decode()
        self.assertIn("R-TEST-002", only.split("<h2>Refunds")[1])
        self.assertNotIn("R-TEST-001", only.split("<h2>Refunds")[1])
        csv = c.get("/manage/refunds/?csv=1")
        self.assertEqual(csv.status_code, 200)
        self.assertIn("text/csv", csv["Content-Type"])
        self.assertIn("R-TEST-001", csv.content.decode())

    def test_a_bad_date_or_status_falls_back_instead_of_failing(self):
        c = Client()
        c.force_login(self.manager)
        self.assertEqual(c.get("/manage/refunds/?from=nonsense&to=2026-13-45&status=HACK").status_code, 200)
