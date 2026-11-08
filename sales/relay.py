"""M-PESA relay (reconciliation part 2): payments the shop PC missed while it was offline or unreachable.

Safaricom can only call the public VPS. The shop PC *pulls* what the VPS recorded (`manage.py mpesa_pull`, or the
"Pull now" button on the Reconciliation screen). Rules are in relay_rules.py; in short: the shop asks and the VPS
never pushes, pulling twice changes nothing (unique receipt code), amounts are never edited, and a payment whose
amount disagrees with one we already hold is NOT applied: it is written to the audit log and counted as a conflict.
"""
import json
import urllib.error
import urllib.request
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core import audit
from core.models import AuditLog

from . import mpesa_rules as rules
from . import mpesa_services as svc
from . import relay_rules
from .models import MpesaTransaction
from .services import NotAllowed

OK_EVERY = timedelta(minutes=10)          # a "relay is fine" line in the audit log at most this often
FAILED_EVERY = timedelta(minutes=60)      # and a "relay is failing" line at most this often


class RelayError(Exception):
    pass


def cfg():
    return settings.MPESA


def enabled():
    """The shop PC knows where the VPS is and holds the token and secret to ask it."""
    c = cfg()
    return bool(c.get("RELAY_URL") and c.get("RELAY_TOKEN") and c.get("CALLBACK_SECRET"))


# --- VPS side: what we hand to the shop ---------------------------------------------------------------------------

def _row(t):
    return {"id": t.pk, "trans_id": t.trans_id, "source": t.source, "amount": str(t.amount),
            "customer_name": t.customer_name, "payer_ref": t.payer_ref, "shortcode": t.shortcode,
            "bill_ref": t.bill_ref, "trans_time": t.trans_time, "raw": t.raw or {},
            "received_at": t.created_at.isoformat()}


def feed(after_id, limit, hours):
    """Payments recorded in the last `hours`, oldest first, after row `after_id`. `more` says there are further pages."""
    since = timezone.now() - timedelta(hours=hours)
    rows = list(MpesaTransaction.objects.filter(pk__gt=after_id, created_at__gte=since).order_by("pk")[:limit + 1])
    more, rows = len(rows) > limit, rows[:limit]
    return {"ok": True, "rows": [_row(t) for t in rows], "last_id": rows[-1].pk if rows else after_id, "more": more}


# --- shop side: pulling -------------------------------------------------------------------------------------------

def _fetch(after, limit, hours):
    """One page from the VPS. Kept separate so tests can replace it."""
    c = cfg()
    url = (f"{c['RELAY_URL'].rstrip('/')}/api/pay/relay/{c['CALLBACK_SECRET']}/"
           f"?after={int(after)}&limit={int(limit)}&hours={int(hours)}")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {c['RELAY_TOKEN']}", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=c.get("TIMEOUT", 12)) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        raise RelayError("The online server refused the request (check MPESA_RELAY_TOKEN and the callback secret)."
                         if exc.code in (401, 403, 404) else f"The online server answered with an error ({exc.code}).")
    except (urllib.error.URLError, OSError, ValueError):
        raise RelayError("Cannot reach the online server right now (no internet?).")


def apply_row(row):
    """Store one relayed payment. Returns 'new', 'known' or 'conflict'; raises MpesaError for a malformed row."""
    p = relay_rules.parse_row(row)
    stk = None
    if relay_rules.is_stk_payload(p["raw"]):
        stk = rules.parse_stk_callback(p["raw"])
        if not stk["ok"] or stk.get("trans_id") != p["trans_id"] or stk.get("amount") != p["amount"]:
            raise rules.MpesaError("The relayed STK message does not match its own payment row.")
    with transaction.atomic():
        existing = MpesaTransaction.objects.select_for_update().filter(trans_id=p["trans_id"]).first()
        if existing is not None and existing.amount != p["amount"]:
            audit.record("mpesa.relay_conflict", object_type="mpesa", object_ref=p["trans_id"],
                         previous={"amount": str(existing.amount)}, new={"amount": str(p["amount"])},
                         reason="The online server reports a different amount for a receipt code we already hold. Not applied.")
            return "conflict"
        created = existing is None
        if stk is not None:
            svc.record_stk_callback(p["raw"])          # also finishes the shop's own pending phone request, if any
        txn = MpesaTransaction.objects.filter(trans_id=p["trans_id"]).first()
        if txn is None:
            txn = MpesaTransaction.objects.create(
                trans_id=p["trans_id"], source=p["source"], amount=p["amount"], customer_name=p["customer_name"],
                payer_ref=p["payer_ref"], shortcode=p["shortcode"], bill_ref=p["bill_ref"], trans_time=p["trans_time"],
                raw=p["raw"])
        svc._enrich(txn, p["customer_name"], p["payer_ref"], p["shortcode"])
    if created:
        audit.record("mpesa.payment_relayed", object_type="mpesa", object_ref=txn.trans_id,
                     new={"amount": str(txn.amount), "source": txn.source, "named": bool(txn.customer_name)})
    return "new" if created else "known"


def _heartbeat(action, new, every):
    last = AuditLog.objects.filter(action=action).order_by("-created_at").first()
    if last is None or timezone.now() - last.created_at >= every:
        audit.record(action, object_type="mpesa", object_ref="relay", new=new)


def pull(hours=relay_rules.DEFAULT_HOURS, max_pages=20):
    """Fetch what the VPS recorded recently and store what we do not have. Returns counts. Safe to run any number of times."""
    if not enabled():
        raise RelayError("The relay is not set up on this computer (MPESA_RELAY_URL and MPESA_RELAY_TOKEN).")
    totals = {"new": 0, "known": 0, "conflict": 0, "bad": 0}
    after = 0
    try:
        for _ in range(max_pages):
            rows, last_id, more = relay_rules.parse_feed(_fetch(after, relay_rules.DEFAULT_LIMIT, hours))
            for row in rows:
                try:
                    totals[apply_row(row)] += 1
                except rules.MpesaError:
                    totals["bad"] += 1
            if not more or last_id <= after:
                break
            after = last_id
    except (RelayError, rules.MpesaError) as exc:
        _heartbeat("mpesa.relay_failed", {"error": str(exc)[:200]}, FAILED_EVERY)
        raise RelayError(str(exc)) from exc
    if totals["new"] or totals["conflict"] or totals["bad"]:
        audit.record("mpesa.relay_pull", object_type="mpesa", object_ref="relay", new=totals)
    _heartbeat("mpesa.relay_ok", totals, OK_EVERY)
    return totals


def pull_now(user):
    """The manager's button. Same as the command, but only for someone who may reconcile."""
    if not user.can("payments.reconcile"):
        raise NotAllowed("You are not allowed to pull payments.")
    return pull()


def status():
    """For the Reconciliation screen: is the relay set up, and when did it last work?"""
    last_ok = AuditLog.objects.filter(action="mpesa.relay_ok").order_by("-created_at").first()
    last_bad = AuditLog.objects.filter(action="mpesa.relay_failed").order_by("-created_at").first()
    failing = bool(last_bad and (last_ok is None or last_bad.created_at > last_ok.created_at))
    return {"enabled": enabled(), "last_ok": last_ok.created_at if last_ok else None, "failing": failing,
            "error": (last_bad.new_value or {}).get("error", "") if failing else ""}
