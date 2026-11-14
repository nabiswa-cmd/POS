# Partial quantity selling: architecture

> Note: the Phase 1 documents were not in the pushed repository when Phase 2 started, so this file was
> (re)written from the source files Phase 2 touched. Merge it with your Phase 1 notes if they differ.

## Existing architecture this builds on (inspected in Phase 2)

| Concern | Where | Notes |
|---|---|---|
| Product, Packaging, ProductCode | `catalog/models.py` | `Product.stock_qty` is whole base units (bottles), only changed by `inventory.services.post_movement` |
| Stock history | `inventory.StockMovement` | Append-only (model guard + PostgreSQL trigger). `quantity` signed int, `balance_after`, cost fields |
| Movement types and sign rules | `inventory/movement_rules.py` (pure) | `check_quantity` validates whole-unit movements |
| Cost layers | `inventory.StockLot` | FIFO / average cost for whole units |
| Sales | `sales.Sale`, `sales.SaleItem` | `SaleItem` snapshots name, prices, tax, cost. Final sales are locked by model guard and trigger |

## Decisions

1. **One inventory system.** Sealed bottles stay in `Product.stock_qty`. Only an *opened* bottle gets a row in
   the new `inventory.Bottle`. Opening a bottle moves one unit out of `stock_qty` and its volume into `Bottle`.
2. **One movement log.** Partial sales, openings, wastage and volume adjustments are `StockMovement` rows
   (new types), not a second ledger. Existing whole-bottle movements are untouched.
3. **Money and volume are `DecimalField`.** Volumes are millilitres, 2 decimal places.
4. **Full-bottle sales are unchanged.** All new `SaleItem` columns are null/blank for them, and a database
   constraint enforces that.
5. **Sealed capacity is configured per product.** `Product.bottle_capacity_ml` is the authoritative capacity
   for opening sealed stock; existing bottles retain their own capacity snapshots.

### `catalog.Product` capacity (migration `catalog/0004_product_bottle_capacity_ml_and_more`)
`bottle_capacity_ml` is nullable for products that do not use partial selling. Managers set it in the existing
product form before relying on first-time bottle opening. It must be positive and at least as large as every
active selling-unit volume. A configured capacity cannot be changed while sealed stock remains, and it must
match all currently opened bottles. Capacity changes are audited. If it is unset, inventory services may use
the capacity recorded by bottle history; they never guess a size.

## Data model (Phase 2)

### `catalog.SellingUnit` (migration `catalog/0003_partial_selling`)
`product` FK (CASCADE, `related_name="selling_units"`), `name`, `volume_ml` Decimal(10,2), `selling_price`
Decimal(12,2), `active`, `created_at`.
Constraints: unique `(product, name)`; unique `(product, volume_ml)` among active units; `volume_ml > 0`;
`selling_price >= 0`.

### `inventory.Bottle` (migration `inventory/0002_partial_selling`)
`product` FK (PROTECT), `capacity_ml`, `remaining_ml`, `status` (`SEALED`/`OPENED`/`EMPTY`), `opened_at`,
`opened_by`, `created_at`. Ordered oldest-opened first.
Constraints: `capacity_ml > 0`; `0 <= remaining_ml <= capacity_ml`; status must match volume
(SEALED = full and never opened, OPENED = some left and has `opened_at`, EMPTY = 0 left).

### `inventory.StockMovement` (extended)
New nullable columns: `bottle` FK (PROTECT), `volume_ml` (signed), `bottle_remaining_after_ml`.
New types in `movement_rules`: `BOTTLE_OPEN`, `PARTIAL_SALE`, `VOLUME_WASTAGE`, `VOLUME_ADJUST`, and
`VOLUME_REFUND` (`BOTTLE_TYPES`). Refund movements also point to the source `PARTIAL_SALE` movement and
the immutable `sales.RefundItem` snapshot; other movement types cannot set those provenance links.

| Type | `quantity` | `volume_ml` |
|---|---|---|
| `BOTTLE_OPEN` | -1 (sealed bottle leaves `stock_qty`) | + capacity |
| `PARTIAL_SALE` | 0 | negative |
| `VOLUME_WASTAGE` | 0 | negative |
| `VOLUME_ADJUST` | 0 | either sign, non-zero |
| `VOLUME_REFUND` | 0 | positive; linked to the source sale movement and refund line |

Constraint `movement_quantity_not_zero` was replaced by `movement_has_quantity_or_volume` (quantity is non-zero
OR volume is non-zero), plus `movement_volume_needs_bottle`. `check_quantity` now refuses bottle types, so they
cannot be posted through the whole-unit path by mistake. `check_bottle_movement` validates them.
Wastage of a *sealed* bottle keeps using the existing `DAMAGED` / `EXPIRED` types. Partial-volume services
set `operation_key` and `operation_sequence`; a conditional unique constraint prevents duplicate movements
when an operation is retried.

