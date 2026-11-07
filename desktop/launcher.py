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
from urllib.parse import urlparse

MODES = {
    "pos": "/app/pos/",
    "manager": "/app/manager/",
    "receiving": "/app/receiving/",
}
TITLES = {"pos": "OPEN POS / CASHIER", "manager": "MANAGER LOGIN", "receiving": "STOCK RECEIVING"}


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

    from PySide6.QtCore import QLockFile, QUrl, Qt
    from PySide6.QtGui import QKeySequence, QShortcut
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
            self.view = QWebEngineView()
            self.view.setPage(LockedPage(self.view))
            self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
            self.setCentralWidget(self.view)
            self.view.load(QUrl(args.server.rstrip("/") + MODES[args.mode]))

        def closeEvent(self, event):
            if args.mode == "pos" and not args.allow_quit:
                event.ignore()  # the cashier cannot close the POS window
            else:
                event.accept()

    app = QApplication(sys.argv[:1])

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
