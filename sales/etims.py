"""Sending completed sales to KRA eTIMS, offline-first.

A sale is never held up by eTIMS. At checkout an EtimsSubmission row is created (PENDING); right after
the sale is saved we try to send it, and anything that cannot be sent (no internet, unit busy) is retried
by `submit_pending()` after later sales and by `manage.py etims_retry`. Only data the KRA unit really
returned is ever stored or printed.

Integration choice (see README): VSCU is a small KRA program that runs on the shop PC and talks to KRA
for you (it tolerates short internet outages); OSCU talks to KRA online every time. Both are plain JSON
over HTTP, which is all this module does. ETIMS_MODE=off (the default) disables everything here.
"""
import json
from decimal import Decimal
import logging
import urllib.error
import urllib.request

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from catalog.models import CodeSequence
from core import audit
from core.models import ShopSettings

from . import etims_rules
from .models import EtimsCreditNote, EtimsSubmission, Sale

log = logging.getLogger(__name__)

ENDPOINT = "saveTrnsSalesOsdc"


class EtimsError(Exception):
    """The unit could not be reached or answered badly. The message is safe to show staff."""


def enabled():
    return settings.ETIMS_MODE in ("sandbox", "live")


def config_problems():
    """What is missing before eTIMS can work. Shown on the manager screen; empty list means ready."""
    problems = []
    if not enabled():
        return ["eTIMS is switched off (ETIMS_MODE=off)."]
    if not ShopSettings.load().kra_pin:
        problems.append("Shop settings has no KRA PIN.")
    if not settings.ETIMS_BASE_URL:
        problems.append("ETIMS_BASE_URL is not set.")
    if settings.ETIMS_INTEGRATION == "oscu" and not settings.ETIMS_COMM_KEY:
        problems.append("ETIMS_COMM_KEY is not set (OSCU needs it).")
    return problems


class HttpUnit:
    """Talks JSON to the eTIMS unit (the local VSCU, or KRA's OSCU endpoint)."""

    def post(self, endpoint, payload):
        url = settings.ETIMS_BASE_URL.rstrip("/") + "/" + endpoint
        headers = {"Content-Type": "application/json"}
        if settings.ETIMS_INTEGRATION == "oscu":
            headers.update({"tin": payload.get("tin") or "", "bhfId": settings.ETIMS_BRANCH_ID,
                            "cmcKey": settings.ETIMS_COMM_KEY})
        request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=settings.ETIMS_TIMEOUT_SECONDS) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise EtimsError(f"The eTIMS unit answered HTTP {exc.code}.") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise EtimsError("Cannot reach the eTIMS unit (offline or not running).") from exc
        except ValueError as exc:
            raise EtimsError("The eTIMS unit sent an answer that could not be read.") from exc


# Tests replace this with a stand-in unit. Production always uses the real HttpUnit.
unit_factory = HttpUnit


def create_submission(sale):
    """Called inside the checkout transaction so the invoice number is gapless with the sale."""
    if not enabled():
        return None
    return EtimsSubmission.objects.create(sale=sale, invoice_no=CodeSequence.next("etims-invoice"))


def _payload(sale, submission):
    shop = ShopSettings.load()
    items = []
    for it in sale.items.select_related("product"):
        items.append({
            "name": it.product_name, "code": it.product.sku, "class_code": it.product.etims_item_class_code,
            "quantity": it.quantity, "unit_price": it.unit_price, "discount": it.discount,
            "tax_class": it.tax_class, "tax_rate": it.tax_rate, "taxable": it.taxable_amount,
            "tax": it.tax_amount, "total": it.line_total})
    return etims_rules.build_sale_payload(
        tin=shop.kra_pin, branch_id=settings.ETIMS_BRANCH_ID, invoice_no=submission.invoice_no,
        receipt_type=submission.receipt_type, sale_time=timezone.localtime(sale.completed_at),
        customer_name=sale.customer_name, customer_pin=sale.customer_pin, items=items,
        payment_method=(sale.payments.first().method if sale.payments.exists() else "CASH"),
        user_label=sale.cashier.username)


def _missing_for_sale(sale):
    shop = ShopSettings.load()
    if not shop.kra_pin:
        return "Shop settings has no KRA PIN."
    blank = [it.product_name for it in sale.items.select_related("product") if not it.product.etims_item_class_code]
    if blank:
        return "No eTIMS item class code on: " + ", ".join(sorted(set(blank))[:3])
    return ""


