# PyInstaller spec. Run on WINDOWS from the project folder:   pyinstaller --noconfirm --clean packaging\winespos.spec
# (packaging\build.ps1 does this for you.) Produces dist\WinesPOS\ holding two programs that share everything else:
#   WinesPOS.exe       no console window  -> the shortcuts (pos / manager / receiving)
#   WinesPOSAdmin.exe  with a console     -> server, manage ..., configure, backups
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent
APPS = ["accounts", "accounting", "catalog", "config", "core", "desktop", "inventory", "sales", "tills"]
EXTRA = ["django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes", "django.contrib.sessions",
         "django.contrib.messages", "django.contrib.staticfiles", "django.db.backends.postgresql",
         "psycopg", "psycopg_binary", "rest_framework", "argon2", "whitenoise", "waitress", "segno", "PIL"]

hidden = []
for name in APPS + EXTRA:
    hidden += collect_submodules(name)

datas = collect_data_files("django") + collect_data_files("rest_framework")
for app in APPS:                                   # each app's screens and styling
    for sub in ("templates", "static"):
        folder = ROOT / app / sub
        if folder.is_dir():
            datas.append((str(folder), f"{app}/{sub}"))
for extra in ("templates", "staticfiles"):         # staticfiles = output of `manage.py collectstatic` (build.ps1 runs it)
    if (ROOT / extra).is_dir():
        datas.append((str(ROOT / extra), extra))

a = Analysis([str(ROOT / "desktop" / "main.py")], pathex=[str(ROOT)], datas=datas, hiddenimports=hidden,
             excludes=["tkinter", "pytest"], noarchive=False)
pyz = PYZ(a.pure)
icon = ROOT / "packaging" / "winespos.ico"
common = dict(exclude_binaries=True, icon=str(icon) if icon.exists() else None)
gui = EXE(pyz, a.scripts, [], name="WinesPOS", console=False, **common)
cli = EXE(pyz, a.scripts, [], name="WinesPOSAdmin", console=True, **common)
coll = COLLECT(gui, cli, a.binaries, a.datas, name="WinesPOS")
