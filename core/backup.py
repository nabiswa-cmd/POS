"""Backup, verify and restore (database + product images in one zip). See docs/BACKUP_AND_RESTORE.md.

PostgreSQL (real shops) uses pg_dump / pg_restore. SQLite (development and tests only) uses its own backup API.
Restoring always makes a safety copy of what is there now first, and refuses a backup from a newer program version.
"""
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from django.conf import settings
from django.db import connection, connections

from . import audit
from . import backup_rules as rules
from .backup_rules import BackupError


# --- helpers -----------------------------------------------------------------------------------------------------

def _pg_tool(name):
    """Find pg_dump / pg_restore: WINESPOS_PG_BIN, then PATH, then the usual Windows install folders."""
    exe = name + (".exe" if os.name == "nt" else "")
    folder = os.environ.get("WINESPOS_PG_BIN")
    if folder and (Path(folder) / exe).exists():
        return str(Path(folder) / exe)
    found = shutil.which(name)
    if found:
        return found
    for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),):
        root = Path(base) / "PostgreSQL"
        if root.is_dir():
            for version in sorted(root.iterdir(), reverse=True):
                candidate = version / "bin" / exe
                if candidate.exists():
                    return str(candidate)
    raise BackupError(f"{name} was not found. Install PostgreSQL's command-line tools or set WINESPOS_PG_BIN to their folder.")


def _pg_args(tool):
    s = connection.settings_dict
    return [tool, "-h", str(s.get("HOST") or "127.0.0.1"), "-p", str(s.get("PORT") or "5432"), "-U", s["USER"]]


def _pg_env():
    return {**os.environ, "PGPASSWORD": connection.settings_dict.get("PASSWORD") or ""}


def _run(cmd, env=None):
    try:
        done = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=3600)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BackupError(f"Could not run {Path(cmd[0]).name}: {exc}")
    if done.returncode != 0:
        raise BackupError(f"{Path(cmd[0]).name} failed: {(done.stderr or done.stdout).strip()[:300]}")


def code_migrations():
    from django.db.migrations.loader import MigrationLoader
    out = {}
    for app, name in MigrationLoader(connection, ignore_no_migrations=True).disk_migrations:
        out.setdefault(app, set()).add(name)
    return out


def applied_migrations():
    """{app: newest applied migration name} for the manifest, read from the live database."""
    from django.db.migrations.recorder import MigrationRecorder
    newest = {}
    for row in MigrationRecorder(connection).migration_qs.order_by("id"):
        newest[row.app] = row.name
    return newest


def _media_files():
    root = Path(settings.MEDIA_ROOT)
    if not root.is_dir():
        return []
    return [p for p in sorted(root.rglob("*")) if p.is_file()]


def _dump(dest):
    vendor = rules.engine_key(connection.vendor)
    connection.ensure_connection()
    if vendor == "sqlite":
        target = sqlite3.connect(dest)
        try:
            connection.connection.backup(target)
        finally:
            target.close()
    else:
        s = connection.settings_dict
        _run(_pg_args(_pg_tool("pg_dump")) + ["--format=custom", "--no-owner", "--no-privileges", "-f", str(dest), s["NAME"]],
             _pg_env())


# --- create --------------------------------------------------------------------------------------------------------

