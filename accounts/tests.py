from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

User = get_user_model()


class LoginTests(TestCase):
    """
    Covers step 1's acceptance test: "You can log in and out."

    Each test creates its own user so the cases don't depend on each
    other or on fixture data.
    """

    def setUp(self):
        self.username = "testuser"
        self.password = "a-strong-test-password-1"
        self.user = User.objects.create_user(
            username=self.username, password=self.password
        )

    def test_home_page_redirects_anonymous_user_to_login(self):
        response = self.client.get(reverse("accounts:home"))
        self.assertRedirects(
            response,
            f"{reverse('accounts:login')}?next={reverse('accounts:home')}",
        )

    def test_login_page_loads(self):
        response = self.client.get(reverse("accounts:login"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "registration/login.html")

    def test_login_with_correct_credentials_succeeds(self):
        response = self.client.post(
            reverse("accounts:login"),
            {"username": self.username, "password": self.password},
        )
        self.assertRedirects(response, reverse("accounts:home"))
        # The session should now be authenticated.
        response = self.client.get(reverse("accounts:home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.username)

    def test_login_with_wrong_password_fails(self):
        response = self.client.post(
            reverse("accounts:login"),
            {"username": self.username, "password": "wrong-password"},
        )
        # Failed login re-renders the login page instead of redirecting.
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["user"].is_authenticated)

    def test_logout_ends_the_session(self):
        self.client.login(username=self.username, password=self.password)
        # Django's LogoutView only accepts POST as of Django 5.
        response = self.client.post(reverse("accounts:logout"))
        self.assertRedirects(response, reverse("accounts:login"))
        # Now the home page should redirect to login again.
        response = self.client.get(reverse("accounts:home"))
        self.assertRedirects(
            response,
            f"{reverse('accounts:login')}?next={reverse('accounts:home')}",
        )
