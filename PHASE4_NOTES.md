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

## Sub-phase 4A.1: pay by cash, M-PESA, or both on one sale

**Done**

- One checkout for every way of paying: `POST /api/sales/checkout/` with `{"mpesa_ids": [...], "cash_tendered": "..."}`. Cash only,
  M-PESA only, or several M-PESA payments plus cash. (`/checkout/cash/` and `/checkout/mpesa/` still exist and are unchanged.)
- Rule: M-PESA is applied first (money Safaricom already confirmed), **cash is the balance** and the only thing that can give change.
  M-PESA can never exceed the sale (overpayments stay in 4B). All-or-nothing: if anything fails, nothing is sold and no M-PESA payment is used up.
- Cashier screen (F8): after choosing M-PESA or Cash, a payment smaller than the balance is a **part payment**. The screen shows
  "Still to pay" and returns to the method list for the rest. Part cash asks for confirmation. "Start over (C)" clears part payments.
  Part payments are held on the screen only; the server is asked once, when the balance is zero, and re-checks every figure.
- STK push can request a part amount (whole shillings), then the confirmed payment is added to the sale like a till payment.
- A split sale has one Payment row per part, so the receipt, the shift summary (cash / M-PESA kept apart) and the drawer figure are correct
  with no further change. eTIMS gets the payment type of the biggest part.
- Shift summary (F11) now shows M-PESA sales too; money figures are always formatted with 2 decimals.

**Still 4B:** overpayments, card, bank transfer, reconciliation, relay.

## Sub-phase 4B-1: refunds (this update)

**How it works (Alt+R on the sales screen)**

1. **Ask**: the cashier types the receipt number (last 4+ characters), types how many of each item come back, picks cash or M-PESA, gives a reason.
   Whole units of any line, full or partial. Never more than was sold, counting earlier refunds. Nothing changes yet.
2. **Approve**: the supervisor's login window opens at once (same window as void / price change). **A cashier cannot approve their own request**;
   a supervisor who asked cannot approve their own either. Only the owner may. Approving is when stock goes back (only goods marked resaleable,
   at the cost they left stock at, as a `REFUND_RETURN` movement). A request nobody approves just waits: Alt+R, 2.
3. **Pay back** (separate step, on purpose):
   - **Cash**: the cashier confirms handing it over. It leaves *that shift's* expected drawer cash, only then, and only if the drawer can cover it.
   - **M-PESA**: paid back outside the system. Someone *other than the requester* with refund authority records the real M-PESA code. One code can close
     only one refund. M-PESA refunds never touch the drawer. M-PESA can only go back up to what was paid by M-PESA on that sale; the rest is cash.
4. **Slip**: "REFUND SLIP", first print is the original, later prints say DUPLICATE and need a supervisor. It says "Not a tax document" (see below).

**Records**: the sale is never edited. `Refund` / `RefundItem` are the correction, never deleted, frozen once paid / rejected / cancelled, every step audited with
who asked, who approved and who paid. F11 shift summary adds `cash_refunds`, `mpesa_refunds_paid`, `refunds_waiting`; expected drawer cash = float + cash sales - cash refunds.

**Run**: `python manage.py migrate` (adds migration `sales/0004_refunds`), then `python manage.py harden_db` on PostgreSQL.

**Not tested here:** the PostgreSQL triggers in `core/db_hardening.py` (tests run on SQLite). Please run `harden_db` on a copy of your real database first.
There is no manager page for refunds yet; approvers use the same Alt+R list on any till.

## Sub-phase 4B-2: eTIMS credit notes (this update)

- **Approving a refund creates a KRA credit note** (receipt type R) for the sale's signed invoice, with a gapless number from the *same* series as sales
  invoices (KRA numbers both together) and a pointer to the original invoice number. It is sent straight away; if the internet or the eTIMS unit is down it
  stays PENDING and is retried by the same queue as sales (`python manage.py etims_retry`, and after later sales). **The refund and the cash payout never wait for KRA.**
- **Order is guaranteed**: if the original sale invoice is still waiting, it is sent first, then the credit note. A credit note is never sent for an unsigned
  or rejected original (it shows the reason instead).
- **Only real data is printed.** The refund slip says CREDIT NOTE with KRA's signature, unit ID and QR only after KRA's unit returned them. Until then it says
  "REFUND SLIP / awaiting KRA signature / not yet a valid credit note". Signed credit notes cannot be changed or deleted.
- **A sale that was never put through eTIMS** (eTIMS was off when it was sold) has no KRA invoice to credit, so no credit note is made; this is written to the audit log
  and the slip says "Not a tax document".
- A rejected credit note is not retried automatically (someone must look); the cashier screen shows "eTIMS REJECTED" on approval and in the Alt+R list. The Tills
  overview counts credit notes in its signed / waiting / rejected figures.
- The credit note is created at **approval** (when stock goes back), not when the customer is paid.

**Run**: `python manage.py migrate` (adds `sales/0005_etims_credit_note`), then `harden_db` (now also freezes signed eTIMS invoices and credit notes).

**Important, same as for sales:** the credit-note message follows KRA's OSCU/VSCU v2.0 field names but has **not** been run against KRA's sandbox (that needs your PIN and device).
Test a refund in the sandbox and fix anything KRA rejects before going live. Credit notes in particular are where KRA is strict about the original invoice reference and amounts.

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
