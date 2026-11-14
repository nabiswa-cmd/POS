# Partial quantity selling: progress

## Phase 1: inspection
Reported complete by the project owner. The Phase 1 documents were not in the pushed repository at the start
of Phase 2 (see the note in `PARTIAL_SELLING_ARCHITECTURE.md`).

## Phase 2: database models (done)
Created
- `inventory/migrations/0002_partial_selling.py`
- `catalog/migrations/0003_partial_selling.py`
- `sales/migrations/0009_partial_selling.py`
- `inventory/test_partial_models.py` (19 tests)
- `sales/test_partial_sale_items.py` (6 tests)
- `PARTIAL_SELLING_ARCHITECTURE.md`, `PARTIAL_SELLING_PROGRESS.md`

Modified
- `catalog/models.py`: `SellingUnit`
- `inventory/models.py`: `Bottle`; `StockMovement.bottle`, `.volume_ml`, `.bottle_remaining_after_ml`; constraints
- `inventory/movement_rules.py`: four bottle movement types, `BOTTLE_TYPES`, `check_bottle_movement`; `check_quantity` refuses bottle types
- `sales/models.py`: `SaleItem` selling-unit snapshot fields, constraints, `is_partial`, `base_qty` returns 0 for partial lines
- `catalog/admin.py`, `inventory/admin.py`: read-only registration of `SellingUnit` and `Bottle`

Verification (SQLite, `DB_ENGINE=sqlite`)
- `manage.py check`: no issues. `makemigrations --check`: no changes pending.
- New tests: 25 pass.
- Full suite: 648 tests. Failures/errors (12 + 12) are the **same 24 tests that failed before any change**
  (`sales.test_cashup`, `sales.test_phase4c`, `sales.tests_screen`); nothing new fails.

Not done in this phase (by design): services, manager screens, POS interface, checkout.

## Phase 3: backend inventory services (done)

Implemented in `inventory/services.py`:
- Open sealed bottles into tracked `Bottle` rows, with a `BOTTLE_OPEN` movement and cost basis.
- Sell a whole-number quantity of an active `SellingUnit` or a requested millilitre volume. Existing opened
  bottles are consumed oldest-first; sealed bottles open only when needed.
- Record volume wastage/spillage and audited per-bottle volume adjustments. Empty bottles remain tracked.
- Query sealed/opened/empty counts and total remaining millilitres with `partial_stock`.
- Require an operation key for each stock-changing service operation and enforce unique operation/sequence
  pairs in the database to block duplicate movement posting.
- Use transactions and product/bottle row locks; prevent negative stock and reject insufficient liquid.

Added `inventory/migrations/0003_stockmovement_operation_key_and_more.py` and
`inventory/test_partial_services.py`.

Verification:
- `manage.py makemigrations --check --dry-run`: no changes detected.
- Partial inventory service, inventory model, and partial sale-item tests: 36 pass.
- Test coverage includes TOT and half-bottle sales, consecutive sales from one open bottle, opening, duplicate
  operation prevention, insufficient stock rollback, wastage/spillage, empty bottles, and volume adjustments.
- Tests ran on SQLite; production row-lock concurrency should also be verified on PostgreSQL.

Next: connect the inventory service and `SaleItem` creation in checkout as a single transaction, then implement
the POS workflow without changing payment processing.

## Phase 4: selling unit administration (done)

Created
- `catalog/test_selling_units.py` (40 tests): configuration, validation, permissions, cashier selection, screens
- `inventory/test_partial_admin.py` (31 tests): overview, bottle screens, authorized wastage and adjustments
- `catalog/templates/catalog/selling_units.html`, `inventory/templates/inventory/bottle_stock.html`, `bottle_form.html`

Modified
- `catalog/services.py`: `create_selling_unit`, `update_selling_unit`, `set_selling_unit_active`, `sellable_units`, `get_sellable_unit`
- `catalog/forms.py`, `catalog/views.py`, `catalog/urls.py`: `SellingUnitForm`, list/add/edit/toggle screens, read-only JSON list for cashiers
- `catalog/admin.py`, `inventory/admin.py`: richer read-only admin (selling-unit inline, `BottleAdmin`)
- `inventory/services.py`: `bottle_overview`; `sell_measures` refuses units of inactive products
- `inventory/forms.py`, `inventory/views.py`, `inventory/urls.py`: bottle overview, wastage and bottle adjustment screens
- `catalog/templates/catalog/product_detail.html`, `inventory/templates/inventory/stock_history.html`: links
- `PARTIAL_SELLING_ARCHITECTURE.md`, `PARTIAL_SELLING_PROGRESS.md`

