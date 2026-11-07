"""M-PESA rules with no Django in them (so they are fast to test and impossible to confuse with the database).

What Safaricom really sends, and what we do with it:

* C2B confirmation (customer pays the Till directly, or an STK push to the Till completes): has TransID (the
  M-PESA receipt code), TransAmount, TransTime and, only sometimes, FirstName / MiddleName / LastName. Since
  Safaricom's v2 API the phone number (MSISDN) is hashed, so it is NOT a usable phone number.
* STK callback: has CheckoutRequestID, ResultCode and, on success, MpesaReceiptNumber + Amount + PhoneNumber.
  It never carries a customer name.

A name is stored ONLY if Safaricom sent one. Nothing is guessed. The cashier can still type one.
"""
import base64
import re
from decimal import Decimal, InvalidOperation


class MpesaError(ValueError):
    pass


# --- phone numbers ---------------------------------------------------------------------------------

def normalize_phone(raw):
    """07xx / 01xx / +2547xx / 2547xx -> 2547XXXXXXXX (12 digits). Raises MpesaError if it is not a Kenyan mobile."""
    digits = re.sub(r"[\s\-()+]", "", str(raw or ""))
    if digits.startswith("0") and len(digits) == 10:
        digits = "254" + digits[1:]
    if not re.fullmatch(r"254[17]\d{8}", digits):
        raise MpesaError("Enter a Kenyan mobile number such as 0712 345 678.")
    return digits


def phone_hint(value):
    """Safe text to show a cashier: last 3 digits of a real number, or nothing for a hashed one."""
    v = str(value or "")
    return ("07xx xxx " + v[-3:]) if re.fullmatch(r"254[17]\d{8}", v) else ""


# --- names -----------------------------------------------------------------------------------------

def clean_name(*parts):
    """Join FirstName/MiddleName/LastName. Returns '' unless it looks like a real name."""
    words = [re.sub(r"\s+", " ", str(p or "")).strip() for p in parts]
    name = " ".join(w for w in words if w)
    if len(name) < 2 or len(name) > 120:
        return ""
    if re.search(r"\d", name) and not re.search(r"[A-Za-z]{2}", name):
        return ""                                   # a phone number or a hash, not a name
    if re.fullmatch(r"[0-9a-fA-F]{32,}", name.replace(" ", "")):
        return ""                                   # SHA-256 style hash
    if name.lower() in {"null", "none", "n/a", "na", "unknown", "-"}:
        return ""
    return name[:120]


def pick_customer_name(mpesa_name, typed_name=""):
    """The name that goes on the sale: what the cashier typed wins, else the M-PESA name, else blank."""
    return (str(typed_name or "").strip() or str(mpesa_name or "").strip())[:120]


# --- amounts ---------------------------------------------------------------------------------------

def parse_amount(raw):
    try:
        value = Decimal(str(raw).replace(",", "").strip())
    except (InvalidOperation, AttributeError):
        raise MpesaError("Payment amount is missing or invalid.")
    if value <= 0:
        raise MpesaError("Payment amount must be more than zero.")
    return value.quantize(Decimal("0.01"))


def stk_amount(total):
    """STK Push accepts whole shillings only. A total with cents cannot be pushed; the customer pays at the Till."""
    total = Decimal(total)
    if total != total.to_integral_value():
        raise MpesaError("STK push needs a whole-shillings total. Ask the customer to pay the exact amount to the Till.")
    return int(total)


def amount_matches(paid, total):
    """Exact match only. Under- and over-payments need the split/overpay handling of sub-phase 4B."""
    return Decimal(paid) == Decimal(total)


# --- Daraja request helpers ------------------------------------------------------------------------

def stk_password(shortcode, passkey, timestamp):
    return base64.b64encode(f"{shortcode}{passkey}{timestamp}".encode()).decode()


def parse_c2b(payload):
    """C2B confirmation JSON -> dict. Raises MpesaError when it has no receipt code or amount."""
    if not isinstance(payload, dict):
        raise MpesaError("Not a payment message.")
    trans_id = str(payload.get("TransID") or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{8,14}", trans_id):
        raise MpesaError("Missing or invalid TransID.")
    return {
        "trans_id": trans_id,
        "amount": parse_amount(payload.get("TransAmount")),
        "customer_name": clean_name(payload.get("FirstName"), payload.get("MiddleName"), payload.get("LastName")),
        "payer_ref": str(payload.get("MSISDN") or "")[:80],
        "shortcode": str(payload.get("BusinessShortCode") or "")[:12],
        "bill_ref": str(payload.get("BillRefNumber") or "")[:60],
        "trans_time": str(payload.get("TransTime") or "")[:14],
    }


def parse_stk_callback(payload):
    """STK callback JSON -> dict with ok, checkout_request_id, result_code, result_desc and (if ok) receipt data."""
    try:
        cb = payload["Body"]["stkCallback"]
        checkout = str(cb["CheckoutRequestID"])
        code = int(cb["ResultCode"])
    except (KeyError, TypeError, ValueError):
        raise MpesaError("Not an STK callback.")
    out = {"checkout_request_id": checkout, "merchant_request_id": str(cb.get("MerchantRequestID") or ""),
           "result_code": code, "result_desc": str(cb.get("ResultDesc") or "")[:200], "ok": code == 0}
    if code == 0:
        items = {}
        for it in (cb.get("CallbackMetadata") or {}).get("Item", []) or []:
            if isinstance(it, dict) and "Name" in it:
                items[it["Name"]] = it.get("Value")
        receipt = str(items.get("MpesaReceiptNumber") or "").strip().upper()
        if not re.fullmatch(r"[A-Z0-9]{8,14}", receipt):
            raise MpesaError("Successful STK callback without a receipt number.")
        out.update(trans_id=receipt, amount=parse_amount(items.get("Amount")),
                   payer_ref=str(items.get("PhoneNumber") or "")[:80], trans_time=str(items.get("TransactionDate") or "")[:14])
    return out


# Result codes worth telling the cashier about (everything else shows Safaricom's own description).
STK_EXPIRED = {1037}                  # customer's phone did not respond in time
STK_CANCELLED = {1032}                # customer cancelled
STK_TERMINAL_FAILS = {1, 17, 1019, 1025, 1032, 1037, 2001, 9999}


def stk_failure_status(code):
    """FAILED or EXPIRED for a non-zero result code."""
    return "EXPIRED" if int(code) in STK_EXPIRED else "FAILED"
