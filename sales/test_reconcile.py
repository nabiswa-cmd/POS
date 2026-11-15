"""Reconciliation part 1: waiting payments, set aside / put back with a reason, daily checks, screen, CSV."""
from datetime import timedelta
from decimal import Decimal as D

from django.test import override_settings
from django.utils import timezone

from core.models import AuditLog

from . import mpesa_services as ms
from . import reconcile
from .models import MpesaStkRequest, MpesaTransaction, Payment, Sale
from .services import NotAllowed, SaleError
from .test_phase4a import CFG
from .tests import SalesCase

REASON = "Customer paid twice, refunding"


def pay(code, amount="3500.00", name="", source="C2B", age_hours=0):
    t = MpesaTransaction.objects.create(trans_id=code, source=source, amount=D(amount), customer_name=name)
    if age_hours:
        MpesaTransaction.objects.filter(pk=t.pk).update(created_at=timezone.now() - timedelta(hours=age_hours))
    t.refresh_from_db()
    return t


@override_settings(MPESA=CFG)
class SetAsideTests(SalesCase):
    def test_owner_sets_a_payment_aside_with_a_reason_and_it_is_audited(self):
        t = pay("RECON00001")
        reconcile.set_aside(self.manager, t.pk, REASON)
        t.refresh_from_db()
        self.assertEqual((t.status, t.resolution_reason, t.resolved_by), (MpesaTransaction.IGNORED, REASON, self.manager))
        self.assertIsNotNone(t.resolved_at)
        self.assertEqual((t.trans_id, t.amount), ("RECON00001", D("3500.00")))      # the money record itself is untouched
        entry = AuditLog.objects.get(action="mpesa.set_aside")
        self.assertEqual((entry.actor, entry.object_ref, entry.reason), (self.manager, "RECON00001", REASON))
        self.assertEqual(entry.new_value["amount"], "3500.00")

    def test_a_reason_is_required_and_must_be_real(self):
        t = pay("RECON00002")
        for bad in ("", "   ", "ok", "short"):
            with self.assertRaises(SaleError):
                reconcile.set_aside(self.manager, t.pk, bad)
        t.refresh_from_db()
        self.assertEqual(t.status, MpesaTransaction.UNMATCHED)
        self.assertFalse(AuditLog.objects.filter(action="mpesa.set_aside").exists())

    def test_supervisor_and_cashier_cannot_set_aside(self):
        t = pay("RECON00003")
        for who in (self.supervisor, self.cashier):
            with self.assertRaises(NotAllowed):
                reconcile.set_aside(who, t.pk, REASON)
        t.refresh_from_db()
        self.assertEqual(t.status, MpesaTransaction.UNMATCHED)

    def test_a_payment_used_on_a_sale_cannot_be_set_aside(self):
        self.scan()
        t = pay("RECON00004")
        ms.checkout_split(self.cashier, self.shift, [t.pk])
        with self.assertRaisesMessage(SaleError, "used on a sale"):
            reconcile.set_aside(self.manager, t.pk, REASON)
        t.refresh_from_db()
        self.assertEqual(t.status, MpesaTransaction.MATCHED)

    def test_cannot_set_aside_twice(self):
        t = pay("RECON00005")
        reconcile.set_aside(self.manager, t.pk, REASON)
        with self.assertRaisesMessage(SaleError, "already set aside"):
            reconcile.set_aside(self.manager, t.pk, REASON)

    def test_a_set_aside_payment_can_no_longer_be_used_at_the_till(self):
        self.scan()
        t = pay("RECON00006")
        reconcile.set_aside(self.manager, t.pk, REASON)
        with self.assertRaisesMessage(SaleError, "already been used"):
            ms.checkout_split(self.cashier, self.shift, [t.pk])
        self.assertEqual(Sale.objects.filter(status=Sale.COMPLETED).count(), 0)
        self.assertEqual(ms.recent_unmatched(self.cashier, self.shift), [])         # not even offered to the cashier

    def test_put_back_makes_it_usable_again_and_clears_the_reason(self):
        self.scan()
        t = pay("RECON00007")
        reconcile.set_aside(self.manager, t.pk, REASON)
        reconcile.restore(self.manager, t.pk, "Set aside by mistake")
        t.refresh_from_db()
        self.assertEqual((t.status, t.resolution_reason, t.resolved_by), (MpesaTransaction.UNMATCHED, "", None))
        sale, _ = ms.checkout_split(self.cashier, self.shift, [t.pk])
        self.assertEqual(sale.payments.get().method, Payment.MPESA)
        restored = AuditLog.objects.get(action="mpesa.restored")
        self.assertEqual(restored.previous_value["reason"], REASON)

    def test_only_a_set_aside_payment_can_be_put_back(self):
        t = pay("RECON00008")
        with self.assertRaisesMessage(SaleError, "set aside can be put back"):
            reconcile.restore(self.manager, t.pk, "no reason needed here")
        with self.assertRaises(NotAllowed):
            reconcile.restore(self.cashier, t.pk, "no reason needed here")

    def test_unknown_payment(self):
        with self.assertRaisesMessage(SaleError, "not found"):
            reconcile.set_aside(self.manager, 999999, REASON)


