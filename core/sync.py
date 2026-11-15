"""Shop <-> online server sync.

The shop keeps working on its own local server and database (offline-first). The online server is the safe copy:

* shop -> online : the shop makes a normal backup (core/backup.py: database + product images in one zip) every
                   SYNC_INTERVAL_MINUTES and uploads it over HTTPS. The online server checks it and keeps the newest
                   SYNC_INBOX_KEEP. If the shop PC is lost, the newest file is the shop.
* online -> shop : M-PESA payments. Safaricom can only call the public online server; the shop fetches what it
                   recorded (sales/relay.py, the same "pull" as before) in the same cycle.

If the internet is down, the cycle records the problem and tries again next time. Nothing in the till depends on it.
Both machines use the same SYNC_TOKEN.
"""
import hashlib
import hmac
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db import close_old_connections
from django.utils import timezone

from . import audit, backup, runtime
from . import backup_rules as rules
from .backup_rules import BackupError

log = logging.getLogger("winespos.sync")


class SyncError(Exception):
    """A sync problem. The message is safe to show the manager."""


# --- shop side -----------------------------------------------------------------------------------------------------

def enabled():
    return bool(settings.SYNC_ENABLED and settings.SYNC_SERVER_URL and settings.SYNC_TOKEN)


def _request(path, *, method="GET", data=None, headers=None, timeout=None):
    url = settings.SYNC_SERVER_URL.rstrip("/") + path
    h = {"Authorization": f"Bearer {settings.SYNC_TOKEN}", "Accept": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout or settings.SYNC_TIMEOUT_SECONDS) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403, 404):
            raise SyncError("The online server refused us. Check the sync token and the server address.")
        if exc.code == 413:
            raise SyncError("The online server says the backup is too big (raise SYNC_MAX_UPLOAD_MB there, and the proxy limit).")
        raise SyncError(f"The online server answered with an error ({exc.code}).")
    except (urllib.error.URLError, OSError, ValueError):
        raise SyncError("Cannot reach the online server (no internet, or the server address changed).")


def ping():
    """Test the connection and the token. Returns the server's answer."""
    if not (settings.SYNC_SERVER_URL and settings.SYNC_TOKEN):
        raise SyncError("Set the online server address and the sync token first.")
    return _request("/api/sync/ping/", timeout=15)


def _upload(path):
    size = os.path.getsize(path)
    digest = rules.sha256_file(path)
    with open(path, "rb") as f:
        return _request(f"/api/sync/upload/?name={Path(path).name}", method="POST", data=f,
                        headers={"Content-Type": "application/zip", "Content-Length": str(size),
                                 "X-Content-SHA256": digest})


def push_backup(user=None, force=False):
    """Make a fresh backup when the newest is older than the interval (or force), then send the newest one that the
    online server does not have yet. Returns a short text."""
    from .models import SyncState

    state = SyncState.load()
    interval = timedelta(minutes=settings.SYNC_INTERVAL_MINUTES)
    rows = [r for r in backup.list_backups() if not r["prerestore"]]
    newest = rows[0] if rows else None
    from datetime import datetime, timezone as dtz
    if force or newest is None or datetime.now(dtz.utc) - newest["when"] >= interval:
        made = backup.create_backup(user=user, note="Sync to the online server")
        newest = {"name": made["path"].name, "path": made["path"], "size": made["size"]}
    if state.last_uploaded_name == newest["name"] and not force:
        return "Online copy is up to date."
    _upload(newest["path"])
    state.last_uploaded_name, state.last_uploaded_bytes = newest["name"], newest["size"]
    state.save(update_fields=["last_uploaded_name", "last_uploaded_bytes"])
    return f"Sent {newest['name']} ({newest['size'] / 1024 / 1024:.1f} MB)."


