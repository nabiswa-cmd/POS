# Set up the shop PC and copy the server data

This guide sets up a Windows **Shop server PC** with a local database, connects it to the shop network, and explains how
to use `shop.devs.surf`.

## Before you start

- Confirm the online server is running the same or a compatible WinesPOS version and uses **PostgreSQL**. The Windows
  installer creates a PostgreSQL database; the backup must also contain PostgreSQL data.
- Make a fresh, verified backup of the online data. A WinesPOS backup includes the whole database and product images.
- Have the online server's public address (for example `https://pos.devs.surf`) and its private `SYNC_TOKEN` ready.
  Do not put the token in a shared document or send it in an ordinary message.
- Decide which machine will be the **Shop server PC**. It stores the local database and should stay on and connected to
  the shop router. Use a reserved/fixed LAN address, for example `192.168.1.10`.

### Know which server backup you have

The installer's automatic first copy downloads the newest backup **received by the online server**. It does not dump the
online server's live database on demand.

- If the online server already receives backups from the shop, check its **Server sync** page and confirm the newest
  backup contains the expected latest sales, products, staff, and stock.
- If the online server is the live POS and has no received backup, create a backup on that server with
  `python manage.py backup_db`, then securely copy the resulting zip to the shop PC. Do not rely on automatic seeding
  in this case; use the manual restore steps below.

## Install and automatically seed a new Shop server PC

1. Connect the PC to the shop's trusted Wi-Fi or Ethernet network. Do not use a guest Wi-Fi network that blocks devices
   from reaching one another. Reserve the PC's LAN IP address in the router.
2. Run the WinesPOS installer as administrator and choose **Shop server PC**.
3. Enter the shop and manager details. Enter the reserved LAN IP if another till or device will connect locally.
4. On **Online server (optional)**, enter the online server URL (for example `https://pos.devs.surf`) and its sync token.
   The online server address is the server that has the sync API; it is not `shop.devs.surf`.
5. Finish setup and inspect `C:\ProgramData\WinesPOS\logs\install.log`. For automatic seeding, look for a message that
   says this PC now starts from the online copy. The installer may set up a fresh shop if it cannot find or restore a
   compatible backup. **Do not start trading until the expected products, stock, sales, and users have been checked.**
6. Open Manager > **Server sync** and use **Test connection**. Check **Sync now** and the latest success time. The
   online server needs to permit the secure sync connection and the shop must have internet for uploads.

The installer only seeds an empty database. It will not overwrite a shop database that already contains real data.
Do not use `sync_seed --force` unless you deliberately want to replace local data; first make and verify a local backup.

## Restore a backup of the live online database

Use these steps when the online server's current database is the source and its **Server sync** inbox does not contain
the current data. First install the Shop server PC as above (the backup can be restored after setup).

1. On the online server, run `python manage.py backup_db` from the application directory. Note the exact zip path printed.
2. Transfer that zip to the shop PC using an approved secure method, such as SSH/SCP or an encrypted USB drive. Do not
   email the database backup or leave it in a public/shared folder.
3. On the shop PC, open PowerShell as administrator and stop the local server:

   ```powershell
   Stop-ScheduledTask "WinesPOS Server"
   ```

4. Restore the copied backup. Replace `<backup-file.zip>` with its actual path:

   ```powershell
   & "C:\Program Files\WinesPOS\WinesPOSAdmin.exe" manage restore_db "C:\path\to\<backup-file.zip>" --yes
   ```

   Restore checks the backup and saves a safety backup of the PC's current database before replacing it. The restored
   server will have the users and passwords from the online database.
5. Start the server and verify the data in Manager:

   ```powershell
   Start-ScheduledTask "WinesPOS Server"
   ```

   Check several known products, stock quantities, recent sales, and product images before 211211using the tills. If restore
   reports that the database engine or application version is incompatible, stop and resolve that before trading.

## Connect tills over shop Wi-Fi

The Shop server PC and each Till PC must be on the same trusted shop router/network. Wi-Fi is fine; a direct cable
between PCs is not needed.

- When installing the Shop server PC, enter its reserved LAN IP in the installer so the shop-only firewall rule is
  created.1
- Install the other PC as **Till PC** and enter the local address, for example `http://192.168.1.10:8000`.
- On a till, open the POS and confirm it can sign in. If it cannot connect, check that both PCs are on the same
  non-guest Wi-Fi/LAN, the server PC is on, its IP has not changed, and the **WinesPOS Server** task is running.
212
For tills inside the shop, prefer this local address. Sales then continue to work if the shop's internet connection is
down.

## Use `shop.devs.surf`

`shop.devs.surf` is a public/remote access name for the shop server, not the shop Wi-Fi network itself. For it to work,
the online server must be configured for the secure tunnel and reverse proxy, and public DNS for `shop.devs.surf` must
point to that online server. The online server also needs a valid HTTPS certificate for `shop.devs.surf`. The automatic
tunnel setup requires the online server's tunnel-key setting and SSH account to be configured as described in
[ONLINE_SERVER_SETUP.md](./ONLINE_SERVER_SETUP.md).

When the installer is given `https://pos.devs.surf`, it derives `shop.devs.surf` as the remote shop hostname. Confirm the
installer log says the tunnel was registered and started, and test `https://shop.devs.surf/health/` from a device outside
the shop network. A DNS record alone is not enough: the tunnel, web-server proxy, and TLS certificate must all be in
place.

On shop Wi-Fi, continue to use the local `http://<shop-server-LAN-IP>:8000` address for tills. Whether
`https://shop.devs.surf` also works from inside the shop depends on the router's DNS/NAT loopback behavior. If you need
the domain to work both inside and outside, configure split DNS or NAT loopback on the router and validate it on the
actual shop network; this is separate from the POS installer.

## Install the Android POS app

The cashier screen is an installable PWA. Open `https://shop.devs.surf/app/pos/` in Chrome on each Android tablet while
online. Sign in and open the till once so the device saves that till's local connection choices, then use **Install POS
on this device** (or Chrome's **Install app / Add to Home screen** menu). A valid HTTPS certificate and the working
tunnel are required for installation.

Managers can open **Tills & sales > Configure local tablet connections** and enter one private shop-server URL per
line for each till (for example `http://192.168.1.10:8000`). Reserve those addresses in the shop router. The browser
does not silently probe or switch to a private address; if the public connection fails, use **Local connection
options**, choose the right till/address, and sign in again. This reaches the same shop database, but the browser
session and unfinished cart do not transfer. The PWA does not cache sales or payment API data for offline selling.

## Related guides

- [Windows installation](./WINDOWS_INSTALL.md)
- [Backup and restore](./BACKUP_AND_RESTORE.md)
- [Online server setup](./ONLINE_SERVER_SETUP.md)
