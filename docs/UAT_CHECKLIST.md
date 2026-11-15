# Acceptance checklist (do this on the real shop PCs before trading)

Part A maps the 20 tests in the specification to the automated test that covers it, and says what still has to be done by hand.
Part B is the hand-run checks for the Windows install. Tick each when done; write the date and who did it.

Run all automated tests: `DB_ENGINE=sqlite DJANGO_DEBUG=true python manage.py test` and `python -m unittest discover -s tests_pure -t .`

## A. Specification tests
| # | Requirement | Automated test | By hand |
|---|---|---|---|
| 1 | Manager can access all authorized modules | `tills/tests.py::test_manager_can_authorize`, `accounting/test_reports.py::ScreenTests` | Log in as manager: open every menu item |
| 2 | Supervisor can approve restricted actions | `sales/tests.py` (remove item with supervisor), `sales/test_refunds.py::test_supervisor_token_approves...` | Do one approval at the till with a real supervisor |
| 3 | Cashier cannot remove a scanned item alone | `sales/tests.py::test_cashier_cannot_remove_a_scanned_item_alone` | Try it at the till |
| 4 | Cashier cannot approve own refund | `sales/test_refunds.py::test_cashier_cannot_approve_their_own_request` | Try it |
| 5 | Refunds stay linked to the original sale | `sales/test_refunds.py`, `sales/test_credit_notes.py` | Refund a sale; open it under Tills & sales |
| 6 | Receiving clerk cannot change restricted prices | `tests_pure/test_rules.py::test_receiving_clerk_cannot_change_prices_or_see_finance` | Log in as clerk: no price editing, no Profit screen |
| 7, 8 | Two tills share stock; a sale on one shows on the other | `sales/tests.py::test_two_tills_share_stock_and_the_last_bottle_sells_once` | **Required**: sell the last bottle on Till 1, look on Till 2 |
| 9 | Barcode scanning adds the right product | `sales/tests.py` (scan tests) | Scan 5 real bottles with the real scanner |
| 10 | Internal product codes can be scanned | `sales/tests.py::test_internally_generated_code_scans` | Print a shelf label, scan it |
| 11 | Shelf labels print correctly | `catalog/tests.py::test_label_sheet_shows_name_price_code_and_barcode` | **Required**: print on the real label paper |
| 12 | Receipt shows customer, cashier, authorization | `sales/tests.py::test_receipt_names_cashier_authorizer_customer_and_change` | Print one on the real printer |
| 13 | M-PESA callbacks cannot create duplicate payments | `sales/test_phase4a.py::test_same_callback_twice_records_one_payment` | Sandbox: send the same callback twice |
| 14 | Offline cash sales are preserved | cash checkout never contacts the internet; `sales/test_relay.py` | **Required**: unplug the internet, sell for cash, plug back in, find the sale |
| 15 | Gross and net profit correct | `accounting/test_reports.py` | Compare one day with the accountant's figure |
| 16 | Cash-up separates cash from M-PESA | `tills/test_cashup.py::test_mpesa_money_is_shown_but_never_expected_in_the_drawer` | Close one real shift |
| 17 | Keyboard shortcuts work without a mouse | see note below | **Required**: run a whole sale with no mouse |
| 18 | Unauthorized users cannot reach restricted modules | `catalog/tests.py`, `sales/test_phase3b.py::test_cashier_cannot_open_the_manager_screens`, each phase's ScreenTests | Try manager URLs while logged in as a cashier |
| 19 | The Windows installer creates working shortcuts | `packaging/verify_install.ps1` (**automated on Windows only**) | Section B |
| 20 | Backups can be restored | `core/test_backup.py::RoundTripTests` | Section B, restore drill |

Note on 17: the tests that check the sales screen's shortcut guide (`sales/tests_screen.py`) currently fail, because they look at an older page than the one in use. The shortcuts must be checked by hand until that is settled.

## B. Windows install checks
**Server PC**
- [ ] `verify_install.ps1` prints "All checks passed"
- [ ] Restart the PC. Without logging in to anything, the Manager shortcut opens the login screen within 2 minutes
- [ ] Desktop shortcuts: POS opens full screen and a cashier cannot close it; Manager and Stock Receiving open maximised
- [ ] `WinesPOSAdmin.exe manage selfcheck` shows no FAIL (WARN for sandbox/second backup location is understood)
- [ ] Manager > Backups > Back up now, then Check it: "is good"
- [ ] **Restore drill**: make a backup, sell one item, stop the server, restore, start: the item is gone and the shift still works
- [ ] Unplug the USB/second location: tonight's backup still completes and the Backups screen/selfcheck warn
- [ ] Printer: `WinesPOS.exe printers` lists the receipt printer; one receipt prints at the right width (with and without `WINESPOS_RECEIPT_PRINTER`)
- [ ] Open a shift, sell for cash and M-PESA, close the shift: the cash-up expects only the cash
**Till 2**
- [ ] `verify_install.ps1` prints "All checks passed" (it can reach the server)
- [ ] Only the POS shortcut exists; it opens and a cashier can log in
- [ ] Unplug the network cable: the window says it cannot reach the server instead of showing a wrong page
- [ ] Both tills selling at once: stock stays right on both
**Uninstall / reinstall**
- [ ] Uninstall keeps `C:\ProgramData\WinesPOS`; installing again over it keeps the data
Signed off by: ______________  Date: ____________
