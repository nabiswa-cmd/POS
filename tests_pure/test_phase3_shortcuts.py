import unittest

from core import shortcuts as sc


class ShortcutRuleTests(unittest.TestCase):
    def test_defaults_are_valid_and_unique(self):
        merged, errors = sc.merge({})
        self.assertEqual(errors, [])
        self.assertEqual(len(set(merged.values())), len(merged))
        self.assertEqual((merged["complete"], merged["hold"], merged["supervisor"]), ("F9", "F5", "F12"))

    def test_normalising(self):
        self.assertEqual(sc.normalize(" f9 "), "F9")
        self.assertEqual(sc.normalize("alt+c"), "Alt+C")
        self.assertEqual(sc.normalize("shift+ctrl+f9"), "Ctrl+Shift+F9")

    def test_plain_letters_and_digits_are_refused_because_they_are_used_when_scanning(self):
        for key in ("A", "7", "Shift+A"):
            with self.assertRaises(sc.ShortcutError):
                sc.normalize(key)
        self.assertEqual(sc.normalize("Ctrl+7"), "Ctrl+7")

    def test_keys_that_must_keep_their_job_or_belong_to_windows_are_refused(self):
        for key in ("Escape", "Enter", "Tab", "ArrowUp", "Ctrl+W", "Alt+F4", "Ctrl+P", "F13", "Ctrl+Ctrl+A", ""):
            with self.assertRaises(sc.ShortcutError, msg=key):
                sc.normalize(key)

    def test_two_actions_cannot_share_a_key(self):
        merged, errors = sc.merge({"hold": "F9"})
        self.assertTrue(errors)
        self.assertEqual(merged, sc.DEFAULTS)

    def test_swapping_two_keys_is_allowed(self):
        merged, errors = sc.merge({"hold": "F6", "retrieve": "F5"})
        self.assertEqual((errors, merged["hold"], merged["retrieve"]), ([], "F6", "F5"))

    def test_unknown_action_and_wrong_type_fall_back(self):
        self.assertTrue(sc.merge({"fly": "F2"})[1])
        self.assertEqual(sc.effective(["F2"]), sc.DEFAULTS)
        self.assertEqual(sc.effective(None), sc.DEFAULTS)


if __name__ == "__main__":
    unittest.main()
