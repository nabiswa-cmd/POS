"""Manager/receiving screens for the catalogue: products, codes, packaging, shelves, suppliers, labels."""
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, F, Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from core import audit
from core.decorators import perm_required
from core.http import client_ip, login_required_json
from core.models import ShopSettings

from . import barcodes, labels, services
from .forms import (BrandForm, CategoryForm, CodeForm, PackagingForm, ProductForm, ShelfForm, SupplierForm)
from .models import Brand, Category, Packaging, Product, ProductCode, Shelf, ShelfTransfer, Supplier

PAGE_SIZE = 50


def _flash_errors(request, exc):
    messages.error(request, str(exc))


# --- Manager home -----------------------------------------------------------------------------

@perm_required("dashboard.view", "stock.receive", "products.view")
def manage_home(request):
    from inventory.models import Receiving

    pending = (Receiving.objects.filter(status=Receiving.PENDING).count()
               if request.user.can("stock.approve_receiving") else 0)
    low = (Product.objects.filter(active=True, reorder_level__gt=0, stock_qty__lte=F("reorder_level")).count()
           if request.user.can("stock.view") else 0)
    return render(request, "core/manage_home.html", {
        "shop_name": ShopSettings.load().shop_name, "is_manager": request.user.is_manager,
        "pending_receivings": pending, "low_count": low})


# --- Products ---------------------------------------------------------------------------------

@perm_required("products.view")
def product_list(request):
    qs = Product.objects.select_related("brand", "category", "shelf")
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(sku__icontains=q.upper()) | Q(brand__name__icontains=q)
                       | Q(codes__code=q.upper())).distinct()
    if request.GET.get("category"):
        qs = qs.filter(category_id=request.GET["category"]) if request.GET["category"].isdigit() else qs
    if request.GET.get("shelf"):
        qs = qs.filter(shelf_id=request.GET["shelf"]) if request.GET["shelf"].isdigit() else qs
    if request.GET.get("inactive") != "1":
        qs = qs.filter(active=True)
    page = Paginator(qs, PAGE_SIZE).get_page(request.GET.get("page"))
    return render(request, "catalog/product_list.html", {
        "page": page, "q": q, "categories": Category.objects.filter(active=True), "shelves": Shelf.objects.filter(active=True),
        "show_cost": services.can_see_cost(request.user), "selected_category": request.GET.get("category", ""),
        "selected_shelf": request.GET.get("shelf", ""), "inactive": request.GET.get("inactive") == "1"})


@perm_required("products.manage")
def product_new(request):
    form = ProductForm(request.POST or None, request.FILES or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        try:
            product = services.create_product(form, request.user, ip=client_ip(request))
        except services.CatalogError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, f"Added {product.name}. Now add its barcode or generate a code.")
            return redirect("product-detail", pk=product.pk)
    return render(request, "core/form_page.html", {"title": "New product", "form": form, "back": "/manage/products/",
                                                   "submit": "Save product"})


@perm_required("products.view")
def product_detail(request, pk):
    product = get_object_or_404(Product.objects.select_related("brand", "category", "shelf", "supplier"), pk=pk)
    can_manage = request.user.can("products.manage")
    form = None
    if can_manage:
        form = ProductForm(request.POST or None, request.FILES or None, instance=product, user=request.user)
        if request.method == "POST" and form.is_valid():
            try:
                services.update_product(form, request.user, ip=client_ip(request))
            except services.CatalogError as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(request, "Product saved.")
                return redirect("product-detail", pk=pk)
    from inventory.models import StockMovement
    movements = StockMovement.objects.filter(product=product).select_related("user")[:10]
    return render(request, "catalog/product_detail.html", {
        "product": product, "form": form, "codes": product.codes.select_related("packaging"),
        "packagings": product.packagings.all(), "code_form": CodeForm(product=product) if can_manage else None,
        "pack_form": PackagingForm(product=product) if can_manage else None, "movements": movements,
        "show_cost": services.can_see_cost(request.user), "stock_text": product.stock_text()})


