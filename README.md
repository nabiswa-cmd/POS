# Wines & Spirits POS (single shop, 1 or 2 tills)

Phase 1 (Foundation): accounts, roles and permissions, supervisor approvals, audit log,
shop settings, tills and shifts, the three desktop entry points (POS, Manager, Receiving).
Later phases (products, sales screen, M-PESA, reports, installer) build on this.

## What is built

| Area | Where |
|---|---|
| Permission catalogue and default roles | `accounts/permissions.py` |
| Approval rules (single-use, expiring tokens bound to action, cashier and item) | `accounts/approval_rules.py`, `accounts/services.py` |
| Password/PIN login, lockout, login history | `accounts/services.py`, `accounts/lockout.py` |
| Optional fingerprint (off by default; server side done, Windows Hello call is Phase 6) | `accounts/services.py`, `desktop/biometric.py` |
| Audit log (append-only; PostgreSQL trigger via `harden_db`) | `core/models.py`, `core/db_hardening.py` |
| Shop and receipt settings, approval policies | `core/models.py`, `accounts/models.py` |
| POS opening flow (supervisor, then cashier, then shift) | `tills/views.py`, `core/templates/core/app.html` |
| Desktop shell and local server | `desktop/launcher.py`, `desktop/run_server.py` |
| Staff / roles / policies management (Phase 1 uses the Django admin, managers only) | `accounts/admin.py` |

## Rules enforced on the server (not just on screen)

- A restricted action needs a valid approval token: single-use, 2-minute expiry (configurable),
  bound to the action, the requesting cashier and the item. Closing the dialog or using shortcuts grants nothing.
- Nobody can approve their own action. The approver must hold the matching `approve.*` permission.
- The supervisor who authorizes opening the POS cannot also be the cashier on that authorization.
- Opening the POS always needs authorization. The manager chooses which other actions need approval (Approval policies).
- Only one open shift per till, enforced by a database constraint.
- Staff are deactivated, never deleted. Audit entries, login history and approvals cannot be edited or deleted.

## Setup (developer PC)

Requires Python 3.11+ and PostgreSQL 15+.

```
python -m venv .venv
.venv\Scripts\activate            (Windows)   or   source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env            then edit .env (set DJANGO_SECRET_KEY and DB_PASSWORD)

# create the database and user in PostgreSQL once:
#   CREATE USER winespos WITH PASSWORD '...';  CREATE DATABASE winespos OWNER winespos;

python manage.py makemigrations accounts core tills catalog inventory
python manage.py migrate
python manage.py harden_db
python manage.py setup_shop --shop-name "Your Shop" --manager-username owner
python manage.py collectstatic --noinput
python -m desktop.run_server
```

In another terminal:

```
python -m desktop.launcher --mode manager
python -m desktop.launcher --mode pos --allow-quit      (--allow-quit is for development only)
python -m desktop.launcher --mode receiving
```

Then in the manager screen open the management screens (Django admin) to create staff, assign roles, set PINs,
and tune approval policies. Staff need a PIN or password to log in at the POS.

`makemigrations` must be run once and the generated `migrations/` folders committed. They were not generated in
the build environment because Django could not be installed there (this applies to Phase 1 and Phase 2).

## Tests

```
python3 -m unittest discover -s tests_pure -t .          # rules only, no Django needed
DB_ENGINE=sqlite DJANGO_DEBUG=true python manage.py test  # full suite (Windows: set the two variables first)
```

SQLite is for tests only: it has no row-level locking, so real tills must use PostgreSQL.

## Configuration

See `.env.example`. Secrets (`DJANGO_SECRET_KEY`, `DB_PASSWORD`, later Daraja keys) come only from `.env` or the
environment, never from source. Two tills: set `WINESPOS_BIND=0.0.0.0` on the server PC, add its LAN address to
`DJANGO_ALLOWED_HOSTS`, and start the launcher on Till 2 with `--server http://<server-ip>:8000`.

## Known limits of Phase 1

- Fingerprint: the server verifies signed assertions, but the Windows Hello call is not wired in yet (Phase 6, needs real hardware to test).
  `DESKTOP_SHELL_SECRET` lives in the installed config; protect that file with Windows permissions.
- The manager UI is the Django admin for now; the custom dashboard and screens come in later phases.
- Full-screen kiosk lock in the POS window blocks closing and navigation inside the app but cannot stop Windows-level
  shortcuts such as Alt+Tab; Phase 6 adds a Windows kiosk/assigned-access guide.
- Till closing and cash-up are Phase 5. Shifts can currently be opened only.

---

# Phase 2: Products, stock receiving, suppliers, shelves, labels

## What is new

