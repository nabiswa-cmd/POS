# MASTER DEVELOPMENT PROMPT: KENYAN WINES & SPIRITS DESKTOP POS SYSTEM

Act as a senior software architect, Django/Python developer, POS systems engineer, database designer and desktop application developer.

I want you to design and build a complete, professional, reliable **Wines & Spirits Point of Sale (POS) and Stock Management System** for a single shop in Kenya.

This is initially for ONE physical shop, with one till or a maximum of TWO tills. Do not overcomplicate the first version with multi-branch or multi-company functionality. However, write clean, modular code that can be expanded in the future.

The system must be practical for a busy wines and spirits shop. It must be fast, secure, easy to operate, and primarily designed for keyboard use without depending on a mouse.

Do not only generate a demonstration or static interface. Build a functioning application with a proper database, authentication, inventory, sales, payment handling, reporting, printing and desktop installation.

---

# 1. TECHNOLOGY STACK

Use the following preferred technologies:

- Backend: Python Django.
- API: Django REST Framework where required.
- Database: PostgreSQL for the main database.
- Frontend: HTML, CSS, Bootstrap and JavaScript.
- Desktop application: PySide6 desktop wrapper or an appropriate desktop architecture.
- Desktop installer: Windows installer that installs the application and creates desktop shortcuts.
- Deployment: Hostinger VPS for the central online services where required.
- Payment integration: Safaricom Daraja API.
- Printing: Support thermal receipt printers, barcode printers and ordinary A4 printers where appropriate.

The desktop application must be installable and launchable like normal Windows software.

The POS must not require the cashier to open a browser manually and enter a website address every time.

Design the application so that local shop operations can continue during internet interruptions.

---

# 2. SINGLE-SHOP ARCHITECTURE

Build the system for:

- One shop.
- One shared shop database.
- One or two tills.
- Multiple staff accounts.
- Shared inventory across both tills.
- Combined shop reports.
- Separate cashier shifts and till cash-up reports.

Both tills must use the same product catalogue and stock records.

If Till 1 sells five bottles of a product, Till 2 must immediately see the reduced stock quantity.

Prevent two tills from selling the same remaining stock simultaneously by using proper database transactions and stock validation.

The system should support a local shop server/database or another reliable local-first arrangement.

The shop should continue recording cash sales, stock movements and other local operations when internet access is unavailable.

Online services, including M-PESA communication and cloud backups, should synchronize when connectivity returns.

Do not build unnecessary multi-branch functionality in this first version.

---

# 3. USER LOGIN SYSTEM AND STAFF ROLES

Create a secure user management system with the following roles.

## A. MANAGER LOGIN

The manager has the highest level of access.

The manager should be able to:

- Access all modules.
- Add, edit, deactivate and manage staff accounts.
- Create supervisors, cashiers and receiving clerks.
- Assign roles and permissions.
- Reset staff passwords and PINs.
- View all sales and transactions.
- View all profits and financial reports.
- View all stock and inventory records.
- Add and edit products.
- Change product buying costs and selling prices.
- Manage suppliers.
- Manage expenses.
- Manage shelves and shelf labels.
- Approve refunds.
- Approve removal of items from an active sale.
- View cashier activities.
- View till opening and closing records.
- Manage payment settings.
- Configure receipt settings.
- Access audit logs.
- Configure the system and backups.

The manager must have a separate login shortcut on the desktop.

## B. SUPERVISOR LOGIN

The supervisor should have controlled administrative access.

The supervisor can:

- Authorize removal of products from an active sale.
- Approve refunds.
- Approve voids and other restricted transactions.
- View stock.
- View selling prices.
- Print shelf labels.
- Assist cashiers.
- View permitted reports.
- Perform other permissions assigned by the manager.

The supervisor must not automatically have unrestricted manager access.

Every approval must be recorded with the supervisor's identity, date, time and reason.

## C. CASHIER LOGIN

Cashiers should have access to the POS sales interface.

They can:

- Scan products.
- Search for products.
- Add products to the current sale.
- Enter quantities.
- Accept permitted payment methods.
- Print receipts.
- View their own sales.
- View their own shift information.
- Enter customer names manually.
- View product selling prices.
- Perform other permissions assigned by the manager.

Cashiers must NOT be allowed to:

- Delete products from the database.
- Change product buying costs.
- Change selling prices without permission.
- Delete completed sales.
- Approve their own refunds.
- Remove an already scanned item from an active sale without supervisor or manager authorization.
- Alter historical transactions.
- Change stock quantities directly.
- Access restricted financial settings.