@require_POST
@perm_required("products.manage")
def product_add_code(request, pk):
    product = get_object_or_404(Product, pk=pk)
    form = CodeForm(request.POST, product=product)
    if form.is_valid():
        try:
            services.add_code(product, form.cleaned_data["code"], request.user, kind=form.cleaned_data["kind"],
                              packaging=form.cleaned_data["packaging"], ip=client_ip(request))
            messages.success(request, "Code added. It can be scanned at the till straight away.")
        except services.CatalogError as exc:
            _flash_errors(request, exc)
    else:
        messages.error(request, "; ".join(e for errs in form.errors.values() for e in errs))
    return redirect("product-detail", pk=pk)


@require_POST
@perm_required("products.manage")
def product_generate_code(request, pk):
    product = get_object_or_404(Product, pk=pk)
    packaging = None
    if request.POST.get("packaging", "").isdigit():
        packaging = get_object_or_404(Packaging, pk=request.POST["packaging"], product=product)
    try:
        pc = services.generate_internal_code(product, request.user, packaging=packaging, ip=client_ip(request))
        messages.success(request, f"Generated code {pc.code}. Print its label from the Labels screen.")
    except services.CatalogError as exc:
        _flash_errors(request, exc)
    return redirect("product-detail", pk=pk)


@require_POST
@perm_required("products.manage")
def product_add_packaging(request, pk):
    product = get_object_or_404(Product, pk=pk)
    form = PackagingForm(request.POST, product=product)
    if form.is_valid():
        pack = form.save()
        audit.record("product.packaging_added", actor=request.user, object_type="product", object_ref=pk,
                     new={"name": pack.name, "units": pack.units_per_pack,
                          "price": str(pack.selling_price) if pack.selling_price is not None else None},
                     ip=client_ip(request))
        messages.success(request, f"Added {pack.name}.")
    else:
        messages.error(request, "; ".join(e for errs in form.errors.values() for e in errs))
    return redirect("product-detail", pk=pk)


@require_POST
@perm_required("products.manage")
def product_toggle_packaging(request, pk, pack_id):
    pack = get_object_or_404(Packaging, pk=pack_id, product_id=pk)
    pack.active = not pack.active
    pack.save(update_fields=["active"])
    audit.record("product.packaging_toggled", actor=request.user, object_type="product", object_ref=pk,
                 new={"packaging": pack.name, "active": pack.active}, ip=client_ip(request))
    return redirect("product-detail", pk=pk)


@perm_required("products.manage")
def lists(request):
    """Categories and brands on one screen."""
    cat_form, brand_form = CategoryForm(prefix="c"), BrandForm(prefix="b")
    if request.method == "POST":
        if "c-name" in request.POST:
            cat_form = CategoryForm(request.POST, prefix="c")
            if cat_form.is_valid():
                obj = cat_form.save()
                audit.record("category.created", actor=request.user, object_type="category", object_ref=obj.pk)
                return redirect("lists")
        elif "b-name" in request.POST:
            brand_form = BrandForm(request.POST, prefix="b")
            if brand_form.is_valid():
                obj = brand_form.save()
                audit.record("brand.created", actor=request.user, object_type="brand", object_ref=obj.pk)
                return redirect("lists")
    return render(request, "catalog/lists.html", {
        "cat_form": cat_form, "brand_form": brand_form,
        "categories": Category.objects.annotate(n=Count("products")), "brands": Brand.objects.annotate(n=Count("products"))})


# --- Suppliers --------------------------------------------------------------------------------

@perm_required("suppliers.manage")
def supplier_list(request):
    return render(request, "catalog/supplier_list.html", {"suppliers": Supplier.objects.annotate(n=Count("products"))})


@perm_required("suppliers.manage")
def supplier_edit(request, pk=None):
    supplier = get_object_or_404(Supplier, pk=pk) if pk else None
    form = SupplierForm(request.POST or None, instance=supplier)
    if request.method == "POST" and form.is_valid():
        obj = form.save()
        audit.record("supplier.saved", actor=request.user, object_type="supplier", object_ref=obj.pk,
                     new={"name": obj.name, "active": obj.active}, ip=client_ip(request))
        messages.success(request, "Supplier saved.")
        return redirect("supplier-list")
    return render(request, "core/form_page.html", {
        "title": "Edit supplier" if supplier else "New supplier", "form": form, "back": "/manage/suppliers/",
        "subtitle": "Suppliers are optional. Deactivate instead of deleting so past deliveries keep their supplier."})


