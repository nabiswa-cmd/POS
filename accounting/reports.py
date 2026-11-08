"""Financial figures for a period: sales, cost of goods sold, gross profit, expenses, net profit.

Every number comes from records that are never edited: completed sales and their frozen line costs, refunds, the stock
ledger (which carries the cost of every movement), expenses and cash-ups. Days are shop-local days.
"""
import csv
import io
from datetime import timedelta
from decimal import Decimal as D

from django.db.models import F, Sum

from . import profit_rules as pr
from . import services as expense_services

ZERO = D("0.00")
MAX_DAYS_TABLE = 62          # the day-by-day table is only drawn for ranges up to about two months


def _sum(qs, field):
    return pr.money(qs.aggregate(t=Sum(field))["t"] or 0)


def _sales(day_from, day_to):
    from sales.models import Sale
    return Sale.objects.filter(status=Sale.COMPLETED, completed_at__date__gte=day_from, completed_at__date__lte=day_to)


def _refunds(day_from, day_to):
    """A refund counts from the moment it is approved (that is when the goods come back and the sale is reduced)."""
    from sales.models import Refund
    return Refund.objects.filter(status__in=(Refund.APPROVED, Refund.PAID),
                                 approved_at__date__gte=day_from, approved_at__date__lte=day_to)


def _moves(day_from, day_to, *types):
    from inventory.models import StockMovement
    return StockMovement.objects.filter(movement_type__in=types, created_at__date__gte=day_from,
                                        created_at__date__lte=day_to)


def figures(day_from, day_to):
    """The profit and loss for the period, plus the supporting numbers."""
    from inventory import movement_rules as mr
    from sales.models import SaleItem
    from tills.models import CashUp

    sales = _sales(day_from, day_to)
    refunds = _refunds(day_from, day_to)
    items = SaleItem.objects.filter(sale__in=sales)

    sales_gross, sales_vat = _sum(sales, "total"), _sum(sales, "tax_total")
    refunds_gross, refunds_vat = _sum(refunds, "total"), _sum(refunds, "tax_total")
    cogs = _sum(items, "cost_total")
    cogs_returned = _sum(_moves(day_from, day_to, mr.REFUND_RETURN), "total_cost")
    adjust_out = _sum(_moves(day_from, day_to, mr.ADJUST).filter(quantity__lt=0), "total_cost")
    adjust_in = _sum(_moves(day_from, day_to, mr.ADJUST).filter(quantity__gt=0), "total_cost")
    damaged = _sum(_moves(day_from, day_to, mr.DAMAGED), "total_cost")
    expired = _sum(_moves(day_from, day_to, mr.EXPIRED), "total_cost")
    losses = pr.stock_losses(damaged, expired, adjust_out, adjust_in)
    expenses = expense_services.total(day_from, day_to)
    till_diff = _sum(CashUp.objects.filter(superseded_at__isnull=True, closed_at__date__gte=day_from,
                                           closed_at__date__lte=day_to), "variance")
    nsales = pr.net_sales(sales_gross, sales_vat, refunds_gross, refunds_vat)
    ncogs = pr.net_cogs(cogs, cogs_returned)
    pl = pr.profit_and_loss(net_sales=nsales, cogs=ncogs, losses=losses, expenses=expenses, till_difference=till_diff)
    bought = _sum(_moves(day_from, day_to, mr.RECEIVE), "total_cost") - _sum(_moves(day_from, day_to, mr.SUPPLIER_RETURN), "total_cost")
    return {
        "day_from": day_from, "day_to": day_to,
        "sales_count": sales.count(), "sales_gross": sales_gross, "sales_vat": sales_vat,
        "refunds_count": refunds.count(), "refunds_gross": refunds_gross, "refunds_vat": refunds_vat,
        "net_sales": nsales, "cogs": cogs, "cogs_returned": cogs_returned, "net_cogs": ncogs,
        "damaged": damaged, "expired": expired, "adjust_out": adjust_out, "adjust_in": adjust_in, "stock_losses": losses,
        "expenses": expenses, "till_difference": till_diff, "stock_bought": pr.money(bought),
        **pl,
        # Lines sold with no recorded cost make COGS too low. Should never happen; shown loudly if it does.
        "unknown_cost_lines": items.filter(cost_total__isnull=True).count(),
    }


def by_day(day_from, day_to):
    if (day_to - day_from).days + 1 > MAX_DAYS_TABLE:
        return []
    rows, day = [], day_from
    while day <= day_to:
        f = figures(day, day)
        if f["sales_count"] or f["refunds_count"] or f["expenses"] or f["stock_losses"] or f["till_difference"]:
            rows.append(f)
        day += timedelta(days=1)
    return rows


