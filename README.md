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

python manage.py makemigrations accounts core tills catalog inventory sales
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


---

# Phase 3, first half: sales engine (cart, approvals, hold, cash checkout, receipt data)

The second half (cashier screen, shortcuts, approval box, receipt printing, till screens) is described in the next section.
Everything the screen needs is available as JSON under `/api/sales/`.

## What is new

| Area | Where |
|---|---|
| Sale, SaleItem, Payment (cash only for now) | `sales/models.py` |
| Money, discount-limit, change and cost-allocation maths (framework-free) | `sales/rules.py` |
| Cart: scan or pick, merge repeat scans, pack sales, quantity, discount, price change, customer name, hold, resume, void | `sales/services.py` |
| Cash checkout: stock, cost basis, payment and sale number in one transaction | `sales.services.checkout_cash` |
| Receipt data and 58 mm / 80 mm text layout, duplicates clearly marked | `sales/receipt.py`, `sales/receipt_text.py` |
| JSON endpoints for the sales screen | `sales/views.py`, `sales/urls.py` |
| Completed sales cannot be changed (model guard and PostgreSQL trigger) | `sales/models.py`, `core/db_hardening.py` |

## Upgrading from Phase 2

```
python manage.py makemigrations sales
python manage.py migrate
python manage.py harden_db          # adds the completed-sale protection
```

No new packages. New optional setting `ORDINARY_DISCOUNT_MAX_PERCENT` (default 5, see `.env.example`).

## Endpoints (all need a signed-in cashier with an open shift)

| Call | Purpose |
|---|---|
| `GET  /api/sales/cart/` | The sale in progress (lines, subtotal, discount, total). Each line carries `ref`, the text an approval is bound to |
| `POST /api/sales/cart/add/` `{code}` or `{product_id, packaging_id}`, `quantity` | Scan or pick |
| `POST /api/sales/cart/quantity/` `{item_id, quantity, approval_token?}` | Raise freely; lowering needs approval |
| `POST /api/sales/cart/remove/` `{item_id, approval_token, reason?}` | Needs supervisor/manager approval (`remove_item`) |
| `POST /api/sales/cart/discount/` `{item_id, amount or percent, approval_token?}` | Within the ordinary limit for staff allowed to discount; otherwise `exceptional_discount` approval |
| `POST /api/sales/cart/price/` `{item_id, price, approval_token?}` | `price_change` approval |
| `POST /api/sales/cart/customer/` `{name}` | Typed customer name (M-PESA names come in Phase 4) |
| `POST /api/sales/cart/hold/` `{label?}`, `GET /api/sales/held/`, `POST /api/sales/held/resume/` `{sale_id}` | Hold and retrieve |
| `POST /api/sales/cart/void/` `{approval_token, reason?}` | Cancel before payment (`void_sale` approval); the record is kept |
| `POST /api/sales/checkout/cash/` `{tendered}` | Complete as a cash sale |
| `GET  /api/sales/receipt/<id>/` | Original receipt, once, for the cashier who made the sale |
| `POST /api/sales/receipt/<id>/reprint/` | Marked duplicate; needs `receipts.reprint`; audited |
| `GET  /api/sales/shift/summary/` | Running shift figures (F11) |