def run_cycle(user=None, force=False):
    """One full cycle on the shop PC. Never raises: the result is stored in SyncState. Returns (ok, text)."""
    from .models import SyncState

    state = SyncState.load()
    state.last_attempt_at = timezone.now()
    notes, problems = [], []
    try:
        if not enabled():
            raise SyncError("Sync is switched off or not set up (Server settings).")
        try:
            notes.append(push_backup(user=user, force=force))
        except (SyncError, BackupError) as exc:
            problems.append(str(exc))
        if settings.SYNC_PULL_MPESA:
            try:
                from sales import relay
                if relay.enabled():
                    t = relay.pull()
                    state.last_mpesa_text = f"new {t['new']}, already had {t['known']}, conflicts {t['conflict']}"
                    notes.append("M-PESA: " + state.last_mpesa_text + ".")
            except Exception as exc:        # RelayError and anything unexpected: the backup part must still be recorded
                problems.append(f"M-PESA: {str(exc)[:150]}")
    except SyncError as exc:
        problems.append(str(exc))
    state.last_error = " ".join(problems)[:300]
    if not problems:
        state.last_success_at = timezone.now()
    state.save(update_fields=["last_attempt_at", "last_success_at", "last_error", "last_mpesa_text"])
    return (not problems), " ".join(problems or notes)


_thread = None


def start_background():
    """Called once by the server: a daemon thread that runs a cycle every minute when it is due."""
    global _thread
    if _thread and _thread.is_alive():
        return

    def loop():
        from .models import SyncState
        while True:
            time.sleep(60)
            try:
                close_old_connections()
                runtime.apply(force=True)
                if enabled():
                    last = SyncState.load().last_attempt_at
                    due = last is None or timezone.now() - last >= timedelta(minutes=min(5, settings.SYNC_INTERVAL_MINUTES))
                    if due:
                        run_cycle()
            except Exception:
                log.exception("sync loop")
            finally:
                close_old_connections()

    _thread = threading.Thread(target=loop, name="winespos-sync", daemon=True)
    _thread.start()


# --- online server side --------------------------------------------------------------------------------------------

def token_ok(header):
    expected = settings.SYNC_TOKEN or ""
    if not expected or not header or not header.startswith("Bearer "):
        return False
    return hmac.compare_digest(header[7:].strip(), expected)


def inbox():
    p = Path(settings.SYNC_INBOX_DIR)
    p.mkdir(parents=True, exist_ok=True)
    return p


def receive(stream, length, name, claimed_sha, ip=None):
    """Store an uploaded backup. `stream.read(n)` supplies the bytes. Raises SyncError (safe text) on any problem."""
    from .models import SyncReceipt

    parsed = rules.parse_name(name)
    if not parsed or parsed[1]:
        raise SyncError("The file name is not a backup name.")
    limit = settings.SYNC_MAX_UPLOAD_MB * 1024 * 1024
    if length <= 0 or length > limit:
        raise SyncError("The upload is empty or larger than the limit.")
    folder = inbox()
    final = folder / name
    part = folder / (name + ".part")
    h, got = hashlib.sha256(), 0
    try:
        with open(part, "wb") as out:
            while got < length:
                chunk = stream.read(min(1024 * 1024, length - got))
                if not chunk:
                    break
                got += len(chunk)
                h.update(chunk)
                out.write(chunk)
        if got != length:
            raise SyncError("The upload was cut off.")
        if claimed_sha and h.hexdigest() != claimed_sha.lower():
            raise SyncError("The upload does not match its checksum.")
        try:
            summary = backup.verify_backup(part)
        except BackupError as exc:
            raise SyncError(f"The backup is not usable: {exc}")
        os.replace(part, final)
    finally:
        if part.exists():
            part.unlink()
    SyncReceipt.objects.create(name=name, size=got, sha256=h.hexdigest(), source_ip=ip or None, checked=summary["checked"][:200])
    keep = [p.name for p in folder.iterdir() if rules.parse_name(p.name)]
    for old in rules.to_prune(keep, settings.SYNC_INBOX_KEEP):
        try:
            (folder / old).unlink()
        except OSError:
            pass
    try:
        audit.record("sync.received", object_type="backup", object_ref=name, new={"size": got}, ip=ip)
    except Exception:
        pass
    return {"name": name, "size": got, "checked": summary["checked"]}


def newest_received():
    folder = Path(settings.SYNC_INBOX_DIR)
    if not folder.is_dir():
        return None
    files = [(rules.parse_name(p.name)[0], p) for p in folder.iterdir() if rules.parse_name(p.name) and p.is_file()]
    return max(files, key=lambda t: t[0])[1] if files else None
