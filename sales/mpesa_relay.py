"""The offline relay: how a payment made while the shop's internet was down still arrives.

Safaricom can only call a public HTTPS address, which is your online (VPS) server. That server keeps a raw log of every
well-formed callback (MpesaCallbackLog) and offers it at /api/pay/relay/export/ to anyone holding MPESA_RELAY_TOKEN.
The shop PC runs `python manage.py mpesa_pull` (Windows Task Scheduler, every minute). It reads the log from where it
stopped last time and replays each message through the same handlers the live callbacks use, so:

* it is safe to run twice, to run late, or while Safaricom is also calling the shop directly: a payment has one unique
  receipt code, so replaying a message changes nothing;
* if the shop was offline for a day, everything that arrived meanwhile appears on the next pull, in order;
* the position only moves forward after a message has been applied. A crash or a lost connection means "try again".
"""
from django.db import transaction

from core import audit

from . import mpesa_rules as rules
from . import mpesa_services as svc
from .models import MpesaCallbackLog, MpesaRelayCursor

EXPORT_LIMIT = 200


def log_callback(kind, payload, ip, outcome):
    return MpesaCallbackLog.objects.create(kind=kind, payload=payload, source_ip=ip or None, outcome=outcome[:200])


def export_after(after_id):
    """What the online server hands out: accepted messages after `after_id`, oldest first."""
    rows = MpesaCallbackLog.objects.filter(id__gt=after_id, outcome="ok").order_by("id")[:EXPORT_LIMIT]
    return [{"id": r.pk, "kind": r.kind, "payload": r.payload, "received_at": r.received_at.isoformat()} for r in rows]


def apply_record(rec):
    """Replay one exported message here. Returns 'applied', or 'skipped' for something we cannot use."""
    if not rules.relay_record_ok(rec):
        return "skipped"
    try:
        if rec["kind"] == MpesaCallbackLog.C2B:
            svc.record_c2b(rec["payload"])
        else:
            svc.record_stk_callback(rec["payload"])
    except rules.MpesaError as exc:
        audit.record("mpesa.relay_skipped", object_type="mpesa", object_ref=rec["id"], reason=str(exc)[:200])
        return "skipped"
    return "applied"


def pull(fetch, max_rounds=50):
    """Catch up with the online server. `fetch(after_id)` returns the export as a dict (the command supplies one that
    does the HTTPS call; tests supply a fake). Returns {'applied': n, 'skipped': n, 'position': id}."""
    cursor = MpesaRelayCursor.load()
    applied = skipped = 0
    for _ in range(max_rounds):
        data = fetch(cursor.last_id)
        records = [r for r in (data.get("records") or []) if isinstance(r, dict) and isinstance(r.get("id"), int)]
        records = sorted((r for r in records if r["id"] > cursor.last_id), key=lambda r: r["id"])
        if not records:
            break
        for rec in records:
            with transaction.atomic():                      # the message and the new position are saved together
                result = apply_record(rec)
                cursor.last_id = rec["id"]
                cursor.save(update_fields=["last_id", "updated_at"])
            applied += result == "applied"
            skipped += result == "skipped"
    return {"applied": applied, "skipped": skipped, "position": cursor.last_id}
