"""Management sales and partial-stock reports."""
from datetime import datetime, timedelta
from decimal import Decimal

from django.core.paginator import Paginator
from django.shortcuts import render
from django.utils import timezone

from catalog.services import can_see_cost
from core.decorators import perm_required

from . import reporting

MOVEMENT_PAGE_SIZE = 50


def _day(raw, default):
    try:
        return datetime.strptime(str(raw), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return default


@perm_required("reports.sales.view")
def sales_report(request):
    today = timezone.localdate()
    day_to = _day(request.GET.get("to"), today)
    day_from = _day(request.GET.get("from"), day_to.replace(day=1))
    if day_from > day_to:
        day_from = day_to

    start_of_week = today - timedelta(days=today.weekday())
    start_of_last_week = start_of_week - timedelta(days=7)
    first_this_month = today.replace(day=1)
    last_month_end = first_this_month - timedelta(days=1)
    presets = [
        ("Today", today, today),
        ("This week", start_of_week, today),
        ("Last week", start_of_last_week, start_of_week - timedelta(days=1)),
        ("This month", first_this_month, today),
        ("Last month", last_month_end.replace(day=1), last_month_end),
    ]

    report = reporting.report(day_from, day_to)
    daily_rows = {}
    product_totals = {}
    for row in report["sales_by_cashier"]:
        daily = daily_rows.setdefault(row["day"], {"sales": 0, "revenue": Decimal("0.00")})
        daily["sales"] += row["sale_count"]
        daily["revenue"] += row["sales_revenue"]
    for row in report["selling_units"]:
        product = product_totals.setdefault(
            row["product_id"], {"name": row["product_name"], "units": 0, "revenue": Decimal("0.00")})
        product["units"] += row["quantity_sold"]
        product["revenue"] += row["sales_gross"]

    chart_start = day_from if (day_to - day_from).days < 31 else day_to - timedelta(days=30)
    chart_days = []
    day = chart_start
    while day <= day_to:
        values = daily_rows.get(day, {"sales": 0, "revenue": Decimal("0.00")})
        chart_days.append({"day": day, **values})
        day += timedelta(days=1)
    max_daily_revenue = max((row["revenue"] for row in chart_days), default=Decimal("0.00"))
    for row in chart_days:
        row["width"] = round(row["revenue"] * 100 / max_daily_revenue) if max_daily_revenue else 0

    best_products = sorted(product_totals.values(), key=lambda item: (-item["units"], -item["revenue"], item["name"].casefold()))[:10]
    max_product_units = max((values["units"] for values in best_products), default=0)
    products_chart = [
        {**values, "width": round(values["units"] * 100 / max_product_units) if max_product_units else 0}
        for values in best_products
    ]

    context = {
        "r": report, "day_from": day_from, "day_to": day_to,
        "today": today, "presets": presets,
        "daily_chart": chart_days, "product_chart": products_chart,
        "chart_start": chart_start,
        "show_stock": request.user.can("stock.view"),
        "show_audit": request.user.can("audit.view"),
        "show_cost": can_see_cost(request.user),
    }
    if context["show_stock"]:
        context["inventory"] = reporting.inventory_position()
        context["movement_summary"] = reporting.movement_summary(day_from, day_to)
        movement_rows = reporting.stock_movements(day_from, day_to).select_related(
            "product", "user", "approved_by", "bottle").order_by("-created_at", "-pk")
        context["movement_page"] = Paginator(movement_rows, MOVEMENT_PAGE_SIZE).get_page(
            request.GET.get("movement_page"))
    if context["show_audit"]:
        activity_page = Paginator(reporting.audit_activity(day_from, day_to), MOVEMENT_PAGE_SIZE).get_page(
            request.GET.get("activity_page"))
        context["activity_page"] = activity_page
    return render(request, "sales/sales_report.html", context)
