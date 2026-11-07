"""Authentication and approval services. All approval enforcement lives here (server side)."""
import hashlib
import hmac
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from core import audit

from . import approval_rules as rules
from . import lockout
from .models import Approval, ApprovalPolicy, LoginAttempt
from .permissions import METHOD_BIOMETRIC, METHOD_PASSWORD, METHOD_PIN, RESTRICTED_ACTIONS

User = get_user_model()

GENERIC_FAILURE = "Invalid username or credentials."
BIOMETRIC_WINDOW_SECONDS = 20


class AuthError(Exception):
    """Authentication or approval was refused. The message is safe to show the user."""


def _log_attempt(username, user, success, method, context, ip):
    LoginAttempt.objects.create(username=(username or "")[:150], user=user, success=success,
                                method=method or "", context=context or "", ip_address=ip or None)


def _check_secret(username, secret, context, ip):
    """Return (user, method, error_message). Never raises, so failure counters are always saved."""
    username = (username or "").strip()
    secret = secret or ""
    with transaction.atomic():
        user = User.objects.select_for_update().filter(username__iexact=username).first()
        now = timezone.now()
        if user is None or not user.is_active:
            _log_attempt(username, user, False, "", context, ip)
            return None, None, GENERIC_FAILURE
        if lockout.is_locked(user.locked_until, now):
            _log_attempt(username, user, False, "", context, ip)
            return None, None, "This account is temporarily locked. Try again later."
        method = None
        if secret:
            if secret.isdigit() and user.check_pin(secret):
                method = METHOD_PIN
            elif user.check_password(secret):
                method = METHOD_PASSWORD
        if method is None:
            user.failed_attempts, user.locked_until = lockout.after_failure(
                user.failed_attempts, now, settings.LOCKOUT_MAX_ATTEMPTS, settings.LOCKOUT_MINUTES)
            user.save(update_fields=["failed_attempts", "locked_until"])
            _log_attempt(username, user, False, "", context, ip)
            return None, None, GENERIC_FAILURE
        if user.failed_attempts or user.locked_until:
            user.failed_attempts, user.locked_until = 0, None
            user.save(update_fields=["failed_attempts", "locked_until"])
        _log_attempt(username, user, True, method, context, ip)
        return user, method, None


def biometric_signature(username, timestamp, nonce, secret):
    """Signature the desktop shell produces after a successful Windows Hello check."""
    message = f"{username.strip().lower()}|{int(timestamp)}|{nonce}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def _check_biometric(username, assertion, context, ip):
    """Return (user, method, error_message). Optional feature; off unless configured."""
    if not (settings.BIOMETRIC_ENABLED and settings.DESKTOP_SHELL_SECRET):
        return None, None, "Fingerprint sign-in is not enabled."
    try:
        ts = int(assertion["ts"])
        nonce = str(assertion["nonce"])
        sig = str(assertion["sig"])
    except (KeyError, TypeError, ValueError):
        return None, None, GENERIC_FAILURE
    expected = biometric_signature(username or "", ts, nonce, settings.DESKTOP_SHELL_SECRET)
    now = timezone.now()
    fresh = abs(now.timestamp() - ts) <= BIOMETRIC_WINDOW_SECONDS
    user = User.objects.filter(username__iexact=(username or "").strip(), is_active=True).first()
    ok = (
        fresh
        and hmac.compare_digest(expected, sig)
        and user is not None
        and user.biometric_enabled
        and cache.add(f"bio-nonce:{nonce}", 1, timeout=BIOMETRIC_WINDOW_SECONDS * 3)  # replay guard
    )
    _log_attempt(username, user, bool(ok), METHOD_BIOMETRIC if ok else "", context, ip)
    if not ok:
        return None, None, GENERIC_FAILURE
    return user, METHOD_BIOMETRIC, None


def authenticate(username, secret=None, biometric=None, *, context, ip=None):
    """Verify a person by password/PIN, or by a fingerprint assertion when enabled."""
    if biometric:
        user, method, error = _check_biometric(username, biometric, context, ip)
    else:
        user, method, error = _check_secret(username, secret, context, ip)
    if error:
        raise AuthError(error)
    return user, method


