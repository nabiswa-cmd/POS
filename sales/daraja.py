"""Safaricom Daraja client. Each operation uses one environment/database config snapshot."""
import base64
import hashlib
import json
import urllib.error
import urllib.request
from datetime import datetime

from django.core.cache import cache

from core import integration_settings

from . import mpesa_rules as rules

BASES = {"sandbox": "https://sandbox.safaricom.co.ke", "live": "https://api.safaricom.co.ke"}


class DarajaError(Exception):
    pass


def cfg():
    return integration_settings.mpesa()


def configured(config=None):
    c = config or cfg()
    return bool(c["CONSUMER_KEY"] and c["CONSUMER_SECRET"] and c["SHORTCODE"] and c["PASSKEY"] and c["CALLBACK_BASE_URL"]
                and c["CALLBACK_SECRET"])


def callbacks_ready(config=None):
    """The public callback address and its secret exist (needed for both STK and till payments)."""
    c = config or cfg()
    return bool(c["CALLBACK_BASE_URL"] and c["CALLBACK_SECRET"])


def callback_url(kind, config=None):
    c = config or cfg()
    return f"{c['CALLBACK_BASE_URL'].rstrip('/')}/api/pay/{kind}/{c['CALLBACK_SECRET']}/"


def _call(method, path, body=None, headers=None, config=None):
    c = config or cfg()
    url = BASES[c["ENV"]] + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=c["TIMEOUT"]) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode() or "{}")
        except ValueError:
            detail = {}
        raise DarajaError(detail.get("errorMessage") or detail.get("ResponseDescription") or f"Safaricom error {exc.code}")
    except (urllib.error.URLError, OSError, ValueError):
        raise DarajaError("Cannot reach Safaricom right now (no internet?).")


def token(config=None):
    c = config or cfg()
    token_key = hashlib.sha256(
        f"{c['ENV']}:{c['CONSUMER_KEY']}:{c['CONSUMER_SECRET']}".encode("utf-8")).hexdigest()
    cache_key = f"daraja-token:{token_key}"
    cached = cache.get(cache_key)
    if cached:
        return cached
    basic = base64.b64encode(f"{c['CONSUMER_KEY']}:{c['CONSUMER_SECRET']}".encode()).decode()
    res = _call("GET", "/oauth/v1/generate?grant_type=client_credentials",
                headers={"Authorization": f"Basic {basic}"}, config=c)
    tok = res.get("access_token")
    if not tok:
        raise DarajaError("Safaricom did not give an access token. Check the consumer key and secret.")
    cache.set(cache_key, tok, 3000)          # tokens last 3600 s
    return tok


def _auth(config=None):
    return {"Authorization": f"Bearer {token(config)}", "Content-Type": "application/json"}


def _stamp():
    return datetime.now().strftime("%Y%m%d%H%M%S")


def stk_push(phone, amount_whole, reference, description="Payment"):
    """Ask the customer's phone for payment. Returns {merchant_request_id, checkout_request_id}."""
    c = cfg()
    if not configured(c):
        raise DarajaError("M-PESA STK push is not configured. Customers can still pay the Till directly.")
    ts = _stamp()
    till = c["ACCOUNT_TYPE"] == "till"
    body = {
        "BusinessShortCode": c["SHORTCODE"],
        "Password": rules.stk_password(c["SHORTCODE"], c["PASSKEY"], ts),
        "Timestamp": ts,
        "TransactionType": "CustomerBuyGoodsOnline" if till else "CustomerPayBillOnline",
        "Amount": amount_whole, "PartyA": phone,
        "PartyB": (c["TILL_NUMBER"] or c["SHORTCODE"]) if till else c["SHORTCODE"],
        "PhoneNumber": phone, "CallBackURL": callback_url("stk", c),
        "AccountReference": str(reference)[:12], "TransactionDesc": str(description)[:13],
    }
    res = _call("POST", "/mpesa/stkpush/v1/processrequest", body, _auth(c), config=c)
    if str(res.get("ResponseCode")) != "0" or not res.get("CheckoutRequestID"):
        raise DarajaError(res.get("ResponseDescription") or res.get("errorMessage") or "Safaricom refused the request.")
    return {"merchant_request_id": res.get("MerchantRequestID", ""), "checkout_request_id": res["CheckoutRequestID"]}


def stk_query(checkout_request_id):
    """Ask Safaricom what happened to a push. Returns (result_code or None, description).
    NOTE: this reply has no receipt number, so it may only be used to FAIL/EXPIRE a request early, never to
    confirm money. Confirmation always comes from the callback with its M-PESA receipt."""
    c = cfg()
    ts = _stamp()
    body = {"BusinessShortCode": c["SHORTCODE"], "Password": rules.stk_password(c["SHORTCODE"], c["PASSKEY"], ts),
            "Timestamp": ts, "CheckoutRequestID": checkout_request_id}
    try:
        res = _call("POST", "/mpesa/stkpushquery/v1/query", body, _auth(c), config=c)
    except DarajaError as exc:
        if "processing" in str(exc).lower():
            return None, "Still being processed."
        raise
    if "ResultCode" not in res:
        return None, res.get("ResponseDescription", "")
    return int(res["ResultCode"]), res.get("ResultDesc", "")


def register_c2b_urls():
    """One-off: tell Safaricom where to send till/paybill payments. Run the command on the server that is public."""
    c = cfg()
    body = {"ShortCode": c["TILL_NUMBER"] or c["SHORTCODE"], "ResponseType": "Completed",
            "ConfirmationURL": callback_url("c2b/confirm", c), "ValidationURL": callback_url("c2b/validate", c)}
    return _call("POST", "/mpesa/c2b/v2/registerurl", body, _auth(c), config=c)


def lookup_ready():
    """Name lookup needs a Daraja initiator with its pre-encrypted credential and the public callback address.
    Without them it is simply off (names still come from the till message)."""
    c = cfg()
    return bool(c.get("CONSUMER_KEY") and c.get("CONSUMER_SECRET") and (c.get("SHORTCODE") or c.get("TILL_NUMBER"))
                and c.get("INITIATOR_NAME") and c.get("SECURITY_CREDENTIAL") and callbacks_ready(c))


def transaction_status(receipt):
    """Ask Safaricom about one payment. The answer is NOT returned here: Safaricom posts it a few seconds later to
    /api/pay/txstatus/result/<secret>/ (mpesa_views.status_result). True when Safaricom accepted the request."""
    c = cfg()
    till = c["ACCOUNT_TYPE"] == "till"
    body = {"Initiator": c["INITIATOR_NAME"], "SecurityCredential": c["SECURITY_CREDENTIAL"],
            "CommandID": "TransactionStatusQuery", "TransactionID": receipt,
            "PartyA": (c["TILL_NUMBER"] or c["SHORTCODE"]) if till else c["SHORTCODE"],
            "IdentifierType": "2" if till else "4",             # 2 = till number, 4 = organisation shortcode
            "ResultURL": callback_url("txstatus/result", c), "QueueTimeOutURL": callback_url("txstatus/timeout", c),
            "Remarks": "Customer name", "Occasion": ""}
    res = _call("POST", "/mpesa/transactionstatus/v1/query", body, _auth(c), config=c)
    return str(res.get("ResponseCode")) == "0"