@override_settings(MPESA=CFG)
class DayReportTests(SalesCase):
    def test_empty_day_passes_every_check(self):
        r = reconcile.day_report(timezone.localdate())
        self.assertEqual((r["received"]["count"], r["problems"], r["waiting"]["count"]), (0, [], 0))
        self.assertGreaterEqual(r["checks"], 7)

    def test_figures_by_status_add_up(self):
        self.scan()
        used = pay("RECON10001", "3500.00")
        ms.checkout_split(self.cashier, self.shift, [used.pk])
        pay("RECON10002", "1200.00")
        aside = pay("RECON10003", "800.00")
        reconcile.set_aside(self.manager, aside.pk, REASON)
        r = reconcile.day_report(timezone.localdate())
        self.assertEqual((r["received"]["count"], r["received"]["total"]), (3, D("5500.00")))
        self.assertEqual(r["by_status"]["MATCHED"]["total"], D("3500.00"))
        self.assertEqual(r["by_status"]["UNMATCHED"]["total"], D("1200.00"))
        self.assertEqual(r["by_status"]["IGNORED"]["total"], D("800.00"))
        self.assertEqual(r["used_today"], D("3500.00"))
        self.assertEqual((r["waiting"]["count"], r["waiting"]["total"]), (1, D("1200.00")))
        self.assertEqual((r["ignored"]["count"], r["ignored"]["total"]), (1, D("800.00")))
        self.assertEqual(r["problems"], [])

    def test_old_waiting_payments_are_flagged(self):
        pay("RECON10004", "500.00", age_hours=30)
        pay("RECON10005", "700.00")
        r = reconcile.day_report(timezone.localdate())
        self.assertEqual((r["waiting"]["count"], r["old_waiting"]["count"], r["old_waiting"]["total"]), (2, 1, D("500.00")))

    def test_a_receipt_that_disagrees_with_safaricom_is_caught(self):
        self.scan()
        t = pay("RECON10006")
        sale, _ = ms.checkout_split(self.cashier, self.shift, [t.pk])
        Payment.objects.filter(sale=sale, method=Payment.MPESA).update(amount=D("3000.00"))      # tamper, bypassing the app
        problems = reconcile.day_report(timezone.localdate())["problems"]
        self.assertTrue(any(sale.number in p and "3,000.00" in p and "3,500.00" in p for p in problems), problems)

    def test_mpesa_payment_row_without_a_safaricom_record_is_caught(self):
        self.scan()
        t = pay("RECON10007")
        sale, _ = ms.checkout_split(self.cashier, self.shift, [t.pk])
        Payment.objects.filter(sale=sale).update(mpesa_transaction=None)
        self.assertTrue(any("no Safaricom confirmation" in p for p in reconcile.day_report(timezone.localdate())["problems"]))

    def test_used_payment_with_no_sale_and_set_aside_without_reason_are_caught(self):
        MpesaTransaction.objects.filter(pk=pay("RECON10008").pk).update(status=MpesaTransaction.MATCHED)
        MpesaTransaction.objects.filter(pk=pay("RECON10009").pk).update(status=MpesaTransaction.IGNORED)
        text = " ".join(reconcile.day_report(timezone.localdate())["problems"])
        self.assertIn("RECON10008", text)
        self.assertIn("without a reason", text)

    def test_phone_request_stuck_for_a_long_time_is_flagged(self):
        self.scan()
        stk = MpesaStkRequest.objects.create(sale=Sale.objects.get(status="OPEN"), cashier=self.cashier, shift=self.shift,
                                             phone="254712345678", amount=D("3500"), checkout_request_id="ws_CO_stuck",
                                             status=MpesaStkRequest.PROCESSING)
        MpesaStkRequest.objects.filter(pk=stk.pk).update(created_at=timezone.now() - timedelta(minutes=30))
        self.assertTrue(any("never got an answer" in p for p in reconcile.day_report(timezone.localdate())["problems"]))

    def test_other_days_are_kept_apart(self):
        t = pay("RECON10010", "900.00")
        MpesaTransaction.objects.filter(pk=t.pk).update(created_at=timezone.now() - timedelta(days=3))
        today = reconcile.day_report(timezone.localdate())
        before = reconcile.day_report(timezone.localdate() - timedelta(days=3))
        self.assertEqual((today["received"]["count"], before["received"]["count"], before["received"]["total"]), (0, 1, D("900.00")))
        self.assertEqual(today["waiting"]["count"], 1)            # still waiting overall, whatever day it came in

    def test_csv_has_one_row_per_payment_with_reason(self):
        t = pay("RECON10011", "950.00", name="JOHN KAMAU")
        reconcile.set_aside(self.manager, t.pk, REASON)
        rows = reconcile.csv_for_day(timezone.localdate()).strip().splitlines()
        self.assertEqual(len(rows), 2)
        self.assertIn("RECON10011", rows[1])
        self.assertIn("JOHN KAMAU", rows[1])
        self.assertIn(REASON, rows[1])


