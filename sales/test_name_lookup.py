"""Safaricom name lookup, and the PostgreSQL triggers on the M-PESA tables (those run only on PostgreSQL)."""
import json
from datetime import timedelta
from decimal import Decimal as D
from unittest import mock, skipUnless

from django.db import DatabaseError, connection, transaction
from django.test import Client, override_settings
from django.utils import timezone

from core import db_hardening
from core.models import AuditLog

from . import daraja
from . import mpesa_rules as rules
from . import mpesa_services as ms
from .models import MpesaStkRequest, MpesaTransaction
from .test_phase4a import CFG, SECRET, c2b
from .tests import SalesCase

LOOKUP = dict(CFG, CONSUMER_KEY="k", CONSUMER_SECRET="s", SHORTCODE="600000", INITIATOR_NAME="init", SECURITY_CREDENTIAL="cred")


def result(receipt="QKA81LK5CY", debit="2547****3456 - JOHN KAMAU", code=0):
    params = [{"Key": "ReceiptNo", "Value": receipt}, {"Key": "DebitPartyName", "Value": debit},
              {"Key": "CreditPartyName", "Value": "600000 - MY SHOP"}]
    return {"Result": {"ResultCode": code, "ResultDesc": "done", "ResultParameters": {"ResultParameter": params}}}


class StatusParserTests(SalesCase):
    def test_name_is_taken_from_the_debit_party_only(self):
        p = rules.parse_status_result(result())
        self.assertEqual((p["ok"], p["trans_id"], p["customer_name"]), (True, "QKA81LK5CY", "JOHN KAMAU"))

    def test_masked_or_missing_names_stay_blank(self):
        for debit in ("2547****3456", "2547****3456 - ", "2547****3456 - 254712345678", ""):
            self.assertEqual(rules.parse_status_result(result(debit=debit))["customer_name"], "", debit)

    def test_failure_and_garbage(self):
        self.assertFalse(rules.parse_status_result(result(code=2001))["ok"])
        for bad in ({}, {"Result": {}}, {"Result": {"ResultCode": "x"}}, None):
            with self.assertRaises(rules.MpesaError):
                rules.parse_status_result(bad)
        self.assertEqual(rules.parse_status_result(result(receipt="short"))["trans_id"], "")


@override_settings(MPESA=LOOKUP)
class NameLookupTests(SalesCase):
    def setUp(self):
        super().setUp()
        from django.core.cache import cache
        cache.clear()                       # the 15-second throttle lock must not leak between tests

    def pay(self, trans_id="QKA81LK5CY", name="", age=60, amount="500.00"):
        t = MpesaTransaction.objects.create(trans_id=trans_id, source="C2B", amount=D(amount), customer_name=name)
        MpesaTransaction.objects.filter(pk=t.pk).update(created_at=timezone.now() - timedelta(seconds=age))
        return t

    def post(self, kind, payload, secret=SECRET):
        return Client().post(f"/api/pay/{kind}/{secret}/", json.dumps(payload), content_type="application/json")

    def test_result_fills_a_blank_name_and_never_changes_money(self):
        t = self.pay()
        self.assertEqual(self.post("txstatus/result", result()).json()["ResultCode"], 0)
        t.refresh_from_db()
        self.assertEqual((t.customer_name, t.amount, t.status), ("JOHN KAMAU", D("500.00"), "UNMATCHED"))
        self.assertTrue(AuditLog.objects.filter(action="mpesa.name_looked_up").exists())

    def test_result_never_overwrites_a_name_and_ignores_unknown_receipts(self):
        t = self.pay(name="MARY WANJIKU")
        self.post("txstatus/result", result())
        t.refresh_from_db()
        self.assertEqual(t.customer_name, "MARY WANJIKU")
        self.assertEqual(self.post("txstatus/result", result(receipt="NOSUCHCODE1")).status_code, 200)
        self.assertEqual(MpesaTransaction.objects.count(), 1)

    def test_endpoint_secret_garbage_and_timeout(self):
        self.assertEqual(self.post("txstatus/result", result(), secret="nope").status_code, 404)
        self.assertEqual(self.post("txstatus/result", {"x": 1}).status_code, 400)
        self.assertEqual(self.post("txstatus/timeout", {}).json()["ResultCode"], 0)

    def test_failed_lookup_is_audited_and_changes_nothing(self):
        t = self.pay()
        self.post("txstatus/result", result(code=2001))
        t.refresh_from_db()
        self.assertEqual(t.customer_name, "")
        self.assertTrue(AuditLog.objects.filter(action="mpesa.name_lookup_failed").exists())

    def test_only_unnamed_old_enough_payments_are_asked_and_only_twice(self):
        old, named, fresh = self.pay("OLDUNNAMED1"), self.pay("NAMEDALREADY", name="X Y"), self.pay("FRESHONE001", age=2)
        with mock.patch.object(daraja, "transaction_status", return_value=True) as q:
            self.assertEqual(ms.lookup_missing_names(), 1)
            q.assert_called_once_with("OLDUNNAMED1")
        old.refresh_from_db(); fresh.refresh_from_db()
        self.assertEqual((old.name_lookup_attempts, fresh.name_lookup_attempts, named.name_lookup_attempts), (1, 0, 0))
        from django.core.cache import cache
        for _ in range(3):
            cache.delete("mpesa-name-lookup-lock")
            MpesaTransaction.objects.filter(pk=old.pk).update(name_lookup_at=timezone.now() - timedelta(minutes=5))
            with mock.patch.object(daraja, "transaction_status", return_value=True):
                ms.lookup_missing_names()
        old.refresh_from_db()
        self.assertEqual(old.name_lookup_attempts, 2)

    def test_off_without_credentials_and_failures_never_reach_the_till(self):
        self.pay()
        with override_settings(MPESA=dict(LOOKUP, INITIATOR_NAME="")):
            with mock.patch.object(daraja, "transaction_status") as q:
                self.assertEqual(ms.lookup_missing_names(), 0)
                q.assert_not_called()
        from django.core.cache import cache
        cache.delete("mpesa-name-lookup-lock")
        with mock.patch.object(daraja, "transaction_status", side_effect=daraja.DarajaError("no internet")):
            self.assertEqual(ms.lookup_missing_names(), 0)
        cache.delete("mpesa-name-lookup-lock")
        with mock.patch.object(daraja, "transaction_status", side_effect=RuntimeError("boom")):
            self.assertEqual(ms.lookup_missing_names(), 0)

    def test_the_cashiers_list_triggers_the_lookup(self):
        self.pay()
        with mock.patch.object(daraja, "transaction_status", return_value=True) as q:
            ms.recent_unmatched(self.cashier, self.shift)
            q.assert_called_once()

    def test_request_body_uses_till_settings(self):
        with mock.patch.object(daraja, "_call", return_value={"ResponseCode": "0"}) as call, \
             mock.patch.object(daraja, "_auth", return_value={}):
            self.assertTrue(daraja.transaction_status("QKA81LK5CY"))
        body = call.call_args[0][2]
        self.assertEqual((body["CommandID"], body["TransactionID"], body["IdentifierType"]), ("TransactionStatusQuery", "QKA81LK5CY", "2"))
        self.assertTrue(body["ResultURL"].endswith(f"/api/pay/txstatus/result/{SECRET}/"))