## D. RECEIVING CLERK LOGIN

Create an optional receiving-clerk role.

The receiving clerk should be able to:

- Receive stock from suppliers.
- Search for existing products.
- Record quantities received.
- Record supplier information.
- Record delivery notes and invoice references.
- Record damaged or missing products.
- Submit stock receiving records for approval if required.
- Print receiving documents.
- View relevant stock information.

The receiving clerk must not automatically have permission to change selling prices, approve refunds or access sensitive financial reports.

## E. OTHER STAFF LOGIN

Allow the manager to create additional staff roles.

The manager should be able to select what each role can access.

Use permission-based access rather than hardcoding every possible future role.

---

# 4. SUPERVISOR OR MANAGER AUTHORIZATION

This is a very important security requirement.

When a cashier scans or adds a product to a sale, that product must not be removable by the cashier without authorization.

If the cashier attempts to remove an item from the active sale:

1. Display an authorization window.
2. Ask for supervisor or manager authentication.
3. Allow the supervisor or manager to enter their password or PIN.
4. Where supported, allow fingerprint authentication.
5. Record who approved the action.
6. Record which product was removed.
7. Record the reason, date and time.
8. Continue the sale only after successful authorization.

Support two authentication methods:

- Password or secure PIN.
- Fingerprint authentication where compatible hardware and operating-system APIs are available.

Fingerprint authentication must be optional. The system must still work reliably with passwords or PINs.

Do not store raw passwords or fingerprint images. Use secure password hashing and appropriate operating-system biometric authentication.

Do not allow a cashier to bypass the approval window by using keyboard shortcuts, closing the window or accessing another screen.

The manager must be able to configure which actions require supervisor approval.

Examples of restricted actions:

- Removing a scanned item from a sale.
- Voiding a transaction.
- Changing a price during checkout.
- Applying an exceptional discount.
- Processing a refund.
- Reopening a closed shift.
- Adjusting stock.

---

# 5. DESKTOP INSTALLATION AND SHORTCUTS

The application must be installable on Windows desktop computers.

Create separate desktop shortcuts:

1. **OPEN POS / CASHIER**
2. **MANAGER LOGIN**
3. **STOCK RECEIVING**, where appropriate.

## Cashier opening procedure

When the cashier opens the POS shortcut:

1. Display the supervisor authorization screen first.
2. Require supervisor or manager authentication to authorize opening the POS session.
3. After authorization, display the cashier login screen.
4. The cashier enters their own username and password/PIN.
5. Check that the till has been opened and the cashier has an active shift.
6. Open the sales interface.

The supervisor must not need to remain logged in as the cashier.

Record which supervisor authorized the POS session and which cashier subsequently logged in.

Where the manager has configured a different secure opening policy, respect that configuration.

## Manager opening procedure

When the manager opens the manager shortcut:

1. Display the manager login screen.
2. Authenticate the manager.
3. Open the manager dashboard.
4. Give the manager access to all authorized modules.

## Receiving shortcut

If the receiving shortcut is used:

1. Display staff authentication.
2. Authenticate the receiving clerk or another authorized user.
3. Open the stock receiving interface.

All shortcuts must open the same application and database. They must not create separate or disconnected copies of the system.

---

# 6. KEYBOARD-FIRST POS OPERATION

The POS must be designed to work efficiently without a mouse.

A cashier should be able to complete almost every normal operation using the keyboard and barcode scanner.

Implement configurable keyboard shortcuts.

Suggested shortcuts:

- F1: Open help/shortcut guide.
- F2: Search product.
- F3: Scan or enter barcode.
- F4: Change quantity.
- F5: Hold current sale.
- F6: Retrieve held sale.
- F7: Apply authorized discount.
- F8: Select payment method.
- F9: Complete sale.
- F10: Print receipt.
- F11: View current shift summary.
- F12: Open supervisor authorization.
- ESC: Close or cancel the current non-destructive dialog.
- ENTER: Confirm or move to the next appropriate field.
- TAB / SHIFT+TAB: Move between fields.

These are initial suggestions. Make shortcuts configurable and prevent conflicts.

A barcode scanner should work as a keyboard input device.

The product scan field should automatically receive focus when the cashier returns to the sales screen.

After scanning a product:

- Add it to the cart.
- Display the product name.
- Display its selling price.
- Display quantity.
- Display the line total.
- Return focus to the scan field.

The cashier must not need to click the scan field after every scan.

Do not allow ESC or another keyboard shortcut to bypass restricted approvals.

