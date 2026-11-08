"""Django settings for the Wines & Spirits POS (single shop, 1-2 tills)."""
import os
import sys
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_env_file(path: Path) -> None:
    """Minimal .env loader (no extra dependency). Real environment wins."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env_file(BASE_DIR / ".env")


def env(name, default=None):
    return os.environ.get(name, default)


def env_bool(name, default=False):
    return str(os.environ.get(name, str(default))).strip().lower() in ("1", "true", "yes", "on")


def env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        raise ImproperlyConfigured(f"{name} must be an integer")


DEBUG = env_bool("DJANGO_DEBUG", False)

SECRET_KEY = env("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "dev-only-insecure-key-do-not-use-in-production"
    else:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set (see .env.example)")

ALLOWED_HOSTS = [h.strip() for h in env("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "core",
    "accounts",
    "tills",
    "catalog",
    "inventory",
    "sales",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context.workspace",
            ],
        },
    },
]

# --- Database -------------------------------------------------------------
if env("DB_ENGINE", "postgres") == "sqlite":
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "dev.sqlite3"}}
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env("DB_NAME", "winespos"),
            "USER": env("DB_USER", "winespos"),
            "PASSWORD": env("DB_PASSWORD", ""),
            "HOST": env("DB_HOST", "127.0.0.1"),
            "PORT": env("DB_PORT", "5432"),
        }
    }
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Auth -----------------------------------------------------------------
AUTH_USER_MODEL = "accounts.User"
LOGIN_URL = "/app/manager/"

PASSWORD_HASHERS = ["django.contrib.auth.hashers.PBKDF2PasswordHasher"]
if env_bool("USE_ARGON2", False):
    PASSWORD_HASHERS.insert(0, "django.contrib.auth.hashers.Argon2PasswordHasher")
    PASSWORD_HASHERS.append("django.contrib.auth.hashers.PBKDF2PasswordHasher")
    PASSWORD_HASHERS = list(dict.fromkeys(PASSWORD_HASHERS))

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

if "test" in sys.argv[1:2]:
    # Real hashers are deliberately slow; tests create many users, so use a fast one there only.
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Idle session timeout: the cookie age is renewed on every request.
SESSION_COOKIE_AGE = env_int("SESSION_IDLE_MINUTES", 30) * 60
SESSION_SAVE_EVERY_REQUEST = True
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Strict"
CSRF_COOKIE_SAMESITE = "Strict"
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True

# --- POS security policy --------------------------------------------------
MAX_TILLS = env_int("MAX_TILLS", 2)
APPROVAL_TOKEN_MINUTES = env_int("APPROVAL_TOKEN_MINUTES", 2)
POS_AUTH_MINUTES = env_int("POS_AUTH_MINUTES", 5)
LOCKOUT_MAX_ATTEMPTS = env_int("LOCKOUT_MAX_ATTEMPTS", 5)
LOCKOUT_MINUTES = env_int("LOCKOUT_MINUTES", 5)
BIOMETRIC_ENABLED = env_bool("BIOMETRIC_ENABLED", False)
DESKTOP_SHELL_SECRET = env("DESKTOP_SHELL_SECRET", "")
# Discounts up to this share of a line need no approval for staff allowed to give discounts.
ORDINARY_DISCOUNT_MAX_PERCENT = env_int("ORDINARY_DISCOUNT_MAX_PERCENT", 5)

# --- Locale ---------------------------------------------------------------
LANGUAGE_CODE = "en-ke"
TIME_ZONE = "Africa/Nairobi"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# Uploaded product images live next to the app data, never inside the code folder.
MEDIA_URL = "media/"
MEDIA_ROOT = Path(env("WINESPOS_MEDIA_ROOT", str(BASE_DIR / "media")))
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 6 * 1024 * 1024

# --- Kenya tax: KRA eTIMS (see README, "VAT and eTIMS") -------------------------------------------
# Secrets and device details come only from .env. ETIMS_MODE=off means receipts show the VAT breakdown
# but are NOT tax invoices; nothing is sent to KRA and no KRA data is ever invented.
ETIMS_MODE = env("ETIMS_MODE", "off").strip().lower()          # off | sandbox | live
if ETIMS_MODE not in ("off", "sandbox", "live"):
    raise ImproperlyConfigured("ETIMS_MODE must be off, sandbox or live")
ETIMS_INTEGRATION = env("ETIMS_INTEGRATION", "vscu").strip().lower()   # vscu (local JAR) | oscu (KRA online)
ETIMS_BASE_URL = env("ETIMS_BASE_URL", "")                      # e.g. http://127.0.0.1:8088 for a local VSCU
ETIMS_BRANCH_ID = env("ETIMS_BRANCH_ID", "00")
ETIMS_DEVICE_SERIAL = env("ETIMS_DEVICE_SERIAL", "")
ETIMS_COMM_KEY = env("ETIMS_COMM_KEY", "")                      # OSCU communication key (secret)
ETIMS_TIMEOUT_SECONDS = env_int("ETIMS_TIMEOUT_SECONDS", 8)
# Where the QR on a signed receipt points. Placeholders: {tin} {bhf} {sign}. Empty = no QR is printed.
ETIMS_QR_URL_TEMPLATE = env("ETIMS_QR_URL_TEMPLATE", "")


# --- M-PESA (Safaricom Daraja). Secrets come only from .env. See README, "Phase 4A". -------------------
MPESA_ENV = env("MPESA_ENV", "sandbox").strip().lower()
if MPESA_ENV not in ("sandbox", "live"):
    raise ImproperlyConfigured("MPESA_ENV must be sandbox or live")
MPESA = {
    "ENV": MPESA_ENV,
    "CONSUMER_KEY": env("MPESA_CONSUMER_KEY", ""),
    "CONSUMER_SECRET": env("MPESA_CONSUMER_SECRET", ""),
    "SHORTCODE": env("MPESA_SHORTCODE", ""),            # Till: the STORE number. Paybill: the paybill number.
    "TILL_NUMBER": env("MPESA_TILL_NUMBER", ""),        # Buy Goods till number (customers pay this)
    "PASSKEY": env("MPESA_PASSKEY", ""),
    "ACCOUNT_TYPE": env("MPESA_ACCOUNT_TYPE", "till").strip().lower(),   # till | paybill
    "CALLBACK_BASE_URL": env("MPESA_CALLBACK_BASE_URL", ""),             # public HTTPS address, no trailing slash
    "CALLBACK_SECRET": env("MPESA_CALLBACK_SECRET", ""),                 # long random text inside the callback path
    "TIMEOUT": env_int("MPESA_TIMEOUT_SECONDS", 12),
    # Relay (reconciliation part 2). On the SHOP PC: where the online server is, and the token to ask it.
    # On the ONLINE server: only the token (the same value) is needed; it also uses MPESA_CALLBACK_SECRET.
    "RELAY_URL": env("MPESA_RELAY_URL", ""),
    "RELAY_TOKEN": env("MPESA_RELAY_TOKEN", ""),
}
