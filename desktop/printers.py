"""Printer helper: list the Windows printers and send a test slip.

    WinesPOS.exe printers                      list printer names (use one as WINESPOS_RECEIPT_PRINTER)
    WinesPOS.exe printers --test "EPSON TM-T20"

With WINESPOS_RECEIPT_PRINTER set, the POS window prints receipts straight to that printer with no dialog.
Without it, the normal Windows print dialog appears (works with any printer).
"""
import argparse
import sys


def list_printers():
    from PySide6.QtPrintSupport import QPrinterInfo
    return QPrinterInfo.availablePrinterNames()


def test_print(name):
    from PySide6.QtGui import QPainter
    from PySide6.QtPrintSupport import QPrinter, QPrinterInfo
    if name not in QPrinterInfo.availablePrinterNames():
        raise ValueError(f"No printer called '{name}'. Run 'printers' to see the exact names.")
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setPrinterName(name)
    painter = QPainter()
    if not painter.begin(printer):
        raise RuntimeError("The printer would not start. Is it on, with paper?")
    painter.drawText(100, 200, "WINES & SPIRITS POS")
    painter.drawText(100, 400, "Printer test: if you can read this, receipts will print.")
    painter.end()


def main(argv=None):
    p = argparse.ArgumentParser(description="Printer helper")
    p.add_argument("--test", metavar="NAME", help="Print a short test slip on this printer.")
    a = p.parse_args(argv)
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication(sys.argv[:1])
    try:
        if a.test:
            test_print(a.test)
            print(f"Test slip sent to '{a.test}'.")
        else:
            names = list_printers()
            print("\n".join(names) if names else "No printers are installed on this PC.")
    except (ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
