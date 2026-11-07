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
from .models import EtimsSubmission, Sale

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
    """Retry the oldest waiting sales. Rejected ones are not retried automatically: someone must look."""
    if not enabled():
        return 0
    done = 0
    for sale_id in list(EtimsSubmission.objects.filter(status=EtimsSubmission.PENDING)
                        .order_by("id").values_list("sale_id", flat=True)[:limit]):
        sub = submit(sale_id)
        done += 1 if sub and sub.status == EtimsSubmission.SUBMITTED else 0
    return done


def counts():
    from django.db.models import Count
    rows = EtimsSubmission.objects.values("status").annotate(n=Count("id"))
    return {r["status"]: r["n"] for r in rows}
