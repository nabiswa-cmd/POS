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
New types in `movement_rules`: `BOTTLE_OPEN`, `PARTIAL_SALE`, `VOLUME_WASTAGE`, `VOLUME_ADJUST`
(`BOTTLE_TYPES`).

| Type | `quantity` | `volume_ml` |
|---|---|---|
| `BOTTLE_OPEN` | -1 (sealed bottle leaves `stock_qty`) | + capacity |
| `PARTIAL_SALE` | 0 | negative |
| `VOLUME_WASTAGE` | 0 | negative |
| `VOLUME_ADJUST` | 0 | either sign, non-zero |

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
bottles; a repeated operation key is rejected, and the database uniqueness constraint is the final duplicate
guard. Each movement records the user, bottle, signed millilitres, remaining volume, reference/reason, and
cost basis where known. Wastage and volume adjustments follow the existing `stock.adjust` approval and audit
rules. When sealed bottles need opening, callers pass `bottle_capacity_ml`; after an opening exists, the
service can infer capacity from the latest tracked bottle.

## Known follow-ups for later phases
- `accounting/reports.py` sums `quantity * units_per_sale_unit` for units sold. Partial lines would be counted
  as whole units there; give them their own treatment when reports are touched.
- `sales/refunds.py` refunds in whole base units; refunding a partial line needs volume handling.
- Connect partial inventory deduction to final `SaleItem` creation in the checkout phase so sale completion and
  volume deductions share the same transaction. The Phase 3 service does not change POS or payment behavior.
- `Product` has no standard bottle capacity; use the explicit capacity on first opening or the capacity recorded
  by an earlier opening. Products containing mixed sealed bottle sizes need a per-product capacity source.
- PostgreSQL append-only triggers are unaffected by these changes; verify on PostgreSQL (tests ran on SQLite).
