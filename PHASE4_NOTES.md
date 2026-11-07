# Phase 4: Payments and refunds, in sub-phases

## Sub-phase 4A (this update): capture M-PESA money

**Done**

- Customer pays the **Till directly**: Safaricom's till confirmation arrives, is stored once (unique M-PESA receipt code), and shows on the
  cashier's M-PESA screen (F8, then 2). The cashier picks it and the sale completes.
- **STK push**: the cashier types the customer's phone, the customer enters their PIN, Safaricom's callback confirms, the sale completes by itself.
- **Customer name** is taken from Safaricom's till message (FirstName / MiddleName / LastName) and printed on the receipt. If Safaricom sends no
  name it stays blank and the cashier can type one (Alt+C); a typed name always wins. Nothing is invented.
- **No duplicates**: one receipt code = one payment record = at most one sale (database constraints). Safaricom retrying a callback changes nothing.
- **Never trusts the cashier**: a payment exists only if Safaricom's callback created it. A screenshot, SMS or typed code confirms nothing.
- Exact amount only. A KES 1,400 payment cannot close a KES 1,500 sale.
- M-PESA is kept apart from cash: the shift summary has its own `mpesa_sales` figure and the expected drawer cash counts only cash.
- Login: the red top border is gone; the separate "I am the owner" button is gone (owner uses the normal first step, second step is pre-filled).

**What Safaricom does and does not give us (important)**

- The STK callback has **no customer name**. Only the till/C2B confirmation can carry one, and even then it can be blank. Since the v2 API the phone
  number in that message is **hashed**, so the screen shows "07xx xxx 678" only for STK payments.
- An STK push to a Till normally also triggers the till confirmation, so the name usually arrives as a second message for the same receipt code.
  Both messages land on the same row in either order. If your till never sends names, that is Safaricom's side, not a fault here.

**Server setup (once)**

1. Safaricom must reach a public HTTPS address. Run this same project on the VPS (its own database is fine for 4A), set `MPESA_CALLBACK_BASE_URL`
   and a long random `MPESA_CALLBACK_SECRET`, then run `python manage.py mpesa_register_urls` there.
2. Callback paths are `/api/pay/...` on purpose: Safaricom rejects addresses that contain words like "mpesa" or "safaricom".
3. **Gap to know about:** the shop PC only sees a payment if the callback reaches *its* database. Until 4B, either run the shop server so it is
   reachable from the internet (HTTPS tunnel), or treat the VPS as the server. Cash sales never depend on any of this.

**Not tested here:** the Django tests (`sales/test_phase4a.py`) and the migration could not be run in the build environment (no Django install, no
internet). The framework-free tests in `tests_pure/test_phase4a_rules.py` pass (17). Nothing was tried against Safaricom's sandbox.
Run: `makemigrations sales`, `migrate`, then `DB_ENGINE=sqlite DJANGO_DEBUG=true python manage.py test sales`, and send me any failure.

## Sub-phase 4B (next update)

1. **Refunds**: request, supervisor/manager approval (cashier cannot approve own), linked to the original sale, full or partial, reason, refund
   receipt. M-PESA refunds track *approved* and *actually paid back* as separate statuses. Return to stock only if approved and resaleable.
   eTIMS credit note (type R). Refunds shown in cash-up separate from cash.
2. **Relay for offline shops**: the VPS stores callbacks, the shop PC pulls them when online (`mpesa_pull`), statuses OFFLINE_PENDING and UNMATCHED,
   so a payment made while the shop's internet was down still arrives later.
3. **Reconciliation screen**: payments received but not used, manager can assign or mark as ignored with a reason, everything audited.
4. **Part payments, overpayments, split payments** (cash + M-PESA on one sale), **card** and **bank transfer**.
5. **Name fallback**: Safaricom Transaction Status query when a payment has no name; auto-match when exactly one unused payment equals the cart total.
6. `harden_db` triggers for the M-PESA tables, an M-PESA line in the F11 shift summary, and Safaricom IP allow-listing notes for the VPS.

Phase 5 (cash-up, reports) then reads the separated cash / M-PESA / other figures already recorded here.
