"""Phase 4A tests: M-PESA capture, idempotent callbacks, customer name, exact-match checkout."""
import json
from decimal import Decimal as D

from django.test import Client, override_settings

from . import mpesa_services as ms
from .models import MpesaStkRequest, MpesaTransaction, Payment, Sale
from .tests import SalesCase

SECRET = "s3cret-callback-path"
CFG = {"ENV": "sandbox", "CONSUMER_KEY": "", "CONSUMER_SECRET": "", "SHORTCODE": "", "TILL_NUMBER": "", "PASSKEY": "",
       "ACCOUNT_TYPE": "till", "CALLBACK_BASE_URL": "https://pos.example.com", "CALLBACK_SECRET": SECRET, "TIMEOUT": 5}


def c2b(trans_id="SGL1ABC23D", amount="3500.00", first="JOHN", last="KAMAU"):
    return {"TransactionType": "Pay Bill", "TransID": trans_id, "TransTime": "20261107201500", "TransAmount": amount,
            "BusinessShortCode": "123456", "BillRefNumber": "", "MSISDN": "f" * 64,
            "FirstName": first, "MiddleName": "", "LastName": last}


def stk_ok(checkout="ws_CO_1", receipt="SGL1ABC23D", amount=3500):
    return {"Body": {"stkCallback": {"MerchantRequestID": "m1", "CheckoutRequestID": checkout, "ResultCode": 0,
            "ResultDesc": "ok", "CallbackMetadata": {"Item": [
                {"Name": "Amount", "Value": amount}, {"Name": "MpesaReceiptNumber", "Value": receipt},
                {"Name": "TransactionDate", "Value": 20261107201500}, {"Name": "PhoneNumber", "Value": 254712345678}]}}}}


@override_settings(MPESA=CFG)
class MpesaCaptureTests(SalesCase):
    def post(self, kind, payload, secret=SECRET):
        return Client().post(f"/api/pay/{kind}/{secret}/", json.dumps(payload), content_type="application/json")

    def test_wrong_secret_is_404_and_records_nothing(self):
        self.assertEqual(self.post("c2b/confirm", c2b(), secret="nope").status_code, 404)
        self.assertEqual(MpesaTransaction.objects.count(), 0)

    def test_same_callback_twice_records_one_payment(self):
        for _ in range(3):
            self.assertEqual(self.post("c2b/confirm", c2b()).json()["ResultCode"], 0)
        self.assertEqual(MpesaTransaction.objects.count(), 1)

    def test_garbage_is_rejected_not_recorded(self):
        self.assertEqual(self.post("c2b/confirm", {"TransID": ""}).status_code, 400)
        self.assertEqual(MpesaTransaction.objects.count(), 0)

    def test_name_is_kept_and_not_invented(self):
        self.post("c2b/confirm", c2b(first="", last=""))
        self.assertEqual(MpesaTransaction.objects.get().customer_name, "")

    def test_name_arrives_after_stk_result(self):
        self.scan()
        stk = MpesaStkRequest.objects.create(sale=Sale.objects.get(status="OPEN"), cashier=self.cashier, shift=self.shift,
                                             phone="254712345678", amount=D("3500"), checkout_request_id="ws_CO_1",
                                             status=MpesaStkRequest.PROCESSING)
        self.post("stk", stk_ok())
        self.assertEqual(MpesaTransaction.objects.get().customer_name, "")
        self.post("c2b/confirm", c2b())                      # the till message with the name comes second
        txn = MpesaTransaction.objects.get()
        self.assertEqual((txn.customer_name, MpesaTransaction.objects.count()), ("JOHN KAMAU", 1))
        stk.refresh_from_db()
        self.assertEqual((stk.status, stk.transaction_id), (MpesaStkRequest.CONFIRMED, txn.pk))

    def test_stk_retry_does_not_change_anything(self):
        self.scan()
        MpesaStkRequest.objects.create(sale=Sale.objects.get(status="OPEN"), cashier=self.cashier, shift=self.shift,
                                       phone="254712345678", amount=D("3500"), checkout_request_id="ws_CO_1",
                                       status=MpesaStkRequest.PROCESSING)
        for _ in range(2):
            self.post("stk", stk_ok())
        self.assertEqual(MpesaTransaction.objects.count(), 1)

    def test_unknown_failed_stk_callback_creates_no_payment(self):
        failed = stk_ok(checkout="never-asked")
        failed["Body"]["stkCallback"].update(ResultCode=1032, ResultDesc="cancelled")
        failed["Body"]["stkCallback"].pop("CallbackMetadata")
        self.post("stk", failed)
        self.assertEqual(MpesaTransaction.objects.count(), 0)

    def test_unknown_successful_stk_callback_keeps_the_money_waiting_once(self):
        # The online server never made this request (the shop PC did). Real money must not be lost.
        for _ in range(2):
            self.post("stk", stk_ok(checkout="never-asked"))
        txn = MpesaTransaction.objects.get()
        self.assertEqual((txn.status, txn.source, txn.amount), (MpesaTransaction.UNMATCHED, "STK", D("3500.00")))


