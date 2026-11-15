"""Framework-free tests for the Phase 4C helpers: callback address allow-list and relay checks.

    python3 -m unittest discover -s tests_pure -t .
"""
import unittest

from sales import mpesa_rules as r


class AllowListTests(unittest.TestCase):
    def test_parse_accepts_addresses_ranges_and_separators(self):
        nets = r.parse_ip_list("196.201.214.200, 10.1.0.0/16 ; 2001:db8::/32")
        self.assertEqual(len(nets), 3)

    def test_empty_means_no_list(self):
        for raw in ("", "  ", None, " , ,"):
            self.assertEqual(r.parse_ip_list(raw), [])

    def test_a_typo_is_an_error_not_a_silent_hole(self):
        for raw in ("196.201.214.2OO", "10.1.0.0/99", "not-an-ip", "1.2.3.4, banana"):
            with self.assertRaises(r.MpesaError, msg=raw):
                r.parse_ip_list(raw)

    def test_no_list_allows_everyone(self):
        self.assertTrue(r.ip_allowed("8.8.8.8", []))
        self.assertTrue(r.ip_allowed(None, []))

    def test_with_a_list_only_listed_addresses_pass(self):
        nets = r.parse_ip_list("196.201.214.200, 10.1.0.0/16")
        self.assertTrue(r.ip_allowed("196.201.214.200", nets))
        self.assertTrue(r.ip_allowed("10.1.255.9", nets))
        self.assertFalse(r.ip_allowed("196.201.214.201", nets))
        self.assertFalse(r.ip_allowed("10.2.0.1", nets))

    def test_a_missing_or_garbled_address_is_never_allowed_once_a_list_exists(self):
        nets = r.parse_ip_list("10.0.0.0/8")
        for bad in (None, "", "unknown", "10.0.0"):
            self.assertFalse(r.ip_allowed(bad, nets), bad)

    def test_ipv6(self):
        nets = r.parse_ip_list("2001:db8::/32")
        self.assertTrue(r.ip_allowed("2001:db8::1", nets))
        self.assertFalse(r.ip_allowed("2001:db9::1", nets))


class ForwardedTests(unittest.TestCase):
    def test_the_last_entry_is_the_one_our_proxy_added(self):
        self.assertEqual(r.forwarded_ip("6.6.6.6, 196.201.214.200"), "196.201.214.200")
        self.assertEqual(r.forwarded_ip("196.201.214.200"), "196.201.214.200")

    def test_empty(self):
        for raw in ("", None, " , "):
            self.assertEqual(r.forwarded_ip(raw), "")


class RelayChecksTests(unittest.TestCase):
    def test_https_only_except_this_machine(self):
        for ok in ("https://pay.example.com", "https://pay.example.com/", "http://127.0.0.1:8000", "http://localhost"):
            self.assertTrue(r.relay_url_ok(ok), ok)
        for bad in ("", "http://pay.example.com", "ftp://pay.example.com", "pay.example.com", "https://"):
            self.assertFalse(r.relay_url_ok(bad), bad)

    def test_record_shape(self):
        good = {"id": 5, "kind": "c2b", "payload": {"a": 1}}
        self.assertTrue(r.relay_record_ok(good))
        self.assertTrue(r.relay_record_ok({**good, "kind": "stk"}))
        for bad in (None, [], {}, {**good, "id": "5"}, {**good, "id": True}, {**good, "kind": "x"},
                    {**good, "payload": "text"}, {"id": 5, "kind": "c2b"}):
            self.assertFalse(r.relay_record_ok(bad), bad)


if __name__ == "__main__":
    unittest.main()