@skipUnless(connection.vendor == "postgresql", "the triggers exist only on PostgreSQL")
class MpesaTriggerTests(SalesCase):
    def setUp(self):
        super().setUp()
        db_hardening.apply(connection)

    def blocked(self, fn):
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                fn()

    def txn(self, trans_id="TRIGGER0001"):
        return MpesaTransaction.objects.create(trans_id=trans_id, source="C2B", amount=D("500.00"))

    def used(self):
        self.scan()
        t = self.txn()
        MpesaTransaction.objects.filter(pk=t.pk).update(status="MATCHED", matched_by=self.cashier, matched_at=timezone.now())
        return t

    def test_payment_rows_cannot_be_deleted_or_have_money_changed(self):
        t = self.txn()
        self.blocked(lambda: MpesaTransaction.objects.filter(pk=t.pk).delete())
        self.blocked(lambda: MpesaTransaction.objects.filter(pk=t.pk).update(amount=D("1.00")))
        self.blocked(lambda: MpesaTransaction.objects.filter(pk=t.pk).update(trans_id="CHANGED0001"))

    def test_a_used_payment_is_locked_but_a_late_name_is_allowed(self):
        t = self.used()
        self.blocked(lambda: MpesaTransaction.objects.filter(pk=t.pk).update(status="UNMATCHED"))
        self.blocked(lambda: MpesaTransaction.objects.filter(pk=t.pk).update(matched_by=self.supervisor))
        self.blocked(lambda: MpesaTransaction.objects.filter(pk=t.pk).update(matched_at=timezone.now()))
        MpesaTransaction.objects.filter(pk=t.pk).update(customer_name="JOHN KAMAU", name_lookup_attempts=1)
        t.refresh_from_db()
        self.assertEqual(t.customer_name, "JOHN KAMAU")

    def test_an_ignored_payment_can_only_be_restored(self):
        t = self.txn()
        MpesaTransaction.objects.filter(pk=t.pk).update(status="IGNORED")
        self.blocked(lambda: MpesaTransaction.objects.filter(pk=t.pk).update(status="MATCHED"))
        MpesaTransaction.objects.filter(pk=t.pk).update(status="UNMATCHED")

    def stk(self, status="PROCESSING"):
        sale, _ = self.scan()
        return MpesaStkRequest.objects.create(sale=sale, cashier=self.cashier, shift=self.shift, phone="254712345678",
                                              amount=D("100.00"), status=status, checkout_request_id="ws_CO_T1")

    def test_payment_requests_are_never_deleted_and_finished_ones_are_frozen(self):
        r = self.stk()
        self.blocked(lambda: MpesaStkRequest.objects.filter(pk=r.pk).delete())
        self.blocked(lambda: MpesaStkRequest.objects.filter(pk=r.pk).update(amount=D("1.00")))
        self.blocked(lambda: MpesaStkRequest.objects.filter(pk=r.pk).update(phone="254700000000"))
        MpesaStkRequest.objects.filter(pk=r.pk).update(status="CONFIRMED")           # an open request may finish
        self.blocked(lambda: MpesaStkRequest.objects.filter(pk=r.pk).update(status="FAILED"))
        self.blocked(lambda: MpesaStkRequest.objects.filter(pk=r.pk).update(result_desc="edited"))
