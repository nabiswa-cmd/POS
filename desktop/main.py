"""The single program behind every shortcut.  WinesPOS.exe <command>   (WinesPOSAdmin.exe is the same with a console)

    pos | manager | receiving     open that window (needs the server running)
    server                          run the shop server (Waitress)
    manage <django command...>      e.g. manage migrate, manage selfcheck, manage backup_db, manage restore_db <zip> --yes
    configure ...                   first-run settings (see desktop/configure.py)
    printers [--test NAME]          list printers / test one
"""
import os
import sys

GUI = ("pos", "manager", "receiving")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    command, rest = argv[0], argv[1:]

    from desktop import paths
    paths.load_env_file()
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

    if command in GUI:
        from desktop import launcher
        return launcher.main(["--mode", command] + rest)
    if command == "server":
        from desktop import run_server
        run_server.main()
        return 0
    if command == "manage":
        from django.core.management import execute_from_command_line
        execute_from_command_line(["winespos"] + rest)
        return 0
    if command == "configure":
        from desktop import configure
        return configure.main(rest)
    if command == "printers":
        from desktop import printers
        return printers.main(rest)
    print(f"Unknown command '{command}'.\n{__doc__}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
