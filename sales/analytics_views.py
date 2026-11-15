"""Charts screen: /manage/charts/ (page) and /manage/charts/data/ (JSON for the page)."""
from datetime import datetime, timedelta

from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone

from core.decorators import perm_required

from . import analytics


def _day(raw, default):
    try:
        return datetime.strptime(str(raw), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return default


def _range(request):
    today = timezone.localdate()
    day_to = _day(request.GET.get("to"), today)
    day_from = _day(request.GET.get("from"), day_to - timedelta(days=29))
    return (day_to, day_to) if day_from > day_to else (day_from, day_to)


@perm_required("reports.sales.view")
def charts(request):
    day_from, day_to = _range(request)
    today = timezone.localdate()
    week = today - timedelta(days=today.weekday())
    month = today.replace(day=1)
    last_month_end = month - timedelta(days=1)
    presets = [("Today", today, today), ("Last 7 days", today - timedelta(days=6), today),
               ("Last 30 days", today - timedelta(days=29), today), ("This week", week, today),
               ("This month", month, today), ("Last month", last_month_end.replace(day=1), last_month_end),
               ("Last 90 days", today - timedelta(days=89), today)]
    return render(request, "sales/charts.html", {"day_from": day_from, "day_to": day_to, "today": today, "presets": presets})


@perm_required("reports.sales.view")
def charts_data(request):
    day_from, day_to = _range(request)
    response = JsonResponse(analytics.build(day_from, day_to))
    response["Cache-Control"] = "no-store"
    return response
