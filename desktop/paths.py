"""Where an installed shop keeps its settings and data, and a tiny .env reader (no Django needed).

Windows:  C:\\ProgramData\\WinesPOS\\{config,data}     (outside the program folder, so updates never touch it)
Other:    ~/.winespos/{config,data}                    (development only)
"""
import os
from pathlib import Path


def data_root():
    override = os.environ.get("WINESPOS_DATA_DIR")
    if override:
        return Path(override)
    if os.name == "nt":
        return Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "WinesPOS"
    return Path.home() / ".winespos"


def env_file_path():
    return Path(os.environ.get("WINESPOS_ENV_FILE") or data_root() / "config" / "winespos.env")


def client_env_file_path():
    return data_root() / "config" / "winespos-client.env"


def parse_env(text):
    values = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_env_file(path=None):
    """Put the installed shop's settings into the environment (a real environment variable still wins)."""
    path = Path(path) if path else env_file_path()
    if not path.is_file():
        return False
    for key, value in parse_env(path.read_text(encoding="utf-8")).items():
        os.environ.setdefault(key, value)
    os.environ.setdefault("WINESPOS_ENV_FILE", str(path))
    return True


def load_client_env_file(path=None):
    """Load only non-secret settings needed by a desktop client."""
    path = Path(path) if path else client_env_file_path()
    if not path.is_file():
        return False
    for key, value in parse_env(path.read_text(encoding="utf-8")).items():
        if key == "WINESPOS_SERVER_URL":
            os.environ.setdefault(key, value)
    return True