Approval flow for the screen: call `POST /api/approvals/request/` with `object_ref` set to the line's `ref`
(or the cart's `ref` for a void), let the supervisor type their own PIN, then send the returned `token` with the action.

## Rules enforced on the server

- A cashier cannot remove or reduce a scanned item without a single-use approval from someone else, bound to that exact line.
  Nobody approves their own action, and the approver needs the matching `approve.*` permission.
- Stock is checked when scanning (so the cashier is warned early) and again, under the row lock, at checkout. If two tills hold
  the last bottle, the first checkout wins and the second is refused with nothing saved.
- The sale number is taken inside the checkout transaction, so completed sales have no gaps (T1-261107-0042: till, date, running number).
- Each line keeps the price charged, the catalogue price, its discount, who approved either, and the cost of the stock sold.
  Later profit reports use that cost, never today's buying cost.
- Completed and voided sales, their lines and payments cannot be edited or deleted. Only the print counters on the sale can move.
- The receipt prints the cashier and, separately, "Authorized by" (the supervisor who authorized the session). Approvers of
  discounts and price changes are listed under the totals. Reprints are headed `*** DUPLICATE RECEIPT ***`.
- Original receipt: once, by the selling cashier. If the printer jams, a supervisor reprints it (marked duplicate).

## Tests

```
python3 -m unittest discover -s tests_pure -t .           # 58 framework-free tests, including Phase 3 maths and receipt layout
DB_ENGINE=sqlite DJANGO_DEBUG=true python manage.py test sales
```

## Known limits of this half

- Written without a Django install, like Phases 1 and 2: the framework-free tests pass, but `sales/tests.py` and the migration
  have not been run. Run `makemigrations sales` and the sales tests first and send me any failures.
- A manager acting as cashier still needs a second person to approve restricted actions (the approval rules forbid self-approval).
  Say if you want the shop owner exempt, as in stock adjustments.
- Payments are cash only. M-PESA, card, bank transfer, split payments and refunds are Phase 4.
- Not built yet: till closing and cash-up (Phase 5).
- Kenyan tax: wines and spirits carry VAT, and KRA eTIMS receipts may be required for your business. This is not in the master
  prompt and is not built; confirm with your accountant before go-live.

---

# Phase 3, second half: cashier screen, shortcuts, printing, till screens

## Upgrading from the first half

```
python manage.py migrate            # one new field: Shop settings > POS shortcuts
python manage.py collectstatic --noinput
```

No new packages. Log in at `/app/pos/` as before; a cashier with an open shift now lands on the sales screen (`/pos/sell/`).

## The sales screen

| Key (default) | Action |
|---|---|
| Scanner / type + Enter | Add the item. `3*code` sells 3. An unknown code keeps its text, beeps and says so |
| F1 | Shortcut guide |
| F2 | Search by name (Up/Down, Enter adds one) |
| F3 | Back to the scan box |
| F4 | Quantity of the selected line (lowering it asks for a supervisor) |
| F5 / F6 | Hold the sale / retrieve a held sale (held sales are per till) |
| F7 | Discount on the selected line (percent or amount) |
| F8 or F9 | Payment box: type the cash received, see the change, **F9 or Enter completes** |
| F10 | Print the receipt |
| F11 | Shift summary (float, sales, cash expected in the drawer) |
| F12 | Supervisor menu: remove item, change price, exceptional discount, void sale (1 to 4) |
| Delete | Remove the selected line (needs a supervisor) |
| Alt+C | Customer name for the receipt |
| Up / Down | Move between lines. Esc closes a box |

- **Supervisor approval.** When the server says an action needs approval, the screen opens a box where the supervisor or manager types
  their own username, PIN or password and a reason. The cashier never sees the credentials. The token is single-use and tied to that exact
  line, and the same request is sent again. Esc or Cancel changes nothing, and it never skips the approval.
- **Printing.** The server lays the receipt out as plain text (32 characters for 58 mm paper, 48 for 80 mm). The screen sends it to a hidden
  frame and prints it, so any Windows printer works. The first print is the original and happens once, automatically after the sale (the
  tick box in the "Sale complete" box turns this off for this PC). Any later copy needs a supervisor or manager and is headed
  `*** DUPLICATE RECEIPT ***`.
- **Manager screens.** `Sales` lists and searches completed and voided sales, shows each sale and lets a supervisor or manager reprint a duplicate.
  `Tills` shows who is on each till, who authorized the session, the opening float, the sales so far and the held sales.

## Changing shortcuts

Admin > Shop settings > "POS shortcuts", for example `{"hold": "Alt+H", "discount": "Alt+D"}`. Two actions cannot share a key, plain letters
and digits are refused (they are used when scanning), and keys that Windows or the browser keep (Ctrl+W, Alt+F4 and similar) are refused.
Saving an invalid change is blocked, and an invalid value already stored falls back to the defaults, so the till never locks up.

## Tests

```
python3 -m unittest discover -s tests_pure -t .
DB_ENGINE=sqlite DJANGO_DEBUG=true python manage.py test sales
```

`sales/tests.py` (first half, 45 tests, now run and passing) and `sales/tests_screen.py` (17 tests: pages, approval hand-shake, receipts as
the screen uses them, manager pages, and a check that every element and endpoint `pos.js` uses exists). The screen was also driven in a real
browser: scan, unknown code, wrong PIN, approval, Esc on the approval box, hold and retrieve, search, discount, payment, receipt, reprint refusal,
shift summary and void.

## Known limits

- Cash only (Phase 4 adds M-PESA, card and refunds). The receipt prints through the browser's print dialog; silent printing to a thermal
  printer on the Windows desktop shell is a Phase 6 setting (Chrome/Edge `--kiosk-printing`).
- If the printer jams on the original, a supervisor reprints it (marked duplicate). This follows the "original once" rule from the first half.
- A manager acting as cashier still needs a second person to approve restricted actions.
- Held sales do not reserve stock; stock is checked again when the sale is completed.
- F5 (refresh), F11 (full screen) and F12 (developer tools) are claimed by the page; some browsers still keep F11/F12. The Windows
  desktop shell does not have this problem.
