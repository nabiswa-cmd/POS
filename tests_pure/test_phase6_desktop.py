"""Phase 6 desktop configuration, no Django or Qt needed."""
import contextlib
import io
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from desktop import configure, main as desktop_main, paths


class EnvEdit(contextlib.ExitStack):
    """Point the data folder and settings file at a temp folder for one test."""
    def __init__(self, root):
        super().__init__()
        self.root = root

    def __enter__(self):
        super().__enter__()
        self.enter_context(unittest.mock.patch.dict(os.environ, {"WINESPOS_DATA_DIR": str(self.root)}))
        os.environ.pop("WINESPOS_ENV_FILE", None)
        return self


import unittest.mock  # noqa: E402  (after the class so the module docstring stays first)


class BuildValuesTests(unittest.TestCase):
    def test_server_settings_are_safe_by_default(self):
        v = configure.build_values("server", root=Path("/x"))
        self.assertEqual((v["DJANGO_DEBUG"], v["DB_ENGINE"], v["WINESPOS_BIND"]), ("false", "postgres", "127.0.0.1"))
        self.assertGreaterEqual(len(v["DJANGO_SECRET_KEY"]), 48)
        self.assertNotEqual(v["DJANGO_SECRET_KEY"], v["DESKTOP_SHELL_SECRET"])
        self.assertNotEqual(v["DJANGO_SECRET_KEY"], v["INTEGRATION_SETTINGS_KEY"])
        self.assertEqual(v["DJANGO_ALLOWED_HOSTS"], "localhost,127.0.0.1")

    def test_a_second_till_opens_the_server_to_the_shop_network_only_when_asked(self):
        v = configure.build_values("server", root=Path("/x"), lan_host="192.168.1.10")
        self.assertEqual(v["WINESPOS_BIND"], "0.0.0.0")
        self.assertIn("192.168.1.10", v["DJANGO_ALLOWED_HOSTS"])

    def test_secrets_are_kept_when_run_again(self):
        first = configure.build_values("server", root=Path("/x"))
        again = configure.build_values("server", root=Path("/x"), existing=first)
        for key in ("DJANGO_SECRET_KEY", "DB_PASSWORD", "DESKTOP_SHELL_SECRET", "INTEGRATION_SETTINGS_KEY"):
            self.assertEqual(first[key], again[key])
        fresh = configure.build_values("server", root=Path("/x"))
        self.assertNotEqual(first["DJANGO_SECRET_KEY"], fresh["DJANGO_SECRET_KEY"])

    def test_till_pc_only_needs_the_server_address(self):
        self.assertEqual(configure.build_values("till", root=Path("/x"), server_url="http://192.168.1.10:8000/"),
                         {"WINESPOS_SERVER_URL": "http://192.168.1.10:8000"})
        for bad in ("", "192.168.1.10", "ftp://x"):
            with self.assertRaises(configure.ConfigError):
                configure.build_values("till", root=Path("/x"), server_url=bad)
        with self.assertRaises(configure.ConfigError):
            configure.build_values("nonsense", root=Path("/x"))

    def test_values_with_line_breaks_are_refused(self):
        with self.assertRaises(configure.ConfigError):
            configure.render_env({"A": "x\nB=evil"})

    def test_render_then_parse_round_trips(self):
        v = configure.build_values("server", root=Path("/x"))
        self.assertEqual(paths.parse_env(configure.render_env(v)), v)