### `sales.SaleItem` (migration `sales/0009_partial_selling`)
Added: `selling_unit` FK (PROTECT, nullable), `selling_unit_name`, `volume_per_unit_ml`, `total_volume_ml`.
Reused, not duplicated: `quantity` (number of measures), `unit_price` (price per measure, the price snapshot),
`list_price`, `line_total`, tax and `cost_total` fields.
Constraints: volume snapshot is all-or-nothing, `volume_per_unit_ml > 0`, name present, and
`total_volume_ml = quantity x volume_per_unit_ml`; a partial line cannot also carry a `packaging`.
`SaleItem.is_partial` is new. `SaleItem.base_qty` returns **0** for partial lines so existing stock-deduction
and costing code can never take whole bottles for a measure.

## Phase 3: inventory services

`inventory.services` now provides:
- `open_bottle`: removes one sealed bottle from `Product.stock_qty`, creates an opened `Bottle`, and records
  the opening movement.
- `sell_measures` / `sell_volume`: use a saved active `SellingUnit` or an explicit millilitre quantity;
  consume opened bottles oldest-first and open sealed stock only when needed.
- `record_wastage` / `record_spillage`: deduct volume only from already opened bottles.
- `adjust_bottle_volume`: audited volume reconciliation, including transitions to/from `EMPTY`.
- `partial_stock`: reports sealed/opened/empty bottle counts and remaining volume.

All service operations run in database transactions. Mutations lock the product first and then the affected
bottles; selling-unit changes use the same product-before-unit lock order. A repeated operation key is
rejected, and the database uniqueness constraint is the final duplicate guard. Each movement records the
user, bottle, signed millilitres, remaining volume, reference/reason, and cost basis where known. Wastage and
volume adjustments follow the existing `stock.adjust` approval and audit rules. When sealed bottles need
opening, services use the configured product capacity first and bottle history as a fallback.

## Phase 4: selling unit administration

Managers configure measures per product and review bottle stock. Nothing here touches the POS cart, checkout
or payments.

### Where it lives
The Django admin stays **read-only**, as it already was for every catalogue and stock model (see the docstring
in `catalog/admin.py`): changes go through the manager screens, which check the project's role permissions and
write the audit trail. Phase 4 improved the read-only admin (`SellingUnitAdmin`, a selling-unit inline on
`ProductAdmin`, `BottleAdmin` with status/remaining/opened columns and a status filter) and added manager screens.

| Screen | URL | Permission |
|---|---|---|
| Selling units list (+ add form) | `/manage/products/<id>/selling-units/` | `products.view` to look; forms need the rows below |
| Add unit | `.../selling-units/new/` (POST) | `products.manage` + `products.change_price` |
| Edit unit | `.../selling-units/<unit>/edit/` | `products.manage` (price field only with `products.change_price`) |
| Turn unit on/off | `.../selling-units/<unit>/toggle/` (POST) | `products.manage` |
| Bottles, opening history | `/manage/stock/<id>/bottles/` | `stock.view` |
| Record wastage | `/manage/stock/<id>/bottles/wastage/` | `stock.adjust` + `stock_adjust` approval policy |
| Adjust one bottle | `/manage/stock/<id>/bottles/<bottle>/adjust/` | `stock.adjust` + `stock_adjust` approval policy |
| Cashier read-only list | `GET /api/catalog/products/<id>/selling-units/` | `sales.make` or `products.view` |

No new permission codes and no new models or migrations: the existing codes already separate "edit products",
"change prices", "view stock" and "adjust stock".

### Rules (`catalog/services.py`: `create_selling_unit`, `update_selling_unit`, `set_selling_unit_active`)
- Every rule is enforced in the service, not only the form; the forms only hide fields.
- Name: required, trimmed, 40 characters, unique per product ignoring case.
- Volume: greater than 0, at most 2 decimals, not larger than the configured product capacity (or the largest
  bottle on record when no capacity is configured). Two *active* units of one product cannot share a volume;
  a switched-off unit does not block its volume, and turning one back on re-checks the clash.
- Price: greater than 0 (stricter than the database, which allows 0), at most 2 decimals.
- Units cannot be added to an inactive product or enabled while the product is inactive.
- Units are never deleted, only switched off. Editing or disabling a unit does not alter past sales, because
  `SaleItem` keeps its own snapshot of name, volume and price (Phase 2), and does not alter stock or bottles.
- Unchanged values are not re-validated, so a form re-submitted with the same volume or price is a no-op with no audit entry.
- Audit actions: `selling_unit.created`, `.updated` (name/volume), `.price_changed`, `.enabled`, `.disabled`.

### Cashiers
Default Cashier and Receiving Clerk roles hold none of the write permissions and Supervisor holds none of
`products.manage`, `products.change_price` or `stock.adjust`, so they cannot change units, prices or inventory
through screens or by calling the services. For selection, `catalog.services.sellable_units(product)` and
`get_sellable_unit(unit_id)` return only active units of active products with the stored price. The POS loads
these units from the cashier API and posts only a selling-unit ID and quantity. Cart add resolves price and
volume from the backend. Checkout uses the server-side `SaleItem` price and volume snapshots and never accepts
browser-submitted price or volume. Inventory also refuses direct measure sales for inactive units/products.

