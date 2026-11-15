"""Pure rules for supervisor/manager approvals (no Django imports).

An approval is a short-lived, single-use token that is bound to one action, one
requester and one object (e.g. a cart line). Servers must demand a valid token for
every restricted action, so hiding or closing a dialog in the UI grants nothing.
"""
import hashlib
import hmac
import secrets


class ApprovalError(Exception):
    """Raised when an approval is missing, invalid or not permitted."""


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def new_token():
    """Return (raw_token, sha256_hash). Only the hash is ever stored."""
    raw = secrets.token_urlsafe(32)
    return raw, hash_token(raw)


def validate_approver(*, requester_id, approver_id, approver_active, approver_can_approve, reason):
    """Check who is approving. requester_id may be None (nobody logged in yet)."""
    if not reason or not str(reason).strip():
        raise ApprovalError("A reason is required.")
    if not approver_active:
        raise ApprovalError("Approver account is not active.")
    if requester_id is not None and requester_id == approver_id:
        raise ApprovalError("You cannot approve your own action.")
    if not approver_can_approve:
        raise ApprovalError("This user is not allowed to approve that action.")


def check_token(
    *,
    stored_hash,
    presented_hash,
    expires_at,
    consumed_at,
    now,
    action,
    expected_action,
    requester_id,
    expected_requester_id,
    object_ref,
    expected_object_ref,
):
    """Validate a presented token against its stored approval. Raises ApprovalError."""
    if not hmac.compare_digest(stored_hash, presented_hash):
        raise ApprovalError("Invalid approval.")
    if consumed_at is not None:
        raise ApprovalError("This approval has already been used.")
    if now >= expires_at:
        raise ApprovalError("This approval has expired.")
    if action != expected_action:
        raise ApprovalError("This approval is for a different action.")
    if requester_id != expected_requester_id:
        raise ApprovalError("This approval was issued to a different user.")
    if (object_ref or "") != (expected_object_ref or ""):
        raise ApprovalError("This approval is for a different item.")