@override_settings(MPESA=CFG)
class MpesaCheckoutTests(SalesCase):
    def paid(self, amount="3500.00", name="JOHN KAMAU", trans_id="SGL1ABC23D"):
        return MpesaTransaction.objects.create(trans_id=trans_id, source="C2B", amount=D(amount), customer_name=name)

    def test_checkout_uses_confirmed_payment_and_takes_name(self):
        self.scan()
        txn = self.paid()
        sale = ms.checkout_mpesa(self.cashier, self.shift, txn.pk)
        pay = sale.payments.get()
        self.assertEqual((pay.method, pay.reference, pay.amount, sale.customer_name),
                         (Payment.MPESA, "SGL1ABC23D", D("3500.00"), "JOHN KAMAU"))
        txn.refresh_from_db()
        self.assertEqual((txn.status, txn.sale_id), (MpesaTransaction.MATCHED, sale.pk))
        self.assertEqual(self.stock(), 9)

    def test_typed_name_is_not_overwritten(self):
        sale, _ = self.scan()
        from . import services
        services.set_customer(self.cashier, self.shift, "Mr Otieno")
        done = ms.checkout_mpesa(self.cashier, self.shift, self.paid().pk)
        self.assertEqual(done.customer_name, "Mr Otieno")

    def test_one_payment_cannot_pay_two_sales(self):
        self.scan()
        txn = self.paid()
        ms.checkout_mpesa(self.cashier, self.shift, txn.pk)
        self.scan()
        with self.assertRaises(ms.SaleError):
            ms.checkout_mpesa(self.cashier, self.shift, txn.pk)
        self.assertEqual(Payment.objects.count(), 1)

    def test_wrong_amount_is_refused_and_nothing_is_sold(self):
        self.scan()
        for amount in ("3499.00", "3501.00"):
            with self.assertRaises(ms.SaleError):
                ms.checkout_mpesa(self.cashier, self.shift, self.paid(amount, trans_id="X" + amount[:4] + "ABCDEF").pk)
        self.assertEqual((self.stock(), Payment.objects.count()), (10, 0))

    def test_mpesa_is_never_counted_as_cash_in_the_drawer(self):
        from . import services
        self.scan()
        ms.checkout_mpesa(self.cashier, self.shift, self.paid().pk)
        summary = services.shift_summary(self.shift)
        self.assertEqual((summary["cash_sales"], summary["mpesa_sales"]), ("0.00", "3500.00"))
        self.assertEqual(summary["cash_in_drawer_expected"], "5000.00")        # only the opening float

    def test_recent_list_shows_only_unused_payments(self):
        self.scan()
        used, free = self.paid(trans_id="USEDABC123"), self.paid(trans_id="FREEABC123")
        ms.checkout_mpesa(self.cashier, self.shift, used.pk)
        self.scan()
        self.assertEqual([t.trans_id for t in ms.recent_unmatched(self.cashier, self.shift)], ["FREEABC123"])