### Bottle screens
`inventory.services.bottle_overview` (read-only, no lock, never raises) feeds the screen: sealed, opened and empty
bottle counts, liquid left in opened bottles, and sealed/total liquid. Sealed liquid uses configured capacity or
tracked bottle history; it is reported as unknown only when neither is available. History lists the bottle movement types (opening, partial sale, wastage,
adjustment) with who, approver, millilitres, remaining volume and reason, filterable by type; cost columns show
only to people who may see costs.
Wastage and adjustment forms call the Phase 3 `record_wastage` / `adjust_bottle_volume`, so they keep the
reason requirement, capacity and non-negative limits, approval and audit. Each form carries a generated
`operation_key`, so a double submit or refresh is recorded once. Approval follows `stock_adjust`: the shop owner
(manager) acts alone and the audit log records that; everyone else needs a supervisor or manager to type their
credentials, and nobody can approve their own request.

## Phase 5: cashier POS integration

The active `/pos/sell/` screen is `sales/templates/sales/pos.html` and retains its existing barcode, product
search, cart editing, and full-bottle workflows. When a product is scanned or selected, it loads active selling
units from `GET /api/catalog/products/<id>/selling-units/`, offers the base unit plus configured measures, and
lets the cashier choose a quantity. Cart lines show the selling unit, backend unit price, and total volume;
quantity edits update the saved volume snapshot.

`sales.services.add_item` resolves the selected selling-unit ID with `catalog.services.get_sellable_unit`;
browser price and volume fields are ignored. Cart checks reserve both full units and partial millilitres to avoid
mixed-line overselling. At checkout, the shared transactional `finish_sale` posts full-bottle movements and
partial `sell_volume` movements using each line's stored millilitre snapshot, with a unique movement key per
sale line. This preserves the price/volume displayed when the item entered the cart and leaves payment handling
unchanged. Receipt item names now include the selling-unit snapshot.

## Phase 6A: partial quantity refunds

The existing refund request, approval, and payout lifecycle is retained. `RefundItem` snapshots the original
selling-unit name, returned millilitres, quantity, and refund amount. The amount is prorated from the completed
`SaleItem.line_total`; neither current selling-unit prices nor today's product configuration are used.

Approval restores returned millilitres to the same tracked bottles that supplied the original sale. The
transaction locks the product and those bottles, caps each restoration at the original `PARTIAL_SALE` volume,
and records one append-only `VOLUME_REFUND` movement per affected source bottle. Each movement records the
source movement, refund line, refund reference, cashier, authorizing approver, volume, and resulting bottle
balance. Empty bottles return to `OPENED`; capacity violations or incomplete source history abort the entire
approval transaction. Existing full-unit returns continue to use `REFUND_RETURN`.

The cashier request includes a unique idempotency key. Exact retries return the existing refund, key reuse with
a different payload is rejected, and refund status locking plus unique movement operation keys prevent repeated
approval from restoring stock again. Existing permissions, manager approval, cash payout, M-PESA payout, and
eTIMS credit-note handling are unchanged.

### Phase 6A limitations

Validation ran on SQLite. PostgreSQL row-lock and concurrent-cashier behavior, and manual browser interaction,
were not verified. No payment-processing change was required for partial stock restoration.

## Phase 6B: accounting and stock valuation

Accounting uses each immutable `SaleItem` / `RefundItem` snapshot to report full base units, partial measures,
partial millilitres, and full/partial net-of-VAT revenue. Approved refunds reduce the corresponding type's
revenue and quantities/volume. The existing reporting dates are preserved: sales count on completion date and
refunds count on approval date.

Restocked partial refunds create `VOLUME_REFUND` movements with cost; accounting now includes these alongside
whole-unit `REFUND_RETURN` movements when reversing COGS. A refund not restocked still reduces revenue and
sold volume, but does not restore stock or reverse COGS. Partial sales' line costs remain the frozen sale-time
costs. These rules avoid counting `BOTTLE_OPEN` as a sale or adding stock movements to sale-line quantities.

Stock-list value now includes remaining liquid in open bottles, valued at the bottle's recorded opening cost per
millilitre, in addition to the existing average-cost value of sealed stock. A missing opening-cost movement is
reported as an error rather than silently treated as zero. Cash-up remains payment-based: partial lines affect
drawer totals only through their actual payment amounts, as before.

## Known follow-ups for later phases
- Validate the Phase 6B reports and opened-bottle valuation against PostgreSQL and manual browser use.
- PostgreSQL append-only triggers are unaffected by these changes; verify on PostgreSQL (tests ran on SQLite).
- Wastage is only taken from opened bottles. Writing off a whole sealed bottle still uses the Damaged/Expired
  stock adjustment.
