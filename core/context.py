"""Template context for the manager/receiving workspace: navigation filtered by permission."""

NAV = [
    ("Products", "/manage/products/", ("products.view",)),
    ("Stock", "/manage/stock/", ("stock.view",)),
    ("Receiving", "/manage/receiving/", ("stock.receive", "stock.approve_receiving")),
    ("Shelves", "/manage/shelves/", ("shelves.manage", "products.view")),
    ("Labels", "/manage/labels/", ("labels.print",)),
    ("Tills & sales", "/manage/tills/", ("shifts.view_all", "sales.view_all")),
    ("Sales reports", "/manage/sales-report/", ("reports.sales.view",)),
    ("Reconciliation", "/manage/reconciliation/", ("shifts.view_all", "payments.reconcile")),
    ("Refunds", "/manage/refunds/", ("shifts.view_all", "approve.refund")),
    ("Cash-ups", "/manage/cashups/", ("shifts.view_all",)),
    ("Expenses", "/manage/expenses/", ("expenses.manage",)),
    ("Profit & reports", "/manage/reports/", ("reports.financial.view",)),
    ("Backups", "/manage/backups/", ("settings.manage",)),
    ("Suppliers", "/manage/suppliers/", ("suppliers.manage",)),
    ("Categories & brands", "/manage/lists/", ("products.manage",)),
]


def workspace(request):
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    perms = user.permission_codes() if user.is_active else set()
    nav = [{"label": label, "url": url, "on": request.path.startswith(url)} for label, url, needed in NAV if any(p in perms for p in needed)]
    return {"nav": nav, "perms": perms, "staff_name": user.display_name}
