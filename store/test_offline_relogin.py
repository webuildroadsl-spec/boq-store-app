"""
Offline sync after the 30-minute idle timeout (Section 7.2).

A storekeeper who fills in a GRN offline for more than 30 minutes comes
back to an expired session. These tests pin down the server behaviour
`pwa/static/pwa/offline-queue.js` relies on to keep that GRN safe:

1. With an expired session, the sync endpoint answers with a redirect
   to the login page, not JSON. The script treats any non-JSON answer
   as "needs login" and keeps the document queued.
2. Logging in again gives the browser a NEW CSRF token. The token saved
   with the queued form is now stale and is refused, which is why the
   script sends the current token from the csrftoken cookie instead.
3. With the current token, the queued GRN syncs and posts.

CSRF checks are switched on here (the default test client skips them).
The JavaScript itself is not run -- there is no browser in this test
suite -- so the in-browser half still needs the manual check described
in the README.
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Company, Project, ProjectMembership, ROLE_STOREKEEPER, UnitOfMeasure

from .models import GRN, ItemCategory, StockMovement, Store, StoreItem, Supplier

User = get_user_model()
PASSWORD = "a-strong-test-password-1"


class OfflineSyncAfterReloginTests(TestCase):
    def setUp(self):
        company = Company.objects.create(name="Test Contractor Ltd")
        self.project = Project.objects.create(company=company, code="RELOG-1", name="Relogin test")
        self.keeper = User.objects.create_user(username="relogkeeper", password=PASSWORD)
        ProjectMembership.objects.create(user=self.keeper, project=self.project, role=ROLE_STOREKEEPER)
        self.store = Store.objects.create(project=self.project, code="MAIN", name="Main yard", storekeeper=self.keeper)
        category = ItemCategory.objects.create(name="Cement RELOG")
        self.item = StoreItem.objects.create(
            code="CEM-RL", name="Cement", category=category, unit=UnitOfMeasure.objects.get(code="t")
        )
        self.supplier = Supplier.objects.create(name="ACME Building Supplies")
        self.client = Client(enforce_csrf_checks=True)
        self.sync_url = reverse("store:grn_offline_sync", args=[self.project.pk, self.store.pk])

    def _login(self):
        self.client.get(reverse("accounts:login"))
        token = self.client.cookies["csrftoken"].value
        response = self.client.post(
            reverse("accounts:login"),
            {"username": "relogkeeper", "password": PASSWORD, "csrfmiddlewaretoken": token},
        )
        self.assertEqual(response.status_code, 302)
        return self.client.cookies["csrftoken"].value

    def _grn(self, token):
        return {
            "csrfmiddlewaretoken": token,
            "date": timezone.localdate().isoformat(),
            "supplier": self.supplier.pk,
            "delivery_note_number": "DN-RELOG-1",
            "vehicle_number": "",
            "item": self.item.pk,
            "quantity": "200",
            "unit_cost": "150.00",
        }

    def test_queued_grn_survives_timeout_and_syncs_after_login(self):
        token_when_queued = self._login()

        # Offline for 45 minutes: the session expires.
        Session.objects.all().update(expire_date=timezone.now() - timedelta(minutes=15))

        # 1. Connection returns, sync attempted while logged out.
        response = self.client.post(self.sync_url, self._grn(token_when_queued))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])
        self.assertNotIn("application/json", response.get("Content-Type", ""))
        self.assertEqual(GRN.objects.count(), 0)

        # 2. User logs in again; the token saved with the form is now stale.
        current_token = self._login()
        self.assertNotEqual(current_token, token_when_queued)
        response = self.client.post(self.sync_url, self._grn(token_when_queued))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(GRN.objects.count(), 0)

        # 3. With the current token (what the script now sends), it syncs.
        response = self.client.post(self.sync_url, self._grn(current_token))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["posted"])
        quantity, value, _ = StockMovement.current_balance(self.store, self.item)
        self.assertEqual(quantity, Decimal("200.000"))
        self.assertEqual(value, Decimal("30000.00"))