# --- Shelves ----------------------------------------------------------------------------------

@perm_required("shelves.manage", "products.view")
def shelf_list(request):
    return render(request, "catalog/shelf_list.html", {
        "shelves": Shelf.objects.annotate(n=Count("products")), "can_manage": request.user.can("shelves.manage"),
        "unassigned": Product.objects.filter(active=True, shelf__isnull=True).count()})


@perm_required("shelves.manage")
def shelf_edit(request, pk=None):
    shelf = get_object_or_404(Shelf, pk=pk) if pk else None
    form = ShelfForm(request.POST or None, instance=shelf)
    if request.method == "POST" and form.is_valid():
        obj = form.save()
        audit.record("shelf.saved", actor=request.user, object_type="shelf", object_ref=obj.pk,
                     new={"code": obj.code, "active": obj.active}, ip=client_ip(request))
        messages.success(request, "Shelf saved.")
        return redirect("shelf-detail", pk=obj.pk)
    return render(request, "core/form_page.html", {"title": "Edit shelf" if shelf else "New shelf", "form": form,
                                                   "back": "/manage/shelves/"})


@perm_required("shelves.manage", "products.view")
def shelf_detail(request, pk):
    shelf = get_object_or_404(Shelf, pk=pk)
    can_manage = request.user.can("shelves.manage")
    if request.method == "POST":
        if not can_manage:
            return render(request, "core/forbidden.html", status=403)
        ids = [int(i) for i in request.POST.getlist("product") if i.isdigit()]
        target = None
        if request.POST.get("target"):
            target = get_object_or_404(Shelf, pk=request.POST["target"]) if request.POST["target"].isdigit() else None
        # "Add by code": scan or type a code to put that product on this shelf.
        scan = request.POST.get("scan", "").strip()
        if scan:
            hit = services.find_by_code(scan)
            if hit is None:
                messages.error(request, f"No active product has the code {scan.upper()}.")
                return redirect("shelf-detail", pk=pk)
            ids, target = [hit.product_id], shelf
        if not ids:
            messages.error(request, "Choose at least one product.")
            return redirect("shelf-detail", pk=pk)
        try:
            n = services.move_products_to_shelf(list(Product.objects.filter(pk__in=ids)), target, request.user,
                                                ip=client_ip(request))
            messages.success(request, f"{n} product{'s' if n != 1 else ''} moved to {target.code if target else 'no shelf'}.")
        except services.CatalogError as exc:
            _flash_errors(request, exc)
        return redirect("shelf-detail", pk=pk)
    products = shelf.products.filter(active=True).select_related("brand")
    return render(request, "catalog/shelf_detail.html", {
        "shelf": shelf, "products": products, "can_manage": can_manage,
        "other_shelves": Shelf.objects.filter(active=True).exclude(pk=pk),
        "history": ShelfTransfer.objects.filter(Q(from_shelf=shelf) | Q(to_shelf=shelf)).select_related(
            "product", "from_shelf", "to_shelf", "user")[:15]})


# --- Labels -----------------------------------------------------------------------------------

@perm_required("labels.print")
def label_select(request):
    return render(request, "catalog/label_select.html", {
        "shelves": Shelf.objects.filter(active=True).annotate(n=Count("products")),
        "sizes": labels.size_choices(), "default_size": labels.DEFAULT_SIZE,
        "preselect": request.GET.get("product", "")})


