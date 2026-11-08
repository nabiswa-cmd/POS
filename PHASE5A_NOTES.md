# Phase 5A: till cash-up and expenses (first half of Phase 5)

Phase 5 in `red.md` = buying costs, cost of goods sold, gross profit, net profit, expenses, till cash-up, financial reports.
This update is **half**: **expenses** and **till cash-up**. The other half (**5B**: buying-cost/COGS reporting, gross and net profit, financial reports) reads what is recorded here.

## What you get

**Close a shift and count the drawer** (cashier, at the till)
- F11 shift summary now has **Close shift and count cash (C)**. It opens `/pos/cashup/`.
- The cashier types pieces per note/coin (1000, 500, 200, 100, 50, 40, 20, 10, 5, 1). The page does **not** show the expected figure.
- The server works out: expected = opening float + cash sales - cash refunds handed back - cash paid out of the drawer.
  M-PESA, card and bank money are stored beside it on the slip but are **never** part of the expected cash.
- A count that does not match (over or short) cannot be closed without a written reason (8+ characters). A match needs none.
- Refused while a sale is in progress or on hold. An empty cart the POS opened by itself is tidied away.
- Closing signs the cashier out and shows a printable slip. Refunds still waiting are named as a warning (not counted).
- A supervisor/owner with the new permission `shifts.close_any` can close a forgotten shift from the manager screen; they must say why.

**Manager screens**
- **Cash-ups** `/manage/cashups/`: every shift, expected vs counted, over/short totals, CSV. Click a shift for the full breakdown, note/coin count, print slip.
- **Reopen** a wrongly counted shift (owner only by default: permission `approve.reopen_shift`). The old count stays on record, marked superseded; the shift must be counted again. Refused if the till already has another open shift.
- **Expenses** `/manage/expenses/` (permission `expenses.manage`, owner by default): record, void, filter by date, totals by category and by source, CSV.
  Categories are seeded by `setup_shop` (rent, electricity, wages, county/liquor licences ...) and editable in the admin.
  An expense is **never edited or deleted**: a wrong one is voided with a reason and entered again.
- **Cash from a till drawer**: choose "Cash taken from a till drawer" and an open shift. It lowers that drawer's expected cash, cannot be more than the drawer should hold, and cannot be voided after that drawer was counted (reopen the shift first).

## Rules enforced on the server
- A cash-up row is never edited or deleted; only the one-time "superseded" mark can be written. Same for expenses and the "void" mark.
- Only the cashier can close their own shift; anyone else needs `shifts.close_any` and a reason.
- F11 and the cash-up use the same function (`tills.cashup.snapshot`), so they cannot disagree.

## Run
```
pip install -r requirements.txt            # unchanged
python manage.py migrate                   # tills/0002_cashup, accounting/0001_initial
python manage.py harden_db                 # adds the two new protections (PostgreSQL)
python manage.py setup_shop                # safe to re-run: adds the default expense categories
```
**Existing roles are never overwritten.** The new permission `shifts.close_any` is given to the Supervisor role only on a fresh `setup_shop`;
for an existing shop add it to Supervisor in the manager admin (Roles) if you want supervisors to close other people's shifts.

## Tests
`python3 -m unittest discover -s tests_pure -t .` (127 pass) and `DB_ENGINE=sqlite DJANGO_DEBUG=true python manage.py test` (536 run).
New: `tests_pure/test_phase5a_rules.py`, `tills/test_cashup.py`, `accounting/tests.py` (56 tests; all pass).
The 18 failures that remain are the same 18 that fail on the code you uploaded (in `sales/tests_screen.py` and `sales/test_phase4c.py`); I did not touch them.

## Not verified here
- The new PostgreSQL triggers (`harden_db`) are written like the existing refund triggers but I had no PostgreSQL to run them against. Run `harden_db` on your dev database and try editing/deleting a row in `tills_cashup` and `accounting_expense`.
- The F11 button and the count page were checked through Django's test client, not in a real browser or on the desktop shell.

## Decisions for you
- F11 still shows "Expected cash in drawer" to the cashier (that was already there). If you want a truly blind count, that line should be hidden for cashiers.
- Expenses record no VAT split yet (input VAT). Say so if the accountant needs it in 5B.
- Reopening a shift is owner-only by default (it undoes a count). Supervisors can have it by adding `approve.reopen_shift`.

## 5B (next)
Buying costs in reports, cost of goods sold from `SaleItem.cost_total`, gross profit, net profit (gross profit minus the non-voided expenses from here), and the financial reports screen with CSV.
