"""Stock screens: levels, history, adjustments and stock receiving."""
from decimal import Decimal

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import DecimalField, ExpressionWrapper, F, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts import services as auth_services
from catalog.models import Product
from catalog.services import can_see_cost
from core.decorators import perm_required
from core.http import client_ip

from . import services
from .forms import AdjustForm, ReceivingForm, ReceivingItemForm
from .models import Receiving, StockMovement

PAGE_SIZE = 50
VALUE = ExpressionWrapper(F("stock_qty") * F("avg_cost"), output_field=DecimalField(max_digits=18, decimal_places=4))


# --- Stock levels -----------------------------------------------------------------------------

@perm_required("stock.view")
def stock_list(request):
    qs = Product.objects.filter(active=True).select_related("shelf")
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(sku__icontains=q.upper()) | Q(codes__code=q.upper())).distinct()
    low_only = request.GET.get("low") == "1"
    if low_only:
        qs = qs.filter(reorder_level__gt=0, stock_qty__lte=F("reorder_level"))
    show_cost = can_see_cost(request.user)
    value = None
    if show_cost:
        value = qs.aggregate(v=Sum(VALUE))["v"] or Decimal("0")
    page = Paginator(qs.annotate(value=VALUE).order_by("name"), PAGE_SIZE).get_page(request.GET.get("page"))
    return render(request, "inventory/stock_list.html", {
        "page": page, "q": q, "low_only": low_only, "show_cost": show_cost, "value": value})


@perm_required("stock.view")
def stock_history(request, pk):
    product = get_object_or_404(Product, pk=pk)
    page = Paginator(StockMovement.objects.filter(product=product).select_related("user", "approved_by"),
                     PAGE_SIZE).get_page(request.GET.get("page"))
    return render(request, "inventory/stock_history.html", {
        "product": product, "page": page, "show_cost": can_see_cost(request.user), "stock_text": product.stock_text()})


@perm_required("stock.adjust")
def stock_adjust(request, pk):
    product = get_object_or_404(Product, pk=pk)
    form = AdjustForm(request.POST or None)
    needs_approver = auth_services.needs_approval(request.user, "stock_adjust")
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        token = None
        try:
            if needs_approver:
                if not d["approver_username"] or not d["approver_secret"]:
                    raise services.StockError("A supervisor or manager must approve stock adjustments. "
                                              "They type their own username and password or PIN below.")
                token, _approval = auth_services.grant_approval(
                    action="stock_adjust", approver_username=d["approver_username"], secret=d["approver_secret"],
                    reason=d["reason"], requester=request.user, object_ref=str(product.pk), ip=client_ip(request))
            services.adjust_stock(product_id=product.pk, movement_type=d["movement_type"], quantity=d["quantity"],
                                  user=request.user, reason=d["reason"], approval_token=token,
                                  reference=d["reference"], ip=client_ip(request))
        except auth_services.AuthError as exc:
            form.add_error(None, str(exc))
        except services.StockError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Stock updated and recorded.")
            return redirect("stock-history", pk=pk)
    return render(request, "inventory/stock_adjust.html", {"product": product, "form": form,
                                                           "needs_approver": needs_approver, "stock_text": product.stock_text()})


# --- Receiving --------------------------------------------------------------------------------

def _visible_receivings(user):
    qs = Receiving.objects.select_related("supplier", "created_by")
    if user.can("stock.approve_receiving"):
        return qs
    return qs.filter(created_by=user)


@perm_required("stock.receive", "stock.approve_receiving", area="receiving")
def receiving_list(request):
    qs = _visible_receivings(request.user)
    status = request.GET.get("status", "")
    if status in dict(Receiving.STATUS):
        qs = qs.filter(status=status)
    page = Paginator(qs, PAGE_SIZE).get_page(request.GET.get("page"))
    return render(request, "inventory/receiving_list.html", {
        "page": page, "status": status, "statuses": Receiving.STATUS, "can_receive": request.user.can("stock.receive")})