| Area | Where |
|---|---|
| Products, categories, brands, pack sizes (carton of 24, crate of 12 ... per product) | `catalog/models.py`, `catalog/units.py` |
| One scannable-code namespace: manufacturer barcodes, shop-generated codes (EAN-13 starting `20`), manual codes, SKU | `catalog/codes.py`, `catalog/services.py` |
| Barcode (EAN-13 / Code 128) and QR rendering for labels, no external barcode library | `catalog/barcodes.py` |
| Label printing: roll sizes and A4 sheets, price / barcode / QR / code / brand / shelf options, whole-shelf or scanned codes | `catalog/labels.py`, `catalog/templates/catalog/label_*.html` |
| Shelves, product placement, move history (append-only) | `catalog/views.py`, `ShelfTransfer` |
| Suppliers (always optional) | `catalog/views.py` |
| Stock movements (append-only), cost lots, weighted-average and FIFO costing | `inventory/models.py`, `inventory/services.py`, `inventory/costing.py` |
| Stock receiving: draft, submit, optional approval, damaged and missing recorded, printable GRN | `inventory/services.py`, `inventory/views.py` |
| Stock adjustments (opening, correction, damaged, expired, returned to supplier) with approval | `inventory/services.py` |
| Manager and receiving screens (keyboard-first, filtered by permission) | `/manage/...`, `core/templates/core/base.html` |
| Scan lookup and name search for the POS (Phase 3 will call these) | `GET /api/catalog/lookup/?code=`, `GET /api/catalog/search/?q=` |

## Upgrading from Phase 1

```
pip install -r requirements.txt                       # adds Pillow and segno
python manage.py makemigrations accounts core tills catalog inventory
python manage.py migrate
python manage.py harden_db                            # adds append-only protection to stock movements and shelf history
python manage.py setup_shop                           # safe to re-run
```

New permission `stock.approve_receiving`. New installs give it to the Supervisor role. If you already ran
`setup_shop` in Phase 1, open Roles in the manager admin and add `stock.approve_receiving` to Supervisor by hand
(existing roles are never overwritten). New shop setting: **Receiving requires approval** (Shop settings, off by default).

## Rules enforced on the server

- Stock is stored in whole **base units** (the smallest unit sold). A pack is only a multiplier, per product.
  `Product.stock_qty` is not editable in any form; the only way to change it is `inventory.services.post_movement`,
  which locks the product row, refuses to go below zero, and writes a movement. Two tills share the same row, so the
  second till always sees the first till's sale and cannot oversell the last bottle.
- A code (barcode, shop code, SKU) can belong to only one product, shop-wide, enforced by a unique constraint.
  Scanning is case and space insensitive. A QR label simply encodes the same code, so QR and barcode find the same product.
- Selling prices and buying costs are removed from the product form for anyone without `products.change_price` /
  `products.change_cost`, re-checked in the service, and every change is audited with old and new value.
  Cashiers and receiving clerks never see buying cost, average cost or stock value.
- A receiving clerk records quantities, damaged, missing, delivery note, invoice and cost from the invoice. Stock changes only
  when the delivery is posted, and a clerk can never touch selling prices. If the manager turns on approval, the clerk cannot
  approve their own delivery.
- Stock adjustments need `stock.adjust` plus the existing single-use supervisor approval (`stock_adjust` policy). The shop
  owner (manager) may adjust alone; that is audited. Opening stock is only allowed before any other movement for that product.
- Costing: every stock-in creates a lot. Average cost is updated on receiving and opening stock. Sales consume lots oldest
  first either way; FIFO also costs at lot cost. The first sale locks the costing method (Phase 1 setting).
- Django admin is read-only for catalogue and stock, so nobody can change a price or stock figure without the audit trail.

## Using it

1. Manager login opens `/manage/`. Add categories and brands (Categories & brands), shelves, suppliers (optional), then products.
2. On a product page: add its barcode (scan into the box), or **Generate code** for bottles without one, add pack sizes, then print its label.
3. Record opening stock with Adjust stock (once per product, before any other movement).
4. Receiving shortcut: new delivery, scan each product (or type part of its name), quantities, Enter. Finish and submit.
5. Labels: scan codes one per line, or choose a shelf; pick size and what appears; Ctrl+P.

Keyboard: `/` focuses search, `Esc` goes back, `Alt+N` new, `Alt+H` home, `Enter` submits.

## Tests

```
python3 -m unittest discover -s tests_pure -t .           # 38 framework-free tests (rules, barcodes, costing)
DB_ENGINE=sqlite DJANGO_DEBUG=true python manage.py test   # full suite: accounts, tills, catalog, inventory
```

## Known limits of Phase 2

- Written without a Django install, so the Django test suite and migrations have **not** been run yet. The framework-free tests pass.
  Run `makemigrations` and the full suite first and send me any failures.
- Barcode tables were checked structurally and by round-trip decoding, but not with a physical scanner. Print a few labels and scan them.
- Labels print through the browser print dialog (any Windows printer, including thermal label printers with a driver). Direct ZPL/TSPL
  to a label printer and silent printing arrive with printer configuration in Phase 6.
- Not in Phase 2 yet: stock-taking sessions, returns to supplier as a document, stock-value report page (the figure shows on the Stock screen),
  fingerprint approval on these screens, and selling (Phase 3 uses `post_movement` with type `SALE`, one product at a time in ascending id order).
