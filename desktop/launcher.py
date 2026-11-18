"""Desktop shell: opens the POS / Manager / Receiving screen in its own window.

    python -m desktop.launcher --mode pos
    python -m desktop.launcher --mode manager
    python -m desktop.launcher --mode receiving

All modes load the same server and database; the shortcut only chooses the first page.
The window can only show pages from the shop server, has no address bar or context
menu, and the POS window is full-screen and cannot be closed by the cashier.
"""
import argparse
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

MODES = {
    "pos": "/app/pos/",
    "manager": "/app/manager/",
    "receiving": "/app/receiving/",
}
TITLES = {"pos": "OPEN POS / CASHIER", "manager": "MANAGER LOGIN", "receiving": "STOCK RECEIVING"}


ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def icon_files():
    """The logo PNGs for the window and the taskbar, found next to the code (development) or inside the installed
    program (PyInstaller unpacks it under sys._MEIPASS). Qt picks the size that suits the taskbar or Alt+Tab."""
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    for folder in (root / "core" / "static" / "core" / "icons", root / "staticfiles" / "core" / "icons"):
        found = [folder / f"icon-{size}.png" for size in ICON_SIZES if (folder / f"icon-{size}.png").is_file()]
        if found:
            return found
    return []


def wait_for_server(server, seconds=30):
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(server.rstrip("/") + "/health/", timeout=3) as response:
                if response.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            time.sleep(1)
    return False


