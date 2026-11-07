# Wines & Spirits POS (single shop, 1 or 2 tills)

Phase 1 (Foundation): accounts, roles and permissions, supervisor approvals, audit log,
shop settings, tills and shifts, the three desktop entry points (POS, Manager, Receiving).
Later phases (products, sales screen, M-PESA, reports, installer) build on this.

## What is built

| Area | Where |
|---|---|
| Permission catalogue and default roles | `accounts/permissions.py` |
| Approval rules (single-use, expiring tokens bound to action, cashier and item) | `accounts/approval_rules.py`, `accounts/services.py` |
| Password/PIN login, lockout, login history | `accounts/services.py`, `accounts/lockout.py` |
| Optional fingerprint (off by default; server side done, Windows Hello call is Phase 6) | `accounts/services.py`, `desktop/biometric.py` |
| Audit log (append-only; PostgreSQL trigger via `harden_db`) | `core/models.py`, `core/db_hardening.py` |
| Shop and receipt settings, approval policies | `core/models.py`, `accounts/models.py` |
| POS opening flow (supervisor, then cashier, then shift) | `tills/views.py`, `core/templates/core/app.html` |
| Desktop shell and local server | `desktop/launcher.py`, `desktop/run_server.py` |
| Staff / roles / policies management (Phase 1 uses the Django admin, managers only) | `accounts/admin.py` |

## Rules enforced on the server (not just on screen)

- A restricted action needs a valid approval token: single-use, 2-minute expiry (configurable),
  bound to the action, the requesting cashier and the item. Closing the dialog or using shortcuts grants nothing.
- Nobody can approve their own action. The approver must hold the matching `approve.*` permission.
- The supervisor who authorizes opening the POS cannot also be the cashier on that authorization.
- Opening the POS always needs authorization. The manager chooses which other actions need approval (Approval policies).
- Only one open shift per till, enforced by a database constraint.
- Staff are deactivated, never deleted. Audit entries, login history and approvals cannot be edited or deleted.

## Setup (developer PC)

Requires Python 3.11+ and PostgreSQL 15+.

```
python -m venv .venv
.venv\Scripts\activate            (Windows)   or   source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env            then edit .env (set DJANGO_SECRET_KEY and DB_PASSWORD)

# create the database and user in PostgreSQL once:
#   CREATE USER winespos WITH PASSWORD '...';  CREATE DATABASE winespos OWNER winespos;

python manage.py makemigrations accounts core tills
python manage.py migrate
python manage.py harden_db
python manage.py setup_shop --shop-name "Your Shop" --manager-username owner
python manage.py collectstatic --noinput
python -m desktop.run_server
```

In another terminal:

```
python -m desktop.launcher --mode manager
python -m desktop.launcher --mode pos --allow-quit      (--allow-quit is for development only)
python -m desktop.launcher --mode receiving
```

Then in the manager screen open the management screens (Django admin) to create staff, assign roles, set PINs,
and tune approval policies. Staff need a PIN or password to log in at the POS.

`makemigrations` must be run once and the generated `migrations/` folders committed. They were not generated in
the build environment because Django could not be installed there.

## Tests

```
python3 -m unittest discover -s tests_pure -t .          # rules only, no Django needed
DB_ENGINE=sqlite DJANGO_DEBUG=true python manage.py test  # full suite (Windows: set the two variables first)
```

SQLite is for tests only: it has no row-level locking, so real tills must use PostgreSQL.

## Configuration

See `.env.example`. Secrets (`DJANGO_SECRET_KEY`, `DB_PASSWORD`, later Daraja keys) come only from `.env` or the
environment, never from source. Two tills: set `WINESPOS_BIND=0.0.0.0` on the server PC, add its LAN address to
`DJANGO_ALLOWED_HOSTS`, and start the launcher on Till 2 with `--server http://<server-ip>:8000`.

## Known limits of Phase 1

- Fingerprint: the server verifies signed assertions, but the Windows Hello call is not wired in yet (Phase 6, needs real hardware to test).
  `DESKTOP_SHELL_SECRET` lives in the installed config; protect that file with Windows permissions.
- The manager UI is the Django admin for now; the custom dashboard and screens come in later phases.
- Full-screen kiosk lock in the POS window blocks closing and navigation inside the app but cannot stop Windows-level
  shortcuts such as Alt+Tab; Phase 6 adds a Windows kiosk/assigned-access guide.
- Till closing and cash-up are Phase 5. Shifts can currently be opened only.
