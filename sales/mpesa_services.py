"""M-PESA capture and matching. The rules in one place:

* A payment is real ONLY when it arrives from Safaricom's callback with an M-PESA receipt code. A cashier
  typing a code, or showing an SMS or screenshot, never confirms anything.
* One receipt code = one MpesaTransaction (unique in the database) = at most one Payment (one-to-one).
  Safaricom retries callbacks, so every handler is safe to run twice.
* Callbacks can arrive in either order (STK result first, till/C2B message first). Both land on the same
  row, keyed by the receipt code, and a customer name is filled in whenever a message carrying one arrives.
* The cashier can only apply a payment whose amount equals the sale total exactly. Part payments, overpayments
  and refunds are sub-phase 4B.
"""
from datetime import timedelta

from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.utils import timezone

from core import audit

from . import daraja, mpesa_rules as rules
from . import services as sales_services
from .models import MpesaStkRequest, MpesaTransaction, Payment, Sale
from .services import SaleError

RECENT_MINUTES = 90


# --- callbacks (no user, no login: authenticated by the secret in the URL, see mpesa_views) -----------

def record_c2b(payload):
    """A till / paybill payment confirmation. Returns (transaction, created)."""
    p = rules.parse_c2b(payload)
    with transaction.atomic():
        txn, created = MpesaTransaction.objects.get_or_create(
            trans_id=p["trans_id"],
            defaults={"source": MpesaTransaction.C2B, "amount": p["amount"], "customer_name": p["customer_name"],
                      "payer_ref": p["payer_ref"], "shortcode": p["shortcode"], "bill_ref": p["bill_ref"],
                      "trans_time": p["trans_time"], "raw": payload})
        if not created:
            _enrich(txn, p["customer_name"], p["payer_ref"], p["shortcode"])
    if created:
        audit.record("mpesa.payment_received", object_type="mpesa", object_ref=txn.trans_id,
                     new={"amount": str(txn.amount), "source": "C2B", "named": bool(txn.customer_name)})
    return txn, created


def _enrich(txn, name, payer_ref="", shortcode=""):
    """A later message about the same receipt may carry what the first one lacked. Never overwrite, never edit the amount."""
    fields = []
    if name and not txn.customer_name:
        txn.customer_name = name; fields.append("customer_name")
    if payer_ref and not txn.payer_ref:
        txn.payer_ref = payer_ref; fields.append("payer_ref")
    if shortcode and not txn.shortcode:
        txn.shortcode = shortcode; fields.append("shortcode")
    if fields:
        txn.save(update_fields=fields + ["updated_at"])


def _capture_stk_payment(p, payload):
    txn, created = MpesaTransaction.objects.get_or_create(
        trans_id=p["trans_id"],
        defaults={"source": MpesaTransaction.STK, "amount": p["amount"], "payer_ref": p["payer_ref"],
                  "trans_time": p["trans_time"], "raw": payload})
    _enrich(txn, "", p["payer_ref"])
    return txn, created


def record_stk_callback(payload):
    """Result of an STK push. Returns the MpesaStkRequest, or None if we never asked for it (logged)."""
    p = rules.parse_stk_callback(payload)
    with transaction.atomic():
        stk = (MpesaStkRequest.objects.select_for_update().filter(checkout_request_id=p["checkout_request_id"]).first())
        if stk is None:
            audit.record("mpesa.unknown_stk_callback", object_type="mpesa", object_ref=p["checkout_request_id"],
                         new={"code": p["result_code"]})
            if p["ok"]:
                # Safaricom confirmed real money for a request this server did not make (this is the online server
                # relaying for the shop PC). Keep it: it waits in the cashier's list and is relayed to the shop.
                _capture_stk_payment(p, payload)
            return None
        if stk.status == MpesaStkRequest.CONFIRMED:
            return stk                                   # Safaricom retried; nothing more to do
        if stk.status in (MpesaStkRequest.FAILED, MpesaStkRequest.EXPIRED):
            if p["ok"]:
                _, created = _capture_stk_payment(p, payload)
                if created:
                    audit.record("mpesa.stk_late_payment", object_type="mpesa", object_ref=stk.checkout_request_id,
                                 new={"receipt": p["trans_id"], "amount": str(p["amount"])})
            return stk
        stk.result_code, stk.result_desc = p["result_code"], p["result_desc"]
        if p["ok"]:
            txn, _ = _capture_stk_payment(p, payload)
            stk.status, stk.transaction = MpesaStkRequest.CONFIRMED, txn
        else:
            stk.status = rules.stk_failure_status(p["result_code"])
        stk.save()
    audit.record("mpesa.stk_result", object_type="mpesa", object_ref=stk.checkout_request_id,
                 new={"status": stk.status, "code": stk.result_code})
    return stk


