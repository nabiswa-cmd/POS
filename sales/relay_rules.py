"""Rules for the M-PESA relay, with no Django in them.

The picture: Safaricom can only call a public HTTPS address (the VPS). The shop PC is usually behind a router and
sometimes offline. So the VPS records every confirmed payment, and the shop PC *pulls* them (`manage.py mpesa_pull`).

* The shop asks; the VPS never pushes into the shop. Nothing on the shop is reachable from the internet.
* Pulling is idempotent: the unique M-PESA receipt code means asking for the same payment twice changes nothing,
  so the shop simply re-reads the last few days every time and never needs to remember where it stopped.
* A pulled payment is only ever what Safaricom told the VPS. The shop never invents or edits one.
"""
import hmac
import re

from .mpesa_rules import MpesaError, clean_name, parse_amount

DEFAULT_HOURS, MAX_HOURS = 72, 24 * 14
DEFAULT_LIMIT, MAX_LIMIT = 200, 500
SOURCES = ("C2B", "STK")


def bearer_ok(header, token):
    """True only for `Authorization: Bearer <token>` with the right token. An empty configured token never matches."""
    if not token or not header:
        return False
    scheme, _, supplied = str(header).partition(" ")
    return scheme.lower() == "bearer" and hmac.compare_digest(supplied.strip().encode(), str(token).encode())


def _int(raw, default, low, high):
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value))


def feed_params(query):
    """GET parameters of the feed -> (after_id, limit, hours), always inside safe bounds."""
    return (_int(query.get("after"), 0, 0, 2**62),
            _int(query.get("limit"), DEFAULT_LIMIT, 1, MAX_LIMIT),
            _int(query.get("hours"), DEFAULT_HOURS, 1, MAX_HOURS))


def is_stk_payload(raw):
    return isinstance(raw, dict) and isinstance(raw.get("Body"), dict) and "stkCallback" in raw["Body"]


def parse_row(row):
    """One feed row from the VPS -> clean dict, or MpesaError. Never trusts the shape of what it is given."""
    if not isinstance(row, dict):
        raise MpesaError("Relay row is not an object.")
    trans_id = str(row.get("trans_id") or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{8,14}", trans_id):
        raise MpesaError("Relay row has an invalid receipt code.")
    source = str(row.get("source") or "").strip().upper()
    if source not in SOURCES:
        raise MpesaError("Relay row has an unknown source.")
    raw = row.get("raw")
    return {
        "trans_id": trans_id, "source": source, "amount": parse_amount(row.get("amount")),
        "customer_name": clean_name(row.get("customer_name")),
        "payer_ref": str(row.get("payer_ref") or "")[:80], "shortcode": str(row.get("shortcode") or "")[:12],
        "bill_ref": str(row.get("bill_ref") or "")[:60], "trans_time": str(row.get("trans_time") or "")[:14],
        "raw": raw if isinstance(raw, dict) else {},
    }


def parse_feed(body):
    """Whole feed reply -> (rows, last_id, more). Raises MpesaError if it is not a feed."""
    if not isinstance(body, dict) or body.get("ok") is not True or not isinstance(body.get("rows"), list):
        raise MpesaError("The relay did not answer with a payment list.")
    try:
        last_id, more = int(body.get("last_id") or 0), bool(body.get("more"))
    except (TypeError, ValueError):
        raise MpesaError("The relay answer has a bad position marker.")
    return body["rows"], last_id, more
