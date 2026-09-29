"""
Step 10's other acceptance test half: "restore from last night's
backup works" (Section 8's build-order table; Section 7.2's "Backups
... tested restore once a month" is exactly this, automated).

Uses `TransactionTestCase`, not the ordinary `TestCase`: `pg_dump` and
`pg_restore` run as a separate `subprocess`, over their own database
connection, so they can only see data this test has actually
committed -- the plain `TestCase`'s wrapping, never-committed
transaction would be invisible to them. Django's test runner already
points `settings.DATABASES["default"]` at a `test_...`-named database
for the whole run, which is also what lets `restore_database` skip
its "type yes to confirm" prompt automatically here (see that
command's own docstring).
"""

import shutil
import tempfile
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.test import TransactionTestCase

from core.models import Company, Project


class BackupRestoreTests(TransactionTestCase):
    def setUp(self):
        self.backup_dir = Path(tempfile.mkdtemp(prefix="boq_store_backup_test_"))

    def tearDown(self):
        shutil.rmtree(self.backup_dir, ignore_errors=True)

    def test_backup_creates_a_dump_file(self):
        with mock.patch("core.management.commands.backup_database.BACKUP_DIR", self.backup_dir):
            call_command("backup_database")
        dumps = list(self.backup_dir.glob("boq_store_*.dump"))
        self.assertEqual(len(dumps), 1)
        self.assertGreater(dumps[0].stat().st_size, 0)

    def test_restore_brings_back_data_deleted_after_the_backup(self):
        company = Company.objects.create(name="Backup Test Co")
        Project.objects.create(company=company, code="BACKUP-TEST", name="Backup marker project")

        with mock.patch("core.management.commands.backup_database.BACKUP_DIR", self.backup_dir):
            call_command("backup_database")

        # Simulate data loss after the backup was taken.
        Project.objects.filter(code="BACKUP-TEST").delete()
        self.assertFalse(Project.objects.filter(code="BACKUP-TEST").exists())

        with mock.patch("core.management.commands.restore_database.BACKUP_DIR", self.backup_dir):
            call_command("restore_database", yes=True)

        self.assertTrue(Project.objects.filter(code="BACKUP-TEST").exists())

    def test_restore_can_target_a_specific_file(self):
        company = Company.objects.create(name="Backup Test Co 2")
        Project.objects.create(company=company, code="BACKUP-TEST-2", name="Backup marker project 2")

        with mock.patch("core.management.commands.backup_database.BACKUP_DIR", self.backup_dir):
            call_command("backup_database")
        dump_file = next(self.backup_dir.glob("boq_store_*.dump"))

        Project.objects.filter(code="BACKUP-TEST-2").delete()

        call_command("restore_database", file=str(dump_file), yes=True)

        self.assertTrue(Project.objects.filter(code="BACKUP-TEST-2").exists())

    def test_backup_prunes_files_older_than_retention(self):
        import os
        import time

        with mock.patch("core.management.commands.backup_database.BACKUP_DIR", self.backup_dir):
            call_command("backup_database")
        dump_file = next(self.backup_dir.glob("boq_store_*.dump"))

        # Backdate the file's mtime past a 1-day retention window, then
        # take a second backup with that short window -- the old file
        # should be pruned, the new one kept.
        old_time = time.time() - (2 * 24 * 60 * 60)
        os.utime(dump_file, (old_time, old_time))

        with mock.patch("core.management.commands.backup_database.BACKUP_DIR", self.backup_dir):
            call_command("backup_database", retention_days=1)

        remaining = list(self.backup_dir.glob("boq_store_*.dump"))
        self.assertEqual(len(remaining), 1)
        self.assertNotEqual(remaining[0], dump_file)

    def test_restore_with_no_backups_raises_a_clear_error(self):
        from django.core.management.base import CommandError

        with mock.patch("core.management.commands.restore_database.BACKUP_DIR", self.backup_dir):
            with self.assertRaises(CommandError):
                call_command("restore_database", yes=True)