class MainConfigureTests(unittest.TestCase):
    def run_configure(self, root, *args):
        with EnvEdit(root), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = configure.main(list(args))
            text = paths.env_file_path().read_text(encoding="utf-8") if paths.env_file_path().exists() else ""
        return code, paths.parse_env(text)

    def test_first_run_creates_the_settings_file_and_folders_and_rerun_keeps_secrets_and_hand_edits(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, first = self.run_configure(tmp, "--role", "server")
            self.assertEqual(code, 0)
            self.assertTrue((Path(tmp) / "data" / "backups").is_dir() and (Path(tmp) / "data" / "media").is_dir())
            client_env = Path(tmp) / "config" / "winespos-client.env"
            client_values = paths.parse_env(client_env.read_text(encoding="utf-8"))
            self.assertEqual(client_values, {"WINESPOS_SERVER_URL": "http://127.0.0.1:8000"})
            self.assertNotIn("DB_PASSWORD", client_values)
            if os.name != "nt":
                mode = stat.S_IMODE((Path(tmp) / "config" / "winespos.env").stat().st_mode)
                self.assertEqual(mode, 0o600)                       # secrets are not world-readable
            env_path = Path(tmp) / "config" / "winespos.env"
            env_path.write_text(env_path.read_text() + "MPESA_ENV=live\nWINESPOS_BACKUP_COPY_DIR=E:\\\\Backups\n")
            code, second = self.run_configure(tmp, "--role", "server", "--lan-host", "192.168.1.10")
            self.assertEqual(code, 0)
            self.assertEqual(second["DJANGO_SECRET_KEY"], first["DJANGO_SECRET_KEY"])
            self.assertEqual(second["DB_PASSWORD"], first["DB_PASSWORD"])
            self.assertEqual(second["MPESA_ENV"], "live")             # nothing the shop added is lost
            self.assertIn("WINESPOS_BACKUP_COPY_DIR", second)
            self.assertEqual(second["WINESPOS_BIND"], "0.0.0.0")

    def test_till_pc_gets_only_the_server_address(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, v = self.run_configure(tmp, "--role", "till", "--server-url", "http://192.168.1.10:8000")
            self.assertEqual((code, v), (0, {"WINESPOS_SERVER_URL": "http://192.168.1.10:8000"}))

    def test_bad_till_address_fails_with_a_message_not_a_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _ = self.run_configure(tmp, "--role", "till", "--server-url", "nonsense")
            self.assertEqual(code, 2)

    def test_provisioning_needs_the_admin_password(self):
        with tempfile.TemporaryDirectory() as tmp, unittest.mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("WINESPOS_PG_ADMIN_PASSWORD", None)
            code, _ = self.run_configure(tmp, "--role", "server", "--provision-db")
            self.assertEqual(code, 2)

    def test_restore_provisioning_refuses_an_existing_database_before_changing_roles(self):
        cursor = unittest.mock.Mock()
        cursor.fetchone.return_value = (1,)
        connection = unittest.mock.MagicMock()
        connection.__enter__.return_value = connection
        connection.execute.return_value = cursor
        psycopg = SimpleNamespace(connect=unittest.mock.Mock(return_value=connection), sql=SimpleNamespace())
        with unittest.mock.patch.dict(sys.modules, {"psycopg": psycopg}):
            with self.assertRaisesRegex(configure.ConfigError, "already exists"):
                configure.provision_postgres(
                    configure.build_values("server", root=Path("/x")),
                    "admin-password",
                    require_new_database=True,
                )
        connection.execute.assert_called_once()


class PathsAndMainTests(unittest.TestCase):
    def test_env_file_is_loaded_but_a_real_environment_variable_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "a.env"
            f.write_text("# c\nWP_TEST_A=from-file\nWP_TEST_B='quoted'\n\nbroken line\n")
            with unittest.mock.patch.dict(os.environ, {"WP_TEST_A": "from-real-env"}):
                os.environ.pop("WP_TEST_B", None)
                os.environ.pop("WINESPOS_ENV_FILE", None)
                self.assertTrue(paths.load_env_file(f))
                self.assertEqual((os.environ["WP_TEST_A"], os.environ["WP_TEST_B"]), ("from-real-env", "quoted"))
            os.environ.pop("WP_TEST_B", None)
            os.environ.pop("WINESPOS_ENV_FILE", None)
        self.assertFalse(paths.load_env_file(Path("/definitely/not/here.env")))

    def test_client_env_loader_ignores_secret_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "client.env"
            path.write_text("WINESPOS_SERVER_URL=http://127.0.0.1:8000\nDB_PASSWORD=not-for-clients\n")
            os.environ.pop("WINESPOS_SERVER_URL", None)
            os.environ.pop("DB_PASSWORD", None)
            self.assertTrue(paths.load_client_env_file(path))
            self.assertEqual(os.environ["WINESPOS_SERVER_URL"], "http://127.0.0.1:8000")
            self.assertNotIn("DB_PASSWORD", os.environ)
            os.environ.pop("WINESPOS_SERVER_URL", None)

    def test_gui_commands_load_only_the_public_client_settings(self):
        with unittest.mock.patch("desktop.paths.load_client_env_file") as client_loader, \
                unittest.mock.patch("desktop.paths.load_env_file") as secret_loader, \
                unittest.mock.patch("desktop.launcher.main", return_value=0):
            self.assertEqual(desktop_main.main(["manager"]), 0)
        client_loader.assert_called_once_with()
        secret_loader.assert_not_called()

    def test_help_and_unknown_commands(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(desktop_main.main([]), 0)
        self.assertIn("pos | manager | receiving", out.getvalue())
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(desktop_main.main(["frobnicate"]), 2)
        self.assertIn("Unknown command", err.getvalue())


if __name__ == "__main__":
    unittest.main()
