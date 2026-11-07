"""JSON endpoints for the POS sales screen (/api/sales/...). The screen itself is built in the second half
of Phase 3; these are the calls it makes. Every endpoint needs a signed-in cashier with an open shift."""
from functools import wraps

from django.http import JsonResponse
from django.views.decorators.http import require_GET

from core.http import bad, client_ip, login_required_json, post_json

from . import receipt, rules, services



def _guard(view):
    """Resolve the cashier's open shift once and turn rule errors into clean JSON errors."""
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        try:
            shift = services.open_shift_for(request.user, request.session.get("pos_till"))
            return view(request, *args, shift=shift, **kwargs)
        except services.NotAllowed as exc:
            return bad(str(exc), 403)
        except services.ApprovalNeeded as exc:
            # Same 400 as before; the screen looks for "approval_required" to open the supervisor box.
            from accounts.permissions import RESTRICTED_ACTIONS
            return JsonResponse({"ok": False, "error": str(exc), "approval_required": {
                "action": exc.action, "object_ref": exc.object_ref,
                "label": RESTRICTED_ACTIONS[exc.action]["label"]}}, status=400)
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
        return {"id": None, "ref": None, "customer": "", "items": [], "count": 0,
                "subtotal": "0.00", "discount": "0.00", "total": "0.00"}
    items = list(sale.items.select_related("product"))
    return {"id": sale.pk, "ref": rules.sale_ref(sale.pk), "customer": sale.customer_name,
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
    services.set_customer(request.user, shift, data.get("name"))
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
    return JsonResponse({"ok": True, "sale_id": sale.pk, "number": sale.number, "total": str(sale.total),
                         "change": str(payment.change_given), "cart": cart_payload(None)})


def _receipt_response(sale, duplicate):
    data, lines = receipt.render(sale, duplicate=duplicate)
    return JsonResponse({"ok": True, "receipt": data, "lines": lines, "paper_mm": data["paper_mm"]})


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