Decisions to review
- The Django admin was left read-only (the project's existing rule, so changes get permission checks and audit);
  editing is on the manager screens. No new permission codes, models or migrations.
- Prices must be above 0 in the service and form (the database only forbids negative).
- Creating a unit needs both `products.manage` and `products.change_price`; renaming and switching on/off need only `products.manage`.

Verification (SQLite, `DB_ENGINE=sqlite`)
- `manage.py check`: no issues. `makemigrations --check`: no changes.
- New tests: 71 pass. Full suite: 730 tests; the 24 failures/errors are exactly the same 24 as on an untouched
  checkout (`sales.test_cashup`, `sales.test_phase4c`, `sales.tests_screen`), compared by test name.
- Three rules (price permission, duplicate active volume, inactive-product sale) were deliberately broken to
  confirm the tests fail when the rule is missing; one test that passed for the wrong reason was fixed.
- Not verified: PostgreSQL (row locks, constraints) and manual use of the screens in a browser.

At the end of Phase 4, POS cart, checkout, refunds of partial lines and reports remained.

## Phase 5: cashier POS integration (done)

Implemented manager-configured capacity and connected configured selling units to the existing cashier POS.

Created
- `catalog/migrations/0004_product_bottle_capacity_ml_and_more.py`
- `sales/test_partial_pos.py`

Modified
- `catalog/models.py`, `catalog/forms.py`, `catalog/services.py`: nullable per-product bottle capacity, form
  validation, capacity-change safety checks, and audit entries.
- `inventory/services.py`: configured capacity is used for first bottle openings; historical bottle capacity is
  the fallback when no product capacity is set. Product-before-unit locking is consistent across configuration
  and checkout.
- `sales/services.py`: server-resolved selling-unit cart lines, volume-aware quantity edits and reservation
  checks, and atomic checkout movements for full bottles and partial liquid.
- `sales/views.py`, `sales/templates/sales/pos.html`: load configured units from the backend, select unit and
  quantity, preserve barcode/package and search behavior, and show unit/price/volume in the cart.
- `sales/receipt.py`: partial lines include the saved selling-unit name.
- `catalog/tests.py`: manager capacity, audit, active-unit limit, and sealed-stock immutability tests.
- `PARTIAL_SELLING_ARCHITECTURE.md`

Verification (SQLite)
- Focused Phase 5 and manager-capacity tests: 16 pass.
- Catalog, inventory, and sales suites: 456 tests run; all Phase 5 tests pass. The same 24 pre-existing failures
  documented in Phase 4 remain in `sales.test_cashup`, `sales.test_phase4c`, and legacy `sales.tests_screen`;
  no new failure is reported by the suite.
- `manage.py check`: no issues. `makemigrations --check --dry-run`: no changes detected.
- PostgreSQL concurrency behavior and manual browser interaction were not verified.

Payment processing was not changed. Partial refunds are implemented below; accounting/report treatment remains a follow-up.

## Phase 6A: partial quantity refunds (done)

Created
- `sales/migrations/0010_refund_request_key_refunditem_requested_restock_and_more.py`
- `inventory/migrations/0004_stockmovement_refund_item_and_more.py`
- `sales/test_partial_refunds.py`

Modified
- `sales/models.py`: immutable refund request key and refund-line selling-unit/volume snapshots; retained the
  original refund amount, quantity, approval, and payout model.
- `sales/refunds.py`: calculate amounts from original sale-line totals; restore partial volume during approval;
  include unit and millilitre snapshots in refund JSON and receipts; preserve whole-bottle `REFUND_RETURN`.
- `sales/views.py`, `sales/templates/sales/pos.html`: require the request idempotency key, show selling-unit
  volume in the existing refund dialog, and avoid re-approving an already recorded request.
- `inventory/models.py`, `inventory/movement_rules.py`, `inventory/services.py`: add positive
  `VOLUME_REFUND` movements linked to their original partial-sale movement and refund line; lock and restore
  the original bottles with per-source volume caps and transaction rollback.
- `sales/test_refunds.py`: exercise request-key retries through the existing refund endpoint.
- `PARTIAL_SELLING_ARCHITECTURE.md`

Refund behaviour
- Partial refunds retain the original sale-item quantity, unit name, volume, and price basis; the refund amount
  is prorated from the original `SaleItem.line_total`, not current catalogue configuration.
- Approval restores liquid to the same bottles used by the original partial sale, including restoring empty
  bottles to `OPENED`. It refuses missing source movements, excessive restored volume, and bottle-capacity
  violations without leaving partial ledger or bottle updates behind.
- Each return movement stores its source sale movement, refund line, cashier, authorizing approver, refund
  reference, volume, and resulting balance. Full-bottle returns still use the existing `REFUND_RETURN` flow.
- Exact idempotency-key retries return the original refund. Reusing the key with a different payload is rejected;
  the existing status lock and operation-key uniqueness prevent duplicate stock restoration.
- Existing permissions, approval rules, payout processing, and eTIMS credit-note handling were not changed.

Verification (SQLite)
- Focused refund, full-bottle regression, credit-note, partial POS, and partial inventory model/service tests:
  **91 passed**.
- `manage.py check`: no issues.
- `manage.py makemigrations --check --dry-run`: no changes detected.
- PostgreSQL concurrency behavior and manual browser use were not verified.

Known limitations and Phase 6B
- Accounting and sales reports still need a scoped update so partial units and millilitres are not reported as
  whole bottles. That is the next known partial-selling follow-up (Phase 6B).
- PostgreSQL locking and concurrent request behavior remain unverified; the tests above ran on SQLite.
- No payment-processing integration was necessary for inventory correctness; existing cash and M-PESA refund
  settlement remains unchanged.