def record_status_result(payload):
    """Safaricom's answer to a name lookup. Fills a MISSING name only; never overwrites one, never touches the amount."""
    p = rules.parse_status_result(payload)
    txn = MpesaTransaction.objects.filter(trans_id=p["trans_id"]).first() if p["trans_id"] else None
    if txn is None:
        return None
    if not p["ok"]:
        audit.record("mpesa.name_lookup_failed", object_type="mpesa", object_ref=txn.trans_id, reason=p["desc"])
    elif p["customer_name"] and not txn.customer_name:
        _enrich(txn, p["customer_name"])
        audit.record("mpesa.name_looked_up", object_type="mpesa", object_ref=txn.trans_id, new={"name_found": True})
    return txn


NAME_LOOKUP_AFTER = timedelta(seconds=20)      # let the till's own message (which can carry the name) arrive first
NAME_LOOKUP_TRIES = 2


def lookup_missing_names(limit=3):
    """Ask Safaricom for the name of unused payments that have none. Throttled, bounded and best effort: it never raises,
    so the till is never slowed or broken by it. Returns how many requests Safaricom accepted."""
    try:
        if not daraja.lookup_ready() or not cache.add("mpesa-name-lookup-lock", 1, 15):
            return 0
        now = timezone.now()
        rows = (MpesaTransaction.objects.filter(status=MpesaTransaction.UNMATCHED, customer_name="",
                                                name_lookup_attempts__lt=NAME_LOOKUP_TRIES,
                                                created_at__lte=now - NAME_LOOKUP_AFTER,
                                                created_at__gte=now - timedelta(minutes=RECENT_MINUTES * 2)
                                                ).order_by("created_at")[:limit])
        sent = 0
        for txn in rows:
            if txn.name_lookup_at and now - txn.name_lookup_at < timedelta(seconds=60):
                continue
            txn.name_lookup_attempts += 1
            txn.name_lookup_at = now
            txn.save(update_fields=["name_lookup_attempts", "name_lookup_at", "updated_at"])
            try:
                sent += bool(daraja.transaction_status(txn.trans_id))
            except daraja.DarajaError:
                pass
        return sent
    except Exception:                                                   # noqa: BLE001
        return 0


# --- cashier actions ------------------------------------------------------------------------------

def _cart_total(user, shift):
    sale = sales_services.active_sale(user, shift)
    if sale is None or not sale.items.exists():
        raise SaleError("The sale is empty.")
    total = sales_services.cart_total(sale)
    if total <= 0:
        raise SaleError("The total must be more than zero.")
    return sale, total


def start_stk(user, shift, phone, amount=None):
    """Send a payment request to the customer's phone. `amount` is for a part payment (the rest is paid in
    cash or by another M-PESA); left out, the whole sale total is requested."""
    sales_services._need(user, "sales.make")
    sales_services._need(user, "payments.accept", "You are not allowed to accept payments.")
    sales_services._shift_still_open(shift)
    sale, cart_total = _cart_total(user, shift)
    try:
        number = rules.normalize_phone(phone)
        total = cart_total if amount in (None, "") else rules.parse_amount(amount)
        if total > cart_total:
            raise rules.MpesaError(f"The request (KES {total}) is more than the sale total (KES {cart_total}).")
        whole = rules.stk_amount(total)
    except rules.MpesaError as exc:
        raise SaleError(str(exc)) from exc
    live = MpesaStkRequest.objects.filter(sale=sale, status__in=MpesaStkRequest.OPEN_STATES,
                                          created_at__gte=timezone.now() - timedelta(seconds=90)).first()
    if live:
        raise SaleError("A request was just sent to this customer. Wait for them to enter their PIN or for it to expire.")
    stk = MpesaStkRequest.objects.create(sale=sale, cashier=user, shift=shift, phone=number, amount=total)
    try:
        res = daraja.stk_push(number, whole, reference=f"T{shift.till.number}-{sale.pk}")
    except daraja.DarajaError as exc:
        stk.status, stk.result_desc = MpesaStkRequest.FAILED, str(exc)[:200]
        stk.save(update_fields=["status", "result_desc", "updated_at"])
        raise SaleError(str(exc)) from exc
    stk.merchant_request_id, stk.checkout_request_id = res["merchant_request_id"], res["checkout_request_id"]
    stk.status = MpesaStkRequest.PROCESSING
    stk.save(update_fields=["merchant_request_id", "checkout_request_id", "status", "updated_at"])
    audit.record("mpesa.stk_sent", actor=user, object_type="sale", object_ref=sale.pk, new={"amount": str(total)},
                 till_number=shift.till.number)
    return stk


