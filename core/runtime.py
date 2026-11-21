"""Settings the administrator can change in the database (Django admin > Server settings) instead of the .env file.

How it works: every name in REGISTRY has one row in core.RuntimeSetting. A row with a value overrides the .env /
default value; an empty row means "use the .env / default value". The values are copied onto django.conf.settings
at the start of a request (cached a few seconds, so every worker picks up a change quickly) and by the background
sync loop. Secrets are stored encrypted with INTEGRATION_SETTINGS_KEY, which must stay in the .env.

What can NOT live in the database, because it is needed before the database can be opened: DJANGO_SECRET_KEY,
DJANGO_DEBUG, DB_* (database address and password), INTEGRATION_SETTINGS_KEY, WINESPOS_BIND / WINESPOS_PORT and
WINESPOS_MEDIA_ROOT. M-PESA, eTIMS, and Barcode Lookup values have their own integration settings screen.
"""
import time
import logging
from collections import namedtuple
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.utils import OperationalError, ProgrammingError

Def = namedtuple("Def", "name kind group label help secret", defaults=(False,))

G_SYNC = "Shop PC: send to the online server"
G_ONLINE = "Online server: receive from shops"
G_POLICY = "Security policy"
G_BACKUP = "Backups"

REGISTRY = [
    Def("SYNC_ENABLED", "bool", G_SYNC, "Sync switched on", "true on the shop PC once the online server is ready."),
    Def("SYNC_SERVER_URL", "url", G_SYNC, "Online server address",
        "Change this whenever the online server's address changes, e.g. https://pos.example.com or http://41.90.1.2:8000"),
    Def("SYNC_TOKEN", "str", G_SYNC, "Sync token (secret)",
        "A long random text. It must be exactly the same on the shop PC and on the online server.", True),
    Def("SYNC_INTERVAL_MINUTES", "int", G_SYNC, "Send a backup every (minutes)", "Default 30. Between 5 and 1440."),
    Def("SYNC_TIMEOUT_SECONDS", "int", G_SYNC, "Upload time limit (seconds)", "Default 120. Raise it on a slow line."),
    Def("SYNC_PULL_MPESA", "bool", G_SYNC, "Also fetch M-PESA payments each cycle",
        "The online server receives Safaricom's messages; the shop collects them here. Needs the M-PESA relay settings."),
    Def("SYNC_MAX_UPLOAD_MB", "int", G_ONLINE, "Largest backup accepted (MB)", "Default 500."),
    Def("SYNC_INBOX_DIR", "path", G_ONLINE, "Folder for received backups", "Folder on the online server."),
    Def("SYNC_INBOX_KEEP", "int", G_ONLINE, "Received backups to keep", "Default 60. Older ones are deleted."),
    Def("SYNC_TUNNEL_AUTHORIZED_KEYS", "path", G_ONLINE, "Tunnel keys file (lets a new shop PC enrol itself)",
        "e.g. /home/tunnel/.ssh/authorized_keys. Leave empty to switch automatic enrolment off (safest once all PCs are set up)."),
    Def("SYNC_TUNNEL_PORT", "int", G_ONLINE, "Tunnel port", "Default 8021."),
    Def("EXTRA_ALLOWED_HOSTS", "str", G_ONLINE, "Extra allowed addresses",
        "Comma separated host names or IPs this server may answer to, e.g. pos.example.com,41.90.1.2"),
    Def("SESSION_IDLE_MINUTES", "int", G_POLICY, "Sign out after idle minutes", "Default 30."),
    Def("APPROVAL_TOKEN_MINUTES", "int", G_POLICY, "Approval valid for (minutes)", "Default 2."),
    Def("POS_AUTH_MINUTES", "int", G_POLICY, "Till authorization valid for (minutes)", "Default 5."),
    Def("LOCKOUT_MAX_ATTEMPTS", "int", G_POLICY, "Wrong passwords before lock", "Default 5."),
    Def("LOCKOUT_MINUTES", "int", G_POLICY, "Lock time (minutes)", "Default 5."),
    Def("MAX_TILLS", "int", G_POLICY, "Most tills", "Default 2."),
    Def("ORDINARY_DISCOUNT_MAX_PERCENT", "int", G_POLICY, "Discount without approval (%)", "Default 5."),
    Def("BIOMETRIC_ENABLED", "bool", G_POLICY, "Fingerprint sign-in", "true / false."),
    Def("DESKTOP_SHELL_SECRET", "str", G_POLICY, "Fingerprint shared secret", "Only if fingerprint sign-in is on.", True),
    Def("BACKUP_DIR", "path", G_BACKUP, "Backup folder", "Folder on this computer."),
    Def("BACKUP_COPY_DIR", "path", G_BACKUP, "Second copy folder", "A different disk, e.g. a USB drive. Empty = none."),
    Def("BACKUP_KEEP", "int", G_BACKUP, "Backups to keep", "Default 30."),
    Def("BACKUP_STALE_HOURS", "int", G_BACKUP, "Warn when newest backup is older than (hours)", "Default 26."),
]
BY_NAME = {d.name: d for d in REGISTRY}
INT_LIMITS = {"SYNC_INTERVAL_MINUTES": (5, 1440), "SYNC_TIMEOUT_SECONDS": (5, 3600), "SYNC_MAX_UPLOAD_MB": (1, 20000),
              "SYNC_INBOX_KEEP": (1, 1000), "SESSION_IDLE_MINUTES": (1, 1440), "APPROVAL_TOKEN_MINUTES": (1, 60),
              "POS_AUTH_MINUTES": (1, 120), "LOCKOUT_MAX_ATTEMPTS": (1, 100), "LOCKOUT_MINUTES": (1, 1440),
              "MAX_TILLS": (1, 20), "ORDINARY_DISCOUNT_MAX_PERCENT": (0, 100), "BACKUP_KEEP": (1, 1000),
              "BACKUP_STALE_HOURS": (1, 720)}
