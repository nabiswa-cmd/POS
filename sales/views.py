"""JSON endpoints for the POS sales screen (/api/sales/...). The screen itself is built in the second half
of Phase 3; these are the calls it makes. Every endpoint needs a signed-in cashier with an open shift."""
from functools import wraps

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET

from accounts import services as auth_services
from catalog import barcodes
from core.http import bad, client_ip, login_required_json, post_json
from core.models import ShopSettings
from tills.models import CashierShift

from . import daraja, etims, mpesa_rules, mpesa_services, receipt, rules, services, shortcuts, tax
from .models import Sale



def _guard(view):
    """Resolve the cashier's open shift once and turn rule errors into clean JSON errors."""
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        try:
            shift = services.open_shift_for(request.user, request.session.get("pos_till"))
            return view(request, *args, shift=shift, **kwargs)
        except services.ApprovalNeeded as exc:
            return JsonResponse({"ok": False, "error": str(exc), "approval_required": exc.action,
                                 "object_ref": exc.object_ref}, status=409)
        except services.NotAllowed as exc:
            return bad(str(exc), 403)
        except services.SaleError as exc:
            return bad(str(exc), 400)

    return wrapper


def _item_json(item):
    product = item.product
    return {
        "id": item.pk, "ref": rules.item_ref(item.sale_id, item.pk), "product_id": item.product_id,
        "name": item.product_name, "pack": item.pack_name, "units": item.units_per_sale_unit,
        "quantity": item.quantity, "list_price": str(item.list_price), "unit_price": str(item.unit_price),
        "discount": str(item.discount), "line_total": str(item.line_total), "stock": product.stock_qty,
        "price_changed": item.unit_price != item.list_price,
    }


def cart_payload(sale):
    if sale is None:
        return {"id": None, "ref": None, "customer": "", "customer_pin": "", "items": [], "count": 0,
                "subtotal": "0.00", "discount": "0.00", "total": "0.00", "vat_included": None}
    items = list(sale.items.select_related("product"))
    vat = None
    shop = ShopSettings.load()
    if shop.vat_registered:
        _, _, vat_total = tax.summarize(
            (i.product.tax_class, tax.rate_for(i.product.tax_class, shop.vat_standard_rate), i.line_total) for i in items)
        vat = str(vat_total)
    return {"vat_included": vat, "id": sale.pk, "ref": rules.sale_ref(sale.pk), "customer": sale.customer_name,
            "customer_pin": sale.customer_pin,
            "items": [_item_json(i) for i in items], "count": sum(i.quantity for i in items),
            "subtotal": str(sale.subtotal), "discount": str(sale.discount_total), "total": str(sale.total)}


def _cart(request, shift, **extra):
    return JsonResponse({"ok": True, "cart": cart_payload(services.active_sale(request.user, shift)), **extra})


@require_GET
@login_required_json
@_guard
def cart(request, shift):
    return _cart(request, shift)


@login_required_json
@post_json
@_guard
def cart_add(request, data, shift):
    sale, line = services.add_item(
        request.user, shift, code=data.get("code"), product_id=data.get("product_id"),
        packaging_id=data.get("packaging_id"), quantity=data.get("quantity", 1))
    return _cart(request, shift, added=line.pk)


@login_required_json
@post_json
@_guard
def cart_quantity(request, data, shift):
    services.set_quantity(request.user, shift, data.get("item_id"), data.get("quantity"),
                          approval_token=data.get("approval_token"))
    return _cart(request, shift)


@login_required_json
@post_json
@_guard
def cart_remove(request, data, shift):
    services.remove_item(request.user, shift, data.get("item_id"), approval_token=data.get("approval_token"),
                         reason=str(data.get("reason") or ""))
    return _cart(request, shift)


@login_required_json
@post_json
@_guard
def cart_discount(request, data, shift):
    services.apply_discount(request.user, shift, data.get("item_id"), amount=data.get("amount"),
                            percent=data.get("percent"), approval_token=data.get("approval_token"))
    return _cart(request, shift)


@login_required_json
@post_json
@_guard
def cart_price(request, data, shift):
    services.change_price(request.user, shift, data.get("item_id"), data.get("price"),
                          approval_token=data.get("approval_token"))
    return _cart(request, shift)


@login_required_json
@post_json
@_guard
def cart_customer(request, data, shift):
    services.set_customer(request.user, shift, data.get("name"), pin=data.get("pin"))
    return _cart(request, shift)


