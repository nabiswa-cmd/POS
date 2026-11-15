"""First-run configuration: writes the shop's settings file and (server PC) creates the PostgreSQL database.

    WinesPOSAdmin.exe configure --role server [--lan-host 192.168.1.10]
    WinesPOSAdmin.exe configure --role till --server-url http://192.168.1.10:8000
    WinesPOSAdmin.exe configure --role server --provision-db      (needs WINESPOS_PG_ADMIN_PASSWORD in the environment)

Safe to run again: existing secrets and the database password are KEPT, never regenerated, unless --force.
The settings file holds secrets, so it lives outside the program folder and is readable only by the owner / administrators.
"""
import argparse
import os
import secrets
import sys
from pathlib import Path

from . import paths


class ConfigError(ValueError):
    pass


def generate_secret(nbytes=48):
    return secrets.token_urlsafe(nbytes)


def _clean(value):
    value = str(value)
    if "\n" in value or "\r" in value:
        raise ConfigError("Settings cannot contain line breaks.")
    return value


def build_values(role, *, root, existing=None, lan_host="", server_url="", port=8000):
    """The settings for this PC. `existing` (a dict from an earlier run) wins for every secret."""
    existing = dict(existing or {})
    if role == "till":
        if not server_url.startswith(("http://", "https://")):
            raise ConfigError("A till PC needs the server's address, e.g. http://192.168.1.10:8000")
        return {"WINESPOS_SERVER_URL": server_url.rstrip("/")}
    if role != "server":
        raise ConfigError("Role must be 'server' or 'till'.")
    root = Path(root)
    hosts = ["localhost", "127.0.0.1"] + ([lan_host] if lan_host else [])
    return {
        "DJANGO_DEBUG": "false",
        "DJANGO_SECRET_KEY": existing.get("DJANGO_SECRET_KEY") or generate_secret(),
        "DJANGO_ALLOWED_HOSTS": ",".join(hosts),
        "DB_ENGINE": "postgres", "DB_NAME": "winespos", "DB_USER": "winespos",
        "DB_PASSWORD": existing.get("DB_PASSWORD") or generate_secret(24),
        "DB_HOST": "127.0.0.1", "DB_PORT": "5432",
        "WINESPOS_BIND": "0.0.0.0" if lan_host else "127.0.0.1",   # only open to the shop network if a second till needs it
        "WINESPOS_PORT": str(port),
        "WINESPOS_SERVER_URL": f"http://127.0.0.1:{port}",
        "WINESPOS_MEDIA_ROOT": str(root / "data" / "media"),
        "WINESPOS_BACKUP_DIR": str(root / "data" / "backups"),
        "DESKTOP_SHELL_SECRET": existing.get("DESKTOP_SHELL_SECRET") or generate_secret(),
        "INTEGRATION_SETTINGS_KEY": existing.get("INTEGRATION_SETTINGS_KEY") or generate_secret(),
        # Everything below keeps the program's own defaults; edit this file to change them.
    }


def render_env(values):
    lines = ["# Wines & Spirits POS settings for this PC. Contains secrets: do not email or share this file."]
    for key in sorted(values):
        lines.append(f"{key}={_clean(values[key])}")
    return "\n".join(lines) + "\n"


def write_env(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(text, encoding="utf-8")
    if os.name != "nt":
        os.chmod(tmp, 0o600)
    os.replace(tmp, path)       # on Windows the installer script then restricts the file with icacls


def provision_postgres(values, admin_password, admin_user="postgres"):
    """Create the shop's database login and database if they do not exist; set the login's password to the saved one."""
    import psycopg
    from psycopg import sql
    with psycopg.connect(host=values["DB_HOST"], port=values["DB_PORT"], user=admin_user, password=admin_password,
                         dbname="postgres", autocommit=True, connect_timeout=10) as conn:
        user, name, password = values["DB_USER"], values["DB_NAME"], values["DB_PASSWORD"]
        if conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (user,)).fetchone():
            conn.execute(sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(sql.Identifier(user), sql.Literal(password)))
        else:
            conn.execute(sql.SQL("CREATE ROLE {} WITH LOGIN PASSWORD {}").format(sql.Identifier(user), sql.Literal(password)))
        if not conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone():
            conn.execute(sql.SQL("CREATE DATABASE {} OWNER {} ENCODING 'UTF8'").format(sql.Identifier(name), sql.Identifier(user)))


def main(argv=None):
    p = argparse.ArgumentParser(description="Configure this PC for the Wines & Spirits POS")
    p.add_argument("--role", choices=("server", "till"), required=True)
    p.add_argument("--lan-host", default="", help="Server PC: its shop-network address, if a second till connects to it.")
    p.add_argument("--server-url", default="", help="Till PC: http://<server address>:8000")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--force", action="store_true", help="Generate new secrets even if a settings file exists (breaks saved logins).")
    p.add_argument("--provision-db", action="store_true", help="Server PC: create the PostgreSQL login and database.")
    a = p.parse_args(argv)
    path = paths.env_file_path()
    existing = {} if a.force or not path.is_file() else paths.parse_env(path.read_text(encoding="utf-8"))
    try:
        values = build_values(a.role, root=paths.data_root(), existing=existing, lan_host=a.lan_host,
                              server_url=a.server_url, port=a.port)
    except ConfigError as exc:
        print(f"Cannot configure: {exc}", file=sys.stderr)
        return 2
    # keep anything the shop added by hand (M-PESA, eTIMS, printer, backup copy folder ...)
    merged = {**{k: v for k, v in existing.items() if k not in values}, **values}
    write_env(path, render_env(merged))
    print(f"Settings written to {path}")
    if a.role == "server":
        for folder in ("media", "backups"):
            (paths.data_root() / "data" / folder).mkdir(parents=True, exist_ok=True)
        if a.provision_db:
            admin_pw = os.environ.get("WINESPOS_PG_ADMIN_PASSWORD", "")
            if not admin_pw:
                print("Set WINESPOS_PG_ADMIN_PASSWORD (the PostgreSQL 'postgres' password) to create the database.", file=sys.stderr)
                return 2
            try:
                provision_postgres(merged, admin_pw)
            except Exception as exc:        # driver errors vary; the message is what the installer log needs
                print(f"Could not create the database: {exc}", file=sys.stderr)
                return 3
            print("Database and login are ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
