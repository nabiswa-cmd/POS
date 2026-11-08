"""Pure backup rules (no Django): file names, the manifest, retention and staleness.

A backup is ONE zip file: the database dump, the product images, and manifest.json (what it is and checksums).
"""
import hashlib
import re
from datetime import datetime, timedelta, timezone

FORMAT = 1
NAME_RE = re.compile(r"^winespos-(\d{8})-(\d{6})(-prerestore)?\.zip$")
DB_FILES = {"sqlite": "database.sqlite3", "postgresql": "database.dump"}


class BackupError(Exception):
    """A backup or restore problem. The message is safe to show the manager."""


def backup_name(moment, *, prerestore=False):
    return f"winespos-{moment:%Y%m%d-%H%M%S}{'-prerestore' if prerestore else ''}.zip"


def parse_name(name):
    """-> (datetime in UTC, is_prerestore) or None when the name is not one of ours."""
    m = NAME_RE.match(name or "")
    if not m:
        return None
    when = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    return when, bool(m.group(3))


def sha256_bytes_hex(chunks):
    h = hashlib.sha256()
    for c in chunks:
        h.update(c)
    return h.hexdigest()


def sha256_file(path, block=1024 * 1024):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(block), b""):
            h.update(chunk)
    return h.hexdigest()


def engine_key(vendor):
    if vendor not in DB_FILES:
        raise BackupError(f"Backups are not supported for database type '{vendor}'.")
    return vendor


def build_manifest(*, created_at, vendor, db_sha256, media_count, migrations, note=""):
    return {"format": FORMAT, "app": "winespos", "created_at": created_at.astimezone(timezone.utc).isoformat(),
            "engine": engine_key(vendor), "db_file": DB_FILES[vendor], "db_sha256": db_sha256,
            "media_files": int(media_count), "migrations": dict(migrations), "note": note}


def check_manifest(manifest, *, vendor, code_migrations):
    """Raise BackupError unless this backup can be restored onto this installation.

    - must be one of our backups, in a format we know
    - must come from the same kind of database
    - must not be NEWER than the program: a backup that has migrations this program has never heard of
      belongs to a later version, and restoring it would break the shop.
    """
    if not isinstance(manifest, dict) or manifest.get("app") != "winespos":
        raise BackupError("This is not a Wines & Spirits POS backup.")
    if manifest.get("format") != FORMAT:
        raise BackupError("This backup was made by a different version of the backup tool.")
    if manifest.get("engine") != vendor:
        raise BackupError(f"This backup is from a {manifest.get('engine')} database but this shop uses {vendor}.")
    for app, name in (manifest.get("migrations") or {}).items():
        known = code_migrations.get(app)
        if known is None or name not in known:
            raise BackupError(f"This backup is from a newer version of the program ({app} {name}). "
                              "Update the program first, then restore.")
    return True


def to_prune(names, keep):
    """Ordinary backups beyond the newest `keep`. Pre-restore safety copies are kept (newest 3 only)."""
    ordinary, safety = [], []
    for n in names:
        parsed = parse_name(n)
        if parsed:
            (safety if parsed[1] else ordinary).append((parsed[0], n))
    ordinary.sort(reverse=True)
    safety.sort(reverse=True)
    return [n for _, n in ordinary[max(keep, 1):]] + [n for _, n in safety[3:]]


def age_hours(newest, now):
    return (now - newest) / timedelta(hours=1)


def status(newest, now, stale_hours):
    """-> ('none'|'ok'|'stale', hours or None)."""
    if newest is None:
        return "none", None
    hours = age_hours(newest, now)
    return ("stale" if hours > stale_hours else "ok"), hours