Create a visible keyboard-shortcut guide.

---

# 7. PRODUCT AND BARCODE MANAGEMENT

Create a complete product management module.

Each product should support:

- Product name.
- Brand.
- Category.
- Product description.
- Barcode.
- QR code, where needed.
- Internal product code/SKU.
- Buying cost.
- Selling price.
- Wholesale price, if enabled.
- Minimum selling price, if enabled.
- Current stock.
- Reorder level.
- Unit of measurement.
- Supplier, optional.
- Shelf location.
- Product image, optional.
- Active/inactive status.

## Products without barcodes

Some products may not have a barcode.

The manager must be able to create an internal product code.

The system should generate a unique code or allow the manager to enter one manually.

Allow the system to generate:

- Barcode labels.
- QR code labels.
- Internal product-code labels.

The cashier must be able to scan the internally generated barcode or QR code and add the correct product to the sale.

Prevent duplicate codes.

Allow barcode printing for products that originally came without barcodes.

Support different packaging sizes where applicable, such as:

- Full bottle.
- Half bottle.
- Quarter bottle.
- Single beer.
- Crate.
- Pack.

Allow packaging conversion rules so that stock quantities are calculated correctly.

For example, if one carton contains 24 bottles, selling one bottle must reduce carton-equivalent stock correctly.

Do not assume all products use the same packaging conversion.

---

# 8. SHELF MANAGEMENT AND SHELF LABEL PRINTING

Create a dedicated module called **SHELF MANAGEMENT**.

The manager or authorized staff should be able to:

- Create shelves.
- Name shelves.
- Assign shelf codes.
- Assign products to shelves.
- Move products between shelves.
- Search products by shelf.
- View products assigned to each shelf.
- Print shelf labels.
- Reprint shelf labels.
- Print labels for selected products.
- Print labels for an entire shelf.

Shelf labels should support:

- Product name.
- Selling price.
- Product code.
- Barcode or QR code.
- Shelf code, optionally.
- Brand, optionally.

Allow different label sizes based on the available printer and label paper.

The label-printing process must be simple and keyboard-friendly.

A cashier or other authorized staff member may view product prices and print shelf labels if the manager grants the required permissions.

---

# 9. SALES AND CHECKOUT

Create a fast POS sales interface.

The interface should support:

- Barcode scanning.
- Manual product-code entry.
- Product search.
- Product selection.
- Quantity changes.
- Discounts with authorization.
- Held sales.
- Resuming held sales.
- Customer information.
- Payment selection.
- Receipt printing.

Display the following clearly:

- Product name.
- Quantity.
- Unit selling price.
- Line total.
- Subtotal.
- Discount.
- Total payable.

Prevent a completed sale from being silently edited or deleted.

Each transaction must have a unique transaction number.

Record:

- Till number.
- Cashier.
- Supervising/approving user, if applicable.
- Transaction date and time.
- Products sold.
- Quantities.
- Prices.
- Discounts.
- Payment method.
- Payment reference.
- Customer information, if available.

Use database transactions to prevent incomplete or duplicate sales.

---

# 10. RECEIPT DESIGN AND PRINTING

Design a professional thermal receipt suitable for a Kenyan wines and spirits shop.

The receipt must show the following information.

## TOP PART OF THE RECEIPT

- Shop name.
- Shop address and contact details.
- Receipt number.
- Date and time.
- Customer name.
- Till number.

The customer name should be populated from M-PESA payment information where Safaricom provides reliable customer-name information.

If the customer name is unavailable, allow the cashier to type it manually.

Do not invent customer names or assume that every payment callback provides a customer name.

## STAFF INFORMATION

The receipt must clearly show:

- Cashier name.
- Supervisor name, where a supervisor authorized the relevant session or transaction.
- Manager name where the manager performed or approved an action, where applicable.

Do not print a supervisor's name as though they personally made the sale if they only authorized the session.

Use clear labels such as:

- Cashier: [Cashier Name]
- Authorized by: [Supervisor Name]

## ITEMS SECTION

For every product, print:

- Product name.
- Quantity.
- Unit price.
- Total price.

Then print:

- Subtotal.
- Discount.
- Total payable.
- Payment method.
- M-PESA transaction reference, when applicable.
- Amount paid.
- Change, where applicable.

Print a thank-you message at the bottom.

Allow the manager to configure receipt information, logo, footer and shop details.

Support 58mm and 80mm thermal printers.

Also allow reprinting of receipts by authorized staff.

