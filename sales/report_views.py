"""Management sales and partial-stock reports."""
from datetime import datetime, timedelta

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

    context = {
        "r": reporting.report(day_from, day_to), "day_from": day_from, "day_to": day_to,
        "today": today, "presets": presets,
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