def by_product(day_from, day_to):
    """Per product: units, net sales, cost, gross profit, margin. Net of refunds, so the rows add up to the totals."""
    from inventory import movement_rules as mr
    from sales.models import RefundItem, SaleItem

    sold = (SaleItem.objects.filter(sale__in=_sales(day_from, day_to)).values("product_id", "product_name")
            .annotate(units=Sum(F("quantity") * F("units_per_sale_unit")), gross=Sum("line_total"),
                      vat=Sum("tax_amount"), cost=Sum("cost_total")))
    data = {}
    for r in sold:
        d = data.setdefault(r["product_id"], {"name": r["product_name"], "units": 0, "sales": ZERO, "cost": ZERO})
        d["units"] += r["units"] or 0
        d["sales"] += (r["gross"] or ZERO) - (r["vat"] or ZERO)
        d["cost"] += r["cost"] or ZERO
    back = (RefundItem.objects.filter(refund__in=_refunds(day_from, day_to))
            .values("sale_item__product_id", "sale_item__product_name")
            .annotate(gross=Sum("amount"), vat=Sum("tax_amount")))
    for r in back:
        d = data.setdefault(r["sale_item__product_id"], {"name": r["sale_item__product_name"], "units": 0, "sales": ZERO, "cost": ZERO})
        d["sales"] -= (r["gross"] or ZERO) - (r["vat"] or ZERO)
    for r in _moves(day_from, day_to, mr.REFUND_RETURN).values("product_id", "product__name").annotate(c=Sum("total_cost")):
        d = data.setdefault(r["product_id"], {"name": r["product__name"], "units": 0, "sales": ZERO, "cost": ZERO})
        d["cost"] -= r["c"] or ZERO
    rows = []
    for pid, d in data.items():
        sales, cost = pr.money(d["sales"]), pr.money(d["cost"])
        profit = pr.money(sales - cost)
        rows.append({"product_id": pid, "name": d["name"], "units": d["units"], "sales": sales, "cost": cost,
                     "profit": profit, "margin": pr.margin_pct(profit, sales)})
    rows.sort(key=lambda r: (-r["profit"], r["name"]))
    return rows


def collections(day_from, day_to):
    """Money taken in by way of payment (separate lines, never mixed) less what went back to customers."""
    from sales.models import Payment, Refund
    pay = Payment.objects.filter(sale__in=_sales(day_from, day_to))
    out = {m: _sum(pay.filter(method=m), "amount") for m in (Payment.CASH, Payment.MPESA, Payment.CARD, Payment.BANK)}
    paid_back = Refund.objects.filter(status=Refund.PAID, paid_at__date__gte=day_from, paid_at__date__lte=day_to)
    return {"cash": out[Payment.CASH], "mpesa": out[Payment.MPESA], "card_bank": out[Payment.CARD] + out[Payment.BANK],
            "refunds_cash": _sum(paid_back.filter(method=Refund.CASH), "total"),
            "refunds_mpesa": _sum(paid_back.filter(method=Refund.MPESA), "total")}


def report(day_from, day_to):
    return {"f": figures(day_from, day_to), "days": by_day(day_from, day_to), "products": by_product(day_from, day_to),
            "expense_categories": expense_services.by_category(day_from, day_to),
            "collections": collections(day_from, day_to), "day_table_limit": MAX_DAYS_TABLE}


# --- CSV for the accountant -----------------------------------------------------------------------------------

def csv_summary(day_from, day_to):
    f = figures(day_from, day_to)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Line", "KES"])
    for label, key in [("Sales (incl. VAT)", "sales_gross"), ("VAT in sales", "sales_vat"), ("Refunds (incl. VAT)", "refunds_gross"),
                       ("VAT in refunds", "refunds_vat"), ("Net sales (excl. VAT)", "net_sales"),
                       ("Cost of goods sold", "cogs"), ("Refunded goods put back (cost)", "cogs_returned"),
                       ("Net cost of goods sold", "net_cogs"), ("Gross profit", "gross_profit"),
                       ("Stock losses", "stock_losses"), ("Expenses", "expenses"), ("Till cash differences", "till_difference"),
                       ("Net profit", "net_profit"), ("Stock bought (not a cost of sales)", "stock_bought")]:
        w.writerow([label, f[key]])
    w.writerow([])
    w.writerow(["Expenses by category", "KES"])
    for name, amount in expense_services.by_category(day_from, day_to):
        w.writerow([name, amount])
    return out.getvalue()


def csv_products(day_from, day_to):
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Product", "Units sold", "Net sales", "Cost of goods", "Gross profit", "Margin %"])
    for r in by_product(day_from, day_to):
        w.writerow([r["name"], r["units"], r["sales"], r["cost"], r["profit"], r["margin"] if r["margin"] is not None else ""])
    return out.getvalue()


def csv_days(day_from, day_to):
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Date", "Net sales", "Net cost of goods", "Gross profit", "Stock losses", "Expenses", "Till differences", "Net profit"])
    for f in by_day(day_from, day_to):
        w.writerow([f["day_from"].isoformat(), f["net_sales"], f["net_cogs"], f["gross_profit"], f["stock_losses"],
                    f["expenses"], f["till_difference"], f["net_profit"]])
    return out.getvalue()