A reprinted receipt must be clearly marked as a duplicate or reprint.

---

# 11. REFUND AND VOID MANAGEMENT

Refunds must be controlled.

A cashier must not independently cancel a completed transaction or remove its financial effect.

When a customer requests a refund:

1. Cashier searches for the original receipt or transaction number.
2. System loads the original transaction.
3. Cashier selects the item or items being returned.
4. System checks the original sale details.
5. Supervisor or manager authorization is required.
6. The approver authenticates using password/PIN or supported fingerprint.
7. The system asks for a refund reason.
8. The refund is recorded against the original transaction.
9. Stock is adjusted according to the condition and return decision.
10. The refund is included in reports and audit logs.

The system must distinguish between:

- Full refund.
- Partial refund.
- Item return.
- Sale void before completion.
- Transaction reversal.
- Refund awaiting payment processing.

A refund must not simply erase the original sale.

Keep the original transaction permanently recorded.

For M-PESA refunds, do not claim that money has been returned merely because a manager approved a refund in the POS. Track approval and actual payment status separately.

The receipt or refund document must show the refund transaction reference and approving supervisor or manager.

---

# 12. INVENTORY AND STOCK MANAGEMENT

Build a complete stock management module.

The system must support:

- Opening stock.
- Stock purchases.
- Stock receiving.
- Stock adjustments.
- Damaged stock.
- Expired stock.
- Stock transfers between shelves.
- Stock returns to suppliers.
- Reorder levels.
- Low-stock alerts.
- Stock-taking.
- Stock valuation.
- Stock movement history.

Every stock movement must have:

- Product.
- Quantity.
- Movement type.
- Date and time.
- User.
- Reference or reason.

Stock must update automatically when sales are completed.

Refunded items should only return to available stock if the return is approved and the item's condition permits resale.

Use FIFO or weighted-average costing consistently for profit calculations.

Make the costing method configurable only where changing it will not corrupt historical reporting.

---

# 13. SUPPLIER MANAGEMENT

Supplier information is optional.

Allow the manager to add supplier records containing:

- Supplier name.
- Contact person.
- Phone number.
- Email.
- Address.
- KRA PIN, if applicable.
- Notes.

Allow products to be associated with suppliers.

Support supplier purchases and receiving documents.

Do not force a supplier to be assigned to every product.

---

# 14. SALES, COST AND PROFIT CALCULATION

The POS must calculate financial performance correctly.

For every sale, preserve the cost basis of the stock sold.

Calculate:

Gross sales = Total value of items sold before discounts and returns.

Net sales revenue = Gross sales − Discounts − Sales returns, using clearly defined report conventions.

Cost of goods sold (COGS) = Cost of the actual inventory sold.

Gross profit = Net sales revenue − COGS.

Net profit = Gross profit − Operating expenses.

Record expenses separately from inventory purchases.

Do not count stock purchases as ordinary operating expenses when calculating gross profit.

Support:

- Daily sales.
- Weekly sales.
- Monthly sales.
- Gross profit.
- Net profit.
- Product-level profit.
- Category-level profit.
- Sales by cashier.
- Sales by till.
- Sales by payment method.
- Discounts.
- Refunds.
- Expenses.
- Stock value.
- Cash-up differences.

The dashboard must clearly distinguish revenue, gross profit, net profit and actual cash received.

Do not confuse sales revenue with cash in the drawer.

---

# 15. TILL OPENING, CASHIER SHIFTS AND CASH-UP

Support one or two tills.

Each till must have a separate opening and closing process.

At shift opening, record:

- Till number.
- Cashier.
- Opening date and time.
- Opening cash float.
- Authorizing supervisor, where required.

At closing, calculate:

- Opening float.
- Cash sales.
- Cash refunds.
- Cash paid in/out, where authorized.
- Expected cash.
- Actual cash counted.
- Cash difference.
- M-PESA sales.
- Other electronic payments.
- Total sales for the shift.

Opening float must never be counted as sales.

M-PESA payments must never be counted as cash in the drawer.

Allow the manager and authorized supervisor to view shift reports.

A cashier should not be able to alter a closed shift without authorization and an audit trail.

---

# 16. EXPENSE MANAGEMENT

Create an expense management module.

Allow authorized users to record:

- Rent.
- Electricity.
- Water.
- Salaries.
- Transport.
- Repairs.
- Licences.
- Other operating expenses.

Record:

- Expense category.
- Amount.
- Date.
- Description.
- Payment method.
- User.
- Supporting reference or attachment, optionally.

Expenses must feed into net-profit reports.