@override_settings(MPESA=CFG)
class ScreenTests(SalesCase):
    URL = "/manage/reconciliation/"

    def login(self, user):
        self.client.force_login(user)
        return self.client

    def test_cashier_cannot_open_it_and_anonymous_is_sent_to_login(self):
        self.assertEqual(self.login(self.cashier).get(self.URL).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(self.URL).status_code, 302)

    def test_supervisor_can_look_but_not_act(self):
        t = pay("RECON20001")
        c = self.login(self.supervisor)
        page = c.get(self.URL)
        self.assertEqual(page.status_code, 200)
        self.assertIn("RECON20001", page.content.decode())
        self.assertNotIn('value="set_aside"', page.content.decode())
        c.post(self.URL, {"action": "set_aside", "txn_id": t.pk, "reason": REASON})
        t.refresh_from_db()
        self.assertEqual(t.status, MpesaTransaction.UNMATCHED)

    def test_owner_sets_aside_and_puts_back_from_the_screen(self):
        t = pay("RECON20002", "1500.00")
        c = self.login(self.manager)
        self.assertIn('value="set_aside"', c.get(self.URL).content.decode())
        res = c.post(self.URL, {"action": "set_aside", "txn_id": t.pk, "reason": REASON}, follow=True)
        self.assertEqual(res.status_code, 200)
        t.refresh_from_db()
        self.assertEqual(t.status, MpesaTransaction.IGNORED)
        self.assertIn(REASON, c.get(self.URL).content.decode())
        c.post(self.URL, {"action": "restore", "txn_id": t.pk, "reason": "Sorted out, use it"})
        t.refresh_from_db()
        self.assertEqual(t.status, MpesaTransaction.UNMATCHED)

    def test_a_short_reason_from_the_screen_is_refused_and_nothing_changes(self):
        t = pay("RECON20003")
        self.login(self.manager).post(self.URL, {"action": "set_aside", "txn_id": t.pk, "reason": "meh"})
        t.refresh_from_db()
        self.assertEqual(t.status, MpesaTransaction.UNMATCHED)

    def test_csv_download_and_date_parameter(self):
        pay("RECON20004", "400.00")
        c = self.login(self.manager)
        res = c.get(self.URL + "?csv=1")
        self.assertEqual((res.status_code, res["Content-Type"].split(";")[0]), (200, "text/csv"))
        self.assertIn("RECON20004", res.content.decode())
        self.assertEqual(c.get(self.URL + "?day=not-a-date").status_code, 200)       # bad date falls back to today
        self.assertEqual(c.get(self.URL + "?day=2020-01-01").status_code, 200)

    def test_nav_shows_reconciliation_to_those_who_may_see_it(self):
        self.assertIn("Reconciliation", self.login(self.supervisor).get(self.URL).content.decode())
