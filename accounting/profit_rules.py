"""Pure profit maths (no Django).

    Net sales      = sales without VAT - refunds without VAT
    Cost of goods  = cost of the stock that was SOLD (frozen on each sale line) - cost of refunded goods put back on the shelf
    Gross profit   = net sales - cost of goods sold
    Net profit     = gross profit - stock losses - expenses - till cash shortages (+ overages)

Profit is never worked out from selling prices alone, and never from what was BOUGHT in the period (stock bought is
shown separately: it is cash spent on stock still on the shelf, not a cost of this period's sales).
"""
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def money(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def margin_pct(profit, sales):
    """Profit as a percentage of net sales, one decimal place. None when there were no sales (no meaningful margin)."""
    sales = Decimal(sales)
    if sales <= 0:
        return None
    return (Decimal(profit) * 100 / sales).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def net_sales(sales_gross, sales_vat, refunds_gross, refunds_vat):
    return money((Decimal(sales_gross) - Decimal(sales_vat)) - (Decimal(refunds_gross) - Decimal(refunds_vat)))


def net_cogs(cogs, cogs_returned):
    return money(Decimal(cogs) - Decimal(cogs_returned))


def stock_losses(damaged, expired, adjust_out, adjust_in):
    """Cost of stock written off, less any stock found. All inputs are positive cost values."""
    return money(Decimal(damaged) + Decimal(expired) + Decimal(adjust_out) - Decimal(adjust_in))


def profit_and_loss(*, net_sales, cogs, losses, expenses, till_difference):
    """till_difference: sum of cash-up variances (negative = short). A shortage lowers profit, an overage raises it."""
    gross = money(Decimal(net_sales) - Decimal(cogs))
    net = money(gross - Decimal(losses) - Decimal(expenses) + Decimal(till_difference))
    return {"gross_profit": gross, "gross_margin": margin_pct(gross, net_sales),
            "net_profit": net, "net_margin": margin_pct(net, net_sales)}