@login_required_json
@post_json
@_guard
def cart_void(request, data, shift):
    services.void_sale(request.user, shift, approval_token=data.get("approval_token"),
                       reason=str(data.get("reason") or ""))
    return _cart(request, shift)


@login_required_json
@post_json
@_guard
def cart_hold(request, data, shift):
    services.hold_sale(request.user, shift, data.get("label"))
    return _cart(request, shift)


@require_GET
@login_required_json
@_guard
def held_list(request, shift):
    rows = [{"id": s.pk, "label": s.hold_label, "cashier": s.cashier.display_name, "total": str(s.total),
             "items": sum(i.quantity for i in s.items.all()), "held_at": s.held_at.isoformat() if s.held_at else None}
            for s in services.held_sales(request.user, shift)]
    return JsonResponse({"ok": True, "held": rows})


@login_required_json
@post_json
@_guard
def held_resume(request, data, shift):
    services.resume_held(request.user, shift, data.get("sale_id"))
    return _cart(request, shift)


@login_required_json
@post_json
@_guard
def checkout_cash(request, data, shift):
    sale = services.checkout_cash(request.user, shift, data.get("tendered"))
    payment = sale.payments.first()
    # The sale is saved. Now try to get it signed by KRA; if that cannot happen right now it simply waits.
    sub = etims.submit(sale.pk)
    etims.submit_pending(3)
    return JsonResponse({"ok": True, "sale_id": sale.pk, "number": sale.number, "total": str(sale.total),
                         "change": str(payment.change_given), "cart": cart_payload(None),
                         "etims": sub.status if sub else None, "etims_note": sub.last_error if sub else ""})


def _receipt_response(sale, duplicate):
    data, lines = receipt.render(sale, duplicate=duplicate)
    qr = (data.get("etims") or {}).get("qr_text") or ""
    return JsonResponse({"ok": True, "receipt": data, "lines": lines, "paper_mm": data["paper_mm"],
                         "qr_svg": barcodes.qr_svg(qr) if qr else ""})


@require_GET
@login_required_json
@_guard
def receipt_original(request, sale_id, shift):
    """First print for the cashier who made the sale. After that, only a reprint (a marked duplicate)."""
    sale = services.mark_original_printed(request.user, sale_id)
    return _receipt_response(receipt.get_completed(sale.pk), duplicate=False)


@login_required_json
@post_json
@_guard
def receipt_reprint(request, sale_id, data, shift):
    sale = services.mark_reprint(request.user, sale_id, ip=client_ip(request))
    return _receipt_response(receipt.get_completed(sale.pk), duplicate=True)


@require_GET
@login_required_json
@_guard
def shift_summary(request, shift):
    if not (request.user.can("shifts.view_own") or request.user.can("shifts.view_all")):
        return bad("You are not allowed to see shift information.", 403)
    return JsonResponse({"ok": True, "shift": services.shift_summary(shift)})


@require_GET
@login_required_json
@_guard
def my_sales(request, shift):
    """The cashier's own completed sales on this shift (newest first), so a receipt can be found and printed."""
    if not (request.user.can("sales.view_own") or request.user.can("sales.view_all")):
        return bad("You are not allowed to see sales.", 403)
    qs = Sale.objects.filter(shift=shift, status=Sale.COMPLETED)
    if not request.user.can("sales.view_all"):
        qs = qs.filter(cashier=request.user)
    rows = [{"id": x.pk, "number": x.number, "total": str(x.total), "customer": x.customer_name,
             "time": x.completed_at.isoformat(), "printed": x.printed_at is not None}
            for x in qs.order_by("-completed_at")[:15]]
    return JsonResponse({"ok": True, "sales": rows})


@require_GET
@login_required_json
@_guard
def pos_config(request, shift):
    """Everything the sales screen needs at start-up."""
    shop = ShopSettings.load()
    try:
        keys = shortcuts.merged(shop.pos_shortcuts)
    except shortcuts.ShortcutError:
        keys = dict(shortcuts.DEFAULTS)         # a bad saved setting must never lock the till
    user = request.user
    return JsonResponse({
        "ok": True, "shop": shop.shop_name, "till": shift.till.name, "till_number": shift.till.number,
        "cashier": user.display_name, "authorized_by": shift.authorized_by.display_name,
        "owner": auth_services.is_exempt(user),
        "shortcuts": keys, "actions": shortcuts.ACTIONS,
        "can": {"hold": user.can("sales.hold"), "reprint": user.can("receipts.reprint"),
                "discount": user.can("discount.apply") or auth_services.is_exempt(user),
                "shift": user.can("shifts.view_own") or user.can("shifts.view_all")},
        "vat": shop.vat_registered,
        "etims": etims.enabled(),
        "payment_methods": [{"code": "CASH", "label": "Cash", "enabled": True},
                            {"code": "MPESA", "label": "M-PESA (Till or phone request)", "enabled": True},
                            {"code": "CARD", "label": "Card (next update)", "enabled": False},
                            {"code": "BANK", "label": "Bank transfer (next update)", "enabled": False}],
        "mpesa": {"stk": daraja.configured(), "till_capture": daraja.callbacks_ready()},
        "approval_minutes": settings.APPROVAL_TOKEN_MINUTES,
    })


