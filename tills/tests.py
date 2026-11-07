import json
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from accounts.models import Approval
from accounts.tests import BaseCase
from core.models import AuditLog, ShopSettings
from tills.models import CashierShift, PosAuthorization


class PosOpeningFlowTests(BaseCase):
    def authorize(self, username="sup", secret="4321", till=1):
        return self.post("/api/pos/authorize/", {"till": till, "username": username, "secret": secret})

    def cashier_login(self, username="cash1", secret="1111", till=1):
        return self.post("/api/pos/cashier-login/", {"username": username, "secret": secret, "till": till})

    def test_full_flow_records_both_people(self):
        self.assertEqual(self.authorize().status_code, 200)
        res = self.cashier_login()
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["authorized_by"], "sup")
        self.assertIsNone(res.json()["shift"])
        res = self.post("/api/pos/shift/open/", {"opening_float": "5000"})
        self.assertEqual(res.status_code, 200)
        shift = CashierShift.objects.get()
        self.assertEqual(shift.cashier, self.cashier)
        self.assertEqual(shift.authorized_by, self.supervisor)
        self.assertEqual(str(shift.opening_float), "5000.00")
        pa = PosAuthorization.objects.get()
        self.assertEqual(pa.cashier, self.cashier)
        self.assertEqual(pa.approval.approver, self.supervisor)
        self.assertTrue(AuditLog.objects.filter(action="pos.session_opened").exists())

    def test_cashier_cannot_open_pos_without_supervisor(self):
        res = self.cashier_login()
        self.assertEqual(res.status_code, 403)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_a_cashier_cannot_authorize_the_pos(self):
        res = self.authorize(username="cash2", secret="Str0ng-pass!")
        self.assertEqual(res.status_code, 403)
        self.assertEqual(self.cashier_login().status_code, 403)

    def test_manager_can_authorize(self):
        self.assertEqual(self.authorize(username="boss", secret="Str0ng-pass!").status_code, 200)
        self.assertEqual(self.cashier_login().status_code, 200)

    def test_supervisor_cannot_log_in_as_cashier_on_own_authorization(self):
        self.authorize()
        res = self.cashier_login(username="sup", secret="4321")
        self.assertEqual(res.status_code, 403)

    def test_authorization_is_single_use(self):
        self.authorize()
        self.assertEqual(self.cashier_login().status_code, 200)
        self.client.logout()
        self.assertEqual(self.cashier_login(username="cash2", secret="Str0ng-pass!").status_code, 403)

    def test_expired_authorization_refused(self):
        self.authorize()
        Approval.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.cashier_login().status_code, 403)

    def test_non_sales_staff_cannot_log_in_to_pos(self):
        self.authorize()
        self.assertEqual(self.cashier_login(username="clerk", secret="Str0ng-pass!").status_code, 403)

    def test_invalid_till_rejected(self):
        self.assertEqual(self.authorize(till=99).status_code, 400)

    def test_second_cashier_cannot_open_a_shift_on_a_busy_till(self):
        self.authorize()
        self.cashier_login()
        self.post("/api/pos/shift/open/", {"opening_float": "1000"})
        self.client.logout()
        self.authorize()
        res = self.cashier_login(username="cash2", secret="Str0ng-pass!")
        self.assertEqual(res.json()["till_busy_with"], "cash1")
        res = self.post("/api/pos/shift/open/", {"opening_float": "1000"})
        self.assertEqual(res.status_code, 409)
        self.assertEqual(CashierShift.objects.count(), 1)

    def test_two_tills_can_each_have_a_shift(self):
        self.authorize(till=1)
        self.cashier_login(till=1)
        self.post("/api/pos/shift/open/", {"opening_float": "1000"})
        self.client.logout()
        self.authorize(till=2)
        self.cashier_login(username="cash2", secret="Str0ng-pass!", till=2)
        self.assertEqual(self.post("/api/pos/shift/open/", {"opening_float": "2000"}).status_code, 200)
        self.assertEqual(CashierShift.objects.filter(closed_at__isnull=True).count(), 2)

    def test_bad_floats_rejected(self):
        self.authorize()
        self.cashier_login()
        for bad in ("", "abc", "-5", "NaN", "Infinity", "99999999999"):
            res = self.post("/api/pos/shift/open/", {"opening_float": bad})
            self.assertEqual(res.status_code, 400, bad)
        self.assertFalse(CashierShift.objects.exists())

    def test_shift_cannot_open_without_supervisor_authorization(self):
        self.client.force_login(self.cashier)
        res = self.post("/api/pos/shift/open/", {"opening_float": "100"})
        self.assertEqual(res.status_code, 403)

    def test_resume_policy_lets_same_cashier_back_in_without_supervisor(self):
        ShopSettings.objects.update_or_create(pk=1, defaults={"pos_open_policy": ShopSettings.POLICY_RESUME})
        self.authorize()
        self.cashier_login()
        self.post("/api/pos/shift/open/", {"opening_float": "1000"})
        self.client.logout()
        self.client.session.flush()
        res = self.cashier_login()
        self.assertEqual(res.status_code, 200)
        self.assertIsNotNone(res.json()["shift"])
        self.client.logout()
        # a different cashier still needs a supervisor
        self.assertEqual(self.cashier_login(username="cash2", secret="Str0ng-pass!").status_code, 403)

    def test_default_policy_requires_supervisor_every_time(self):
        self.authorize()
        self.cashier_login()
        self.post("/api/pos/shift/open/", {"opening_float": "1000"})
        self.client.logout()
        self.assertEqual(self.cashier_login().status_code, 403)

    def test_second_open_shift_on_same_till_blocked_by_database(self):
        from django.db import IntegrityError, transaction
        from tills.models import Till
        till = Till.objects.get(number=1)
        CashierShift.objects.create(till=till, cashier=self.cashier, authorized_by=self.supervisor)
        with self.assertRaises(IntegrityError), transaction.atomic():
            CashierShift.objects.create(till=till, cashier=self.cashier2, authorized_by=self.supervisor)


class PagesTests(TestCase):
    def test_pages_render(self):
        from django.core.management import call_command
        call_command("setup_shop", verbosity=0)
        for mode in ("pos", "manager", "receiving"):
            res = self.client.get(f"/app/{mode}/")
            self.assertEqual(res.status_code, 200)
        self.assertEqual(self.client.get("/app/nope/").status_code, 404)
        self.assertEqual(self.client.get("/health/").status_code, 200)
