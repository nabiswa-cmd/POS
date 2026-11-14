"""A plain-words health check of an installation: `WinesPOSAdmin.exe manage selfcheck`.

Each line is (level, text) with level OK, WARN or FAIL. FAIL means the shop should not trade until fixed.
"""
import os
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import connection

OK, WARN, FAIL = "OK", "WARN", "FAIL"


def _writable(folder):
    try:
        Path(folder).mkdir(parents=True, exist_ok=True)
        probe = Path(folder) / ".write-test"
        probe.write_text("x")
        probe.unlink()
        return True
    except OSError:
        return False


def run_checks():
    from accounts.models import User
    from core import backup
    from tills.models import Till

    out = []

    def add(level, text):
        out.append((level, text))

    add(WARN if settings.DEBUG else OK,
        "Debug mode is ON. Real shops must run with DJANGO_DEBUG=false." if settings.DEBUG else "Debug mode is off.")
    if settings.SECRET_KEY.startswith("dev-only"):
        add(FAIL, "The secret key is the development one. Set DJANGO_SECRET_KEY.")
    else:
        add(OK, "A private secret key is set.")

    try:
        connection.ensure_connection()
    except Exception as exc:        # any driver error means the same thing to the shop
        add(FAIL, f"Cannot reach the database: {exc}")
        return out
    if connection.vendor == "postgresql":
        add(OK, "Database: PostgreSQL.")
    else:
        add(WARN if settings.DEBUG else FAIL,
            "Database is SQLite. That is for testing only: it cannot share stock safely between two tills.")

    from django.db.migrations.executor import MigrationExecutor
    executor = MigrationExecutor(connection)
    pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
    add(FAIL if pending else OK,
        f"{len(pending)} database update(s) waiting: run migrate." if pending else "Database is up to date.")

    has_till = Till.objects.filter(active=True).exists()
    add(OK if has_till else FAIL, "At least one till is set up." if has_till else "No till exists: run setup_shop.")
    has_mgr = User.objects.filter(is_manager=True, is_active=True).exists()
    add(OK if has_mgr else FAIL, "A manager account exists." if has_mgr else "No active manager account: run setup_shop.")

    root = Path(settings.STATIC_ROOT)
    styled = root.is_dir() and any(root.rglob("*.css"))
    add(OK if styled else WARN, "The screens' styling files are in place." if styled else "Static files are missing: run collectstatic.")
    media_ok = _writable(settings.MEDIA_ROOT)
    add(OK if media_ok else FAIL, "Product image folder is writable." if media_ok else f"Cannot write to the product image folder {settings.MEDIA_ROOT}.")

    if not _writable(settings.BACKUP_DIR):
        add(FAIL, f"Cannot write to the backup folder {settings.BACKUP_DIR}.")
    else:
        state, hours, _ = backup.current_status()
        if state == "ok":
            add(OK, f"Newest backup is {hours:.1f} hours old.")
        elif state == "stale":
            add(WARN, f"Newest backup is {hours:.0f} hours old (limit {settings.BACKUP_STALE_HOURS}). Is the scheduled backup running?")
        else:
            add(WARN, "No backup has been made yet. Make one now.")
    copy_dir = (settings.BACKUP_COPY_DIR or "").strip()
    if copy_dir:
        ok = _writable(copy_dir)
        add(OK if ok else WARN, "Second backup location is reachable." if ok else
            f"Second backup location {copy_dir} is not reachable (USB drive plugged in?).")
    else:
        add(WARN, "No second backup location is set. A backup on the same disk is lost if the disk fails (set WINESPOS_BACKUP_COPY_DIR).")

    if settings.BIOMETRIC_ENABLED and not settings.DESKTOP_SHELL_SECRET:
        add(FAIL, "Fingerprint is on but DESKTOP_SHELL_SECRET is empty.")
    from core import integration_settings
    try:
        mpesa_config = integration_settings.mpesa()
        etims_config = integration_settings.etims()
    except ImproperlyConfigured as exc:
        add(FAIL, str(exc))
    else:
        if mpesa_config["CONSUMER_KEY"] and mpesa_config["ENV"] == "sandbox":
            add(WARN, "M-PESA is in SANDBOX mode: payments are not real.")
        if etims_config["MODE"] == "sandbox":
            add(WARN, "eTIMS is in SANDBOX mode: receipts are not real tax invoices.")
    printer = os.environ.get("WINESPOS_RECEIPT_PRINTER", "")
    add(OK, f"Receipts print silently to '{printer}'." if printer else
        "Receipts use the Windows print dialog (set WINESPOS_RECEIPT_PRINTER for silent printing).")
    return out