@ensure_csrf_cookie
@require_GET
def pos_page(request):
    """The cashier's sales screen. Only reachable by a signed-in cashier with an open shift on this till."""
    if not request.user.is_authenticated or not request.user.can("sales.make"):
        return redirect("/app/pos/")
    till_number = request.session.get("pos_till")
    if not till_number or not CashierShift.objects.filter(
            till__number=till_number, cashier=request.user, closed_at__isnull=True).exists():
        return redirect("/app/pos/")
    return render(request, "sales/pos.html", {"shop_name": ShopSettings.load().shop_name})


def reprint_page(request, sale_id):
    """Manager/supervisor reprint from the Tills & sales screen. Always a marked, audited duplicate."""
    from django.http import HttpResponseNotAllowed
    from core.decorators import perm_required

    @perm_required("receipts.reprint")
    def inner(request):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        try:
            sale = services.mark_reprint(request.user, sale_id, ip=client_ip(request))
        except services.SaleError as exc:
            return bad(str(exc), 400)
        data, lines = receipt.render(receipt.get_completed(sale.pk), duplicate=True)
        qr = (data.get("etims") or {}).get("qr_text") or ""
        mm = 58 if data["paper_mm"] == 58 else 80
        return render(request, "sales/receipt_print.html", {
            "number": data["number"], "text": "\n".join(lines), "mm": mm, "inner": mm - 4,
            "font": 11 if mm == 58 else 10, "qr": barcodes.qr_svg(qr) if qr else ""})

    return inner(request)


# --- Phase 4A: M-PESA --------------------------------------------------------------------------------

def _mpesa_row(txn, total):
    from django.utils import timezone
    return {"id": txn.pk, "receipt": txn.trans_id, "amount": str(txn.amount), "name": txn.customer_name,
            "who": mpesa_rules.phone_hint(txn.payer_ref),
            "time": timezone.localtime(txn.created_at).strftime("%H:%M"),
            "matches": total is not None and txn.amount == total}


@require_GET
@login_required_json
@_guard
def mpesa_recent(request, shift):
    """Confirmed payments nobody has used yet. Exact matches for the current sale come first."""
    try:
        total = mpesa_services._cart_total(request.user, shift)[1]
    except services.SaleError:
        total = None
    rows = mpesa_services.recent_unmatched(request.user, shift)
    rows.sort(key=lambda t: (total is None or t.amount != total))       # stable: newest first inside each group
    return JsonResponse({"ok": True, "total": str(total) if total is not None else None,
                         "payments": [_mpesa_row(t, total) for t in rows]})


@login_required_json
@post_json
@_guard
def mpesa_stk_start(request, data, shift):
    stk = mpesa_services.start_stk(request.user, shift, data.get("phone"))
    return JsonResponse({"ok": True, "stk_id": stk.pk, "status": stk.status})


@require_GET
@login_required_json
@_guard
def mpesa_stk_status(request, stk_id, shift):
    stk = mpesa_services.stk_status(request.user, shift, stk_id)
    return JsonResponse({"ok": True, "status": stk.status, "message": stk.result_desc,
                         "transaction_id": stk.transaction_id})


@login_required_json
@post_json
@_guard
def checkout_mpesa(request, data, shift):
    try:
        transaction_id = int(data.get("transaction_id"))
    except (TypeError, ValueError):
        return bad("Choose a payment first.")
    sale = mpesa_services.checkout_mpesa(request.user, shift, transaction_id)
    sub = etims.submit(sale.pk)
    etims.submit_pending(3)
    return JsonResponse({"ok": True, "sale_id": sale.pk, "number": sale.number, "total": str(sale.total),
                         "change": "0.00", "customer": sale.customer_name,
                         "reference": sale.payments.first().reference, "cart": cart_payload(None),
                         "etims": sub.status if sub else None, "etims_note": sub.last_error if sub else ""})
