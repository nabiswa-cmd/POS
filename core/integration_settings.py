"""Environment-backed or encrypted database configuration for payment/tax integrations."""
import base64
import hashlib
import json

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def _fernet():
    key = settings.INTEGRATION_SETTINGS_KEY
    if not key:
        raise ImproperlyConfigured(
            "INTEGRATION_SETTINGS_KEY must be configured outside the database to use integration settings.")
    derived = base64.urlsafe_b64encode(hashlib.sha256(key.encode("utf-8")).digest())
    return Fernet(derived)


def encrypt_values(values):
    payload = json.dumps(values, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _fernet().encrypt(payload).decode("ascii")


def decrypt_values(ciphertext, label):
    if not ciphertext:
        return {}
    try:
        values = json.loads(_fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8"))
    except (InvalidToken, UnicodeDecodeError, ValueError) as exc:
        raise ImproperlyConfigured(
            f"Cannot decrypt {label} settings. Check the external INTEGRATION_SETTINGS_KEY.") from exc
    if not isinstance(values, dict) or not all(isinstance(key, str) for key in values):
        raise ImproperlyConfigured(f"Stored {label} settings have an invalid format.")
    return values


def environment_mpesa():
    return dict(settings.MPESA)


def environment_etims():
    return {
        "MODE": settings.ETIMS_MODE,
        "INTEGRATION": settings.ETIMS_INTEGRATION,
        "BASE_URL": settings.ETIMS_BASE_URL,
        "BRANCH_ID": settings.ETIMS_BRANCH_ID,
        "DEVICE_SERIAL": settings.ETIMS_DEVICE_SERIAL,
        "COMM_KEY": settings.ETIMS_COMM_KEY,
        "TIMEOUT_SECONDS": settings.ETIMS_TIMEOUT_SECONDS,
        "QR_URL_TEMPLATE": settings.ETIMS_QR_URL_TEMPLATE,
    }


def _configured_values(source_field, ciphertext_field, environment_values, label):
    from core.models import IntegrationConfiguration

    row = IntegrationConfiguration.objects.filter(pk=1).only(source_field, ciphertext_field).first()
    if row is None or getattr(row, source_field) != IntegrationConfiguration.SOURCE_DATABASE:
        return environment_values()
    values = decrypt_values(getattr(row, ciphertext_field), label)
    if not values:
        raise ImproperlyConfigured(f"{label} is set to database source but has no saved configuration.")
    missing = set(environment_values()) - set(values)
    if missing:
        raise ImproperlyConfigured(f"{label} database configuration is missing: {', '.join(sorted(missing))}.")
    return values


def mpesa():
    values = _configured_values("mpesa_source", "mpesa_config_ciphertext", environment_mpesa, "M-PESA")
    values["TIMEOUT"] = int(values["TIMEOUT"])
    return values


def etims():
    values = _configured_values("etims_source", "etims_config_ciphertext", environment_etims, "eTIMS")
    values["TIMEOUT_SECONDS"] = int(values["TIMEOUT_SECONDS"])
    return values
