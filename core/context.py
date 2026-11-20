"""Template context for the manager/receiving workspace: navigation filtered by permission.

Navigation is grouped for the sidebar. Reports is ONE entry; its three screens (Sales reports, Charts, Profit &
reports) are shown as tabs on each of those pages (core/_report_tabs.html). Every URL and permission is unchanged.
"""

# Stroke icons (24x24 viewBox, drawn with a 1.7 stroke). One path string per icon.
ICONS = {
    "home": "M3 11l9-8 9 8M5 10v10h5v-6h4v6h5V10",
    "box": "M21 8l-9-5-9 5v8l9 5 9-5V8zM3 8l9 5 9-5M12 13v8",
    "truck": "M1 6h13v10H1zM14 10h4l3 3v3h-7M6 19a2 2 0 1 0 0-.01M17 19a2 2 0 1 0 0-.01",
    "shelf": "M3 4h18M3 12h18M3 20h18M6 4v8M12 12v8M18 4v8",
    "tag": "M3 12V4h8l10 10-8 8L3 12zM7.5 8h.01",
    "till": "M4 3h16v6H4zM4 9h16v12H4zM8 13h2M8 17h2M14 13h2M14 17h2",
    "chart": "M4 20V10M10 20V4M16 20v-7M22 20H2",
    "reports": "M6 3h9l4 4v14H6zM14 3v5h5M9 13h7M9 17h7",
    "check": "M4 12l5 5L20 6",
    "undo": "M9 14L4 9l5-5M4 9h10a6 6 0 0 1 0 12h-3",
    "cash": "M3 7h18v10H3zM12 12m-2.5 0a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0-5 0M6 10v.01M18 14v.01",
    "wallet": "M3 7h15a3 3 0 0 1 3 3v8H6a3 3 0 0 1-3-3V7zM3 7l12-3v3M17 13h.01",
    "backup": "M12 3v12M7 10l5 5 5-5M4 20h16",
    "sync": "M20 8A8 8 0 0 0 5 6M4 4v4h4M4 16a8 8 0 0 0 15 2M20 20v-4h-4",
    "people": "M16 19v-1a4 4 0 0 0-4-4H7a4 4 0 0 0-4 4v1M9.5 10a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7M21 19v-1a4 4 0 0 0-3-3.9",
    "list": "M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01",
    "search": "M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM21 21l-4.3-4.3",
    "bell": "M18 8a6 6 0 1 0-12 0c0 7-3 9-3 9h18s-3-2-3-9M13.7 21a2 2 0 0 1-3.4 0",
    "menu": "M4 6h16M4 12h16M4 18h16",
    "bag": "M5 7h14l-1 13H6L5 7zM9 7a3 3 0 0 1 6 0",
}

REPORT_PATHS = ("/manage/sales-report/", "/manage/charts/", "/manage/reports/")

# (group, label, url, icon, permissions needed, extra paths that also mark it active)
NAV = [
    ("Stock", "Products", "/manage/products/", "tag", ("products.view",), ()),
    ("Stock", "Stock", "/manage/stock/", "box", ("stock.view",), ()),
    ("Stock", "Receiving", "/manage/receiving/", "truck", ("stock.receive", "stock.approve_receiving"), ()),
    ("Stock", "Shelves", "/manage/shelves/", "shelf", ("shelves.manage", "products.view"), ()),
    ("Stock", "Labels", "/manage/labels/", "tag", ("labels.print",), ()),
    ("Sales", "Tills & sales", "/manage/tills/", "till", ("shifts.view_all", "sales.view_all"), ()),
    ("Sales", "Cash-ups", "/manage/cashups/", "cash", ("shifts.view_all",), ()),
    ("Sales", "Refunds", "/manage/refunds/", "undo", ("shifts.view_all", "approve.refund"), ()),
    ("Sales", "Reconciliation", "/manage/reconciliation/", "check", ("shifts.view_all", "payments.reconcile"), ()),
    ("Reports", "Reports", "/manage/sales-report/", "reports", ("reports.sales.view", "reports.financial.view"), REPORT_PATHS),
    ("Money", "Expenses", "/manage/expenses/", "wallet", ("expenses.manage",), ()),
    ("Setup", "Suppliers", "/manage/suppliers/", "people", ("suppliers.manage",), ()),
    ("Setup", "Categories & brands", "/manage/lists/", "list", ("products.manage",), ()),
    ("Setup", "Backups", "/manage/backups/", "backup", ("settings.manage",), ()),
    ("Setup", "Server sync", "/manage/sync/", "sync", ("settings.manage",), ()),
    ("Setup", "Staff", "/manage/staff/", "people", ("staff.manage",), ()),
]

REPORT_TABS = [
    ("Sales reports", "/manage/sales-report/", ("reports.sales.view",)),
    ("Charts", "/manage/charts/", ("reports.sales.view",)),
    ("Profit & reports", "/manage/reports/", ("reports.financial.view",)),
]


def workspace(request):
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    perms = user.permission_codes() if user.is_active else set()
    path = request.path
    nav = []
    for group, label, url, icon, needed, extra in NAV:
        if not any(p in perms for p in needed):
            continue
        nav.append({"group": group, "label": label, "url": url, "icon": ICONS[icon],
                    "on": path.startswith(url) or any(path.startswith(e) for e in extra)})
    report_tabs = [{"label": label, "url": url, "on": path.startswith(url)}
                   for label, url, needed in REPORT_TABS if any(p in perms for p in needed)]
    return {"nav": nav, "perms": perms, "staff_name": user.display_name,
            "report_tabs": report_tabs, "icons": ICONS, "home_on": path.rstrip("/") == "/manage",
            "quick_search_on": path.startswith("/manage/products/"),
            "quick_alerts_on": path.startswith("/manage/stock/")}
