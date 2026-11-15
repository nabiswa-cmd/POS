"""Optional fingerprint (Windows Hello) support: the signing side of the assertion.

The shell asks Windows to verify the person (UserConsentVerifier), and only on success
signs a short-lived assertion with the shared DESKTOP_SHELL_SECRET. The server
(accounts.services) verifies the signature, a 20-second freshness window and a
one-time nonce. No fingerprint data is ever read, stored or sent by this application.

The Windows Hello call itself is added in Phase 6, where it can be tested on real
hardware. Until then `windows_hello_verify()` reports "unavailable" and the interface
keeps using password/PIN, which always works.
"""
import hashlib
import hmac
import secrets
import time


def sign_assertion(username, secret, now=None):
    ts = int(now if now is not None else time.time())
    nonce = secrets.token_hex(16)
    message = f"{username.strip().lower()}|{ts}|{nonce}".encode("utf-8")
    sig = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return {"ts": ts, "nonce": nonce, "sig": sig}


def windows_hello_verify(username):
    """Return True when Windows confirmed the person's fingerprint/face/PIN. Phase 6."""
    return False
