"""Phase 6: backup, verify, restore (SQLite stands in for PostgreSQL here), the Backups screen, and the self-check."""
import json
import shutil
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from django.core.management import call_command
from django.test import Client, TransactionTestCase, override_settings

from accounting.models import ExpenseCategory
from accounts.models import User
from core import backup, selfcheck
from core.backup_rules import BackupError, backup_name


class BackupCase(TransactionTestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.media, self.dir, self.copy = self.tmp / "media", self.tmp / "backups", self.tmp / "usb"
        (self.media / "products").mkdir(parents=True)
        (self.media / "products" / "wine.jpg").write_bytes(b"IMAGE-ONE")
        ctx = override_settings(BACKUP_DIR=self.dir, BACKUP_COPY_DIR="", MEDIA_ROOT=self.media, BACKUP_KEEP=30)
        ctx.enable()
        self.addCleanup(ctx.disable)
        ExpenseCategory.objects.create(name="Original category")

    def names(self):
        return sorted(ExpenseCategory.objects.values_list("name", flat=True))


class RoundTripTests(BackupCase):
    def test_backup_then_restore_brings_back_the_old_data_and_images(self):
        r = backup.create_backup(note="test")
        self.assertTrue(r["path"].exists())
        # the shop carries on, then something goes wrong
        ExpenseCategory.objects.all().delete()
        ExpenseCategory.objects.create(name="Made after the backup")
        (self.media / "products" / "wine.jpg").write_bytes(b"DAMAGED")
        (self.media / "products" / "new.jpg").write_bytes(b"NEW")

        result = backup.restore_backup(r["path"])

        self.assertEqual(self.names(), ["Original category"])
        self.assertEqual((self.media / "products" / "wine.jpg").read_bytes(), b"IMAGE-ONE")
        self.assertFalse((self.media / "products" / "new.jpg").exists())
        # nothing is lost: the state just before the restore was saved, and the old images were kept aside
        self.assertTrue(result["safety_copy"].exists())
        self.assertIn("prerestore", result["safety_copy"].name)
        self.assertTrue(result["old_media"].exists())
        self.assertEqual((result["old_media"] / "products" / "new.jpg").read_bytes(), b"NEW")
        backup.verify_backup(result["safety_copy"])

    def test_the_safety_copy_can_undo_the_restore(self):
        r = backup.create_backup()
        ExpenseCategory.objects.create(name="Added later")
        done = backup.restore_backup(r["path"])
        self.assertEqual(self.names(), ["Original category"])
        backup.restore_backup(done["safety_copy"])
        self.assertEqual(self.names(), ["Added later", "Original category"])

    def test_two_backups_in_the_same_second_do_not_overwrite_each_other(self):
        now = datetime(2026, 11, 8, 10, 0, 0, tzinfo=timezone.utc)
        a = backup.create_backup(now=now)["path"]
        b = backup.create_backup(now=now)["path"]
        self.assertNotEqual(a, b)
        self.assertTrue(a.exists() and b.exists())


class VerifyTests(BackupCase):
    def test_a_good_backup_verifies_and_reports_what_it_holds(self):
        v = backup.verify_backup(backup.create_backup()["path"])
        self.assertEqual((v["engine"], v["media_files"]), ("sqlite", 1))
        self.assertIn("integrity", v["checked"])

    def test_bad_files_are_refused(self):
        junk = self.tmp / "junk.zip"
        junk.write_bytes(b"this is not a zip")
        with self.assertRaises(BackupError):
            backup.verify_backup(junk)
        with self.assertRaises(BackupError):
            backup.verify_backup(self.tmp / "missing.zip")
        other = self.tmp / "other.zip"
        with zipfile.ZipFile(other, "w") as z:
            z.writestr("manifest.json", json.dumps({"app": "someone-else"}))
        with self.assertRaises(BackupError):
            backup.verify_backup(other)

    def test_a_tampered_database_fails_its_checksum(self):
        good = backup.create_backup()["path"]
        bad = self.tmp / "tampered.zip"
        with zipfile.ZipFile(good) as src, zipfile.ZipFile(bad, "w") as dst:
            for name in src.namelist():
                data = src.read(name)
                dst.writestr(name, data + b"x" if name == "database.sqlite3" else data)
        with self.assertRaises(BackupError) as ctx:
            backup.verify_backup(bad)
        self.assertIn("checksum", str(ctx.exception))

    def test_a_damaged_zip_is_noticed(self):
        good = backup.create_backup()["path"]
        raw = bytearray(good.read_bytes())
        raw[len(raw) // 3] ^= 0xFF
        bad = self.tmp / "flipped.zip"
        bad.write_bytes(bytes(raw))
        with self.assertRaises(BackupError):
            backup.verify_backup(bad)


class RestoreRefusalTests(BackupCase):
    def rewrite_manifest(self, src_path, **changes):
        out = self.tmp / "edited.zip"
        with zipfile.ZipFile(src_path) as src, zipfile.ZipFile(out, "w") as dst:
            for name in src.namelist():
                data = src.read(name)
                if name == "manifest.json":
                    m = json.loads(data)
                    m.update(changes)
                    data = json.dumps(m).encode()
                dst.writestr(name, data)
        return out

    def test_a_backup_from_a_newer_program_is_refused_and_nothing_changes(self):
        good = backup.create_backup()["path"]
        newer = self.rewrite_manifest(good, migrations={"sales": "9999_from_the_future"})
        ExpenseCategory.objects.create(name="Today's data")
        with self.assertRaises(BackupError) as ctx:
            backup.restore_backup(newer)
        self.assertIn("newer version", str(ctx.exception))
        self.assertIn("Today's data", self.names())
        self.assertFalse([r for r in backup.list_backups() if r["prerestore"]])      # refused before touching anything

    def test_a_backup_from_another_kind_of_database_is_refused(self):
        good = backup.create_backup()["path"]
        with self.assertRaises(BackupError):
            backup.restore_backup(self.rewrite_manifest(good, engine="postgresql"))

    def test_an_unsafe_media_path_is_never_written_outside_the_media_folder(self):
        good = backup.create_backup()["path"]
        evil = self.tmp / "evil.zip"
        with zipfile.ZipFile(good) as src, zipfile.ZipFile(evil, "w") as dst:
            for name in src.namelist():
                dst.writestr(name, src.read(name))
            dst.writestr("media/../../escaped.txt", "bad")
        with self.assertRaises(BackupError):
            backup.restore_backup(evil)
        self.assertFalse((self.tmp.parent / "escaped.txt").exists())
        self.assertFalse((self.tmp / "escaped.txt").exists())


class CopyAndPruneTests(BackupCase):
    def test_a_second_copy_is_written(self):
        with override_settings(BACKUP_COPY_DIR=str(self.copy)):
            r = backup.create_backup()
        self.assertEqual(r["copy_path"].read_bytes(), r["path"].read_bytes())

    def test_an_unreachable_second_location_warns_but_the_backup_still_succeeds(self):
        blocker = self.tmp / "afile"
        blocker.write_text("not a folder")
        with override_settings(BACKUP_COPY_DIR=str(blocker / "sub")):
            r = backup.create_backup()
        self.assertTrue(r["path"].exists())
        self.assertIn("second copy", r["copy_error"])

    def test_only_the_newest_backups_are_kept(self):
        base = datetime(2026, 11, 1, 8, 0, 0, tzinfo=timezone.utc)
        with override_settings(BACKUP_KEEP=3):
            for i in range(5):
                backup.create_backup(now=base + timedelta(days=i))
        kept = [r["name"] for r in backup.list_backups()]
        self.assertEqual(kept, [backup_name(base + timedelta(days=i)) for i in (4, 3, 2)])

    def test_status_reports_none_ok_and_stale(self):
        self.assertEqual(backup.current_status()[0], "none")
        backup.create_backup(now=datetime.now(timezone.utc) - timedelta(hours=1))
        self.assertEqual(backup.current_status()[0], "ok")
        self.assertEqual(backup.current_status(now=datetime.now(timezone.utc) + timedelta(hours=30))[0], "stale")


class CommandTests(BackupCase):
    def test_commands_back_up_verify_and_restore(self):
        call_command("backup_db", stdout=open("/dev/null", "w"))
        path = backup.list_backups()[0]["path"]
        call_command("verify_backup", str(path), stdout=open("/dev/null", "w"))
        ExpenseCategory.objects.create(name="Later")
        from django.core.management.base import CommandError
        with self.assertRaises(CommandError):
            call_command("restore_db", str(path))                  # refuses without --yes
        self.assertIn("Later", self.names())
        call_command("restore_db", str(path), yes=True, stdout=open("/dev/null", "w"))
        self.assertEqual(self.names(), ["Original category"])


class ScreenTests(BackupCase):
    def login(self, **kw):
        user = User.objects.create(username=kw.pop("username"), **kw)
        c = Client()
        c.force_login(user)
        return c

    def test_only_people_with_the_settings_permission_may_open_it(self):
        self.assertEqual(Client().get("/manage/backups/").status_code, 302)
        self.assertEqual(self.login(username="clerk").get("/manage/backups/").status_code, 403)
        self.assertEqual(self.login(username="boss", is_manager=True).get("/manage/backups/").status_code, 200)

    def test_back_up_now_and_check_it(self):
        boss = self.login(username="boss", is_manager=True)
        self.assertContains(boss.get("/manage/backups/"), "No backup has been made yet")
        boss.post("/manage/backups/", {"action": "backup_now"})
        row = backup.list_backups()[0]
        page = boss.get("/manage/backups/")
        self.assertContains(page, row["name"])
        self.assertContains(page, "The newest backup is")
        resp = boss.post("/manage/backups/", {"action": "verify", "name": row["name"]}, follow=True)
        self.assertContains(resp, "is good")

    def test_a_typed_path_cannot_be_checked(self):
        boss = self.login(username="boss", is_manager=True)
        resp = boss.post("/manage/backups/", {"action": "verify", "name": "../../etc/passwd"}, follow=True)
        self.assertContains(resp, "was not found")

    def test_a_clerk_cannot_trigger_a_backup(self):
        self.login(username="clerk").post("/manage/backups/", {"action": "backup_now"})
        self.assertEqual(backup.list_backups(), [])


class SelfCheckTests(BackupCase):
    def lines(self):
        return {text: level for level, text in selfcheck.run_checks()}

    def test_it_reports_in_plain_words(self):
        levels = self.lines()
        self.assertTrue(any("No backup has been made yet" in t for t in levels))
        self.assertTrue(any("No till exists" in t for t in levels))
        self.assertTrue(any("SQLite" in t and lv == "FAIL" for t, lv in levels.items()))   # debug is off in tests

    def test_a_fresh_backup_clears_the_backup_warning(self):
        backup.create_backup()
        self.assertTrue(any("Newest backup is" in t and lv == "OK" for t, lv in self.lines().items()))