def submit(sale_id):
    """Try to get one sale signed. Safe to call twice (two tills): the second call waits, then sees SUBMITTED."""
    if not enabled():
        return None
    with transaction.atomic():
        sub = (EtimsSubmission.objects.select_for_update().select_related("sale__cashier")
               .filter(sale_id=sale_id).first())
        if sub is None or sub.status == EtimsSubmission.SUBMITTED:
            return sub
        sale = Sale.objects.select_related("cashier").get(pk=sale_id)
        sub.attempts += 1
        sub.last_attempt_at = timezone.now()
        problem = config_problems() or _missing_for_sale(sale)
        if problem:
            sub.last_error = (problem if isinstance(problem, str) else problem[0])[:300]
            sub.save()
            return sub
        try:
            result = etims_rules.parse_response(unit_factory().post(ENDPOINT, _payload(sale, sub)))
        except EtimsError as exc:
            sub.last_error = str(exc)[:300]          # stays PENDING: retried later, the sale is untouched
            sub.save()
            return sub
        if not result["ok"]:
            sub.status = EtimsSubmission.REJECTED
            sub.last_error = f"KRA {result['code']}: {result['message']}"[:300]
            sub.save()
            audit.record("etims.rejected", object_type="sale", object_ref=sale.number, reason=sub.last_error,
                         till_number=sale.till.number)
            return sub
        data = result["data"]
        shop = ShopSettings.load()
        sub.status, sub.submitted_at, sub.last_error = EtimsSubmission.SUBMITTED, timezone.now(), ""
        sub.scu_id = str(data.get("sdcId", ""))[:40]
        sub.scu_receipt_no = str(data.get("rcptNo", ""))[:20]
        sub.scu_total_receipt_no = str(data.get("totRcptNo", ""))[:20]
        sub.scu_datetime = str(data.get("vsdcRcptPbctDate", ""))[:20]
        sub.internal_data = str(data.get("intrlData", ""))[:200]
        sub.signature = str(data.get("rcptSign", ""))[:200]
        sub.qr_text = etims_rules.qr_text(settings.ETIMS_QR_URL_TEMPLATE, shop.kra_pin,
                                          settings.ETIMS_BRANCH_ID, sub.signature)[:400]
        sub.save()
        audit.record("etims.signed", object_type="sale", object_ref=sale.number,
                     new={"invoice_no": sub.invoice_no, "scu_receipt_no": sub.scu_receipt_no},
                     till_number=sale.till.number)
        return sub


def submit_pending(limit=5):
    """Retry the oldest waiting sales, then waiting credit notes. Rejected ones are not retried automatically:
    someone must look."""
    if not enabled():
        return 0
    done = 0
    for sale_id in list(EtimsSubmission.objects.filter(status=EtimsSubmission.PENDING)
                        .order_by("id").values_list("sale_id", flat=True)[:limit]):
        sub = submit(sale_id)
        done += 1 if sub and sub.status == EtimsSubmission.SUBMITTED else 0
    for refund_id in list(EtimsCreditNote.objects.filter(status=EtimsCreditNote.PENDING)
                          .order_by("id").values_list("refund_id", flat=True)[:limit]):
        note = submit_credit_note(refund_id)
        done += 1 if note and note.status == EtimsCreditNote.SUBMITTED else 0
    return done


def counts():
    """Signed / waiting / rejected, sales invoices and credit notes together."""
    from django.db.models import Count
    out = {}
    for model in (EtimsSubmission, EtimsCreditNote):
        for r in model.objects.values("status").annotate(n=Count("id")):
            out[r["status"]] = out.get(r["status"], 0) + r["n"]
    return out


# --- credit notes (refunds) ----------------------------------------------------------------------

def create_credit_note(refund):
    """Called inside the approval transaction, so the number is gapless with the approval.

    Only a sale that was itself put through eTIMS can be credited. If it never was (eTIMS was off when it was
    sold) there is no KRA invoice to refer to, so nothing is created and the caller records that fact."""
    if not enabled():
        return None
    original = EtimsSubmission.objects.filter(sale_id=refund.sale_id).first()
    if original is None:
        return None
    return EtimsCreditNote.objects.create(refund=refund, invoice_no=CodeSequence.next("etims-invoice"),
                                          original_invoice_no=original.invoice_no)


