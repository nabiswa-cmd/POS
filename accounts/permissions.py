"""Permission catalogue, restricted actions and default roles.

Pure Python (no Django imports) so it can be tested and reused anywhere.
Access is permission-based: roles are just named sets of these codes, and the
manager can edit roles or add new ones without any code change.
"""

PERMISSIONS = {
    # Areas
    "dashboard.view": "Open the manager dashboard",
    "reports.sales.view": "View sales reports",
    "reports.financial.view": "View profit and financial reports",
    "audit.view": "View audit logs",
    "settings.manage": "Change shop, receipt, payment and backup settings",
    "staff.manage": "Create, edit and deactivate staff; reset passwords and PINs",
    "roles.manage": "Create roles and assign permissions",
    # Catalogue and stock
    "products.view": "View products and selling prices",
    "products.manage": "Add and edit products",
    "products.change_cost": "Change product buying costs",
    "products.change_price": "Change product selling prices",
    "stock.view": "View stock levels and history",
    "stock.receive": "Receive stock from suppliers",
    "stock.adjust": "Adjust stock quantities",
    "suppliers.manage": "Manage suppliers",
    "shelves.manage": "Manage shelves and product placement",
    "labels.print": "Print shelf, barcode and QR labels",
    # Selling
    "sales.make": "Use the POS sales screen",
    "sales.hold": "Hold and retrieve sales",
    "sales.view_own": "View own sales",
    "sales.view_all": "View all sales",
    "payments.accept": "Accept payments",
    "discount.apply": "Apply ordinary discounts within policy",
    "receipts.reprint": "Reprint receipts (marked as duplicates)",
    "refunds.request": "Start a refund (needs approval to complete)",
    # Tills
    "shifts.view_own": "View own shift information",
    "shifts.view_all": "View all till shifts and cash-ups",
    "shifts.open": "Open a till shift",
    # Money
    "expenses.manage": "Record and manage expenses",
    # Approval rights (see RESTRICTED_ACTIONS)
    "approve.open_pos": "Authorize opening the POS session",
    "approve.remove_item": "Authorize removing a scanned item",
    "approve.void_sale": "Authorize voiding a sale",
    "approve.price_change": "Authorize a price change at checkout",
    "approve.exceptional_discount": "Authorize an exceptional discount",
    "approve.refund": "Authorize refunds",
    "approve.reopen_shift": "Authorize reopening a closed shift",
    "approve.stock_adjust": "Authorize stock adjustments",
}

# Actions that can require supervisor/manager approval. The manager decides which
# actually require it (ApprovalPolicy); the permission says who may grant it.
RESTRICTED_ACTIONS = {
    "open_pos": {"label": "Open POS session", "permission": "approve.open_pos"},
    "remove_item": {"label": "Remove scanned item from sale", "permission": "approve.remove_item"},
    "void_sale": {"label": "Void a transaction", "permission": "approve.void_sale"},
    "price_change": {"label": "Change price during checkout", "permission": "approve.price_change"},
    "exceptional_discount": {"label": "Exceptional discount", "permission": "approve.exceptional_discount"},
    "refund": {"label": "Process a refund", "permission": "approve.refund"},
    "reopen_shift": {"label": "Reopen a closed shift", "permission": "approve.reopen_shift"},
    "stock_adjust": {"label": "Adjust stock", "permission": "approve.stock_adjust"},
}

METHOD_PASSWORD = "PASSWORD"
METHOD_PIN = "PIN"
METHOD_BIOMETRIC = "BIOMETRIC"
METHODS = (METHOD_PASSWORD, METHOD_PIN, METHOD_BIOMETRIC)

# Which permission is needed to enter each desktop area after login.
AREA_PERMS = {
    "manager": "dashboard.view",
    "receiving": "stock.receive",
}

_SUPERVISOR_APPROVALS = [
    "approve.open_pos",
    "approve.remove_item",
    "approve.void_sale",
    "approve.price_change",
    "approve.exceptional_discount",
    "approve.refund",
    "approve.stock_adjust",
]

# Manager is special-cased as "all permissions" (is_manager=True), so it is not listed.
DEFAULT_ROLES = {
    "Supervisor": {
        "description": "Controlled administrative access; approves restricted actions.",
        "permissions": [
            "dashboard.view", "reports.sales.view", "products.view", "stock.view",
            "labels.print", "sales.make", "sales.hold", "sales.view_all", "payments.accept",
            "receipts.reprint", "refunds.request", "shifts.view_all", "shifts.open",
        ] + _SUPERVISOR_APPROVALS,
    },
    "Cashier": {
        "description": "POS sales only.",
        "permissions": [
            "products.view", "sales.make", "sales.hold", "sales.view_own",
            "payments.accept", "refunds.request", "shifts.view_own",
        ],
    },
    "Receiving Clerk": {
        "description": "Receives stock from suppliers.",
        "permissions": ["products.view", "stock.view", "stock.receive"],
    },
}

for _name, _spec in DEFAULT_ROLES.items():
    _unknown = set(_spec["permissions"]) - set(PERMISSIONS)
    assert not _unknown, f"{_name} references unknown permissions: {_unknown}"
for _action, _spec in RESTRICTED_ACTIONS.items():
    assert _spec["permission"] in PERMISSIONS, _action
