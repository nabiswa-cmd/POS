"""Framework-free tests. Run from the project root:

    python3 -m unittest discover -s tests_pure -t .
"""
import unittest
from datetime import datetime, timedelta, timezone

from accounts import approval_rules as rules
from accounts import permissions as perms
from accounts.lockout import after_failure, is_locked

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)


class ValidateApproverTests(unittest.TestCase):
    base = dict(requester_id=1, approver_id=2, approver_active=True, approver_can_approve=True, reason="Customer changed mind")

    def test_valid(self):
        rules.validate_approver(**self.base)

    def test_cannot_approve_own_action(self):
        with self.assertRaises(rules.ApprovalError):
            rules.validate_approver(**{**self.base, "approver_id": 1})

    def test_approver_without_permission(self):
        with self.assertRaises(rules.ApprovalError):
            rules.validate_approver(**{**self.base, "approver_can_approve": False})

    def test_inactive_approver(self):
        with self.assertRaises(rules.ApprovalError):
            rules.validate_approver(**{**self.base, "approver_active": False})

    def test_reason_required(self):
        for bad in ("", "   ", None):
            with self.assertRaises(rules.ApprovalError):
                rules.validate_approver(**{**self.base, "reason": bad})

    def test_nobody_logged_in_is_allowed_for_pos_open(self):
        rules.validate_approver(**{**self.base, "requester_id": None})


class TokenTests(unittest.TestCase):
    def setUp(self):
        self.raw, self.hash = rules.new_token()
        self.kw = dict(
            stored_hash=self.hash, presented_hash=rules.hash_token(self.raw),
            expires_at=NOW + timedelta(minutes=2), consumed_at=None, now=NOW,
            action="remove_item", expected_action="remove_item",
            requester_id=7, expected_requester_id=7,
            object_ref="line-3", expected_object_ref="line-3",
        )

    def test_tokens_are_unique_and_only_hash_is_stored(self):
        raw2, hash2 = rules.new_token()
        self.assertNotEqual(self.raw, raw2)
        self.assertNotEqual(self.raw, self.hash)
        self.assertEqual(len(self.hash), 64)

    def test_valid_token(self):
        rules.check_token(**self.kw)

    def test_wrong_token(self):
        self.kw["presented_hash"] = rules.hash_token("guess")
        with self.assertRaises(rules.ApprovalError):
            rules.check_token(**self.kw)

    def test_single_use(self):
        self.kw["consumed_at"] = NOW
        with self.assertRaises(rules.ApprovalError):
            rules.check_token(**self.kw)

    def test_expired(self):
        self.kw["now"] = NOW + timedelta(minutes=2)
        with self.assertRaises(rules.ApprovalError):
            rules.check_token(**self.kw)

    def test_wrong_action(self):
        self.kw["expected_action"] = "refund"
        with self.assertRaises(rules.ApprovalError):
            rules.check_token(**self.kw)

    def test_other_cashier_cannot_reuse(self):
        self.kw["expected_requester_id"] = 8
        with self.assertRaises(rules.ApprovalError):
            rules.check_token(**self.kw)

    def test_bound_to_item(self):
        self.kw["expected_object_ref"] = "line-4"
        with self.assertRaises(rules.ApprovalError):
            rules.check_token(**self.kw)


class LockoutTests(unittest.TestCase):
    def test_locks_after_limit_and_resets_counter(self):
        failed, locked = 0, None
        for i in range(4):
            failed, locked = after_failure(failed, NOW, max_attempts=5, lock_minutes=5)
            self.assertIsNone(locked)
        failed, locked = after_failure(failed, NOW, max_attempts=5, lock_minutes=5)
        self.assertEqual(failed, 0)
        self.assertEqual(locked, NOW + timedelta(minutes=5))
        self.assertTrue(is_locked(locked, NOW + timedelta(minutes=4)))
        self.assertFalse(is_locked(locked, NOW + timedelta(minutes=5)))

    def test_not_locked_when_none(self):
        self.assertFalse(is_locked(None, NOW))


class PermissionCatalogueTests(unittest.TestCase):
    def test_cashier_cannot_do_restricted_things(self):
        cashier = set(perms.DEFAULT_ROLES["Cashier"]["permissions"])
        forbidden = {"products.change_cost", "products.change_price", "stock.adjust",
                     "reports.financial.view", "settings.manage", "audit.view"}
        self.assertFalse(cashier & forbidden)
        self.assertFalse({p for p in cashier if p.startswith("approve.")})

    def test_receiving_clerk_cannot_change_prices_or_see_finance(self):
        clerk = set(perms.DEFAULT_ROLES["Receiving Clerk"]["permissions"])
        self.assertIn("stock.receive", clerk)
        self.assertFalse(clerk & {"products.change_price", "products.change_cost", "approve.refund",
                                  "reports.financial.view", "reports.sales.view"})

    def test_supervisor_is_not_a_manager(self):
        sup = set(perms.DEFAULT_ROLES["Supervisor"]["permissions"])
        self.assertIn("approve.remove_item", sup)
        self.assertIn("approve.refund", sup)
        self.assertFalse(sup & {"staff.manage", "roles.manage", "settings.manage", "audit.view",
                                "reports.financial.view", "products.change_cost", "approve.reopen_shift"})

    def test_every_restricted_action_has_a_known_permission(self):
        for spec in perms.RESTRICTED_ACTIONS.values():
            self.assertIn(spec["permission"], perms.PERMISSIONS)


if __name__ == "__main__":
    unittest.main()
