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