CACHE_SECONDS = 5
ENC = "enc:"

_cache = {"at": 0.0}
_defaults = {}
_applied = set()
logger = logging.getLogger(__name__)


# --- value handling (pure) -----------------------------------------------------------------------------------------

def parse(defn, raw):
    """Text typed by the administrator -> the Python value. Raises ValidationError with a plain message."""
    raw = (raw or "").strip()
    if defn.kind == "bool":
        if raw.lower() in ("1", "true", "yes", "on"):
            return True
        if raw.lower() in ("0", "false", "no", "off"):
            return False
        raise ValidationError("Enter true or false.")
    if defn.kind == "int":
        try:
            n = int(raw)
        except ValueError:
            raise ValidationError("Enter a whole number.")
        lo, hi = INT_LIMITS.get(defn.name, (0, 10 ** 9))
        if not lo <= n <= hi:
            raise ValidationError(f"Enter a number from {lo} to {hi}.")
        return n
    if defn.kind == "url":
        if raw and not raw.lower().startswith(("http://", "https://")):
            raise ValidationError("The address must start with http:// or https://")
        return raw.rstrip("/")
    if defn.kind == "path":
        return raw
    return raw


def _fernet_ready():
    from core import integration_settings
    integration_settings._fernet()


def encode(defn, raw):
    """Value as stored in the row."""
    raw = (raw or "").strip()
    if not raw or not defn.secret:
        return raw
    from core import integration_settings
    return ENC + integration_settings.encrypt_values({"v": raw})


def decode(defn, stored):
    if defn.secret and stored.startswith(ENC):
        from core import integration_settings
        return integration_settings.decrypt_values(stored[len(ENC):], defn.name).get("v", "")
    return stored


def _remember_defaults():
    if _defaults:
        return
    for d in REGISTRY:
        if d.name == "SESSION_IDLE_MINUTES":
            _defaults[d.name] = int(settings.SESSION_COOKIE_AGE // 60)
        elif d.name == "EXTRA_ALLOWED_HOSTS":
            _defaults[d.name] = list(settings.ALLOWED_HOSTS)
        else:
            _defaults[d.name] = getattr(settings, d.name, "")


def _set(name, value):
    if name == "SESSION_IDLE_MINUTES":
        settings.SESSION_COOKIE_AGE = int(value) * 60
    elif name == "EXTRA_ALLOWED_HOSTS":
        base = list(_defaults["EXTRA_ALLOWED_HOSTS"])
        extra = [h.strip() for h in str(value).split(",") if h.strip()]
        settings.ALLOWED_HOSTS = base + [h for h in extra if h not in base]
    else:
        setattr(settings, name, value)


def ensure_rows():
    from core.models import RuntimeSetting
    have = set(RuntimeSetting.objects.values_list("name", flat=True))
    RuntimeSetting.objects.bulk_create([RuntimeSetting(name=d.name) for d in REGISTRY if d.name not in have])


def effective(name):
    """What the program is using right now (for display)."""
    if name == "SESSION_IDLE_MINUTES":
        return int(settings.SESSION_COOKIE_AGE // 60)
    if name == "EXTRA_ALLOWED_HOSTS":
        return ", ".join(settings.ALLOWED_HOSTS)
    return getattr(settings, name, "")


def _restore(name):
    if name == "SESSION_IDLE_MINUTES":
        settings.SESSION_COOKIE_AGE = int(_defaults[name]) * 60
    elif name == "EXTRA_ALLOWED_HOSTS":
        settings.ALLOWED_HOSTS = list(_defaults[name])
    else:
        setattr(settings, name, _defaults[name])


def apply(force=False):
    """Copy the database values onto settings. Never raises: if the database is unreachable the .env values stay.

    Only settings that have a value saved in the database are touched; clearing a value puts the .env / default value
    back. Everything else is left exactly as it is."""
    now = time.monotonic()
    if not force and now - _cache["at"] < CACHE_SECONDS:
        return
    _remember_defaults()
    try:
        from core.models import RuntimeSetting
        rows = dict(RuntimeSetting.objects.values_list("name", "value"))
    except Exception:
        return
    _cache["at"] = now
    for d in REGISTRY:
        stored = rows.get(d.name, "")
        value = None
        if stored:
            try:
                value = parse(d, decode(d, stored))
            except Exception:
                value = None                       # a damaged or undecryptable row never breaks the site
        if value is not None:
            _set(d.name, Path(value) if d.name == "BACKUP_DIR" else value)
            _applied.add(d.name)
        elif d.name in _applied:
            _restore(d.name)
            _applied.discard(d.name)
    _apply_local_till_hosts(rows)


def _apply_local_till_hosts(rows):
    hosts = list(_defaults["EXTRA_ALLOWED_HOSTS"])
    stored = rows.get("EXTRA_ALLOWED_HOSTS", "")
    if stored:
        try:
            hosts.extend(host.strip() for host in parse(BY_NAME["EXTRA_ALLOWED_HOSTS"], stored).split(",")
                         if host.strip())
        except ValidationError:
            logger.warning("Ignoring invalid saved extra host names while applying local till hosts.")
    try:
        from tills.local_servers import local_server_hosts
        hosts.extend(local_server_hosts())
    except (OperationalError, ProgrammingError):
        logger.warning("Till local host names are unavailable until the database migration is applied.")
    settings.ALLOWED_HOSTS = list(dict.fromkeys(hosts))


class RuntimeSettingsMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        apply()
        return self.get_response(request)
