import unittest
from decimal import Decimal

from sales import mpesa_rules as r


class Phone(unittest.TestCase):
    def test_formats(self):
        for raw in ("0712345678", "+254712345678", "254712345678", "0712 345 678", "0112345678"):
            self.assertTrue(r.normalize_phone(raw).startswith("254"))
        self.assertEqual(r.normalize_phone("0712345678"), "254712345678")

    def test_bad(self):
        for raw in ("", "12345", "0612345678", "25471234567", "abc"):
            with self.assertRaises(r.MpesaError):
                r.normalize_phone(raw)

    def test_hint_never_shows_hash(self):
        self.assertEqual(r.phone_hint("254712345678"), "07xx xxx 678")
        self.assertEqual(r.phone_hint("a" * 64), "")


class Names(unittest.TestCase):
    def test_real_name(self):
        self.assertEqual(r.clean_name("JOHN", "", "KAMAU"), "JOHN KAMAU")

    def test_not_names(self):
        for parts in ((), ("",), ("254712345678",), ("null",), ("a" * 64,), ("7",)):
            self.assertEqual(r.clean_name(*parts), "")

    def test_typed_name_wins(self):
        self.assertEqual(r.pick_customer_name("JOHN KAMAU", "Mr Otieno"), "Mr Otieno")
        self.assertEqual(r.pick_customer_name("JOHN KAMAU", ""), "JOHN KAMAU")
        self.assertEqual(r.pick_customer_name("", ""), "")


class Amounts(unittest.TestCase):
    def test_exact_only(self):
        self.assertTrue(r.amount_matches(Decimal("1500.00"), Decimal("1500")))
        self.assertFalse(r.amount_matches(Decimal("1499.00"), Decimal("1500")))
        self.assertFalse(r.amount_matches(Decimal("1501.00"), Decimal("1500")))

    def test_stk_whole_shillings(self):
        self.assertEqual(r.stk_amount(Decimal("1500.00")), 1500)
        with self.assertRaises(r.MpesaError):
            r.stk_amount(Decimal("1500.50"))

    def test_parse(self):
        self.assertEqual(r.parse_amount("1,500"), Decimal("1500.00"))
        for bad in ("", "0", "-5", "x"):
            with self.assertRaises(r.MpesaError):
                r.parse_amount(bad)


class Payloads(unittest.TestCase):
    C2B = {"TransactionType": "Pay Bill", "TransID": "SGL1ABC23D", "TransTime": "20261107201500",
           "TransAmount": "1500.00", "BusinessShortCode": "123456", "BillRefNumber": "",
           "MSISDN": "a" * 64, "FirstName": "JOHN", "MiddleName": "", "LastName": "KAMAU"}

    def test_c2b(self):
        p = r.parse_c2b(self.C2B)
        self.assertEqual((p["trans_id"], p["amount"], p["customer_name"]), ("SGL1ABC23D", Decimal("1500.00"), "JOHN KAMAU"))

    def test_c2b_without_name_is_blank_not_invented(self):
        d = dict(self.C2B, FirstName="", LastName="")
        self.assertEqual(r.parse_c2b(d)["customer_name"], "")

    def test_c2b_rejects_junk(self):
        for d in ({}, dict(self.C2B, TransID=""), dict(self.C2B, TransAmount="0"), "x"):
            with self.assertRaises(r.MpesaError):
                r.parse_c2b(d)

    def stk(self, code=0, receipt="SGL1ABC23D"):
        cb = {"MerchantRequestID": "m1", "CheckoutRequestID": "ws_CO_1", "ResultCode": code, "ResultDesc": "x"}
        if code == 0:
            cb["CallbackMetadata"] = {"Item": [{"Name": "Amount", "Value": 1500.0},
                                               {"Name": "MpesaReceiptNumber", "Value": receipt},
                                               {"Name": "TransactionDate", "Value": 20261107201500},
                                               {"Name": "PhoneNumber", "Value": 254712345678}]}
        return {"Body": {"stkCallback": cb}}

    def test_stk_ok(self):
        p = r.parse_stk_callback(self.stk())
        self.assertTrue(p["ok"]); self.assertEqual(p["trans_id"], "SGL1ABC23D"); self.assertEqual(p["amount"], Decimal("1500.00"))
        self.assertNotIn("customer_name", p)

    def test_stk_failed(self):
        p = r.parse_stk_callback(self.stk(1032))
        self.assertFalse(p["ok"]); self.assertEqual(r.stk_failure_status(1032), "FAILED"); self.assertEqual(r.stk_failure_status(1037), "EXPIRED")

    def test_stk_success_without_receipt_refused(self):
        with self.assertRaises(r.MpesaError):
            r.parse_stk_callback(self.stk(receipt=""))

    def test_stk_junk(self):
        with self.assertRaises(r.MpesaError):
            r.parse_stk_callback({"Body": {}})

    def test_password(self):
        self.assertEqual(r.stk_password("174379", "pass", "20260101000000"), "MTc0Mzc5cGFzczIwMjYwMTAxMDAwMDAw")


if __name__ == "__main__":
    unittest.main()
