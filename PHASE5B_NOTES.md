# Phase 5B: profit and financial reports (second half of Phase 5)

Screen: **Profit & reports** `/manage/reports/` (permission `reports.financial.view`, owner by default; cashiers, supervisors and receiving clerks get "forbidden", so costs stay hidden).
No migration, no new tables: it only reads records that are never edited.

## How profit is worked out
```
Net sales      = sales without VAT - refunds without VAT
Cost of goods  = cost frozen on each SOLD line - cost of refunded goods put back on the shelf
Gross profit   = net sales - cost of goods
Net profit     = gross profit - stock losses - expenses +/- till cash differences
```
- Cost is the cost basis recorded when each sale completed (FIFO or average, per your shop setting). A later price or cost change never rewrites old profit.
- **Stock bought** (receiving) is shown as its own line. It is cash spent on stock, not a cost of sales, so it is not subtracted (red.md: never calculate profit from selling prices alone, nor from purchases).
- **Refunds** count from approval. Goods put back on the shelf return their cost; goods not fit to resell keep their cost as a loss.
- **Stock losses**: damaged and expired stock, and adjustments, valued at cost; stock found offsets losses. Opening stock and returns to supplier are not losses.
- **Expenses**: from Phase 5A, voided ones excluded.
- **Till cash differences**: the over/short from cash-ups closed in the period (a shortage lowers profit). This is my choice: it is shown as its own line, so it is easy to drop if your accountant prefers it outside profit.
- If any sold line has no recorded cost, a red warning appears (profit would be overstated).

## What the screen shows
Presets (today, this month, last month) or any dates; the profit and loss; money taken in by cash / M-PESA / card-bank (never mixed) less refunds paid back; expenses by category; profit by product (rows add up to the totals); profit by day (ranges up to 62 days). CSV downloads: summary, products, days.

## Tests
`accounting/test_reports.py` and 5 pure tests (all pass). Full suite: 559 run; the same 18 failures that exist in the code you uploaded remain (`sales/tests_screen.py`, `sales/test_phase4c.py`), none new.

## Not verified
Checked with Django's test client and SQLite, not in a browser or on PostgreSQL. Day boundaries use the shop time zone setting.
## Decisions for you
- Till differences inside net profit (above).
- No VAT-on-expenses (input VAT) yet, so net profit is before any VAT reclaim.
- Not built (not in red.md's Phase 5): stock valuation report, supplier payables, and period closing/locking.
