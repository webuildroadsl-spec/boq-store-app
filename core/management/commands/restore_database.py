"""
Section 7.2's "Backups": "... tested restore once a month." This
command is that test's tool -- restore a `backup_database` dump (the
most recent one, unless `--file` names another) into the database,
overwriting whatever is there.

Because it's destructive, it refuses to run without `--yes` unless
the target database's name contains "test" (so Django's own test
runner, and this app's own test for this command, can call it without
an interactive prompt getting in the way -- the same reasoning
Django's own `flush`/`migrate --run-syncdb` commands use for their
own confirmation prompts).
"""

import os
import subprocess
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

BACKUP_DIR = Path(os.environ.get("DB_BACKUP_DIR", settings.BASE_DIR / "backups"))


class Command(BaseCommand):
    help = "Restore the database from a backup_database dump (the most recent one by default)."

    def add_arguments(self, parser):
        parser.add_argument("--file", type=str, default=None, help="Path to a specific .dump file to restore.")
        parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt.")

    def handle(self, *args, **options):
        db = settings.DATABASES["default"]
        backup_file = self._resolve_backup_file(options["file"])

        if not options["yes"] and "test" not in db["NAME"]:
            confirm = input(
                f"This will overwrite every table in database '{db['NAME']}' with the contents of "
                f"{backup_file}. Type 'yes' to continue: "
            )
            if confirm.strip().lower() != "yes":
                self.stdout.write("Restore cancelled.")
                return

        env = os.environ.copy()
        if db.get("PASSWORD"):
            env["PGPASSWORD"] = db["PASSWORD"]

        command = [
            "pg_restore",
            "--clean",
            "--if-exists",
            "--no-owner",
            "-h", db.get("HOST") or "localhost",
            "-p", str(db.get("PORT") or "5432"),
            "-U", db["USER"],
            "-d", db["NAME"],
            str(backup_file),
        ]
        result = subprocess.run(command, env=env, capture_output=True, text=True)
        # pg_restore exits 1 for warnings (e.g. "role does not exist"
        # on --no-owner) as well as real failures -- --no-owner and
        # --if-exists already suppress the common, harmless ones, so
        # anything left on stderr at this point is worth surfacing,
        # but only a genuinely empty successful run is treated as
        # success without a printed warning.
        if result.returncode != 0 and "error" in result.stderr.lower():
            raise CommandError(f"pg_restore failed: {result.stderr.strip()}")

        self.stdout.write(self.style.SUCCESS(f"Restored from {backup_file}"))
        if result.stderr.strip():
            self.stdout.write(f"pg_restore warnings: {result.stderr.strip()}")

    def _resolve_backup_file(self, explicit_path):
        if explicit_path:
            path = Path(explicit_path)
            if not path.exists():
                raise CommandError(f"Backup file not found: {path}")
            return path
        if not BACKUP_DIR.exists():
            raise CommandError(f"Backup directory does not exist: {BACKUP_DIR}")
        candidates = sorted(BACKUP_DIR.glob("boq_store_*.dump"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not candidates:
            raise CommandError(f"No backups found in {BACKUP_DIR}")
        return candidates[0]
