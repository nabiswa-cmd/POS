# Online server, backup sync and M-PESA callback: step by step

The shop keeps selling on its own PC and database (it works with no internet). The **online server** is a second copy of this
program on a rented Linux server with a public address. It does two jobs:

1. **Safe copy.** Every `SYNC_INTERVAL_MINUTES` (default 30) the shop makes a backup (database + product pictures) and uploads it.
   The online server checks it and keeps the newest 60. If the shop PC dies, the newest file is your shop.
2. **M-PESA.** Safaricom can only call a public address, so it calls the online server. The shop collects those payments
   in the same cycle (the "relay", already in the program).

If the internet is down nothing at the till stops. The shop records the problem and sends the backup when the line is back.

What is **not** a two-way live database sync: sales are made on the shop PC only; the online server holds backups, not a
second live till. (Restoring the newest backup on any machine gives you the shop as it was.)

## Use the online server as the live POS instead

This is the cloud-first option if phones, tablets, and tills must use the same live database from anywhere. It is different
from the offline-first backup setup above: install this application and PostgreSQL on the VPS, then point every till and
browser to its HTTPS domain. The tills connect over their normal Wi-Fi or Ethernet internet connection; no `192.168.x.x`
address or direct cable to the VPS is involved. If the internet or VPS is unavailable, sales cannot be made until it returns.

For this mode, run the migration and static-file commands from section A, but use `setup_shop` instead of
`createsuperuser`; it creates the manager account, roles, and tills needed by the POS:

```bash
../venv/bin/python manage.py setup_shop --shop-name "Your Shop" --manager-username owner --tills 2
```

Then open `https://pos.yourdomain.com/app/pos/?till=1` on each till or phone. On each Windows till, run the installer,
choose **Till PC**, and enter `https://pos.yourdomain.com`. The manager and cashier accounts are created on the VPS and
are shared across connected devices. Keep PostgreSQL bound to the VPS itself; expose only HTTPS (and SSH restricted to
administrators), not port 5432. Configure and test database backups before using this as the live server.

## What can be changed from the Django admin (no file editing)

Django admin > **Server settings**. Leave a value empty to use the `.env` value. Secrets are stored encrypted.
Server address, token, sync on/off and interval, upload limit, extra allowed hosts, session / lockout / approval times,
discount limit, number of tills, backup folders and how many to keep.

M-PESA, eTIMS, and Barcode Lookup values (including **Callback base URL**) are in Django admin >
**Payment, tax, and lookup integration settings**
(set *Source* to Database).

