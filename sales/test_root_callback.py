import json

from django.test import Client, override_settings

from .models import MpesaCallbackLog, MpesaTransaction
from .test_phase4a import CFG, c2b
from .tests import SalesCase


@override_settings(MPESA={**CFG, "SHORTCODE": "123456", "TILL_NUMBER": "3453945"})
class RootC2bCallbackTests(SalesCase):
    def post_root(self, payload, **extra):
        return Client().post("/", json.dumps(payload), content_type="application/json", **extra)

    def test_get_keeps_the_existing_home_redirect(self):
        response = Client().get("/")
        self.assertEqual(response.status_code, 302)

    def test_confirmation_is_accepted_recorded_and_logged_once(self):
        payload = c2b("ROOTCALL01")
        for _ in range(2):
            response = self.post_root(payload)
            self.assertEqual((response.status_code, response.json()["ResultCode"]), (200, 0))
        self.assertEqual(MpesaTransaction.objects.filter(trans_id="ROOTCALL01").count(), 1)
        self.assertEqual(MpesaCallbackLog.objects.filter(kind="c2b", outcome="ok").count(), 2)

    def test_validation_style_payload_is_acknowledged_without_recording(self):
        payload = {"TransactionType": "Pay Bill", "BusinessShortCode": "123456",
                   "BillRefNumber": "", "MSISDN": "254700000000"}
        response = self.post_root(payload)
        self.assertEqual((response.status_code, response.json()["ResultCode"]), (200, 0))
        self.assertFalse(MpesaTransaction.objects.exists())

    def test_confirmation_for_another_shortcode_is_rejected(self):
        payload = c2b("ROOTCALL02")
        payload["BusinessShortCode"] = "999999"
        response = self.post_root(payload)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(MpesaTransaction.objects.exists())

    def test_malformed_json_is_rejected_and_body_methods_are_not_allowed(self):
        response = Client().post("/", "{bad", content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Client().put("/", "{}", content_type="application/json").status_code, 405)
