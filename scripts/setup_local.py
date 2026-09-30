"""
One-time setup of the app on your own computer (Windows, macOS or Linux).

Run it by double-clicking windows\\1_setup.bat on Windows. It is safe to
run again: every step checks what is already done and skips it.

What it does:
  1. Creates a private Python environment (the venv folder) and installs
     the app's libraries into it.
  2. Creates the PostgreSQL database and a database user for the app,
     with a random password it generates itself. PostgreSQL asks for
     YOUR postgres password here -- type it in the window; this script
     never sees or stores it.
  3. Writes the .env settings file (random secret key, the database
     password from step 2, and settings that let your phone connect).
  4. Creates the database tables.
  5. Asks you to choose an admin username and password, if there is no
     admin yet.
"""

import glob
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / "venv"
ENV_FILE = ROOT / ".env"
DB_NAME = "boq_store_db"
DB_USER = "boq_user"


def say(message):
    print(f"\n=== {message}")


def fail(message):
    print(f"\n*** STOPPED: {message}")
    sys.exit(1)


def venv_python():
    windows = VENV / "Scripts" / "python.exe"
    return windows if windows.exists() else VENV / "bin" / "python"


def run(*args, **kwargs):
    return subprocess.run([str(a) for a in args], cwd=ROOT, **kwargs)


def find_psql():
    found = shutil.which("psql")
    if found:
        return found
    candidates = sorted(glob.glob(r"C:\Program Files\PostgreSQL\*\bin\psql.exe"), reverse=True)
    return candidates[0] if candidates else None


def step_python_environment():
    if sys.version_info < (3, 10):
        fail(f"This app needs Python 3.10 or newer; this is {sys.version.split()[0]}.")
    if not venv_python().exists():
        say("Creating the Python environment (venv)")
        if run(sys.executable, "-m", "venv", VENV).returncode != 0:
            fail("Could not create the venv folder.")
    say("Installing the app's libraries (a few minutes the first time)")
    if run(venv_python(), "-m", "pip", "install", "--disable-pip-version-check", "-q",
           "-r", ROOT / "requirements.txt").returncode != 0:
        fail("Installing libraries failed. Check your internet connection and run this again.")


def step_database_and_env():
    if ENV_FILE.exists():
        say(".env already exists -- keeping your database settings as they are")
        return
    psql = find_psql()
    if not psql:
        fail("Could not find PostgreSQL (psql.exe). Is PostgreSQL installed?")

    db_password = secrets.token_hex(16)
    sql = (
        "DO $$ BEGIN\n"
        f"  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{DB_USER}') THEN\n"
        f"    CREATE ROLE {DB_USER} LOGIN CREATEDB PASSWORD '{db_password}';\n"
        "  ELSE\n"
        f"    ALTER ROLE {DB_USER} WITH LOGIN CREATEDB PASSWORD '{db_password}';\n"
        "  END IF;\n"
        "END $$;\n"
        f"SELECT 'CREATE DATABASE {DB_NAME} OWNER {DB_USER}'\n"
        f"  WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '{DB_NAME}')\\gexec\n"
    )
    say("Creating the database")
    print("PostgreSQL will now ask for the password of its 'postgres' user --")
    print("the one you chose when you installed PostgreSQL. Nothing shows while you type; press Enter.")
    handle, sql_path = tempfile.mkstemp(suffix=".sql")
    try:
        with os.fdopen(handle, "w") as f:
            f.write(sql)
        result = run(psql, "-U", "postgres", "-h", "localhost", "-d", "postgres",
                     "-v", "ON_ERROR_STOP=1", "-q", "-f", sql_path)
    finally:
        os.remove(sql_path)
    if result.returncode != 0:
        fail("PostgreSQL refused. Usually a wrong password -- run this again and retype it.\n"
             "If it says it can't connect, PostgreSQL isn't running: open Services and start 'postgresql'.")

    ENV_FILE.write_text(
        "# Written by scripts/setup_local.py for running on your own computer.\n"
        f"SECRET_KEY={secrets.token_urlsafe(50)}\n"
        "DEBUG=True\n"
        "# * lets your phone reach this computer over Wi-Fi. Local use only.\n"
        "ALLOWED_HOSTS=*\n"
        f"DB_NAME={DB_NAME}\n"
        f"DB_USER={DB_USER}\n"
        f"DB_PASSWORD={db_password}\n"
        "DB_HOST=localhost\n"
        "DB_PORT=5432\n"
    )
    print("Database created and .env written.")


def step_tables_and_admin():
    say("Creating the database tables")
    if run(venv_python(), "manage.py", "migrate", "-v", "0").returncode != 0:
        fail("Creating tables failed -- see the message above.")
    check = run(venv_python(), "manage.py", "shell", "-c",
                "from django.contrib.auth import get_user_model as g; "
                "print('HAS_ADMIN' if g().objects.filter(is_superuser=True).exists() else 'NO_ADMIN')",
                capture_output=True, text=True)
    if "HAS_ADMIN" in check.stdout:
        say("An admin account already exists -- skipping")
        return
    say("Choose your admin login (for the /admin/ setup pages)")
    print("Pick a username and a password. Nothing shows while you type the password.")
    run(venv_python(), "manage.py", "createsuperuser")


if __name__ == "__main__":
    step_python_environment()
    step_database_and_env()
    step_tables_and_admin()
    say("Setup finished. Next: double-click windows\\2_run_for_phone.bat")