def create_backup(*, user=None, prerestore=False, note="", now=None):
    """Write one backup zip into BACKUP_DIR (and a second copy to BACKUP_COPY_DIR if set). Returns a result dict."""
    now = now or datetime.now(timezone.utc)
    out_dir = Path(settings.BACKUP_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    final = out_dir / rules.backup_name(now, prerestore=prerestore)
    while final.exists():                       # two backups in the same second must not overwrite each other
        now += timedelta(seconds=1)
        final = out_dir / rules.backup_name(now, prerestore=prerestore)
    vendor = rules.engine_key(connection.vendor)
    media = _media_files()
    with tempfile.TemporaryDirectory(dir=out_dir) as tmp:
        dump = Path(tmp) / rules.DB_FILES[vendor]
        _dump(dump)
        manifest = rules.build_manifest(created_at=now, vendor=vendor, db_sha256=rules.sha256_file(dump),
                                        media_count=len(media), migrations=applied_migrations(), note=note)
        part = final.with_suffix(".part")
        try:
            with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED) as z:
                z.write(dump, rules.DB_FILES[vendor])
                root = Path(settings.MEDIA_ROOT)
                for p in media:
                    z.write(p, "media/" + p.relative_to(root).as_posix())
                z.writestr("manifest.json", json.dumps(manifest, indent=2))
            os.replace(part, final)
        finally:
            if part.exists():
                part.unlink()
    result = {"path": final, "size": final.stat().st_size, "copy_path": None, "copy_error": "", "pruned": [],
              "manifest": manifest}
    copy_dir = (settings.BACKUP_COPY_DIR or "").strip()
    if copy_dir:
        try:
            target = Path(copy_dir)
            target.mkdir(parents=True, exist_ok=True)
            tmp_copy = target / (final.name + ".part")
            shutil.copy2(final, tmp_copy)
            os.replace(tmp_copy, target / final.name)
            result["copy_path"] = target / final.name
        except OSError as exc:          # a missing USB drive must not make the main backup look failed
            result["copy_error"] = f"The second copy to {copy_dir} failed: {exc}"
    result["pruned"] = prune()
    try:
        audit.record("backup.created", actor=user, object_type="backup", object_ref=final.name,
                     new={"size": result["size"], "copy_error": result["copy_error"], "prerestore": prerestore})
    except Exception:       # the backup itself is safe on disk; a logging problem must not hide that
        pass
    return result


def prune():
    removed = []
    folders = [Path(settings.BACKUP_DIR)]
    if (settings.BACKUP_COPY_DIR or "").strip():
        folders.append(Path(settings.BACKUP_COPY_DIR))
    for folder in folders:
        if not folder.is_dir():
            continue
        for name in rules.to_prune([p.name for p in folder.iterdir()], settings.BACKUP_KEEP):
            try:
                (folder / name).unlink()
                removed.append(name)
            except OSError:
                pass
    return removed


# --- list / status -------------------------------------------------------------------------------------------------

def list_backups():
    folder = Path(settings.BACKUP_DIR)
    rows = []
    if folder.is_dir():
        for p in folder.iterdir():
            parsed = rules.parse_name(p.name)
            if parsed and p.is_file():
                rows.append({"name": p.name, "path": p, "when": parsed[0], "prerestore": parsed[1], "size": p.stat().st_size})
    rows.sort(key=lambda r: r["when"], reverse=True)
    return rows


def current_status(now=None):
    """('none'|'ok'|'stale', hours, newest_row) for the newest ordinary backup."""
    now = now or datetime.now(timezone.utc)
    ordinary = [r for r in list_backups() if not r["prerestore"]]
    newest = ordinary[0] if ordinary else None
    state, hours = rules.status(newest["when"] if newest else None, now, settings.BACKUP_STALE_HOURS)
    return state, hours, newest


# --- verify --------------------------------------------------------------------------------------------------------

def _read_manifest(z):
    try:
        return json.loads(z.read("manifest.json"))
    except (KeyError, ValueError):
        raise BackupError("The backup has no readable manifest, so it is not a valid backup.")


