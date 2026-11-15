import json
from datetime import timedelta

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts import services
from accounts.approval_rules import ApprovalError
from accounts.models import Approval, ApprovalPolicy, LoginAttempt, User
from core.models import AuditLog


def make_user(username, *, role=None, manager=False, password="Str0ng-pass!", pin=None):
    from accounts.models import Role
    user = User(username=username, is_manager=manager)
    if role:
        user.role = Role.objects.get(name=role)
    user.set_password(password)
    if pin:
        user.set_pin(pin)
    user.save()
    return user


class BaseCase(TestCase):
    def setUp(self):
        call_command("setup_shop", verbosity=0)
        self.manager = make_user("boss", manager=True)
        self.supervisor = make_user("sup", role="Supervisor", pin="4321")
        self.cashier = make_user("cash1", role="Cashier", pin="1111")
        self.cashier2 = make_user("cash2", role="Cashier")
        self.clerk = make_user("clerk", role="Receiving Clerk")

    def post(self, url, payload):
        return self.client.post(url, data=json.dumps(payload), content_type="application/json")


class PermissionTests(BaseCase):
    def test_manager_has_everything(self):
        self.assertTrue(self.manager.can("reports.financial.view"))
        self.assertTrue(self.manager.can("approve.reopen_shift"))

    def test_supervisor_is_not_manager(self):
        self.assertTrue(self.supervisor.can("approve.refund"))
        self.assertTrue(self.supervisor.can("approve.negative_stock_sale"))
        self.assertFalse(self.supervisor.can("staff.manage"))
        self.assertFalse(self.supervisor.can("products.change_cost"))

    def test_cashier_limits(self):
        self.assertTrue(self.cashier.can("sales.make"))
        self.assertFalse(self.cashier.can("products.change_cost"))
        self.assertFalse(self.cashier.can("approve.remove_item"))
        self.assertFalse(self.cashier.can("stock.adjust"))

    def test_receiving_clerk_can_receive_but_not_change_prices(self):
        self.assertTrue(self.clerk.can("stock.receive"))
        self.assertFalse(self.clerk.can("products.change_price"))
        self.assertFalse(self.clerk.can("reports.financial.view"))

    def test_inactive_user_has_no_access(self):
        self.supervisor.is_active = False
        self.supervisor.save()
        self.assertFalse(self.supervisor.can("approve.refund"))

    def test_unknown_permission_code_is_a_programming_error(self):
        with self.assertRaises(ValueError):
            self.manager.can("typo.permission")

    def test_only_managers_are_staff(self):
        self.assertTrue(self.manager.is_staff)
        self.assertFalse(self.supervisor.is_staff)

    def test_pin_and_password_are_hashed(self):
        self.assertNotIn("4321", self.supervisor.pin_hash)
        self.assertNotIn("Str0ng-pass!", self.supervisor.password)


class LoginTests(BaseCase):
    def test_password_and_pin_both_work(self):
        _, method = services.authenticate("sup", "4321", context="t")
        self.assertEqual(method, "PIN")
        _, method = services.authenticate("sup", "Str0ng-pass!", context="t")
        self.assertEqual(method, "PASSWORD")

    def test_lockout_after_repeated_failures_then_correct_password_refused(self):
        for _ in range(5):
            with self.assertRaises(services.AuthError):
                services.authenticate("cash2", "wrong", context="t")
        with self.assertRaises(services.AuthError) as ctx:
            services.authenticate("cash2", "Str0ng-pass!", context="t")
        self.assertIn("locked", str(ctx.exception))
        self.assertGreaterEqual(LoginAttempt.objects.filter(username="cash2", success=False).count(), 6)

    def test_lock_expires(self):
        for _ in range(5):
            with self.assertRaises(services.AuthError):
                services.authenticate("cash2", "wrong", context="t")
        User.objects.filter(username="cash2").update(locked_until=timezone.now() - timedelta(seconds=1))
        user, _ = services.authenticate("cash2", "Str0ng-pass!", context="t")
        self.assertEqual(user.username, "cash2")

    def test_unknown_user_gets_generic_error(self):
        with self.assertRaises(services.AuthError) as ctx:
            services.authenticate("nobody", "x", context="t")
        self.assertEqual(str(ctx.exception), services.GENERIC_FAILURE)

    def test_area_login_checks_permission(self):
        ok = self.post("/api/auth/login/", {"area": "manager", "username": "boss", "secret": "Str0ng-pass!"})
        self.assertEqual(ok.status_code, 200)
        self.client.logout()
        denied = self.post("/api/auth/login/", {"area": "manager", "username": "cash1", "secret": "1111"})
        self.assertEqual(denied.status_code, 403)
        clerk = self.post("/api/auth/login/", {"area": "receiving", "username": "clerk", "secret": "Str0ng-pass!"})
        self.assertEqual(clerk.status_code, 200)

    def test_biometric_is_off_by_default(self):
        with self.assertRaises(services.AuthError):
            services.authenticate("sup", biometric={"ts": 1, "nonce": "n", "sig": "s"}, context="t")

    @override_settings(BIOMETRIC_ENABLED=True, DESKTOP_SHELL_SECRET="shell-secret")
    def test_biometric_assertion_valid_once_only(self):
        from desktop.biometric import sign_assertion
        User.objects.filter(username="sup").update(biometric_enabled=True)
        assertion = sign_assertion("sup", "shell-secret")
        user, method = services.authenticate("sup", biometric=assertion, context="t")
        self.assertEqual(method, "BIOMETRIC")
        with self.assertRaises(services.AuthError):  # replay refused
            services.authenticate("sup", biometric=assertion, context="t")

    @override_settings(BIOMETRIC_ENABLED=True, DESKTOP_SHELL_SECRET="shell-secret")
    def test_biometric_requires_user_opt_in_and_valid_signature(self):
        from desktop.biometric import sign_assertion
        with self.assertRaises(services.AuthError):  # user not opted in
            services.authenticate("sup", biometric=sign_assertion("sup", "shell-secret"), context="t")
        User.objects.filter(username="sup").update(biometric_enabled=True)
        with self.assertRaises(services.AuthError):  # wrong secret
            services.authenticate("sup", biometric=sign_assertion("sup", "other"), context="t")
        with self.assertRaises(services.AuthError):  # stale
            services.authenticate("sup", biometric=sign_assertion("sup", "shell-secret", now=1), context="t")


