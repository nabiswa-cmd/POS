"""Production-style local server (Waitress). Runs on the shop server PC.

    python -m desktop.run_server

Set WINESPOS_BIND=0.0.0.0 on the server PC so Till 2 can reach it over the shop LAN,
and add the server's LAN address to DJANGO_ALLOWED_HOSTS.
"""
import os


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    from waitress import serve

    from config.wsgi import application

    host = os.environ.get("WINESPOS_BIND", "127.0.0.1")
    port = int(os.environ.get("WINESPOS_PORT", "8000"))
    serve(application, host=host, port=port, threads=8)


if __name__ == "__main__":
    main()
