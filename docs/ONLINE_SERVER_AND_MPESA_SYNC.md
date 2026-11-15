# Online server and M-PESA relay

The shop database remains local so the tills can keep selling while the internet is down. The online server receives
Safaricom callbacks; the shop periodically pulls confirmed payment records from it. This relay synchronizes M-PESA
payments only. It does **not** replicate sales, stock, staff, or the whole shop database to the online server.

## Configure the online server

1. Give the VPS a stable DNS name and HTTPS certificate. Safaricom callback URLs and relay requests must use HTTPS;
   a raw IP address is not a suitable long-term URL unless it has a valid certificate for that IP.
2. In the VPS environment file, set `MPESA_CALLBACK_BASE_URL`, a unique random `MPESA_CALLBACK_SECRET` (at least
   32 characters), and `MPESA_RELAY_TOKEN`. Keep the token and callback secret private.
3. Run `python manage.py mpesa_register_urls` on the VPS to register the callback URLs with Safaricom.
4. Confirm the VPS firewall and HTTPS reverse proxy permit Safaricom's callback requests.

## Configure the shop in Django admin

1. Keep `INTEGRATION_SETTINGS_KEY` in the shop's protected environment file. Back it up securely; without the same
   key, encrypted integration settings cannot be read after a restart or restore.
2. Open **Admin → Core → Payment, tax, and lookup integration settings** as a manager.
3. Set **M-PESA source** to **Database**. Enter the M-PESA relay's HTTPS base URL in **M-PESA relay URL**, and set
   the same `MPESA_RELAY_TOKEN` and `MPESA_CALLBACK_SECRET` used by the VPS. Save.
4. To change the server address later, update **M-PESA relay URL** and save. No application restart is needed.
   Prefer changing DNS while keeping the same HTTPS name; if the host name changes, update the URL and TLS setup.
5. Use **Manager → Reconciliation → Pull now** to check connectivity and confirm that new payments arrive.

The admin-managed integration values are encrypted in the local database. Django's signing key, database connection,
allowed hosts, and other process-start settings remain in the protected environment file: changing those at runtime
through the database would not safely reconfigure Django and could expose or lock out the shop.

## Run automatic pulls on the shop PC

The manager's **Pull now** action is manual. For automatic synchronization, run the included command on the shop PC:

```powershell
python manage.py mpesa_pull --loop 60
```

This pulls approximately once a minute and retries transient connection failures. Run it under the same Windows account
and installation environment as the POS server. For unattended operation, create a Windows Task Scheduler task that
starts at system startup, runs whether or not the user is logged in, and restarts the task if it exits. Use the
installation's Python executable and set **Start in** to the POS application directory. Keep the task running; do not
start multiple copies.

Nightly local database backups are configured separately by the installer. `WINESPOS_BACKUP_COPY_DIR` can place a
second backup on a reachable network share, but that is a file copy, not database replication. Plan and test a separate
secure backup/restore process if a full online copy of sales and inventory is required.
