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

## Lock and sleep
`Ctrl+Alt+L` (or the Lock & sleep button) locks the cashier's shift and puts the computer to sleep. The sale
in progress stays saved. On waking, the same cashier types their own login: no supervisor is needed.
Sleep works on Windows only. Windows decides between sleep and hibernate: if the computer hibernates instead,
turn hibernation off with `powercfg /hibernate off` in an administrator command prompt.