Do not mix expenses with stock purchases.

---

# 17. PAYMENT METHODS AND M-PESA INTEGRATION

Support:

- Cash.
- M-PESA.
- Card, if enabled.
- Bank transfer.
- Split payments, if enabled.

Integrate Safaricom Daraja API for M-PESA where credentials and merchant configuration are available.

Support relevant M-PESA payment workflows, including:

- STK Push where applicable.
- C2B payments for a Till or PayBill arrangement.
- Payment callbacks.
- Payment validation and reconciliation where supported.

The POS must never treat a customer's payment screenshot or SMS shown to the cashier as final proof of payment.

Use authenticated provider callbacks and transaction references.

Maintain clear payment statuses, for example:

- PENDING.
- PROCESSING.
- CONFIRMED.
- FAILED.
- EXPIRED.
- UNMATCHED.
- OFFLINE_PENDING.

Use idempotency so that the same callback cannot record a payment twice.

When shop internet is unavailable:

- Local cash sales should continue.
- Record payment attempts and pending transactions safely.
- Do not falsely mark an unverified M-PESA payment as successful.
- Synchronize and reconcile pending transactions when connectivity returns.

Safaricom callbacks must reach a secure public HTTPS endpoint hosted on the online server.

The branch/local POS should receive confirmed payment updates when connectivity is restored.

Never hardcode Daraja credentials in source code.

Provide secure configuration for:

- Consumer key.
- Consumer secret.
- Passkey.
- Shortcode/Till configuration.
- Callback URLs.
- Environment selection.

Support sandbox testing before live deployment.

---

# 18. REPORTS AND DASHBOARD

Create a clear dashboard for the manager.

Show:

- Today's sales.
- Today's gross profit.
- Today's expenses.
- Estimated net profit.
- Number of transactions.
- Cash sales.
- M-PESA sales.
- Other payments.
- Current stock value.
- Low-stock products.
- Best-selling products.
- Recent refunds.
- Till performance.

Provide date filters:

- Today.
- Yesterday.
- This week.
- This month.
- Custom date range.

Provide downloadable or printable reports.

Cashiers should only see reports permitted by their role.

The manager should have access to the complete financial dashboard.

---

# 19. SECURITY AND AUDIT LOGS

Security is essential.

Implement:

- Secure authentication.
- Password hashing.
- PIN protection where enabled.
- Role-based access control.
- Session timeout.
- Login history.
- Failed-login tracking.
- Authorization logs.
- Transaction audit logs.
- Database backups.
- Secure API authentication.
- Protection against SQL injection.
- CSRF protection.
- Input validation.

Record sensitive actions, including:

- Removing an item from a sale.
- Changing prices.
- Giving discounts.
- Refunding a transaction.
- Voiding a sale.
- Adjusting stock.
- Changing product costs.
- Closing or reopening a till.
- Changing user permissions.

For every sensitive action, record:

- User performing the action.
- Approving supervisor or manager, where applicable.
- Action performed.
- Previous value.
- New value, where relevant.
- Reason.
- Date and time.
- Related transaction or product.

Audit logs must not be editable by ordinary staff.

---

# 20. DATABASE DESIGN

Design a clean relational database.

Include appropriate models/tables for:

- Shop settings.
- Users.
- Roles.
- Permissions.
- User sessions.
- Products.
- Categories.
- Brands.
- Barcodes.
- Product packaging conversions.
- Shelves.
- Suppliers.
- Purchases.
- Purchase items.
- Stock receiving.
- Stock movements.
- Stock adjustments.
- Sales.
- Sale items.
- Payments.
- Refunds.
- Refund items.
- Expenses.
- Tills.
- Cashier shifts.
- Cash-up records.
- Supervisor approvals.
- Audit logs.
- M-PESA transactions.
- Payment reconciliation.
- Receipt settings.

Use appropriate foreign keys, constraints, indexes and database transactions.

Preserve historical product costs and selling prices used in completed transactions.

Do not calculate historical profit using only the current product buying price.

---

# 21. OFFLINE OPERATION AND BACKUPS

The shop must not stop selling cash transactions simply because internet access is lost.

Build a reliable local-first arrangement.

The local database must preserve sales and stock movements.

When internet connectivity returns:

- Synchronize pending records.
- Reconcile online payment statuses.
- Send backups to the online server.
- Prevent duplicate transactions.
- Log synchronization failures.

Provide automatic database backups.

Allow the manager to configure backup frequency and backup destination.

Do not allow synchronization failures to silently delete or overwrite sales records.

