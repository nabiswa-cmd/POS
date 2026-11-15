"""Failed-login lockout policy (pure functions, no Django imports)."""
from datetime import timedelta


def is_locked(locked_until, now) -> bool:
    return locked_until is not None and now < locked_until


def after_failure(failed_attempts, now, max_attempts=5, lock_minutes=5):
    """Return (new_failed_attempts, locked_until) after one more failed attempt.

    When the limit is reached the account is locked and the counter resets, so the
    next window of attempts starts fresh once the lock expires.
    """
    failed_attempts += 1
    if failed_attempts >= max_attempts:
        return 0, now + timedelta(minutes=lock_minutes)
    return failed_attempts, None
