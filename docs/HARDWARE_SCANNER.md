# Barcode scanners and the lock shortcut

## Plugging in a scanner
Any USB or Bluetooth scanner that acts as a keyboard ("keyboard wedge", the factory default) works with no
setup and no driver. It types the code and presses Enter.

* The POS keeps the cursor in the scan box. If it wandered (after a click or a closed window), the first
  character of a scan moves it back, so a scan is never lost.
* A scanner set to end with **Tab** instead of Enter also works.
* Invisible characters (Enter, Tab, STX/ETX wrappers) and an AIM prefix such as `]E0` are removed before
  the lookup.
* A 12-digit UPC-A label finds the product filed as the EAN-13 starting with 0, and the other way round.
* `3*code` still sells three of a product. Scan a quantity-times prefix by typing `3*` first, then scan.

To test: open the sales screen, scan any product label. If the product appears, the scanner is working.
If the scanner types nothing at all, check it is in "USB HID keyboard" mode (see the scanner's manual).

## Product search and unknown barcodes
POS search results show the product's manufacturer barcode when one is registered, otherwise its shop SKU.
When an unknown UPC/EAN/GTIN barcode is scanned, the POS can look it up in online catalogues. Your own
catalogue is always checked first. Sources are asked in this order and the first match wins:

1. Open Food Facts (food and drink)
2. Open Products Facts (general goods)
3. UPCitemdb (general products; the free tier allows roughly 100 lookups a day)
4. Barcode Lookup (general products; requires an API key and may incur charges)

If a match is found, the name, brand and scanned barcode are saved locally as an **inactive** product. A manager
must review it, set its selling price, activate it and receive stock before it can be sold. Each barcode is asked
of each source at most once every 6 hours when nothing is found. Internal shop codes and barcodes with a bad check
digit are never sent out. If the shop server is offline the lookup stops straight away instead of waiting.
Kenyan and local labels are often missing from every global catalogue, so your own product list stays the main source.

**When nothing is found:** a manager or owner sees an "Add this product" link next to the message. It opens the new
product form with the barcode already filled in, so only the name and price need typing. A cashier is told to ask a manager.

**Turning it on** (needs internet on the shop server; saved products keep scanning offline): read the
[Open Food Facts API documentation](https://openfoodfacts.github.io/openfoodfacts-server/api/), review its reuse
terms and submit its [API usage form](https://docs.google.com/forms/d/e/1FAIpQLSdIE3D8qvjC_zRJw1W8OmuHhsWJ_NSckiiniAHlfaVwUZCziQ/viewform).
For Barcode Lookup, obtain an API key from your Barcode Lookup account. Save it in Django admin >
**Payment, tax, and lookup integration settings** using Database source, or set `BARCODE_LOOKUP_API_KEY` in the
environment and select Environment source. This key is only needed when using Barcode Lookup; the free sources
continue to work without it. Database values are encrypted using `INTEGRATION_SETTINGS_KEY`.
Then set `OPEN_FOOD_FACTS_ENABLED=true` in the shop environment and restart the server. That one switch controls
every online source. To skip a source or change the order, edit `EXTERNAL_LOOKUP_PROVIDERS`
(default `openfoodfacts,openproductsfacts,upcitemdb,barcodelookup`). A network or provider error is shown instead of being
reported as a local barcode miss; a source that is only rate-limited is skipped and the next one is tried.

## Lock and sleep
`Ctrl+Alt+L` (or the Lock & sleep button) signs the cashier out and puts the computer to sleep. The sale
in progress and shift stay open, but the shared till is not locked. On waking, use the normal till-opening
flow: a supervisor authorizes the till, then the cashier on the open shift signs back in. If the shop uses
the resume-open-shift policy, the same cashier can sign back in without a new supervisor authorization.
Sleep works on Windows only. Windows decides between sleep and hibernate: if the computer hibernates instead,
turn hibernation off with `powercfg /hibernate off` in an administrator command prompt.