---

# 22. DESKTOP USER INTERFACE

Create a professional, modern interface.

The POS screen should be optimized for speed.

The cashier screen should prioritize:

- Barcode scanning.
- Product search.
- Cart.
- Quantity.
- Total payable.
- Payment method.
- Checkout.
- Receipt printing.

Keep unnecessary administration menus away from the cashier screen.

The manager dashboard should provide navigation to:

- Dashboard.
- POS.
- Products.
- Stock.
- Stock receiving.
- Shelves.
- Shelf labels.
- Suppliers.
- Sales.
- Refunds.
- Expenses.
- Staff.
- Tills and shifts.
- Reports.
- Settings.
- Audit logs.

Use clear buttons, readable fonts and consistent layouts.

The application must support keyboard navigation throughout.

---

# 23. DEVELOPMENT APPROACH

Do not attempt to generate the entire application in one unstructured response.

Work in phases and maintain a working project after each phase.

## Phase 1: Foundation

- Project structure.
- Database setup.
- Authentication.
- Roles and permissions.
- Desktop launcher.
- Shop configuration.

## Phase 2: Products and inventory

- Product management.
- Barcode and QR code generation.
- Packaging conversions.
- Stock receiving.
- Suppliers.
- Shelves.
- Shelf-label printing.

## Phase 3: POS

- Cashier interface.
- Barcode scanning.
- Product search.
- Cart.
- Keyboard shortcuts.
- Supervisor authorization.
- Till management.
- Receipt printing.

## Phase 4: Payments and refunds

- Cash payments.
- M-PESA integration.
- Payment status management.
- Refund authorization.
- Refund records.
- Reconciliation.

## Phase 5: Accounting and reports

- Buying costs.
- Cost of goods sold.
- Gross profit.
- Net profit.
- Expenses.
- Till cash-up.
- Financial reports.

## Phase 6: Desktop deployment

- Windows application packaging.
- Installer.
- Desktop shortcuts.
- Local database configuration.
- Printer configuration.
- Backup configuration.
- Testing.

---

# 24. TESTING REQUIREMENTS

Create automated tests and practical user acceptance tests.

At minimum, test:

1. Manager can access all authorized modules.
2. Supervisor can approve restricted actions.
3. Cashier cannot remove a scanned item without authorization.
4. Cashier cannot approve their own refund.
5. Refunds remain linked to original transactions.
6. Receiving clerk can receive stock but cannot change restricted prices.
7. Two tills correctly share inventory.
8. Selling stock from one till updates the other till.
9. Barcode scanning adds the correct product.
10. Internally generated product codes can be scanned.
11. Shelf labels print correctly.
12. Receipt contains customer, cashier and authorization details where applicable.
13. M-PESA callbacks cannot create duplicate payments.
14. Offline cash sales are preserved.
15. Gross and net profit calculations are correct.
16. Cash-up separates cash from M-PESA.
17. Keyboard shortcuts work without a mouse.
18. Unauthorized users cannot access restricted modules.
19. The Windows installer creates working shortcuts.
20. Backups can be restored successfully.

---

# 25. IMPORTANT DEVELOPMENT RULES

- Build real working functionality, not just UI mockups.
- Do not use fake payment confirmations.
- Do not hardcode secrets.
- Do not allow cashiers to bypass supervisor approvals.
- Do not delete historical sales or refund records.
- Do not calculate profit from selling prices alone.
- Do not mix M-PESA collections with physical cash.
- Do not create separate databases for each till.
- Do not make the system dependent on internet access for ordinary local cash sales.
- Do not make fingerprint authentication compulsory.
- Do not assume every M-PESA callback contains a customer name.
- Do not make suppliers compulsory.
- Do not build multi-branch features into the initial version.
- Use clean, modular, documented code.
- Include database migrations.
- Include setup instructions.
- Include environment configuration examples.
- Include testing instructions.
- Include Windows installation instructions.

When a requirement is ambiguous, explain the options and recommend the safest practical implementation.

Before writing large amounts of code, first present:

1. Proposed architecture.
2. Database design.
3. User-role and permission matrix.
4. Main screen layout.
5. Authentication and approval workflow.
6. Desktop installation approach.
7. Development phases.

Wait for approval of the architecture before proceeding to large-scale implementation.

The final goal is a professional, dependable Kenyan wines and spirits POS that can be installed on a shop desktop, operated with a keyboard and barcode scanner, used by different staff according to their permissions, and managed by the owner without needing a complicated multi-branch ERP system.