def _credit_payload(refund, note):
    shop = ShopSettings.load()
    sale = refund.sale
    items = []
    for it in refund.items.select_related("sale_item__product"):
        sold = it.sale_item
        gross = etims_rules.money(sold.unit_price * it.quantity)
        items.append({
            "name": it.product_name, "code": sold.product.sku, "class_code": sold.product.etims_item_class_code,
            "quantity": it.quantity, "unit_price": sold.unit_price,
            "discount": max(gross - it.amount, Decimal("0.00")),           # so that gross - discount = what is credited
            "tax_class": it.tax_class, "tax_rate": it.tax_rate, "taxable": it.taxable_amount,
            "tax": it.tax_amount, "total": it.amount})
    return etims_rules.build_sale_payload(
        tin=shop.kra_pin, branch_id=settings.ETIMS_BRANCH_ID, invoice_no=note.invoice_no, receipt_type="R",
        sale_time=timezone.localtime(refund.approved_at or refund.requested_at), customer_name=sale.customer_name,
        customer_pin=sale.customer_pin, items=items, payment_method=refund.method,
        user_label=refund.requested_by.username, original_invoice_no=note.original_invoice_no)


def _missing_for_credit(refund):
    shop = ShopSettings.load()
    if not shop.kra_pin:
        return "Shop settings has no KRA PIN."
    blank = [i.product_name for i in refund.items.select_related("sale_item__product")
             if not i.sale_item.product.etims_item_class_code]
    if blank:
        return "No eTIMS item class code on: " + ", ".join(sorted(set(blank))[:3])
    return ""


def submit_credit_note(refund_id):
    """Try to get one credit note signed. Needs the original sale invoice to be signed first (KRA checks the
    reference), so a still-waiting sale is sent first. Safe to call twice."""
    if not enabled():
        return None
    original_sale_id = (EtimsCreditNote.objects.filter(refund_id=refund_id, status=EtimsCreditNote.PENDING)
                        .values_list("refund__sale_id", flat=True).first())
    if original_sale_id is not None:
        orig = EtimsSubmission.objects.filter(sale_id=original_sale_id).first()
        if orig is not None and orig.status == EtimsSubmission.PENDING:
            submit(original_sale_id)
    with transaction.atomic():
        note = (EtimsCreditNote.objects.select_for_update().select_related("refund__sale", "refund__requested_by")
                .filter(refund_id=refund_id).first())
        if note is None or note.status == EtimsCreditNote.SUBMITTED:
            return note
        refund = note.refund
        note.attempts += 1
        note.last_attempt_at = timezone.now()
        orig = EtimsSubmission.objects.filter(sale_id=refund.sale_id).first()
        problem = config_problems() or _missing_for_credit(refund)
        if not problem and (orig is None or orig.status != EtimsSubmission.SUBMITTED):
            problem = ("The sale invoice was rejected by KRA, so it cannot be credited yet."
                       if orig is not None and orig.status == EtimsSubmission.REJECTED
                       else "Waiting for the original sale invoice to be signed by KRA.")
        if problem:
            note.last_error = (problem if isinstance(problem, str) else problem[0])[:300]
            note.save()
            return note
        try:
            result = etims_rules.parse_response(unit_factory().post(ENDPOINT, _credit_payload(refund, note)))
        except EtimsError as exc:
            note.last_error = str(exc)[:300]                 # stays PENDING: retried later, the refund is untouched
            note.save()
            return note
        if not result["ok"]:
            note.status = EtimsCreditNote.REJECTED
            note.last_error = f"KRA {result['code']}: {result['message']}"[:300]
            note.save()
            audit.record("etims.credit_note_rejected", object_type="refund", object_ref=refund.number,
                         reason=note.last_error, till_number=refund.till.number)
            return note
        data = result["data"]
        shop = ShopSettings.load()
        note.status, note.submitted_at, note.last_error = EtimsCreditNote.SUBMITTED, timezone.now(), ""
        note.scu_id = str(data.get("sdcId", ""))[:40]
        note.scu_receipt_no = str(data.get("rcptNo", ""))[:20]
        note.scu_total_receipt_no = str(data.get("totRcptNo", ""))[:20]
        note.scu_datetime = str(data.get("vsdcRcptPbctDate", ""))[:20]
        note.internal_data = str(data.get("intrlData", ""))[:200]
        note.signature = str(data.get("rcptSign", ""))[:200]
        note.qr_text = etims_rules.qr_text(settings.ETIMS_QR_URL_TEMPLATE, shop.kra_pin,
                                           settings.ETIMS_BRANCH_ID, note.signature)[:400]
        note.save()
        audit.record("etims.credit_note_signed", object_type="refund", object_ref=refund.number,
                     new={"invoice_no": note.invoice_no, "original_invoice_no": note.original_invoice_no,
                          "scu_receipt_no": note.scu_receipt_no}, till_number=refund.till.number)
        return note
