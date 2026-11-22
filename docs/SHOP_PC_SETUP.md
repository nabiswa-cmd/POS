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

### Confirm the source database

The installer asks the configured online server for a fresh snapshot of its **active POS database**. This is the correct
source when `https://pos.devs.surf` is the live POS containing the users, products, stock, and sales you want. The
separate `/api/sync/latest/` endpoint contains the newest backup uploaded by a shop PC; it is not used by the installer
or the live-server restore button.

Before replacing local data, confirm the online address and token belong to the server whose live database is
authoritative. The restore replaces active local data only after validation; a pre-restore safety backup and the former
database/media are retained.

## Install or replace data on the Shop server PC

1. Connect the PC to the shop's trusted Wi-Fi or Ethernet network. Do not use a guest Wi-Fi network that blocks devices
   from reaching one another. Reserve the PC's LAN IP address in the router.
2. Run the WinesPOS installer as administrator and choose **Shop server PC**.
3. Enter the shop and manager details. Enter the reserved LAN IP if another till or device will connect locally.
4. On **Online server (optional)**, enter the online server URL (for example `https://pos.devs.surf`) and its sync token.
   The online server address is the server that has the sync API; it is not `shop.devs.surf`.
5. Finish setup and inspect `C:\ProgramData\WinesPOS\logs\install.log`. For online restoration, confirm it reports the
   live snapshot filename and successful restore. **Do not start trading until the expected products, stock, sales, and
   users have been checked.**
6. Open Manager > **Server sync** and use **Test connection**. Check **Sync now** and the latest success time. The
   online server needs to permit the secure sync connection and the shop must have internet for uploads.

When both the online server and this PC already contain data, online restore deliberately replaces the active local
database and media. It creates a pre-restore backup and retains the former database/media for recovery. To run that
operation manually with the shop server stopped, use `WinesPOSAdmin.exe manage sync_seed --force`.

## Manual restore from a transferred backup

Use these steps only when you have a verified backup zip transferred from the authoritative server. The installer and
Manager > Server sync button already request a live snapshot directly.

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
