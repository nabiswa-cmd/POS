"""Relay rules with no Django. Run: python3 -I -m unittest discover -s tests_pure -t ."""
import unittest
from decimal import Decimal as D

from sales import relay_rules as r
from sales.mpesa_rules import MpesaError

GOOD = {"trans_id": "sgl1abc23d", "source": "c2b", "amount": "3500.00", "customer_name": "JOHN KAMAU",
        "payer_ref": "f" * 64, "shortcode": "123456", "bill_ref": "", "trans_time": "20261107201500", "raw": {"TransID": "x"}}


class BearerTests(unittest.TestCase):
    def test_right_token(self):
        self.assertTrue(r.bearer_ok("Bearer abc123", "abc123"))
        self.assertTrue(r.bearer_ok("bearer abc123", "abc123"))

    def test_wrong_missing_or_unconfigured_token_never_passes(self):
        for header, token in (("Bearer nope", "abc123"), ("abc123", "abc123"), ("", "abc123"), (None, "abc123"),
                              ("Bearer ", ""), ("Bearer ", None), ("Basic abc123", "abc123"), ("Bearer abc1234", "abc123")):
            self.assertFalse(r.bearer_ok(header, token), (header, token))


class FeedParamTests(unittest.TestCase):
    def test_defaults_and_bounds(self):
        self.assertEqual(r.feed_params({}), (0, 200, 72))
        self.assertEqual(r.feed_params({"after": "15", "limit": "50", "hours": "6"}), (15, 50, 6))
        self.assertEqual(r.feed_params({"after": "-5", "limit": "100000", "hours": "99999"}), (0, 500, 24 * 14))
        self.assertEqual(r.feed_params({"after": "x", "limit": "0", "hours": ""}), (0, 1, 72))


class RowTests(unittest.TestCase):
    def test_good_row_is_cleaned(self):
        row = r.parse_row(GOOD)
        self.assertEqual((row["trans_id"], row["source"], row["amount"], row["customer_name"]),
                         ("SGL1ABC23D", "C2B", D("3500.00"), "JOHN KAMAU"))

    def test_bad_rows_are_refused(self):
        for change in ({"trans_id": ""}, {"trans_id": "short"}, {"trans_id": "has space 123"}, {"source": "CARD"},
                       {"amount": "0"}, {"amount": "-5"}, {"amount": "abc"}, {"amount": None}):
            with self.assertRaises(MpesaError, msg=str(change)):
                r.parse_row({**GOOD, **change})
        for bad in (None, [], "x", 5):
            with self.assertRaises(MpesaError):
                r.parse_row(bad)

    def test_hash_is_not_a_name_and_raw_must_be_an_object(self):
        row = r.parse_row({**GOOD, "customer_name": "a" * 64, "raw": "junk"})
        self.assertEqual((row["customer_name"], row["raw"]), ("", {}))

    def test_stk_payload_detection(self):
        self.assertTrue(r.is_stk_payload({"Body": {"stkCallback": {}}}))
        for no in ({}, None, {"Body": {}}, {"Body": "x"}, {"TransID": "A"}):
            self.assertFalse(r.is_stk_payload(no))


class FeedTests(unittest.TestCase):
    def test_good_feed(self):
        self.assertEqual(r.parse_feed({"ok": True, "rows": [1], "last_id": "7", "more": 1}), ([1], 7, True))

    def test_bad_feeds(self):
        for bad in (None, [], {"ok": False, "rows": []}, {"ok": True, "rows": "x"}, {"ok": True, "rows": [], "last_id": "zz"}):
            with self.assertRaises(MpesaError):
                r.parse_feed(bad)


if __name__ == "__main__":
    unittest.main()