def main(argv=None):
    parser = argparse.ArgumentParser(description="Wines & Spirits POS desktop shell")
    parser.add_argument("--mode", choices=sorted(MODES), default="manager")
    parser.add_argument("--server", default=os.environ.get("WINESPOS_SERVER_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--allow-quit", action="store_true", help="Development only: Ctrl+Shift+Q closes the POS window.")
    args = parser.parse_args(argv)

    from PySide6.QtCore import QLockFile, QUrl, QUrlQuery, Qt, QTimer
    from PySide6.QtGui import QIcon, QKeySequence, QShortcut
    from PySide6.QtWebEngineCore import QWebEnginePage
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox

    allowed = urlparse(args.server)

    class LockedPage(QWebEnginePage):
        """Refuses to navigate anywhere except the shop server."""

        def acceptNavigationRequest(self, url, nav_type, is_main_frame):
            return url.host() == allowed.hostname and (url.port() in (-1, allowed.port))

    class Window(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle(TITLES[args.mode])
            if not app_icon.isNull():
                self.setWindowIcon(app_icon)
            self.view = QWebEngineView()
            self.view.setPage(LockedPage(self.view))
            self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
            self.setCentralWidget(self.view)
            self.view.loadFinished.connect(self._sleep_if_requested)
            if args.mode == "pos":
                self._lock_shortcut = QShortcut(QKeySequence("Ctrl+Alt+L"), self)
                self._lock_shortcut.activated.connect(self._request_lock)
            self._printer = None
            self.view.printRequested.connect(self.print_requested)
            self.view.load(QUrl(args.server.rstrip("/") + MODES[args.mode]))

        def _request_lock(self):
            self.view.page().runJavaScript(
                "window.dispatchEvent(new Event('winespos-lock-request'));")

        def _sleep_if_requested(self, loaded):
            url = self.view.url()
            if not loaded or args.mode != "pos" or url.path() != "/app/pos/":
                return
            if QUrlQuery(url).queryItemValue("sleep") != "1":
                return
            till = QUrlQuery(url).queryItemValue("till")
            target = "/app/pos/" + (f"?till={till}" if till.isdigit() else "")
            self.view.page().runJavaScript(f"history.replaceState(null, '', '{target}');")
            QTimer.singleShot(500, self._sleep_computer)

        def _sleep_computer(self):
            if sys.platform != "win32":
                QMessageBox.warning(self, TITLES[args.mode], "Desktop sleep is supported only on Windows.")
                return
            import ctypes

            power = ctypes.WinDLL("PowrProf.dll", use_last_error=True)
            suspend = power.SetSuspendState
            suspend.argtypes = [ctypes.c_bool, ctypes.c_bool, ctypes.c_bool]
            suspend.restype = ctypes.c_bool
            if not suspend(False, False, False):
                error = ctypes.get_last_error()
                message = ctypes.FormatError(error).strip() if error else "Windows rejected the sleep request."
                QMessageBox.warning(self, TITLES[args.mode], f"Could not put the computer to sleep: {message}")

        def print_receipt(self, name):
            """Send the page to the configured receipt printer with no dialog."""
            from PySide6.QtPrintSupport import QPrinter, QPrinterInfo
            if name not in QPrinterInfo.availablePrinterNames():
                QMessageBox.warning(self, TITLES[args.mode], f"The receipt printer '{name}' was not found.\n"
                                    "Check it is on and plugged in, or ask the manager to check Receipt settings.")
                return
            self._printer = QPrinter(QPrinter.PrinterMode.HighResolution)
            self._printer.setPrinterName(name)
            self.view.print(self._printer)

        def _print_with_settings(self, printer_name):
            if printer_name is None:
                QMessageBox.warning(self, TITLES[args.mode], "Could not read the receipt printer setting from the shop server.")
            elif printer_name:
                self.print_receipt(printer_name)
            else:
                self._show_print_preview()

        def _show_print_preview(self):
            from PySide6.QtPrintSupport import QPrintPreviewDialog, QPrinter

            printer = QPrinter(QPrinter.PrinterMode.HighResolution)
            preview = QPrintPreviewDialog(printer, self)
            preview.setWindowTitle("Print preview")
            preview.paintRequested.connect(self.view.print)
            preview.exec()

        def print_requested(self):
            """Keep label sheets previewable; only receipt pages use the configured silent printer."""
            path = self.view.url().path()
            label_preview = path.startswith("/manage/labels/print/")
            receipt_page = path == "/pos/sell/" or (
                path.startswith("/manage/sales/") and path.endswith("/reprint/"))
            if receipt_page:
                self.view.page().runJavaScript(
                    "fetch('/api/receipt/printer/', {credentials:'same-origin', cache:'no-store'})"
                    ".then(function(r){return r.ok?r.json():Promise.reject();})"
                    ".then(function(d){return d.printer_name;}).catch(function(){return null;})",
                    self._print_with_settings)
                return

            from PySide6.QtPrintSupport import QPrintPreviewDialog, QPrinter

            printer = QPrinter(QPrinter.PrinterMode.HighResolution)
            preview = QPrintPreviewDialog(printer, self)
            preview.setWindowTitle("Label print preview" if label_preview else "Print preview")
            preview.paintRequested.connect(self.view.print)
            preview.exec()

        def closeEvent(self, event):
            if args.mode == "pos" and not args.allow_quit:
                event.ignore()  # the cashier cannot close the POS window
            else:
                event.accept()

    # Windows groups taskbar buttons and picks their icon by an application id. Without our own id the window is
    # filed under the generic Python / launcher identity. Each screen gets its own id, so POS, Manager and Receiving
    # show as separate taskbar buttons that all carry the logo.
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"WinesPOS.Desktop.{args.mode}")
        except Exception:
            pass                                   # cosmetic only: never stop the POS from starting

    app = QApplication(sys.argv[:1])
    app_icon = QIcon()
    for path in icon_files():
        app_icon.addFile(str(path))
    if not app_icon.isNull():
        app.setWindowIcon(app_icon)                # every window and message box carries the logo

    lock = QLockFile(os.path.join(tempfile.gettempdir(), f"winespos-{args.mode}.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.information(None, TITLES[args.mode], "This window is already open.")
        return 0

    if not wait_for_server(args.server):
        QMessageBox.critical(None, TITLES[args.mode],
                             f"Cannot reach the shop server at {args.server}.\nCheck that the server PC is on.")
        return 1

    window = Window()
    if args.mode == "pos":
        window.showFullScreen()
        if args.allow_quit:
            QShortcut(QKeySequence("Ctrl+Shift+Q"), window, activated=window.close)
    else:
        window.showMaximized()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
