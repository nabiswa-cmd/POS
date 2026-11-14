import unittest

from catalog import codes


class ScannerCleaningTests(unittest.TestCase):
    def test_enter_tab_and_wrapper_characters_are_removed(self):
        for raw in ("6001234567890\r", "6001234567890\n", "6001234567890\t", "\x026001234567890\x03", " 6001234567890 "):
            self.assertEqual(codes.from_scanner(raw), "6001234567890", repr(raw))

    def test_symbology_identifier_is_removed_only_when_a_code_follows(self):
        self.assertEqual(codes.from_scanner("]E06001234567890"), "6001234567890")
        self.assertEqual(codes.from_scanner("]C1abc-123"), "ABC-123")
        self.assertEqual(codes.from_scanner("]E0"), "]E0")        # nothing after it: left alone, finds nothing

    def test_case_is_ignored_for_text_codes(self):
        self.assertEqual(codes.from_scanner("tusker-500"), "TUSKER-500")

    def test_empty_input(self):
        for raw in (None, "", "   ", "\r\n"):
            self.assertEqual(codes.lookup_candidates(raw), [])

    def test_upca_and_ean13_twins_are_both_tried_exact_code_first(self):
        self.assertEqual(codes.lookup_candidates("036000291452"), ["036000291452", "0036000291452"])
        self.assertEqual(codes.lookup_candidates("0036000291452"), ["0036000291452", "036000291452"])

    def test_other_lengths_and_text_codes_are_not_guessed(self):
        self.assertEqual(codes.lookup_candidates("6001234567890"), ["6001234567890"])
        self.assertEqual(codes.lookup_candidates("12345"), ["12345"])
        self.assertEqual(codes.lookup_candidates("AB1234567890"), ["AB1234567890"])

    def test_saving_a_code_still_refuses_junk_and_does_not_accept_a_scanner_prefix(self):
        self.assertEqual(codes.validate_text(" abc-12 "), "ABC-12")
        with self.assertRaises(codes.CodeError):
            codes.validate_text("]E0123456")


if __name__ == "__main__":
    unittest.main()
