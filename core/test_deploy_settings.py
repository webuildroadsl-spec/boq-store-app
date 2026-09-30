"""
Production settings (Section 7.2 "Security": HTTPS only).

Each test starts a separate `manage.py` process with server-style
environment variables. python-decouple reads real environment
variables before the .env file, so these override whatever the
developer's own .env says without touching it.
"""

import os
import subprocess
import sys
from pathlib import Path

from django.test import SimpleTestCase

BASE_DIR = Path(__file__).resolve().parent.parent

SERVER_ENV = {
    "PRODUCTION": "True",
    "DEBUG": "False",
    "SECRET_KEY": "k3Jz9vQ2mX7pL4tR8wY1cN6bH5sD0fG2aE9uI3oP7lK4jM8nB1vC6xZ5qW2eR0tY",
    "ALLOWED_HOSTS": "boq.example.com",
    "CSRF_TRUSTED_ORIGINS": "https://boq.example.com",
}


def run_manage(*args, **env):
    return subprocess.run(
        [sys.executable, "manage.py", *args],
        cwd=BASE_DIR,
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        timeout=120,
    )


class ProductionSettingsTests(SimpleTestCase):
    def test_deploy_check_passes_with_no_warnings(self):
        result = run_manage("check", "--deploy", "--fail-level", "WARNING", **SERVER_ENV)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("no issues", result.stdout)

    def test_production_settings_are_on(self):
        code = (
            "from django.conf import settings as s;"
            "print(s.SECURE_SSL_REDIRECT, s.SESSION_COOKIE_SECURE, s.CSRF_COOKIE_SECURE,"
            " s.SECURE_HSTS_SECONDS > 0, s.SECURE_PROXY_SSL_HEADER, s.SESSION_COOKIE_AGE)"
        )
        result = run_manage("shell", "-c", code, **SERVER_ENV)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.strip().splitlines()[-1],
            "True True True True ('HTTP_X_FORWARDED_PROTO', 'https') 1800",
        )

    def test_refuses_to_start_with_debug_on_in_production(self):
        result = run_manage("check", **{**SERVER_ENV, "DEBUG": "True"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DEBUG=False", result.stderr)

    def test_collectstatic_has_a_target(self):
        result = run_manage("collectstatic", "--noinput", "--dry-run", **SERVER_ENV)
        self.assertEqual(result.returncode, 0, result.stderr)
