"""Numbers behind the Charts screen: sales over time, best sellers, busy hours, payment mix.

Sales are completed sales (Sale.total, VAT included) on the completed day in the shop's time zone. Refunds are shown
separately and are not subtracted from the daily line, so the chart matches the till's takings.
"""
from datetime import timedelta

from django.db.models import Count, F, Sum
from django.db.models.functions import ExtractHour, TruncDate

from catalog.models import Product

from .models import Payment, Refund, Sale, SaleItem

MAX_DAYS = 400
TOP_N = 10


def _num(x):
    return float(x or 0)


def build(day_from, day_to):
    if (day_to - day_from).days > MAX_DAYS:
        day_from = day_to - timedelta(days=MAX_DAYS)
    sales = Sale.objects.filter(status=Sale.COMPLETED, completed_at__date__range=(day_from, day_to))
    items = SaleItem.objects.filter(sale__in=sales)

    by_day = {r["d"]: r for r in sales.annotate(d=TruncDate("completed_at")).values("d")
              .annotate(total=Sum("total"), n=Count("id"))}
    daily, d = [], day_from
    while d <= day_to:
        r = by_day.get(d)
        daily.append({"day": d.isoformat(), "total": _num(r["total"]) if r else 0, "count": r["n"] if r else 0})
        d += timedelta(days=1)

    hours = {r["h"]: r for r in sales.annotate(h=ExtractHour("completed_at")).values("h")
             .annotate(total=Sum("total"), n=Count("id"))}
    hourly = [{"hour": h, "total": _num(hours[h]["total"]) if h in hours else 0, "count": hours[h]["n"] if h in hours else 0}
              for h in range(24)]

    full = items.filter(total_volume_ml__isnull=True)
    per_product = {}
    for r in items.values("product_id", "product_name").annotate(rev=Sum("line_total")):
        per_product[r["product_id"]] = {"name": r["product_name"], "revenue": _num(r["rev"]), "bottles": 0, "measures": 0}
    for r in full.values("product_id").annotate(b=Sum(F("quantity") * F("units_per_sale_unit"))):
        per_product[r["product_id"]]["bottles"] = int(r["b"] or 0)
    for r in items.filter(total_volume_ml__isnull=False).values("product_id").annotate(m=Sum("quantity")):
        per_product[r["product_id"]]["measures"] = int(r["m"] or 0)
    rows = list(per_product.values())
    by_revenue = sorted(rows, key=lambda r: -r["revenue"])[:TOP_N]
    by_units = sorted((r for r in rows if r["bottles"] or r["measures"]),
                      key=lambda r: -(r["bottles"] + r["measures"]))[:TOP_N]

    payments = (Payment.objects.filter(sale__in=sales, status=Payment.CONFIRMED).values("method")
                .annotate(total=Sum("amount"), n=Count("id")).order_by("-total"))
    labels = dict(Payment.METHODS)
    payment_mix = [{"label": labels.get(r["method"], r["method"]), "total": _num(r["total"]), "count": r["n"]} for r in payments]

    categories = items.values("product__category__name").annotate(rev=Sum("line_total")).order_by("-rev")[:TOP_N]
    category_mix = [{"label": r["product__category__name"] or "No category", "total": _num(r["rev"])} for r in categories]

    cashiers = (sales.values("cashier__first_name", "cashier__last_name", "cashier__username")
                .annotate(total=Sum("total"), n=Count("id")).order_by("-total")[:TOP_N])
    cashier_mix = [{"label": (f"{r['cashier__first_name']} {r['cashier__last_name']}".strip() or r["cashier__username"]),
                    "total": _num(r["total"]), "count": r["n"]} for r in cashiers]

    sold_ids = set(per_product)
    slow = (Product.objects.filter(active=True, stock_qty__gt=0).exclude(pk__in=sold_ids)
            .order_by("-stock_qty").values("name", "stock_qty")[:TOP_N])

    total = sum(x["total"] for x in daily)
    count = sum(x["count"] for x in daily)
    best = max(daily, key=lambda x: x["total"]) if daily and total else None
    refunds = Refund.objects.filter(status__in=(Refund.APPROVED, Refund.PAID),
                                    approved_at__date__range=(day_from, day_to)).aggregate(t=Sum("total"))["t"]
    return {
        "from": day_from.isoformat(), "to": day_to.isoformat(),
        "kpi": {"total": total, "count": count, "average": (total / count) if count else 0,
                "best_day": best["day"] if best else None, "best_day_total": best["total"] if best else 0,
                "refunds": _num(refunds)},
        "daily": daily, "hourly": hourly, "top_revenue": by_revenue, "top_units": by_units,
        "payments": payment_mix, "categories": category_mix, "cashiers": cashier_mix,
        "not_selling": [{"name": r["name"], "stock": r["stock_qty"]} for r in slow],
    }
