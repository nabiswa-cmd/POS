"""Safaricom Daraja client (standard library only, no extra package). Credentials come from settings.MPESA,
which reads .env; nothing is hardcoded. Every call has a timeout and returns plain dicts or raises DarajaError,
so a dead internet connection never freezes a till: cash sales do not touch this module at all."""
import base64
import json
import urllib.error
import urllib.request
from datetime import datetime

from django.conf import settings
from django.core.cache import cache

from . import mpesa_rules as rules

BASES = {"sandbox": "https://sandbox.safaricom.co.ke", "live": "https://api.safaricom.co.ke"}


class DarajaError(Exception):
    pass


def cfg():
    return settings.MPESA


def configured():
    c = cfg()
    return bool(c["CONSUMER_KEY"] and c["CONSUMER_SECRET"] and c["SHORTCODE"] and c["PASSKEY"] and c["CALLBACK_BASE_URL"]
                and c["CALLBACK_SECRET"])


def callbacks_ready():
    """The public callback address and its secret exist (needed for both STK and till payments)."""
    c = cfg()
    return bool(c["CALLBACK_BASE_URL"] and c["CALLBACK_SECRET"])


def callback_url(kind):
    c = cfg()
    return f"{c['CALLBACK_BASE_URL'].rstrip('/')}/api/pay/{kind}/{c['CALLBACK_SECRET']}/"


def _call(method, path, body=None, headers=None):
    url = BASES[cfg()["ENV"]] + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=cfg()["TIMEOUT"]) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode() or "{}")
        except ValueError:
            detail = {}
        raise DarajaError(detail.get("errorMessage") or detail.get("ResponseDescription") or f"Safaricom error {exc.code}")
    except (urllib.error.URLError, OSError, ValueError):
        raise DarajaError("Cannot reach Safaricom right now (no internet?).")


def token():
    c = cfg()
    cached = cache.get("daraja-token")
    if cached:
        return cached
    basic = base64.b64encode(f"{c['CONSUMER_KEY']}:{c['CONSUMER_SECRET']}".encode()).decode()
    res = _call("GET", "/oauth/v1/generate?grant_type=client_credentials", headers={"Authorization": f"Basic {basic}"})
    tok = res.get("access_token")
    if not tok:
        raise DarajaError("Safaricom did not give an access token. Check the consumer key and secret.")
    cache.set("daraja-token", tok, 3000)          # tokens last 3600 s
    return tok


def _auth():
    return {"Authorization": f"Bearer {token()}", "Content-Type": "application/json"}


def _stamp():
    return datetime.now().strftime("%Y%m%d%H%M%S")


def stk_push(phone, amount_whole, reference, description="Payment"):
    """Ask the customer's phone for payment. Returns {merchant_request_id, checkout_request_id}."""
    c = cfg()
    if not configured():
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
        "PhoneNumber": phone, "CallBackURL": callback_url("stk"),
        "AccountReference": str(reference)[:12], "TransactionDesc": str(description)[:13],
    }
    res = _call("POST", "/mpesa/stkpush/v1/processrequest", body, _auth())
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
        res = _call("POST", "/mpesa/stkpushquery/v1/query", body, _auth())
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
    body = {"ShortCode": c["SHORTCODE"], "ResponseType": "Completed",
            "ConfirmationURL": callback_url("c2b/confirm"), "ValidationURL": callback_url("c2b/validate")}
    return _call("POST", "/mpesa/c2b/v2/registerurl", body, _auth())
