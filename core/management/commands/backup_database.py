"""
Section 7.2's "Backups": "automatic daily database backup kept for 30
days, stored off the main server; tested restore once a month."

This command does the "daily database backup kept for 30 days" half
with a plain `pg_dump` (custom format, `-Fc`, so `pg_restore` -- see
`restore_database.py` -- can restore it selectively and in parallel).
"Automatic daily" and "off the main server" are both deployment
concerns this Django command can't enforce by itself:

  - "Automatic daily" means this command needs a scheduler pointed at
    it -- a cron entry or a systemd timer on whatever host runs the
    app, e.g. `0 2 * * * cd /path/to/app && venv/bin/python manage.py
    backup_database`. Nothing here schedules itself.
  - "Off the main server" means BACKUP_DIR (below) should itself be a
    mount point for other storage (network storage, a synced cloud
    folder, an attached volume) -- again outside what a Django
    command can arrange on its own.

Both are disclosed simplifications: this command produces the file
correctly and prunes it correctly; where that file's directory
actually lives is a hosting decision, documented in the README rather
than hard-coded here.
"""

import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

BACKUP_DIR = Path(os.environ.get("DB_BACKUP_DIR", settings.BASE_DIR / "backups"))
RETENTION_DAYS = 30


class Command(BaseCommand):
    help = "Back up the database with pg_dump and prune backups older than 30 days."

    def add_arguments(self, parser):
        parser.add_argument(
            "--retention-days",
            type=int,
            default=RETENTION_DAYS,
            help="Delete backups older than this many days (default 30, per Section 7.2).",
        )

    def handle(self, *args, **options):
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        db = settings.DATABASES["default"]
        # Microseconds too: a daily cron only needs second precision,
        # but this command being called twice within the same second
        # (as the retention test below does) would otherwise silently
        # overwrite the first file instead of creating a second one.
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup_file = BACKUP_DIR / f"boq_store_{timestamp}.dump"

        env = os.environ.copy()
        if db.get("PASSWORD"):
            env["PGPASSWORD"] = db["PASSWORD"]

        command = [
            "pg_dump",
            "-Fc",  # custom format: needed for pg_restore's --clean/--if-exists below
            "-h", db.get("HOST") or "localhost",
            "-p", str(db.get("PORT") or "5432"),
            "-U", db["USER"],
            "-f", str(backup_file),
            db["NAME"],
        ]
        result = subprocess.run(command, env=env, capture_output=True, text=True)
        if result.returncode != 0:
            raise CommandError(f"pg_dump failed: {result.stderr.strip()}")

        self.stdout.write(self.style.SUCCESS(f"Backup written to {backup_file}"))

        cutoff = datetime.now() - timedelta(days=options["retention_days"])
        removed = 0
        for existing in BACKUP_DIR.glob("boq_store_*.dump"):
            if datetime.fromtimestamp(existing.stat().st_mtime) < cutoff:
                existing.unlink()
                removed += 1
        if removed:
            self.stdout.write(f"Pruned {removed} backup(s) older than {options['retention_days']} days.")