Only these must stay in the `.env` file, because the program needs them before it can open the database:
`DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, `DB_*` (database address/password), `INTEGRATION_SETTINGS_KEY`,
`WINESPOS_BIND`, `WINESPOS_PORT`, `WINESPOS_MEDIA_ROOT`.

## A. Online server (once)

Use Ubuntu 22.04 or 24.04, with a **domain name** pointed at the server (a free one such as DuckDNS works). Use the domain,
not the IP, everywhere: then when the server's IP changes you only update the domain's DNS record.

```bash
sudo apt update && sudo apt install -y python3-venv python3-pip postgresql postgresql-client caddy git
sudo -u postgres psql -c "CREATE USER winespos WITH PASSWORD 'CHANGE-ME-DB';"
sudo -u postgres psql -c "CREATE DATABASE winespos OWNER winespos;"
sudo mkdir -p /opt/winespos /var/lib/winespos && sudo chown $USER /opt/winespos /var/lib/winespos
git clone https://github.com/nabiswa-cmd/POS.git /opt/winespos/app && cd /opt/winespos/app
python3 -m venv ../venv && ../venv/bin/pip install -r requirements.txt
../venv/bin/python -c "import secrets;print(secrets.token_urlsafe(48))"      # run 3 times: SECRET_KEY, SYNC_TOKEN, MPESA relay token
../venv/bin/python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"   # INTEGRATION_SETTINGS_KEY
```

Create `/opt/winespos/app/.env` (copy `.env.example`) with at least:

```
DJANGO_SECRET_KEY=<generated>
DJANGO_DEBUG=false
DJANGO_ALLOWED_HOSTS=pos.yourdomain.com
DB_ENGINE=postgres   # use the same DB_* names as .env.example: name winespos, user winespos, password, host 127.0.0.1
ONLINE_SERVER_HTTPS=true
SYNC_TOKEN=<generated>
SYNC_INBOX_DIR=/var/lib/winespos/sync_inbox
INTEGRATION_SETTINGS_KEY=<generated fernet key>
```

```bash
../venv/bin/python manage.py migrate
../venv/bin/python manage.py collectstatic --noinput
../venv/bin/python manage.py createsuperuser
```

Run it as a service, `/etc/systemd/system/winespos.service`:

```
[Service]
WorkingDirectory=/opt/winespos/app
EnvironmentFile=/opt/winespos/app/.env
ExecStart=/opt/winespos/venv/bin/waitress-serve --listen=127.0.0.1:8000 config.wsgi:application
Restart=always
User=YOUR_LINUX_USER
[Install]
WantedBy=multi-user.target
```

`sudo systemctl enable --now winespos`. HTTPS with Caddy, `/etc/caddy/Caddyfile`:

```
pos.yourdomain.com {
    request_body { max_size 600MB }
    reverse_proxy 127.0.0.1:8000
}
```

`sudo systemctl reload caddy`. Open ports 80 and 443 in the server firewall. Visit `https://pos.yourdomain.com/admin/`.

## B. Shop PC

1. Django admin > **Server settings**, set:
   `SYNC_SERVER_URL` = `https://pos.yourdomain.com`, `SYNC_TOKEN` = the same token as the server, `SYNC_ENABLED` = `true`.
   (If it refuses to save a secret, put `INTEGRATION_SETTINGS_KEY` in the shop `.env` once and restart.)
2. Manager screens > **Server sync** > **Test connection**, then **Sync now**. The online server's *Server sync* page
   lists the received backups. From then on it runs by itself (a timer inside the server program, every minute it checks if a
   send is due). Without the installed program you can also run `python manage.py sync_run --loop 300`.

## C. M-PESA callback

1. On the **online** server, Django admin > Payment, tax, and lookup integration settings: Source = Database, fill the Daraja keys,
   **Callback base URL** = `https://pos.yourdomain.com`, a long **Callback secret**, and a **Relay token**.
2. On the shop PC set the relay to the online server (`MPESA_RELAY_URL` = the same address, same relay token and callback secret).
3. On the online server run `python manage.py mpesa_register_urls` once. Manager screens > **Server sync** shows the exact three
   callback addresses (they must start with `https://`).
4. Cycle: customer pays > Safaricom calls the online server > the shop collects it in its next sync cycle (or press
   *Pull now* on the Reconciliation screen).

## When the server's IP or address changes

* Using a domain: update the domain's DNS record to the new IP. Nothing else.
* Not using a domain: on the shop PC change **Online server address** in Server settings; on the online server add the new
  address in **Extra allowed addresses**; change **Callback base URL** and run `mpesa_register_urls` again.

## Disaster recovery (shop PC lost)

New PC: install the program, then download the newest backup:
`curl -H "Authorization: Bearer <SYNC_TOKEN>" -o latest.zip https://pos.yourdomain.com/api/sync/latest/`
and restore it as described in `docs/BACKUP_AND_RESTORE.md`.

## Phone camera scanning

The sales screen shows a **Camera** button on phones. Browsers only allow the camera on **HTTPS** pages, so open the shop on an
https address (for example through the online server, or Caddy on the shop LAN). On a plain `http://192.168...` address the button
explains this and the cashier types the code instead. Chrome on Android uses its built-in reader; other browsers use a bundled one.
