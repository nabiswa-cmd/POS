"""Production-style local server (Waitress). Runs on the shop server PC.

    python -m desktop.run_server

Set WINESPOS_BIND=0.0.0.0 on the server PC so Till 2 can reach it over the shop LAN,
and add the server's LAN address to DJANGO_ALLOWED_HOSTS.
"""
import logging
import logging.handlers
import os


def setup_logging():
    """The server runs hidden under Task Scheduler, so errors go to a file: <data folder>\\logs\\server.log (5 x 2 MB)."""
    from desktop import paths

    folder = paths.data_root() / "logs"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(folder / "server.log", maxBytes=2_000_000, backupCount=5,
                                                       encoding="utf-8")
    except OSError:
        return None            # no writable log folder (development): fall back to the console
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    for name in ("django.request", "django.server", "waitress", "winespos"):
        log = logging.getLogger(name)
        log.setLevel(logging.INFO)
        log.addHandler(handler)
    return folder / "server.log"


def main():
    from desktop import paths

    paths.load_env_file()
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    setup_logging()
    from waitress import serve

    from config.wsgi import application

    host = os.environ.get("WINESPOS_BIND", "127.0.0.1")
    port = int(os.environ.get("WINESPOS_PORT", "8000"))
    serve(application, host=host, port=port, threads=8)


if __name__ == "__main__":
    main()
