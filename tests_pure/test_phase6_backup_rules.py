"""Phase 6 pure backup rules (no Django): python3 -m unittest discover -s tests_pure -t ."""
import unittest
from datetime import datetime, timedelta, timezone

from core import backup_rules as br

T = datetime(2026, 11, 8, 14, 30, 5, tzinfo=timezone.utc)


class NameTests(unittest.TestCase):
    def test_name_round_trip(self):
        n = br.backup_name(T)
        self.assertEqual(n, "winespos-20261108-143005.zip")
        self.assertEqual(br.parse_name(n), (T, False))
        self.assertEqual(br.parse_name(br.backup_name(T, prerestore=True)), (T, True))

    def test_foreign_names_are_ignored(self):
        for bad in ("notes.txt", "winespos-2026-11-08.zip", "winespos-20261108-143005.zip.part", "", None):
            self.assertIsNone(br.parse_name(bad))


class ManifestTests(unittest.TestCase):
    def man(self, **kw):
        base = dict(created_at=T, vendor="postgresql", db_sha256="ab", media_count=3, migrations={"sales": "0005_x"})
        base.update(kw)
        return br.build_manifest(**base)

    def test_a_fitting_backup_passes(self):
        self.assertTrue(br.check_manifest(self.man(), vendor="postgresql", code_migrations={"sales": {"0005_x", "0006_y"}}))

    def test_backup_from_a_newer_program_is_refused(self):
        with self.assertRaises(br.BackupError):
            br.check_manifest(self.man(migrations={"sales": "0009_future"}), vendor="postgresql", code_migrations={"sales": {"0005_x"}})
        with self.assertRaises(br.BackupError):
            br.check_manifest(self.man(migrations={"newapp": "0001"}), vendor="postgresql", code_migrations={"sales": {"0005_x"}})

    def test_wrong_engine_or_foreign_file_is_refused(self):
        with self.assertRaises(br.BackupError):
            br.check_manifest(self.man(), vendor="sqlite", code_migrations={"sales": {"0005_x"}})
        for bad in ({}, {"app": "other"}, None, {"app": "winespos", "format": 99}):
            with self.assertRaises(br.BackupError):
                br.check_manifest(bad, vendor="postgresql", code_migrations={})

    def test_unsupported_database_type(self):
        with self.assertRaises(br.BackupError):
            br.engine_key("mysql")


class RetentionTests(unittest.TestCase):
    def names(self, n, prerestore=False):
        return [br.backup_name(T - timedelta(days=i), prerestore=prerestore) for i in range(n)]

    def test_keeps_the_newest_n_ordinary_backups(self):
        names = self.names(10)
        gone = br.to_prune(names, 7)
        self.assertEqual(sorted(gone), sorted(names[7:]))

    def test_safety_copies_are_kept_three_deep_and_do_not_count_against_the_limit(self):
        names = self.names(3) + self.names(5, prerestore=True)
        gone = br.to_prune(names, 3)
        self.assertEqual(len(gone), 2)
        self.assertTrue(all("prerestore" in g for g in gone))

    def test_never_prunes_everything_and_ignores_foreign_files(self):
        self.assertEqual(len(br.to_prune(self.names(4), 0)), 3)
        self.assertEqual(br.to_prune(["readme.txt"], 1), [])


class StatusTests(unittest.TestCase):
    def test_status(self):
        self.assertEqual(br.status(None, T, 26), ("none", None))
        self.assertEqual(br.status(T - timedelta(hours=2), T, 26)[0], "ok")
        self.assertEqual(br.status(T - timedelta(hours=30), T, 26)[0], "stale")


if __name__ == "__main__":
    unittest.main()
