"""
Section 7.2 "Security": session timeout after 30 minutes idle.

"Idle" is simulated by moving the session's stored expiry date in the
database, which is exactly what the clock passing would do -- no need to
wait 30 real minutes in a test.
"""

from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

User = get_user_model()


class IdleSessionTimeoutTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="idleuser", password="a-strong-test-password-1")
        self.client.post(
            reverse("accounts:login"),
            {"username": "idleuser", "password": "a-strong-test-password-1"},
        )
        self.session_key = self.client.session.session_key

    def _set_expiry_from_now(self, delta):
        Session.objects.filter(session_key=self.session_key).update(expire_date=timezone.now() + delta)

    def _expiry(self):
        return Session.objects.get(session_key=self.session_key).expire_date

    def test_timeout_is_thirty_minutes_and_refreshed_on_every_request(self):
        self.assertEqual(settings.SESSION_COOKIE_AGE, 30 * 60)
        self.assertTrue(settings.SESSION_SAVE_EVERY_REQUEST)

    def test_session_expires_thirty_minutes_after_login(self):
        remaining = self._expiry() - timezone.now()
        self.assertAlmostEqual(remaining.total_seconds(), 30 * 60, delta=60)

    def test_user_idle_for_over_thirty_minutes_is_sent_to_login(self):
        # 31 minutes idle: the 30-minute deadline passed a minute ago.
        self._set_expiry_from_now(timedelta(minutes=-1))
        response = self.client.get(reverse("accounts:home"))
        self.assertRedirects(
            response,
            f"{reverse('accounts:login')}?next={reverse('accounts:home')}",
        )

    def test_activity_pushes_the_deadline_forward(self):
        # 25 minutes idle, 5 left -- then the user opens a page.
        self._set_expiry_from_now(timedelta(minutes=5))
        response = self.client.get(reverse("accounts:home"))
        self.assertEqual(response.status_code, 200)
        # A fresh 30 minutes from now, not the 5 that were left.
        remaining = self._expiry() - timezone.now()
        self.assertGreater(remaining, timedelta(minutes=29))