class ApprovalTests(BaseCase):
    def grant(self, approver="sup", secret="4321", requester=None, action="remove_item", ref="line-1", reason="Wrong item"):
        return services.grant_approval(
            action=action, approver_username=approver, secret=secret, reason=reason,
            requester=requester or self.cashier, object_ref=ref)

    def test_supervisor_can_approve_and_it_is_recorded(self):
        raw, approval = self.grant()
        self.assertEqual(approval.approver, self.supervisor)
        self.assertEqual(approval.requester, self.cashier)
        self.assertEqual(approval.reason, "Wrong item")
        self.assertEqual(approval.method, "PIN")
        self.assertTrue(AuditLog.objects.filter(action="approval.granted", approver=self.supervisor).exists())
        self.assertNotEqual(approval.token_hash, raw)

    def test_cashier_cannot_approve_for_another_cashier(self):
        with self.assertRaises(services.AuthError):
            self.grant(approver="cash2", secret="Str0ng-pass!")
        self.assertTrue(AuditLog.objects.filter(action="approval.denied").exists())

    def test_nobody_can_approve_their_own_action(self):
        with self.assertRaises(services.AuthError):
            self.grant(approver="sup", secret="4321", requester=self.supervisor)

    def test_reason_required(self):
        with self.assertRaises(services.AuthError):
            self.grant(reason="  ")

    def test_wrong_pin_refused(self):
        with self.assertRaises(services.AuthError):
            self.grant(secret="0000")

    def test_token_is_single_use(self):
        raw, _ = self.grant()
        services.consume_approval(raw, action="remove_item", requester=self.cashier, object_ref="line-1")
        with self.assertRaises(ApprovalError):
            services.consume_approval(raw, action="remove_item", requester=self.cashier, object_ref="line-1")

    def test_token_bound_to_item_action_and_cashier(self):
        raw, _ = self.grant()
        with self.assertRaises(ApprovalError):
            services.consume_approval(raw, action="remove_item", requester=self.cashier, object_ref="line-2")
        with self.assertRaises(ApprovalError):
            services.consume_approval(raw, action="refund", requester=self.cashier, object_ref="line-1")
        with self.assertRaises(ApprovalError):
            services.consume_approval(raw, action="remove_item", requester=self.cashier2, object_ref="line-1")
        # still usable by the right caller (failed attempts did not burn it)
        services.consume_approval(raw, action="remove_item", requester=self.cashier, object_ref="line-1")

    def test_expired_token_refused(self):
        raw, approval = self.grant()
        Approval.objects.filter(pk=approval.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        with self.assertRaises(ApprovalError):
            services.consume_approval(raw, action="remove_item", requester=self.cashier, object_ref="line-1")

    def test_garbage_token_refused(self):
        with self.assertRaises(ApprovalError):
            services.require_approval("remove_item", "not-a-token", requester=self.cashier, object_ref="line-1")
        with self.assertRaises(ApprovalError):
            services.require_approval("remove_item", None, requester=self.cashier, object_ref="line-1")

    def test_manager_policy_can_waive_approval_except_for_opening_pos(self):
        ApprovalPolicy.objects.filter(action="remove_item").update(required=False)
        self.assertIsNone(services.require_approval("remove_item", None, requester=self.cashier))
        ApprovalPolicy.objects.filter(action="open_pos").update(required=False)
        self.assertTrue(services.is_required("open_pos"))

    def test_approval_request_endpoint_flow(self):
        self.client.force_login(self.cashier)
        res = self.post("/api/approvals/request/", {
            "action": "remove_item", "approver_username": "sup", "secret": "4321",
            "reason": "Customer changed mind", "object_ref": "line-9"})
        self.assertEqual(res.status_code, 200)
        token = res.json()["token"]
        services.require_approval("remove_item", token, requester=self.cashier, object_ref="line-9")

    def test_approval_request_requires_login(self):
        res = self.post("/api/approvals/request/", {"action": "remove_item"})
        self.assertEqual(res.status_code, 401)

    def test_approvals_cannot_be_deleted(self):
        _, approval = self.grant()
        with self.assertRaises(PermissionError):
            approval.delete()


class AuditLogTests(BaseCase):
    def test_audit_entries_are_immutable(self):
        from core import audit
        entry = audit.record("test.action", actor=self.manager, reason="x")
        entry.reason = "changed"
        with self.assertRaises(PermissionError):
            entry.save()
        with self.assertRaises(PermissionError):
            entry.delete()

    def test_login_history_is_immutable(self):
        services.authenticate("sup", "4321", context="t")
        attempt = LoginAttempt.objects.first()
        with self.assertRaises(PermissionError):
            attempt.delete()
