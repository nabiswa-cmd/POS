from django.test import override_settings
from django.utils import timezone

from accounts.tests import BaseCase
from core.models import AuditLog
from tills.models import CashierShift, PosAuthorization, Till


class ChangeTillTests(BaseCase):
    def setUp(self):
        super().setUp()
        for n in (1, 2):
            Till.objects.get_or_create(number=n, defaults={"name": f"Till {n}", "active": True})

    def authorize(self, till=1):
        return self.post("/api/pos/authorize/", {"till": till, "username": "sup", "secret": "4321"})

    def cashier_login(self, till=1, username="cash1", secret="1111"):
        return self.post("/api/pos/cashier-login/", {"username": username, "secret": secret, "till": till})

    def change(self, till):
        return self.post("/api/pos/till/change/", {"till": till})

    def test_change_till_after_supervisor_step(self):
        self.authorize(till=1)
        res = self.change(2)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["till"], 2)
        pa = PosAuthorization.objects.get()
        self.assertEqual(pa.till.number, 2)
        self.assertEqual(pa.approval.till_number, 2)
        self.assertEqual(self.client.session["pos_till"], 2)
        self.assertTrue(AuditLog.objects.filter(action="pos.till_changed_before_shift").exists())
        # the cashier now lands on till 2 and opens the shift there
        self.assertEqual(self.cashier_login(till=2).status_code, 200)
        self.assertEqual(self.post("/api/pos/shift/open/", {"opening_float": "1000"}).status_code, 200)
        self.assertEqual(CashierShift.objects.get().till.number, 2)

    def test_change_till_after_cashier_login(self):
        self.authorize(till=1)
        self.cashier_login(till=1)
        res = self.change(2)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.post("/api/pos/shift/open/", {"opening_float": "500"}).status_code, 200)
        self.assertEqual(CashierShift.objects.get().till.number, 2)

    def test_busy_till_can_be_swapped_for_the_free_one(self):
        CashierShift.objects.create(till=Till.objects.get(number=1), cashier=self.cashier2,
                                    authorized_by=self.supervisor, opening_float=0)
        self.authorize(till=1)
        res = self.cashier_login(till=1)
        self.assertEqual(res.json()["till_busy_with"], self.cashier2.display_name)
        res = self.change(2)
        self.assertEqual(res.status_code, 200)
        self.assertIsNone(res.json()["till_busy_with"])
        self.assertEqual(self.post("/api/pos/shift/open/", {"opening_float": "0"}).status_code, 200)

    def test_cannot_change_away_from_own_open_shift(self):
        self.authorize(till=1)
        self.cashier_login(till=1)
        self.post("/api/pos/shift/open/", {"opening_float": "0"})
        self.assertEqual(self.change(2).status_code, 409)
        self.assertEqual(PosAuthorization.objects.get().till.number, 1)

    def test_cannot_move_onto_a_locked_till(self):
        CashierShift.objects.create(till=Till.objects.get(number=2), cashier=self.cashier2,
                                    authorized_by=self.supervisor, opening_float=0,
                                    locked_at=timezone.now())
        self.authorize(till=1)
        self.assertEqual(self.change(2).status_code, 409)
        self.assertEqual(PosAuthorization.objects.get().till.number, 1)

    def test_no_pending_opening_is_refused(self):
        self.assertEqual(self.change(2).status_code, 409)

    def test_invalid_till_is_refused(self):
        self.authorize(till=1)
        self.assertEqual(self.change(9).status_code, 400)

    def test_another_person_cannot_change_a_cashiers_pending_opening(self):
        self.authorize(till=1)
        self.cashier_login(till=1)
        PosAuthorization.objects.update(cashier=self.cashier2)   # the opening belongs to someone else
        self.assertEqual(self.change(2).status_code, 403)
        self.assertEqual(PosAuthorization.objects.get().till.number, 1)

    @override_settings(POS_TILL_CHANGE_REAUTH=True)
    def test_reauth_mode_cancels_the_authorization(self):
        self.authorize(till=1)
        res = self.change(2)
        self.assertTrue(res.json()["reauthorize"])
        self.assertIsNotNone(PosAuthorization.objects.get().cancelled_at)
        self.assertEqual(self.cashier_login(till=2).status_code, 403)   # needs a fresh authorization
        self.assertEqual(self.authorize(till=2).status_code, 200)
        self.assertEqual(self.cashier_login(till=2).status_code, 200)
