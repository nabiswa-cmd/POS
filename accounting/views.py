"""The manager's expenses screen: /manage/expenses/ (record, void, look, download)."""
from datetime import datetime, timedelta

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone

from core.decorators import perm_required
from core.http import client_ip

from . import reports, rules, services
from .models import ExpenseCategory


def _day(raw, default):
    try:
        return datetime.strptime(str(raw), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return default


@perm_required("expenses.manage")
def expenses(request):
    today = timezone.localdate()
    if not ExpenseCategory.objects.exists():
        services.ensure_default_categories()
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "add":
                e = services.record_expense(
                    request.user, date=_day(request.POST.get("date"), None), category_id=request.POST.get("category"),
                    amount=request.POST.get("amount"), description=request.POST.get("description"),
                    paid_from=request.POST.get("paid_from"), payee=request.POST.get("payee"),
                    reference=request.POST.get("reference"), shift_id=request.POST.get("shift") or None,
                    ip=client_ip(request))
                messages.success(request, f"Recorded KES {e.amount} for {e.category.name}.")
            elif action == "void":
                e = services.void_expense(request.user, request.POST.get("expense_id"), request.POST.get("reason"),
                                          ip=client_ip(request))
                messages.success(request, f"Expense of KES {e.amount} voided. It stays on record with your reason.")
            else:
                messages.error(request, "Unknown action.")
        except (rules.ExpenseError, PermissionError) as exc:
            messages.error(request, str(exc))
        return redirect(request.get_full_path())
    day_to = _day(request.GET.get("to"), today)
    day_from = _day(request.GET.get("from"), day_to.replace(day=1))
    if day_from > day_to:
        day_from = day_to
    if request.GET.get("csv"):
        resp = HttpResponse(services.csv_for(day_from, day_to), content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = f'attachment; filename="expenses-{day_from.isoformat()}-to-{day_to.isoformat()}.csv"'
        return resp
    from tills.models import CashierShift
    return render(request, "accounting/manage_expenses.html", {
        "day_from": day_from, "day_to": day_to, "today": today,
        "rows": services.listing(day_from, day_to), "total": services.total(day_from, day_to),
        "by_category": services.by_category(day_from, day_to), "by_paid_from": services.by_paid_from(day_from, day_to),
        "categories": ExpenseCategory.objects.filter(active=True), "paid_from": rules.PAID_FROM,
        "open_shifts": CashierShift.objects.select_related("till", "cashier").filter(closed_at__isnull=True).order_by("till__number"),
        "min_reason": rules.MIN_REASON})


@perm_required("reports.financial.view")
def financial_report(request):
    """Profit and loss for a period: /manage/reports/ (owner by default; cashiers never see costs)."""
    today = timezone.localdate()
    day_to = _day(request.GET.get("to"), today)
    day_from = _day(request.GET.get("from"), day_to.replace(day=1))
    if day_from > day_to:
        day_from = day_to
    csv_kind = request.GET.get("csv")
    if csv_kind in ("summary", "products", "days"):
        body = {"summary": reports.csv_summary, "products": reports.csv_products, "days": reports.csv_days}[csv_kind](day_from, day_to)
        resp = HttpResponse(body, content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = f'attachment; filename="{csv_kind}-{day_from.isoformat()}-to-{day_to.isoformat()}.csv"'
        return resp
    first_this = today.replace(day=1)
    last_prev = first_this - timedelta(days=1)
    presets = [("Today", today, today), ("This month", first_this, today),
               ("Last month", last_prev.replace(day=1), last_prev)]
    return render(request, "accounting/financial_report.html", {
        "r": reports.report(day_from, day_to), "day_from": day_from, "day_to": day_to, "today": today, "presets": presets})
