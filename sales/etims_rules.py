"""Pure helpers for KRA eTIMS (no Django, no network): building the sales message and reading the answer.

IMPORTANT: this follows the field names of KRA's OSCU/VSCU specification (v2.0) as understood when it was
written. It has NOT been run against KRA's sandbox, because that needs your own KRA PIN, branch and device
credentials. Test in KRA's sandbox and fix anything it rejects before turning ETIMS_MODE to live.
Nothing here ever invents a signature, receipt number or QR: those only exist if KRA's unit returned them.
"""
from decimal import Decimal

from .rules import money

SUCCESS_CODE = "000"
PAYMENT_CODES = {"CASH": "01", "MPESA": "06", "CARD": "05", "BANK": "04"}   # Phase 4 adds the others
TAX_LETTERS = ("A", "B", "C", "D", "E")


def _num(value):
    return float(money(value))


def build_sale_payload(*, tin, branch_id, invoice_no, receipt_type="S", sale_time, customer_name="",
                       customer_pin="", payment_method="CASH", items, user_label="POS", original_invoice_no=0):
    """items: dicts with name, code, class_code, quantity, unit_price, discount, tax_class, tax_rate,
    taxable, tax, total. Returns the JSON-ready dict for saveTrnsSalesOsdc."""
    stamp = sale_time.strftime("%Y%m%d%H%M%S")
    per_class = {c: {"taxable": Decimal("0"), "tax": Decimal("0"), "rate": Decimal("0")} for c in TAX_LETTERS}
    lines, total_taxable, total_tax, total = [], Decimal("0"), Decimal("0"), Decimal("0")
    for seq, item in enumerate(items, start=1):
        letter = item["tax_class"]
        per_class[letter]["taxable"] += item["taxable"]
        per_class[letter]["tax"] += item["tax"]
        per_class[letter]["rate"] = Decimal(item["tax_rate"])
        total_taxable += item["taxable"]
        total_tax += item["tax"]
        total += item["total"]
        gross = money(Decimal(item["unit_price"]) * int(item["quantity"]))
        discount = money(item["discount"])
        lines.append({
            "itemSeq": seq, "itemCd": item["code"], "itemClsCd": item["class_code"], "itemNm": item["name"][:200],
            "pkgUnitCd": "NT", "pkg": int(item["quantity"]), "qtyUnitCd": "U", "qty": int(item["quantity"]),
            "prc": _num(item["unit_price"]), "splyAmt": _num(gross),
            "dcRt": _num(discount * 100 / gross) if gross > 0 else 0.0, "dcAmt": _num(discount),
            "taxTyCd": letter, "taxblAmt": _num(item["taxable"]), "taxAmt": _num(item["tax"]),
            "totAmt": _num(item["total"]),
        })
    payload = {
        "tin": tin, "bhfId": branch_id, "invcNo": int(invoice_no), "orgInvcNo": int(original_invoice_no),
        "custTin": customer_pin or None, "custNm": customer_name or None,
        "salesTyCd": "N", "rcptTyCd": receipt_type, "pmtTyCd": PAYMENT_CODES.get(payment_method, "01"),
        "salesSttsCd": "02", "cfmDt": stamp, "salesDt": stamp[:8], "stockRlsDt": stamp,
        "totItemCnt": len(lines),
        "totTaxblAmt": _num(total_taxable), "totTaxAmt": _num(total_tax), "totAmt": _num(total),
        "regrId": user_label, "regrNm": user_label, "modrId": user_label, "modrNm": user_label,
        "receipt": {"custTin": customer_pin or None, "prchrAcptcYn": "N"},
        "itemList": lines,
    }
    for letter in TAX_LETTERS:
        payload[f"taxblAmt{letter}"] = _num(per_class[letter]["taxable"])
        payload[f"taxRt{letter}"] = _num(per_class[letter]["rate"])
        payload[f"taxAmt{letter}"] = _num(per_class[letter]["tax"])
    return payload


def parse_response(body):
    """Read KRA's answer. Returns a dict: ok, code, message and, on success, the signed receipt data."""
    if not isinstance(body, dict):
        return {"ok": False, "code": "", "message": "Unreadable answer from the eTIMS unit.", "data": {}}
    code = str(body.get("resultCd", ""))
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    ok = code == SUCCESS_CODE and bool(data.get("rcptSign"))
    message = str(body.get("resultMsg") or "")
    if code == SUCCESS_CODE and not ok:
        message = "The eTIMS unit said success but sent no receipt signature; the receipt is not valid."
    return {"ok": ok, "code": code, "message": message, "data": data}


def qr_text(template, tin, branch_id, signature):
    """The text for the receipt QR, only when a template is configured and a real signature exists."""
    if not (template and signature):
        return ""
    return template.format(tin=tin, bhf=branch_id, sign=signature)
