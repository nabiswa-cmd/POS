"""Runtime (database) settings and the shop <-> online server sync."""
import io
import shutil
import tempfile
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core.exceptions import ValidationError
from django.test import Client, TransactionTestCase, override_settings
from django.core.management.base import CommandError

from accounting.models import ExpenseCategory
from accounts.models import User
from core import backup, runtime, sync, windows_restore
from core.management.commands.restore_server_backup import Command as RestoreServerBackupCommand
from core.models import RuntimeSetting, SyncReceipt, SyncState
from tills.models import CashierShift

KEY = "test-only-external-encryption-key"


def put(name, value):
    d = runtime.BY_NAME[name]
    RuntimeSetting.objects.update_or_create(name=name, defaults={"value": runtime.encode(d, value)})
    runtime.apply(force=True)


@override_settings(INTEGRATION_SETTINGS_KEY=KEY)
class RuntimeSettingsTests(TransactionTestCase):
    def tearDown(self):
        RuntimeSetting.objects.all().delete()
        runtime.apply(force=True)

    def test_database_value_overrides_and_clearing_restores_the_env_value(self):
        before = settings.LOCKOUT_MINUTES
        put("LOCKOUT_MINUTES", "17")
        self.assertEqual(settings.LOCKOUT_MINUTES, 17)
        put("LOCKOUT_MINUTES", "")
        self.assertEqual(settings.LOCKOUT_MINUTES, before)

    def test_idle_minutes_becomes_cookie_age_and_hosts_are_added(self):
        put("SESSION_IDLE_MINUTES", "12")
        self.assertEqual(settings.SESSION_COOKIE_AGE, 720)
        put("EXTRA_ALLOWED_HOSTS", "pos.example.com, 10.1.2.3")
        self.assertIn("pos.example.com", settings.ALLOWED_HOSTS)
        self.assertIn("localhost", settings.ALLOWED_HOSTS)
        put("EXTRA_ALLOWED_HOSTS", "")
        self.assertNotIn("pos.example.com", settings.ALLOWED_HOSTS)
        put("SESSION_IDLE_MINUTES", "")

    def test_secret_is_stored_encrypted(self):
        put("SYNC_TOKEN", "super-long-secret-token")
        row = RuntimeSetting.objects.get(name="SYNC_TOKEN")
        self.assertTrue(row.value.startswith("enc:"))
        self.assertNotIn("super-long", row.value)
        self.assertEqual(settings.SYNC_TOKEN, "super-long-secret-token")

    def test_bad_values_are_refused_and_a_damaged_row_is_ignored(self):
        with self.assertRaises(ValidationError):
            runtime.parse(runtime.BY_NAME["SYNC_INTERVAL_MINUTES"], "1")
        with self.assertRaises(ValidationError):
            runtime.parse(runtime.BY_NAME["SYNC_SERVER_URL"], "ftp://x")
        RuntimeSetting.objects.create(name="MAX_TILLS", value="abc")
        before = settings.MAX_TILLS
        runtime.apply(force=True)
        self.assertEqual(settings.MAX_TILLS, before)

    def test_admin_form_saves_validates_and_applies(self):
        boss = User.objects.create(username="boss", is_manager=True, is_staff=True, is_superuser=True)
        c = Client()
        c.force_login(boss)
        page = c.get("/admin/core/runtimesetting/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Online server address")
        row = RuntimeSetting.objects.get(name="SYNC_SERVER_URL")
        r = c.post(f"/admin/core/runtimesetting/{row.pk}/change/", {"value": "http://41.90.1.2:8000/"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(settings.SYNC_SERVER_URL, "http://41.90.1.2:8000")
        r = c.post(f"/admin/core/runtimesetting/{row.pk}/change/", {"value": "nonsense"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "must start with http")


class SyncCase(TransactionTestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        ctx = override_settings(BACKUP_DIR=self.tmp / "backups", BACKUP_COPY_DIR="", MEDIA_ROOT=self.tmp / "media",
                                SYNC_INBOX_DIR=str(self.tmp / "inbox"), SYNC_TOKEN="tok-" + "x" * 40, SYNC_MAX_UPLOAD_MB=50,
                                SYNC_INBOX_KEEP=2, SYNC_ENABLED=True, SYNC_SERVER_URL="https://online.test", SYNC_PULL_MPESA=False)
        ctx.enable()
        self.addCleanup(ctx.disable)
        ExpenseCategory.objects.create(name="x")
        self.auth = {"HTTP_AUTHORIZATION": "Bearer " + settings.SYNC_TOKEN}


class OnlineServerTests(SyncCase):
    def upload(self, data, name, sha=None, token=True):
        import hashlib
        extra = dict(self.auth) if token else {}
        extra["HTTP_X_CONTENT_SHA256"] = sha or hashlib.sha256(data).hexdigest()
        return Client().generic("POST", f"/api/sync/upload/?name={name}", data, content_type="application/zip", **extra)

    def make_backup(self):
        r = backup.create_backup(note="t")
        return r["path"].name, r["path"].read_bytes()

    def test_endpoints_do_not_exist_without_the_token(self):
        for url in ("/api/sync/ping/", "/api/sync/status/", "/api/sync/latest/"):
            self.assertEqual(Client().get(url).status_code, 404)
            self.assertEqual(Client().get(url, HTTP_AUTHORIZATION="Bearer wrong").status_code, 404)
        self.assertEqual(Client().get("/api/sync/ping/", **self.auth).status_code, 200)
        with override_settings(SYNC_TOKEN=""):
            self.assertEqual(Client().get("/api/sync/ping/", HTTP_AUTHORIZATION="Bearer ").status_code, 404)

    def test_valid_backup_is_stored_checked_and_listed(self):
        name, data = self.make_backup()
        r = self.upload(data, name)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue((self.tmp / "inbox" / name).exists())
        self.assertEqual(SyncReceipt.objects.get().name, name)
        status = Client().get("/api/sync/status/", **self.auth).json()
        self.assertEqual(status["latest"]["name"], name)
        got = b"".join(Client().get("/api/sync/latest/", **self.auth).streaming_content)
        self.assertEqual(got, data)

    def test_bad_uploads_are_refused(self):
        name, data = self.make_backup()
        self.assertEqual(self.upload(data, name, token=False).status_code, 404)
        self.assertEqual(self.upload(data, name, sha="0" * 64).status_code, 400)          # wrong checksum
        self.assertEqual(self.upload(b"not a zip at all", name).status_code, 400)          # not a backup
        self.assertEqual(self.upload(data, "../evil.zip").status_code, 400)                # unsafe name
        self.assertEqual(self.upload(data, "winespos-20200101-000000-prerestore.zip").status_code, 400)
        self.assertEqual(SyncReceipt.objects.count(), 0)
        self.assertEqual([p.name for p in (self.tmp / "inbox").iterdir()], [])             # no partial files left

    def test_only_the_newest_few_are_kept(self):
        for stamp in ("20260101-010101", "20260102-010101", "20260103-010101"):
            data = self.make_backup()[1]
            self.assertEqual(self.upload(data, f"winespos-{stamp}.zip").status_code, 200)
        kept = sorted(p.name for p in (self.tmp / "inbox").iterdir())
        self.assertEqual(kept, ["winespos-20260102-010101.zip", "winespos-20260103-010101.zip"])


class ShopCycleTests(SyncCase):
    def fake_upload(self, calls):
        def fn(path):
            calls.append(Path(path).name)
            return {"ok": True}
        return fn

    def test_cycle_makes_a_backup_sends_it_and_does_not_resend(self):
        calls = []
        with mock.patch.object(sync, "_upload", self.fake_upload(calls)):
            ok, text = sync.run_cycle()
            self.assertTrue(ok, text)
            self.assertEqual(len(calls), 1)
            ok, text = sync.run_cycle()
            self.assertTrue(ok)
            self.assertEqual(len(calls), 1)                         # nothing new, nothing sent
            self.assertIn("up to date", text)
            sync.run_cycle(force=True)
            self.assertEqual(len(calls), 2)
        state = SyncState.load()
        self.assertIsNotNone(state.last_success_at)
        self.assertEqual(state.last_error, "")

    def test_offline_is_recorded_and_retried_later(self):
        def down(path):
            raise sync.SyncError("Cannot reach the online server (no internet, or the server address changed).")
        with mock.patch.object(sync, "_upload", down):
            ok, text = sync.run_cycle()
        self.assertFalse(ok)
        self.assertIn("Cannot reach", SyncState.load().last_error)
        calls = []
        with mock.patch.object(sync, "_upload", self.fake_upload(calls)):
            ok, _ = sync.run_cycle()
        self.assertTrue(ok)
        self.assertEqual(len(calls), 1)                             # the backup waited for us and went now

    def test_switched_off_does_nothing(self):
        with override_settings(SYNC_ENABLED=False), mock.patch.object(sync, "_upload") as up:
            ok, text = sync.run_cycle()
        self.assertFalse(ok)
        up.assert_not_called()

    def test_screen_needs_the_settings_permission_and_shows_state(self):
        boss = User.objects.create(username="boss", is_manager=True)
        clerk = User.objects.create(username="clerk")
        c1, c2 = Client(), Client()
        c1.force_login(boss)
        c2.force_login(clerk)
        self.assertEqual(c2.get("/manage/sync/").status_code, 403)
        page = c1.get("/manage/sync/")
        self.assertContains(page, "Server sync")
        self.assertContains(page, "overflow-wrap:anywhere")
        with mock.patch.object(sync, "_upload", lambda p: {"ok": True}):
            c1.post("/manage/sync/", {"action": "now"})
        page = c1.get("/manage/sync/")
        self.assertContains(page, "last success")
        self.assertNotContains(page, "last success: <b>never")

    def test_manager_can_download_a_verified_server_backup_but_not_restore_live_data(self):
        boss = User.objects.create(username="boss", is_manager=True)
        client = Client()
        client.force_login(boss)
        path = backup.create_backup(note="server copy")["path"]
        with mock.patch.object(sync, "download_latest", return_value=path):
            response = client.post("/manage/sync/", {"action": "download_server"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Disposition"], f'attachment; filename="{path.name}"')
        self.assertEqual(b"".join(response.streaming_content), path.read_bytes())
        self.assertContains(client.get("/manage/sync/"), "Download server database backup")

    def test_manager_can_queue_verified_server_restore(self):
        boss = User.objects.create(username="boss", is_manager=True)
        client = Client()
        client.force_login(boss)
        path = backup.create_backup(note="server copy")["path"]
        folder = self.tmp / "backups" / "server-downloads"
        folder.mkdir()
        staged = folder / path.name
        shutil.copyfile(path, staged)
        with mock.patch.object(sync, "download_latest", return_value=staged), \
                mock.patch.object(windows_restore, "check_ready"), \
                mock.patch.object(windows_restore, "launch_restore") as launch:
            response = client.post("/manage/sync/", {"action": "restore_server"})
        self.assertEqual(response.status_code, 302)
        launch.assert_called_once_with(path.name)
        self.assertTrue(staged.is_file())

    def test_manager_cannot_queue_restore_with_open_cashier_shift(self):
        boss = User.objects.create(username="boss", is_manager=True)
        client = Client()
        client.force_login(boss)
        with mock.patch.object(CashierShift.objects, "filter") as active_shifts, \
                mock.patch.object(sync, "download_latest") as download:
            active_shifts.return_value.exists.return_value = True
            response = client.post("/manage/sync/", {"action": "restore_server"})
        self.assertEqual(response.status_code, 302)
        download.assert_not_called()
        self.assertContains(client.get("/manage/sync/"), "Close every cashier shift")

    def test_restore_helper_marks_queue_and_starts_detached_worker(self):
        name = "winespos-20260101-010101.zip"
        with mock.patch.object(windows_restore, "check_ready", return_value={
                windows_restore.SERVER_TASK: "Running", windows_restore.PRINTER_TASK: "Running"}), \
                mock.patch.object(windows_restore.subprocess, "Popen") as start:
            status = windows_restore.launch_restore(name)
        self.assertEqual(status["state"], "scheduled")
        self.assertTrue(status["printer_was_running"])
        self.assertTrue((self.tmp / "backups" / windows_restore.LOCK_NAME).is_file())
        self.assertEqual(start.call_args.args[0][-2:], ["restore_server_backup", name])

    def test_restore_helper_releases_lock_if_worker_cannot_start(self):
        name = "winespos-20260101-010101.zip"
        with mock.patch.object(windows_restore, "check_ready", return_value={
                windows_restore.SERVER_TASK: "Running"}), \
                mock.patch.object(windows_restore.subprocess, "Popen", side_effect=OSError("blocked")):
            with self.assertRaises(windows_restore.RestoreOperationError):
                windows_restore.launch_restore(name)
        self.assertFalse((self.tmp / "backups" / windows_restore.LOCK_NAME).exists())
        self.assertEqual(windows_restore.read_status()["state"], "failed")

    def run_restore_worker(self, restore_side_effect=None):
        name = "winespos-20260101-010101.zip"
        folder = self.tmp / "backups" / "server-downloads"
        folder.mkdir(parents=True)
        (folder / name).write_bytes(b"staged")
        (self.tmp / "backups" / windows_restore.LOCK_NAME).write_text("queued", encoding="utf-8")
        windows_restore.set_state("scheduled", backup=name)
        tasks = {
            windows_restore.SERVER_TASK: "Running",
            windows_restore.PRINTER_TASK: "Running",
            windows_restore.BACKUP_TASK: "Ready",
        }

        def stop(task):
            tasks[task] = "Ready"

        def start(task):
            tasks[task] = "Running"

        with mock.patch("core.management.commands.restore_server_backup.os.name", "nt"), \
                mock.patch("core.management.commands.restore_server_backup.time.sleep"), \
                mock.patch("core.management.commands.restore_server_backup.backup.verify_backup",
                           return_value={"manifest": {}}), \
                mock.patch("core.management.commands.restore_server_backup.backup_rules.check_manifest"), \
                mock.patch("core.management.commands.restore_server_backup.backup.restore_backup",
                           return_value={"safety_copy": self.tmp / "safety.zip"},
                           side_effect=restore_side_effect if isinstance(restore_side_effect, Exception) else None), \
                mock.patch.object(windows_restore, "task_states", side_effect=lambda: dict(tasks)), \
                mock.patch.object(windows_restore, "stop_task", side_effect=stop), \
                mock.patch.object(windows_restore, "start_task", side_effect=start) as start_tasks, \
                mock.patch.object(windows_restore, "wait_for_server"):
            command = RestoreServerBackupCommand()
            command.stdout = io.StringIO()
            self.restore_start_tasks = start_tasks
            command.handle(backup_name=name)
            return command.stdout.getvalue(), start_tasks

    def test_restore_worker_restarts_tasks_and_records_success(self):
        result, start_tasks = self.run_restore_worker()
        self.assertIn("Restored winespos-", result)
        self.assertEqual(start_tasks.call_args_list, [
            mock.call(windows_restore.SERVER_TASK), mock.call(windows_restore.PRINTER_TASK)])
        self.assertEqual(windows_restore.read_status()["state"], "succeeded")
        self.assertFalse((self.tmp / "backups" / windows_restore.LOCK_NAME).exists())

    def test_restore_worker_restarts_tasks_and_records_restore_failure(self):
        with self.assertRaises(CommandError):
            self.run_restore_worker(backup.BackupError("restore failed"))
        self.assertEqual(self.restore_start_tasks.call_args_list, [
            mock.call(windows_restore.SERVER_TASK), mock.call(windows_restore.PRINTER_TASK)])
        self.assertEqual(windows_restore.read_status()["state"], "failed")
        self.assertIn("restore failed", windows_restore.read_status()["error"])
        self.assertFalse((self.tmp / "backups" / windows_restore.LOCK_NAME).exists())