def is_required(action):
    """Does this action need approval? Opening the POS always does; others follow the manager's policy."""
    if action not in RESTRICTED_ACTIONS:
        raise ValueError(f"Unknown restricted action: {action}")
    if action == "open_pos":
        return True
    policy = ApprovalPolicy.objects.filter(action=action).first()
    return True if policy is None else policy.required


def is_exempt(user):
    """The shop owner (an active manager) needs no second person's approval for anything.

    Their restricted actions are still written to the audit log with their name. Everyone else,
    including supervisors, always follows the approval policy.
    """
    return bool(user is not None and getattr(user, "is_manager", False) and getattr(user, "is_active", False))


def needs_approval(user, action):
    """Does THIS user need someone else's approval for this action right now?"""
    return (not is_exempt(user)) and is_required(action)


def grant_approval(*, action, approver_username, secret=None, biometric=None, reason, requester=None,
                   object_ref="", till_number=None, detail=None, ip=None):
    """Authenticate an approver and issue a single-use approval. Returns (raw_token, Approval)."""
    if action not in RESTRICTED_ACTIONS:
        raise AuthError("Unknown action.")
    approver, method = authenticate(approver_username, secret, biometric, context="approval", ip=ip)
    try:
        rules.validate_approver(
            requester_id=getattr(requester, "pk", None),
            approver_id=approver.pk,
            approver_active=approver.is_active,
            approver_can_approve=approver.can(RESTRICTED_ACTIONS[action]["permission"]),
            reason=reason,
        )
    except rules.ApprovalError as exc:
        audit.record("approval.denied", actor=requester, approver=approver, object_type=action,
                     object_ref=object_ref, reason=str(exc), ip=ip, till_number=till_number)
        raise AuthError(str(exc)) from exc

    minutes = settings.POS_AUTH_MINUTES if action == "open_pos" else settings.APPROVAL_TOKEN_MINUTES
    raw, token_hash = rules.new_token()
    with transaction.atomic():
        approval = Approval.objects.create(
            action=action, requester=requester, approver=approver, method=method,
            reason=reason.strip(), object_ref=object_ref or "", till_number=till_number,
            detail=detail or {}, token_hash=token_hash,
            expires_at=timezone.now() + timedelta(minutes=minutes),
        )
        audit.record("approval.granted", actor=requester, approver=approver, object_type=action,
                     object_ref=object_ref, reason=reason.strip(), ip=ip, till_number=till_number,
                     new={"method": method, "approval_id": approval.pk})
    return raw, approval


@transaction.atomic
def consume_approval(raw_token, *, action, requester, object_ref=""):
    """Validate and use up an approval token. Raises approval_rules.ApprovalError if not valid."""
    token_hash = rules.hash_token(raw_token or "")
    approval = Approval.objects.select_for_update().filter(token_hash=token_hash).first()
    if approval is None:
        raise rules.ApprovalError("Invalid approval.")
    now = timezone.now()
    rules.check_token(
        stored_hash=approval.token_hash, presented_hash=token_hash,
        expires_at=approval.expires_at, consumed_at=approval.consumed_at, now=now,
        action=approval.action, expected_action=action,
        requester_id=approval.requester_id, expected_requester_id=getattr(requester, "pk", None),
        object_ref=approval.object_ref, expected_object_ref=object_ref,
    )
    approval.consumed_at = now
    approval.save(update_fields=["consumed_at"])
    return approval


def require_approval(action, raw_token, *, requester, object_ref=""):
    """Gate for restricted actions. Returns the consumed Approval, or None if policy waives approval.

    Every endpoint that performs a restricted action must call this before acting. The owner (manager)
    is exempt: nothing is consumed and the caller records the action under the owner's own name.
    """
    if not needs_approval(requester, action):
        return None
    return consume_approval(raw_token, action=action, requester=requester, object_ref=object_ref)
