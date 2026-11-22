"""First install: automatic tunnel enrolment, seeding an empty shop PC from the online server, installer settings."""
import tempfile
import importlib
from pathlib import Path
from unittest import mock
from types import SimpleNamespace

from django.core.management import call_command
from django.core.management.base import CommandError
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
    def test_unmigrated_empty_database_is_safe_to_seed(self):
        with mock.patch("core.sync.connection.introspection.table_names", return_value=[]):
            self.assertTrue(sync.local_is_empty())

    def test_partially_migrated_database_is_not_assumed_empty(self):
        with mock.patch("core.sync.connection.introspection.table_names", return_value=["django_migrations"]):
            self.assertFalse(sync.local_is_empty())

    @override_settings(SYNC_SERVER_URL="https://x.example", SYNC_TOKEN=TOKEN)
    def test_refuses_a_pc_that_already_has_data(self):
        User.objects.create_user(username="owner", password="x" * 12)
        with mock.patch("core.sync.download_live_server") as dl:
            with self.assertRaises(sync.SyncError):
                sync.seed_from_server()
            dl.assert_not_called()

    @override_settings(SYNC_SERVER_URL="https://x.example", SYNC_TOKEN=TOKEN)
    def test_force_replaces_existing_shop_data_from_the_server_backup(self):
        User.objects.create_user(username="owner", password="x" * 12)
        with mock.patch("core.sync.download_live_server", return_value=Path("b.zip")) as dl, \
                mock.patch("core.sync.backup.restore_backup",
                           return_value={"restored": "b.zip", "created_at": "today"}) as rb:
            call_command("sync_seed", "--force")
            dl.assert_called_once()
            rb.assert_called_once_with(Path("b.zip"), user=None)

    @override_settings(SYNC_SERVER_URL="https://x.example", SYNC_TOKEN=TOKEN)
    def test_migrated_but_empty_pc_downloads_and_restores(self):
        with mock.patch("core.sync.download_live_server", return_value=Path("b.zip")) as dl, \
                mock.patch("core.sync.backup.restore_backup", return_value={"restored": "b.zip"}) as rb:
            self.assertEqual(sync.seed_from_server(), {"restored": "b.zip"})
            dl.assert_called_once()
            rb.assert_called_once()

    @override_settings(SYNC_SERVER_URL="", SYNC_TOKEN="")
    def test_needs_address_and_token(self):
        with self.assertRaises(sync.SyncError):
            sync.download_live_server(tempfile.gettempdir())

    def test_seed_failure_is_a_command_error_not_a_successful_skip(self):
        with mock.patch("core.sync.seed_from_server", side_effect=sync.SyncError("online backup unavailable")):
            with self.assertRaisesMessage(CommandError, "online backup unavailable"):
                call_command("sync_seed")


class ReceiptPrinterMigrationRepairTests(TestCase):
    def test_repairs_a_column_missing_from_a_recorded_migration(self):
        migration = importlib.import_module(
            "core.migrations.0011_repair_receiptsettings_printer_name")
        model = mock.Mock()
        model._meta.db_table = "core_receiptsettings"
        model._meta.get_field.return_value = "printer_name_field"
        apps = mock.Mock()
        apps.get_model.return_value = model
        cursor = mock.Mock()
        cursor_context = mock.MagicMock()
        cursor_context.__enter__.return_value = cursor
        connection = mock.Mock()
        connection.cursor.return_value = cursor_context
        connection.introspection.table_names.return_value = ["core_receiptsettings"]
        connection.introspection.get_table_description.return_value = [SimpleNamespace(name="id")]
        schema_editor = mock.Mock(connection=connection)

        migration.repair_printer_name_column(apps, schema_editor)

        schema_editor.add_field.assert_called_once_with(model, "printer_name_field")

    def test_does_not_add_an_existing_printer_column_twice(self):
        migration = importlib.import_module(
            "core.migrations.0011_repair_receiptsettings_printer_name")
        model = mock.Mock()
        model._meta.db_table = "core_receiptsettings"
        apps = mock.Mock()
        apps.get_model.return_value = model
        cursor = mock.Mock()
        cursor_context = mock.MagicMock()
        cursor_context.__enter__.return_value = cursor
        connection = mock.Mock()
        connection.cursor.return_value = cursor_context
        connection.introspection.table_names.return_value = ["core_receiptsettings"]
        connection.introspection.get_table_description.return_value = [SimpleNamespace(name="printer_name")]
        schema_editor = mock.Mock(connection=connection)

        migration.repair_printer_name_column(apps, schema_editor)

        schema_editor.add_field.assert_not_called()


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

    def test_both_shop_and_pos_hosts(self):
        v = configure.build_values("server", root="/x", remote_host="shop.devs.surf,pos.devs.surf")
        hosts = v["DJANGO_ALLOWED_HOSTS"].split(",")
        self.assertTrue({"shop.devs.surf", "pos.devs.surf"} <= set(hosts))
        self.assertEqual(v["DJANGO_CSRF_TRUSTED_ORIGINS"], "https://shop.devs.surf,https://pos.devs.surf")
