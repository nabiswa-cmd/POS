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
        return self.post("/api/pos/authorize/", {
            "till": till, "username": username, "secret": secret,
        })

    def cashier_login(self, username="cash1", secret="1111", till=1):
        return self.post("/api/pos/cashier-login/", {"username": username, "secret": secret, "till": till})

    def test_full_flow_records_both_people(self):
        self.assertEqual(self.authorize().status_code, 200)
        pending = PosAuthorization.objects.get()
        self.assertIsNone(pending.cashier)
        self.assertIsNone(pending.cashier_logged_in_at)
        res = self.cashier_login()
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["authorized_by"], "sup")
        self.assertIsNone(res.json()["shift"])
        pending.refresh_from_db()
        self.assertEqual(pending.cashier, self.cashier)
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

    def test_supervisor_authorizes_the_till_for_any_cashier_until_first_login(self):
        self.assertEqual(self.authorize().status_code, 200)
        authorization = PosAuthorization.objects.get()
        self.assertIsNone(authorization.cashier)
        self.assertIsNone(authorization.cashier_logged_in_at)

        self.assertEqual(self.cashier_login(username="cash2", secret="Str0ng-pass!").status_code, 200)
        authorization.refresh_from_db()
        self.assertEqual(authorization.cashier, self.cashier2)
        self.assertIsNotNone(authorization.cashier_logged_in_at)

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

    def test_supervisor_can_cancel_authorization_before_cashier_login(self):
        self.authorize()
        authorization = PosAuthorization.objects.get()

        response = self.post("/api/pos/authorization/cancel/", {})

        self.assertEqual(response.status_code, 200)
        authorization.refresh_from_db()
        self.assertIsNotNone(authorization.cancelled_at)
        self.assertEqual(self.cashier_login().status_code, 403)
        self.assertTrue(AuditLog.objects.filter(action="pos.authorization_cancelled").exists())

    def test_changing_cashier_requires_a_new_supervisor_authorization(self):
        self.authorize()
        self.assertEqual(self.cashier_login().status_code, 200)
        authorization = PosAuthorization.objects.get()

        response = self.post("/api/pos/cashier/change/", {})

        self.assertEqual(response.status_code, 200)
        authorization.refresh_from_db()
        self.assertEqual(authorization.cashier, self.cashier)
        self.assertIsNotNone(authorization.cancelled_at)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertNotIn("pos_auth_id", self.client.session)
        self.assertEqual(self.authorize().status_code, 200)
        second = self.cashier_login(username="cash2", secret="Str0ng-pass!")
        self.assertEqual(second.status_code, 200)
        self.assertEqual(self.post("/api/pos/shift/open/", {"opening_float": "500"}).status_code, 200)
        shift = CashierShift.objects.get()
        self.assertEqual((shift.cashier, shift.authorized_by), (self.cashier2, self.supervisor))
        self.assertTrue(AuditLog.objects.filter(action="pos.cashier_changed_before_shift").exists())

    def test_open_shift_allows_supervisor_change_but_not_cashier_change(self):
        self.authorize()
        self.cashier_login()
        self.assertEqual(self.post("/api/pos/shift/open/", {"opening_float": "500"}).status_code, 200)

        self.assertEqual(self.post("/api/pos/cashier/change/", {}).status_code, 409)
        authorization = PosAuthorization.objects.get()
        self.assertEqual(self.post("/api/pos/authorization/cancel/", {}).status_code, 200)
        authorization.refresh_from_db()
        self.assertIsNotNone(authorization.cancelled_at)

        self.assertEqual(self.authorize(username="boss", secret="Str0ng-pass!").status_code, 200)
        response = self.cashier_login()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["shift"]["opening_float"], "500.00")

        shift = CashierShift.objects.get()
        self.assertEqual(shift.authorized_by, self.manager)
        self.assertEqual(str(shift.opening_float), "500.00")
        self.assertEqual(CashierShift.objects.count(), 1)

    def test_expired_authorization_refused(self):
        self.authorize()
        Approval.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.cashier_login().status_code, 403)

    def test_non_sales_staff_cannot_log_in_to_pos(self):
        self.authorize()
        self.assertEqual(self.cashier_login(username="clerk", secret="Str0ng-pass!").status_code, 403)
        authorization = PosAuthorization.objects.get()
        self.assertIsNone(authorization.cashier)
        self.assertIsNone(authorization.approval.consumed_at)

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

    def test_manager_policy_allows_direct_cashier_login_and_audits_self_authorization(self):
        ShopSettings.objects.update_or_create(
            pk=1, defaults={"pos_open_policy": ShopSettings.POLICY_CASHIER_LOGIN})

        response = self.cashier_login()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["authorized_by"], self.cashier.display_name)
        authorization = PosAuthorization.objects.get()
        self.assertEqual(authorization.cashier, self.cashier)
        self.assertEqual(authorization.approval.approver, self.cashier)
        self.assertIn("self-authorized", authorization.approval.reason)
        self.assertEqual(self.post("/api/pos/shift/open/", {"opening_float": "500"}).status_code, 200)
        shift = CashierShift.objects.get()
        self.assertEqual((shift.cashier, shift.authorized_by), (self.cashier, self.cashier))
        self.assertTrue(AuditLog.objects.filter(
            action="pos.session_opened", actor=self.cashier, approver=self.cashier).exists())

    def test_direct_login_policy_still_checks_credentials_and_pos_permission(self):
        ShopSettings.objects.update_or_create(
            pk=1, defaults={"pos_open_policy": ShopSettings.POLICY_CASHIER_LOGIN})

        self.assertEqual(self.cashier_login(secret="wrong").status_code, 401)
        self.assertEqual(self.cashier_login(username="clerk", secret="Str0ng-pass!").status_code, 403)
        self.assertFalse(PosAuthorization.objects.exists())

    def test_direct_login_policy_refuses_a_till_with_another_cashiers_open_shift(self):
        self.authorize()
        self.cashier_login()
        self.assertEqual(self.post("/api/pos/shift/open/", {"opening_float": "500"}).status_code, 200)
        self.client.logout()
        self.client.session.flush()
        ShopSettings.objects.update_or_create(
            pk=1, defaults={"pos_open_policy": ShopSettings.POLICY_CASHIER_LOGIN})

        response = self.cashier_login(username="cash2", secret="Str0ng-pass!")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(PosAuthorization.objects.count(), 1)

    def test_cashier_screen_lock_resumes_same_cashier_without_supervisor(self):
        self.authorize()
        self.cashier_login()
        self.assertEqual(self.post("/api/pos/shift/open/", {"opening_float": "1000"}).status_code, 200)

        locked = self.post("/api/pos/lock/", {})
        self.assertEqual(locked.status_code, 200)
        shift = CashierShift.objects.get()
        self.assertIsNotNone(shift.locked_at)
        self.assertNotIn("_auth_user_id", self.client.session)
        state = self.client.get("/api/pos/state/?till=1").json()
        self.assertEqual(state["locked_shift"], {"till": 1, "cashier": "cash1"})

        resumed = self.post("/api/pos/unlock/", {"till": 1, "username": "cash1", "secret": "1111"})
        self.assertEqual(resumed.status_code, 200)
        self.assertIsNotNone(resumed.json()["shift"])
        shift.refresh_from_db()
        self.assertIsNone(shift.locked_at)
        self.assertEqual(PosAuthorization.objects.count(), 1)
        self.assertTrue(AuditLog.objects.filter(action="pos.session_locked").exists())
        self.assertTrue(AuditLog.objects.filter(action="pos.session_unlocked").exists())

    def test_supervisor_can_authorize_a_legacy_locked_shift_for_its_cashier(self):
        self.authorize()
        self.cashier_login()
        self.post("/api/pos/shift/open/", {"opening_float": "1000"})
        shift = CashierShift.objects.get()
        shift.locked_at = timezone.now()
        shift.save(update_fields=["locked_at"])
        self.client.logout()
        self.client.session.flush()

        self.assertEqual(self.authorize().status_code, 200)
        resumed = self.cashier_login()

        self.assertEqual(resumed.status_code, 200)
        self.assertIsNotNone(resumed.json()["shift"])
        shift.refresh_from_db()
        self.assertIsNone(shift.locked_at)
        self.assertTrue(AuditLog.objects.filter(action="pos.session_unlocked").exists())

    def test_only_the_cashier_on_a_locked_shift_can_unlock_it(self):
        self.authorize()
        self.cashier_login()
        self.post("/api/pos/shift/open/", {"opening_float": "1000"})
        shift = CashierShift.objects.get()
        shift.locked_at = timezone.now()
        shift.save(update_fields=["locked_at"])

        denied = self.post("/api/pos/unlock/", {
            "till": 1, "username": "cash2", "secret": "Str0ng-pass!"
        })
        self.assertEqual(denied.status_code, 403)
        self.assertIsNotNone(CashierShift.objects.get().locked_at)
        self.assertEqual(self.post("/api/pos/unlock/", {
            "till": 1, "username": "cash1", "secret": "1111"
        }).status_code, 200)

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
        pos_page = self.client.get("/app/pos/")
        for element_id in ("b-back-authorize", "b-back-cashier", "b-change-authorizer", "f-authorize"):
            self.assertContains(pos_page, f'id="{element_id}"')
        self.assertNotContains(pos_page, 'id="a-cashier"')
        self.assertContains(pos_page, 'id="c-user"')
        self.assertContains(pos_page, "/api/pos/unlock/")
        self.assertEqual(self.client.get("/app/nope/").status_code, 404)
        self.assertEqual(self.client.get("/health/").status_code, 200)

    def test_cashier_login_screen_uses_manager_pos_opening_policy(self):
        from django.core.management import call_command
        from core.models import ShopSettings

        call_command("setup_shop", verbosity=0)
        state = self.client.get("/api/pos/state/").json()
        self.assertFalse(state["cashier_login_without_supervisor"])
        ShopSettings.objects.update_or_create(
            pk=1, defaults={"pos_open_policy": ShopSettings.POLICY_CASHIER_LOGIN})

        state = self.client.get("/api/pos/state/").json()
        page = self.client.get("/app/pos/")

        self.assertTrue(state["cashier_login_without_supervisor"])
        self.assertContains(page, "Supervisor authorization is disabled by the manager.")
        self.assertContains(page, "Step 1 of 2: Cashier login")