def verify_backup(path):
    """Check a backup file is complete and undamaged. Returns a summary dict; raises BackupError if not usable."""
    path = Path(path)
    if not path.is_file() or not zipfile.is_zipfile(path):
        raise BackupError("That file is not a backup (not a zip file).")
    with zipfile.ZipFile(path) as z:
        bad = z.testzip()
        if bad:
            raise BackupError(f"The backup is damaged (file '{bad}' does not read back correctly).")
        manifest = _read_manifest(z)
        if not isinstance(manifest, dict) or manifest.get("app") != "winespos":
            raise BackupError("This is not a Wines & Spirits POS backup.")
        db_file = manifest.get("db_file")
        if db_file not in z.namelist():
            raise BackupError("The database is missing from this backup.")
        with z.open(db_file) as f:
            digest = rules.sha256_bytes_hex(iter(lambda: f.read(1024 * 1024), b""))
        if digest != manifest.get("db_sha256"):
            raise BackupError("The database inside the backup does not match its checksum. The file is damaged.")
        checked = "checksum ok"
        with tempfile.TemporaryDirectory() as tmp:
            extracted = Path(tmp) / db_file
            with z.open(db_file) as src, open(extracted, "wb") as dst:
                shutil.copyfileobj(src, dst)
            if manifest.get("engine") == "sqlite":
                conn = sqlite3.connect(f"file:{extracted.as_posix()}?mode=ro", uri=True)
                try:
                    if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise BackupError("The database inside the backup is damaged.")
                    tables = conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
                except sqlite3.DatabaseError as exc:
                    raise BackupError(f"The database inside the backup cannot be opened: {exc}")
                finally:
                    conn.close()
                checked = f"checksum ok, database opens and passes its own integrity check ({tables} tables)"
            else:
                try:
                    _run([_pg_tool("pg_restore"), "--list", str(extracted)])
                    checked = "checksum ok, PostgreSQL can read the dump"
                except BackupError as exc:
                    if "was not found" not in str(exc):
                        raise
                    checked = "checksum ok (PostgreSQL tools not found, so the dump itself was not opened)"
        names = z.namelist()
    return {"path": path, "created_at": manifest["created_at"], "engine": manifest["engine"],
            "media_files": sum(1 for n in names if n.startswith("media/")), "checked": checked,
            "size": path.stat().st_size, "manifest": manifest}


# --- restore -------------------------------------------------------------------------------------------------------

def _restore_media(z, stamp):
    root = Path(settings.MEDIA_ROOT)
    moved = None
    if root.exists():
        moved = root.with_name(f"{root.name}.before-restore-{stamp}")
        shutil.move(str(root), str(moved))
    root.mkdir(parents=True, exist_ok=True)
    base = root.resolve()
    for name in z.namelist():
        if not name.startswith("media/") or name.endswith("/"):
            continue
        target = (root / name[len("media/"):]).resolve()
        if base not in target.parents:                   # a crafted zip must never write outside the media folder
            raise BackupError("The backup contains an unsafe file path and was not restored.")
        target.parent.mkdir(parents=True, exist_ok=True)
        with z.open(name) as src, open(target, "wb") as dst:
            shutil.copyfileobj(src, dst)
    return moved


def restore_backup(path, *, user=None, now=None):
    """Put a backup back. STOP the server first (see the guide). Returns a summary dict.

    Order: verify the file, check it fits this program, save the CURRENT state as a pre-restore backup, then replace
    the database and the product images. If anything fails before the replace, nothing has changed.
    """
    summary = verify_backup(path)
    rules.check_manifest(summary["manifest"], vendor=rules.engine_key(connection.vendor), code_migrations=code_migrations())
    now = now or datetime.now(timezone.utc)
    safety = create_backup(user=user, prerestore=True, note=f"Before restoring {Path(path).name}", now=now)
    with zipfile.ZipFile(path) as z, tempfile.TemporaryDirectory() as tmp:
        db_file = summary["manifest"]["db_file"]
        extracted = Path(tmp) / db_file
        with z.open(db_file) as src, open(extracted, "wb") as dst:
            shutil.copyfileobj(src, dst)
        if connection.vendor == "sqlite":
            source = sqlite3.connect(extracted)
            try:
                connection.ensure_connection()
                source.backup(connection.connection)
            finally:
                source.close()
        else:
            s = connection.settings_dict
            connections.close_all()
            _run(_pg_args(_pg_tool("pg_restore")) + ["--clean", "--if-exists", "--no-owner", "--no-privileges",
                                                    "-d", s["NAME"], str(extracted)], _pg_env())
        moved = _restore_media(z, f"{now:%Y%m%d-%H%M%S}")
    connections.close_all()
    try:
        audit.record("backup.restored", actor=user, object_type="backup", object_ref=Path(path).name,
                     new={"safety_copy": safety["path"].name})
    except Exception:
        pass
    return {"restored": Path(path).name, "safety_copy": safety["path"], "old_media": moved, "created_at": summary["created_at"]}