@require_GET
@perm_required("labels.print")
def label_print(request):
    """Render a printable sheet. Products come from a shelf, a list of ids, or scanned/typed codes."""
    products = []
    shelf_id, ids, codes_text = request.GET.get("shelf"), request.GET.getlist("product"), request.GET.get("codes", "")
    if shelf_id and shelf_id.isdigit():
        products = list(Product.objects.filter(shelf_id=shelf_id, active=True))
    else:
        found = {}
        for pid in ids:
            if pid.isdigit():
                for p in Product.objects.filter(pk=pid, active=True):
                    found[p.pk] = p
        for raw in codes_text.replace(",", "\n").splitlines():
            hit = services.find_by_code(raw)
            if hit:
                found[hit.product_id] = hit.product
        products = list(found.values())
    if not products:
        messages.error(request, "No matching active products. Scan or type codes, or choose a shelf.")
        return redirect("label-select")
    try:
        copies = max(1, min(labels.MAX_COPIES, int(request.GET.get("copies", 1))))
    except ValueError:
        copies = 1
    if len(products) * copies > labels.MAX_LABELS:
        messages.error(request, f"That is more than {labels.MAX_LABELS} labels at once. Print in smaller batches.")
        return redirect("label-select")
    params = {"price": "price", "brand": "brand", "shelf": "showshelf", "barcode": "barcode", "qr": "qr", "code": "code"}
    opts = {k: request.GET.get(param) == "1" for k, param in params.items()}
    if not any(opts.values()):  # nothing ticked: sensible defaults
        opts = {"price": True, "brand": False, "shelf": False, "barcode": True, "qr": False, "code": True}
    if opts["price"] and not request.user.can("products.view"):
        opts["price"] = False
    size = labels.size_info(request.GET.get("size", labels.DEFAULT_SIZE))
    items = []
    for product in products:
        code = services.label_code_for(product)
        try:
            bars = barcodes.barcode_svg(code) if opts["barcode"] else ""
            qr = barcodes.qr_svg(code) if opts["qr"] else ""
        except (RuntimeError, ValueError) as exc:
            messages.error(request, f"Cannot draw the code for {product.name}: {exc}")
            return redirect("label-select")
        items.append({"product": product, "code": code, "bars": bars, "qr": qr,
                      "shelf": product.shelf.code if product.shelf_id else ""})
    sheet = [item for item in items for _ in range(copies)]
    audit.record("labels.printed", actor=request.user, object_type="labels",
                 new={"products": len(items), "copies": copies, "size": size["key"], "shelf": shelf_id or None},
                 ip=client_ip(request))
    return render(request, "catalog/label_sheet.html", {"sheet": sheet, "size": size, "opts": opts,
                                                        "shop": ShopSettings.load().shop_name})


# --- JSON for the POS and receiving scan fields -------------------------------------------------

def _product_json(product, packaging=None, code=None):
    unit = product.selling_price
    return {
        "product_id": product.pk, "name": product.name, "sku": product.sku, "brand": product.brand.name if product.brand_id else "",
        "base_unit": product.base_unit, "unit_price": str(unit),
        "pack": ({"id": packaging.pk, "name": packaging.name, "units": packaging.units_per_pack,
                  "price": str(packaging.price)} if packaging else None),
        "price": str(packaging.price if packaging else unit), "units": packaging.units_per_pack if packaging else 1,
        "stock": product.stock_qty, "code": code}


@require_GET
@login_required_json
def api_lookup(request):
    """Exact code lookup, as used after every barcode/QR scan or typed code."""
    if not (request.user.can("sales.make") or request.user.can("stock.receive") or request.user.can("products.view")):
        return JsonResponse({"ok": False, "error": "Not allowed."}, status=403)
    hit = services.find_by_code(request.GET.get("code", ""))
    if hit is None:
        return JsonResponse({"ok": False, "error": "Code not found."}, status=404)
    return JsonResponse({"ok": True, "item": _product_json(hit.product, hit.packaging, hit.code)})


@require_GET
@login_required_json
def api_search(request):
    """Name/brand/code search for the POS search box (F2) and receiving."""
    if not (request.user.can("sales.make") or request.user.can("stock.receive") or request.user.can("products.view")):
        return JsonResponse({"ok": False, "error": "Not allowed."}, status=403)
    q = request.GET.get("q", "").strip()
    if len(q) < 2:
        return JsonResponse({"ok": True, "items": []})
    qs = (Product.objects.filter(active=True)
          .filter(Q(name__icontains=q) | Q(brand__name__icontains=q) | Q(sku__icontains=q.upper()) | Q(codes__code__startswith=q.upper()))
          .distinct().select_related("brand")[:20])
    return JsonResponse({"ok": True, "items": [_product_json(p) for p in qs]})
