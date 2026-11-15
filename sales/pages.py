"""HTML pages of Phase 3: the cashier's sales screen and the manager's sales and till screens.
All selling rules stay in sales.services; the screen talks to the JSON endpoints in sales.views."""
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import ensure_csrf_cookie

from core import shortcuts
from core.decorators import perm_required
from core.models import ShopSettings
from tills.models import CashierShift, Till

from .models import Sale


@ensure_csrf_cookie
def sell_page(request):
    """The sales screen. Needs a signed-in cashier with an open shift on this till; otherwise back to the POS login."""
    user = request.user
    if not user.is_authenticated or not user.can("sales.make"):
        return redirect("/app/pos/")
    number = request.session.get("pos_till")
    shift = (CashierShift.objects.select_related("till", "authorized_by")
             .filter(till__number=number, cashier=user, closed_at__isnull=True).first()) if number else None
    if shift is None:
        return redirect("/app/pos/")
    shop = ShopSettings.load()
    keymap = shortcuts.effective(shop.pos_shortcuts)    # a bad setting falls back to the defaults
    return render(request, "sales/sell.html", {
        "shop_name": shop.shop_name, "till": shift.till, "cashier": user.display_name,
        "authorized_by": shift.authorized_by.display_name, "keymap": keymap,
        "key_labels": [(keymap[a], shortcuts.LABELS[a]) for a in shortcuts.DEFAULTS],
        "caps": {"hold": user.can("sales.hold"), "pay": user.can("payments.accept"),
                 "reprint": user.can("receipts.reprint")},
    })


@perm_required("sales.view_all")
def manage_sales(request):
    sales = Sale.objects.filter(status__in=[Sale.COMPLETED, Sale.VOIDED]).select_related("cashier", "till")
    q = request.GET.get("q", "").strip()
    if q:
        sales = sales.filter(Q(number__icontains=q) | Q(customer_name__icontains=q))
    return render(request, "sales/manage_sales.html", {"sales": sales.order_by("-id")[:200], "q": q})


@perm_required("sales.view_all")
def manage_sale_detail(request, pk):
    sale = get_object_or_404(Sale.objects.select_related("cashier", "till", "authorized_by"), pk=pk,
                             status__in=[Sale.COMPLETED, Sale.VOIDED])
    return render(request, "sales/manage_sale_detail.html", {
        "sale": sale, "items": sale.items.all(), "payments": sale.payments.all(),
        "can_reprint": request.user.can("receipts.reprint") and sale.status == Sale.COMPLETED})


@perm_required("shifts.view_all")
def manage_tills(request):
    rows = []
    for till in Till.objects.order_by("number"):
        shift = (CashierShift.objects.select_related("cashier", "authorized_by")
                 .filter(till=till, closed_at__isnull=True).first())
        row = {"till": till, "shift": shift, "held": Sale.objects.filter(till=till, status=Sale.HELD).count()}
        if shift:
            agg = Sale.objects.filter(shift=shift, status=Sale.COMPLETED).aggregate(n=Count("id"), t=Sum("total"))
            row.update(sales_count=agg["n"], sales_total=agg["t"] or 0)
        rows.append(row)
    return render(request, "sales/manage_tills.html", {"rows": rows})
