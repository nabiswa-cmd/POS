"""First install: automatic tunnel enrolment, seeding an empty shop PC from the online server, installer settings."""
import tempfile
from pathlib import Path
from unittest import mock

from django.test import Client, TestCase, override_settings

from accounts.models import User
from core import sync
from desktop import configure

KEY = "ssh-ed25519 " + "A" * 43 + " shop-pc"
TOKEN = "t" * 40


class TunnelEnrolmentTests(TestCase):
    def test_off_by_default(self):
        with override_settings(SYNC_TUNNEL_AUTHORIZED_KEYS=""):
            with self.assertRaises(sync.SyncError):
                sync.enrol_tunnel_key(KEY)

    def test_adds_one_restricted_line_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "ak"
            with override_settings(SYNC_TUNNEL_AUTHORIZED_KEYS=str(f), SYNC_TUNNEL_PORT=8021):
                self.assertEqual(sync.enrol_tunnel_key(KEY), "added")
                self.assertEqual(sync.enrol_tunnel_key(KEY), "already there")
            lines = f.read_text().splitlines()
            self.assertEqual(len(lines), 1)
            self.assertTrue(lines[0].startswith('restrict,port-forwarding,permitlisten="127.0.0.1:8021" ssh-ed25519 '))

    def test_rejects_options_and_other_key_types(self):
        with tempfile.TemporaryDirectory() as d, override_settings(SYNC_TUNNEL_AUTHORIZED_KEYS=str(Path(d) / "ak")):
            for bad in ('command="rm -rf /" ' + KEY, "ssh-rsa " + "A" * 100, KEY + "\nssh-ed25519 " + "B" * 43, ""):
                with self.assertRaises(sync.SyncError, msg=bad):
                    sync.enrol_tunnel_key(bad)

    def test_endpoint_needs_the_token(self):
        with tempfile.TemporaryDirectory() as d, override_settings(SYNC_TOKEN=TOKEN, SYNC_TUNNEL_AUTHORIZED_KEYS=str(Path(d) / "ak")):
            c = Client()
            r = c.post("/api/sync/tunnel-key/", {"public_key": KEY}, content_type="application/json")
            self.assertEqual(r.status_code, 404)
            r = c.post("/api/sync/tunnel-key/", {"public_key": KEY}, content_type="application/json",
                       HTTP_AUTHORIZATION=f"Bearer {TOKEN}")
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["port"], 8021)


class SeedTests(TestCase):
    @override_settings(SYNC_SERVER_URL="https://x.example", SYNC_TOKEN=TOKEN)
    def test_refuses_a_pc_that_already_has_data(self):
        User.objects.create_user(username="owner", password="x" * 12)
        with mock.patch("core.sync.download_latest") as dl:
            with self.assertRaises(sync.SyncError):
                sync.seed_from_server()
            dl.assert_not_called()

    @override_settings(SYNC_SERVER_URL="https://x.example", SYNC_TOKEN=TOKEN)
    def test_empty_pc_downloads_and_restores(self):
        with mock.patch("core.sync.download_latest", return_value=Path("b.zip")) as dl, \
                mock.patch("core.sync.backup.restore_backup", return_value={"restored": "b.zip"}) as rb:
            self.assertEqual(sync.seed_from_server(), {"restored": "b.zip"})
            dl.assert_called_once()
            rb.assert_called_once()

    @override_settings(SYNC_SERVER_URL="", SYNC_TOKEN="")
    def test_needs_address_and_token(self):
        with self.assertRaises(sync.SyncError):
            sync.download_latest(tempfile.gettempdir())


class InstallerSettingsTests(TestCase):
    def test_sync_values_written_for_a_server_pc(self):
        v = configure.build_values("server", root="/x", sync_url="https://pos.devs.surf/", sync_token=TOKEN)
        self.assertEqual((v["SYNC_ENABLED"], v["SYNC_SERVER_URL"], v["SYNC_TOKEN"]), ("true", "https://pos.devs.surf", TOKEN))

    def test_no_sync_values_without_input_and_short_token_refused(self):
        self.assertNotIn("SYNC_TOKEN", configure.build_values("server", root="/x"))
        with self.assertRaises(configure.ConfigError):
            configure.build_values("server", root="/x", sync_url="https://a.b", sync_token="short")

    def test_remote_host_is_allowed_and_trusted_for_csrf(self):
        v = configure.build_values("server", root="/x", remote_host="shop.devs.surf")
        self.assertIn("shop.devs.surf", v["DJANGO_ALLOWED_HOSTS"].split(","))
        self.assertEqual(v["DJANGO_CSRF_TRUSTED_ORIGINS"], "https://shop.devs.surf")
        for bad in ("https://shop.devs.surf", "shop", "a b.c"):
            with self.assertRaises(configure.ConfigError, msg=bad):
                configure.build_values("server", root="/x", remote_host=bad)