def stk_status(user, shift, stk_id):
    """What the screen polls. If the callback is late (or lost) we may ask Safaricom, but only to fail/expire."""
    stk = MpesaStkRequest.objects.select_related("transaction").filter(pk=stk_id, cashier=user, shift=shift).first()
    if stk is None:
        raise SaleError("Unknown payment request.")
    if stk.status == MpesaStkRequest.PROCESSING and stk.checkout_request_id and \
            timezone.now() - stk.created_at > timedelta(seconds=25) and daraja.configured():
        try:
            code, desc = daraja.stk_query(stk.checkout_request_id)
        except daraja.DarajaError:
            code = None
        if code not in (None, 0):
            MpesaStkRequest.objects.filter(pk=stk.pk, status=MpesaStkRequest.PROCESSING).update(
                status=rules.stk_failure_status(code), result_code=code, result_desc=desc[:200],
                updated_at=timezone.now())
        elif timezone.now() - stk.created_at > timedelta(minutes=3):
            MpesaStkRequest.objects.filter(pk=stk.pk, status=MpesaStkRequest.PROCESSING).update(
                status=MpesaStkRequest.EXPIRED, result_desc="No answer from the customer's phone.",
                updated_at=timezone.now())
    elif stk.status == MpesaStkRequest.PROCESSING and timezone.now() - stk.created_at > timedelta(minutes=3):
        MpesaStkRequest.objects.filter(pk=stk.pk, status=MpesaStkRequest.PROCESSING).update(
            status=MpesaStkRequest.EXPIRED, result_desc="No answer from the customer's phone.",
            updated_at=timezone.now())
    return MpesaStkRequest.objects.select_related("transaction").get(pk=stk.pk)


def recent_unmatched(user, shift, total=None):
    """Confirmed payments that no sale has used yet (newest first). With `total`, only exact matches."""
    sales_services._need(user, "payments.accept", "You are not allowed to accept payments.")
    lookup_missing_names()
    since = timezone.now() - timedelta(minutes=RECENT_MINUTES)
    qs = MpesaTransaction.objects.filter(status=MpesaTransaction.UNMATCHED, created_at__gte=since).order_by("-created_at")
    if total is not None:
        qs = qs.filter(amount=total)
    return list(qs[:12])


@transaction.atomic
def checkout_mpesa(user, shift, transaction_id):
    """Complete the sale with a payment Safaricom has already confirmed to us."""
    sales_services._need(user, "sales.make")
    sales_services._need(user, "payments.accept", "You are not allowed to accept payments.")
    sales_services._shift_still_open(shift)
    sale, total = _cart_total(user, shift)
    txn = MpesaTransaction.objects.select_for_update().filter(pk=transaction_id).first()
    if txn is None:
        raise SaleError("That payment was not found.")
    if txn.status != MpesaTransaction.UNMATCHED:
        raise SaleError("That M-PESA payment has already been used on another sale.")
    if not rules.amount_matches(txn.amount, total):
        raise SaleError(f"That payment is KES {txn.amount} but this sale is KES {total}. "
                        "Part payments and overpayments are handled in the next update.")
    name = rules.pick_customer_name(txn.customer_name, sale.customer_name)
    done = sales_services.finish_sale(user, shift, payment={
        "method": Payment.MPESA, "reference": txn.trans_id, "mpesa_transaction": txn}, customer_name=name)
    return done


@transaction.atomic
def checkout_split(user, shift, mpesa_ids, cash_tendered=None):
    """Complete the sale from ANY mix: cash only, M-PESA only, or several M-PESA payments plus cash.

    * Every M-PESA payment must be one Safaricom already confirmed to us and nobody has used.
    * M-PESA is applied first; cash is the balance (and the only thing that can give change).
    * All-or-nothing: if anything is wrong, nothing is sold and no M-PESA payment is marked used.
    """
    sales_services._need(user, "sales.make")
    sales_services._need(user, "payments.accept", "You are not allowed to accept payments.")
    sales_services._shift_still_open(shift)
    sale, total = _cart_total(user, shift)

    ids = list(mpesa_ids or [])
    if len(set(ids)) != len(ids):
        raise SaleError("The same M-PESA payment was chosen twice.")
    txns = list(MpesaTransaction.objects.select_for_update().filter(pk__in=ids).order_by("pk"))
    if len(txns) != len(ids):
        raise SaleError("One of the chosen M-PESA payments was not found.")
    for txn in txns:
        if txn.status != MpesaTransaction.UNMATCHED:
            raise SaleError(f"M-PESA payment {txn.trans_id} has already been used on another sale.")

    try:
        plan = sales_services.rules.split_tender(total, [t.amount for t in txns], cash_tendered)
    except sales_services.rules.RuleError as exc:
        raise SaleError(str(exc)) from exc

    parts = [{"method": Payment.MPESA, "reference": t.trans_id, "mpesa_transaction": t, "amount": t.amount} for t in txns]
    if plan["cash_due"] > 0:
        parts.append({"method": Payment.CASH, "amount": plan["cash_due"], "tendered": plan["tendered"],
                      "change_given": plan["change"]})
    parts.sort(key=lambda p: -p["amount"])        # the biggest part first: eTIMS records one payment type per invoice

    named = next((t.customer_name for t in txns if t.customer_name), "")
    name = rules.pick_customer_name(named, sale.customer_name)
    done = sales_services.finish_sale(user, shift, payments=parts, customer_name=name)

    return done, plan