@perm_required("stock.receive", area="receiving")
def receiving_new(request):
    form = ReceivingForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            receiving = services.new_receiving(request.user, **form.cleaned_data)
        except services.StockError as exc:
            form.add_error(None, str(exc))
        else:
            return redirect("receiving-detail", pk=receiving.pk)
    return render(request, "core/form_page.html", {
        "title": "Receive stock: new delivery", "form": form, "back": "/manage/receiving/", "submit": "Start delivery",
        "subtitle": "Enter the delivery note and invoice references, then scan each product."})


def _can_edit(user, receiving):
    return receiving.editable and user.can("stock.receive") and (receiving.created_by_id == user.pk or user.is_manager)


@perm_required("stock.receive", "stock.approve_receiving", area="receiving")
def receiving_detail(request, pk):
    receiving = get_object_or_404(_visible_receivings(request.user), pk=pk)
    item_form = ReceivingItemForm()
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "add_item":
                if not _can_edit(request.user, receiving):
                    raise services.StockError("You cannot change this delivery.")
                item_form = ReceivingItemForm(request.POST)
                if item_form.is_valid():
                    d = item_form.cleaned_data
                    services.add_receiving_item(
                        receiving, request.user, product=d["product"], packaging=d["packaging"],
                        qty_good=d["qty_good"], qty_damaged=d["qty_damaged"], qty_missing=d["qty_missing"],
                        cost_per_pack=d.get("cost_per_pack"))
                    messages.success(request, f"Added {d['product'].name}. Scan the next product.")
                    return redirect("receiving-detail", pk=pk)
            elif action == "remove_item":
                if not _can_edit(request.user, receiving):
                    raise services.StockError("You cannot change this delivery.")
                item = get_object_or_404(receiving.items, pk=request.POST.get("item"))
                services.remove_receiving_item(receiving, item, request.user)
                return redirect("receiving-detail", pk=pk)
            elif action == "submit":
                if not _can_edit(request.user, receiving):
                    raise services.StockError("You cannot submit this delivery.")
                done = services.submit_receiving(pk, request.user, ip=client_ip(request))
                messages.success(request, "Delivery added to stock." if done.status == Receiving.POSTED
                                 else "Delivery submitted. Stock changes once a supervisor or manager approves it.")
                return redirect("receiving-detail", pk=pk)
            elif action in ("approve", "reject"):
                services.decide_receiving(pk, request.user, approve=(action == "approve"),
                                          note=request.POST.get("note", ""), ip=client_ip(request))
                messages.success(request, "Delivery approved and added to stock." if action == "approve" else "Delivery rejected.")
                return redirect("receiving-detail", pk=pk)
        except services.StockError as exc:
            messages.error(request, str(exc))
        receiving.refresh_from_db()
    items = receiving.items.select_related("product", "packaging")
    show_cost = can_see_cost(request.user) or request.user.can("stock.approve_receiving") or receiving.created_by_id == request.user.pk
    return render(request, "inventory/receiving_detail.html", {
        "r": receiving, "items": items, "item_form": item_form, "can_edit": _can_edit(request.user, receiving),
        "can_decide": receiving.status == Receiving.PENDING and request.user.can("stock.approve_receiving")
                      and (receiving.created_by_id != request.user.pk or request.user.is_manager),
        "show_cost": show_cost,
        "total": sum((i.line_cost for i in items), Decimal("0")) if show_cost else None})


@perm_required("stock.receive", "stock.approve_receiving", area="receiving")
def receiving_print(request, pk):
    """Printable receiving document (A4 or thermal width via the browser print dialog)."""
    from core.models import ShopSettings

    receiving = get_object_or_404(_visible_receivings(request.user), pk=pk)
    items = receiving.items.select_related("product", "packaging")
    show_cost = can_see_cost(request.user) or request.user.can("stock.approve_receiving") or receiving.created_by_id == request.user.pk
    return render(request, "inventory/receiving_print.html", {
        "r": receiving, "items": items, "shop": ShopSettings.load(), "show_cost": show_cost,
        "total": sum((i.line_cost for i in items), Decimal("0")) if show_cost else None})
