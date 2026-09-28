"""
View and permission tests for step 6: recording, editing and posting a
GRN through the Django test client, and the store-visibility scoping
("Own store" for a Storekeeper) from store.permissions.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import (
    Company,
    Project,
    ProjectMembership,
    ROLE_PROJECT_MANAGER,
    ROLE_STOREKEEPER,
    ROLE_VIEWER,
    UnitOfMeasure,
)

from .models import GRN, ItemCategory, Store, StockMovement, StoreItem, Supplier

User = get_user_model()


class StoreViewsTestCase(TestCase):
    def setUp(self):
        company = Company.objects.create(name="Test Contractor Ltd")
        self.project = Project.objects.create(company=company, code="STOREV-1", name="Store views test")

        self.storekeeper_a = User.objects.create_user(username="keeper_a", password="pw")
        self.storekeeper_b = User.objects.create_user(username="keeper_b", password="pw")
        self.viewer_user = User.objects.create_user(username="viewer", password="pw")
        self.pm_user = User.objects.create_user(username="pm", password="pw")
        self.outsider = User.objects.create_user(username="outsider", password="pw")

        ProjectMembership.objects.create(user=self.storekeeper_a, project=self.project, role=ROLE_STOREKEEPER)
        ProjectMembership.objects.create(user=self.storekeeper_b, project=self.project, role=ROLE_STOREKEEPER)
        ProjectMembership.objects.create(user=self.viewer_user, project=self.project, role=ROLE_VIEWER)
        ProjectMembership.objects.create(user=self.pm_user, project=self.project, role=ROLE_PROJECT_MANAGER)

        self.store_a = Store.objects.create(
            project=self.project, code="A", name="Store A", storekeeper=self.storekeeper_a
        )
        self.store_b = Store.objects.create(
            project=self.project, code="B", name="Store B", storekeeper=self.storekeeper_b
        )

        category = ItemCategory.objects.create(name="Cement and Binders")
        self.item = StoreItem.objects.create(
            code="CEM-01", name="Cement, 50kg bag", category=category, unit=UnitOfMeasure.objects.get(code="t")
        )
        self.supplier = Supplier.objects.create(name="ACME Building Supplies")


class StoreVisibilityTests(StoreViewsTestCase):
    def test_pm_sees_every_store(self):
        self.client.force_login(self.pm_user)
        response = self.client.get(reverse("store:store_list", args=[self.project.pk]))
        self.assertContains(response, "Store A")
        self.assertContains(response, "Store B")

    def test_storekeeper_sees_only_their_own_store(self):
        self.client.force_login(self.storekeeper_a)
        response = self.client.get(reverse("store:store_list", args=[self.project.pk]))
        self.assertContains(response, "Store A")
        self.assertNotContains(response, "Store B")

    def test_storekeeper_b_cannot_open_store_a_directly(self):
        self.client.force_login(self.storekeeper_b)
        response = self.client.get(reverse("store:store_detail", args=[self.project.pk, self.store_a.pk]))
        self.assertEqual(response.status_code, 404)

    def test_outsider_gets_404_on_the_project(self):
        self.client.force_login(self.outsider)
        response = self.client.get(reverse("store:store_list", args=[self.project.pk]))
        self.assertEqual(response.status_code, 404)


class GRNWorkflowTests(StoreViewsTestCase):
    def _create_grn(self, user, store):
        response = self.client.post(
            reverse("store:grn_create", args=[self.project.pk, store.pk]),
            {
                "date": "2026-01-15",
                "supplier": self.supplier.pk,
                "delivery_note_number": "DN-001",
                "vehicle_number": "ABC-123",
            },
        )
        return response

    def test_storekeeper_can_create_edit_and_post_a_grn(self):
        self.client.force_login(self.storekeeper_a)

        create_response = self._create_grn(self.storekeeper_a, self.store_a)
        grn = GRN.objects.get(store=self.store_a)
        self.assertRedirects(
            create_response,
            reverse("store:grn_detail", args=[self.project.pk, self.store_a.pk, grn.pk]),
        )
        self.assertEqual(grn.number, 1)
        self.assertEqual(grn.status, GRN.STATUS_DRAFT)
        self.assertEqual(grn.received_by, self.storekeeper_a)

        add_line_response = self.client.post(
            reverse("store:grn_detail", args=[self.project.pk, self.store_a.pk, grn.pk]),
            {"item": self.item.pk, "quantity": "200", "unit_cost": "150.00"},
        )
        self.assertEqual(add_line_response.status_code, 302)
        self.assertEqual(grn.lines.count(), 1)

        post_response = self.client.post(
            reverse("store:grn_post", args=[self.project.pk, self.store_a.pk, grn.pk])
        )
        self.assertRedirects(
            post_response, reverse("store:grn_detail", args=[self.project.pk, self.store_a.pk, grn.pk])
        )
        grn.refresh_from_db()
        self.assertEqual(grn.status, GRN.STATUS_POSTED)

        quantity, value, average = StockMovement.current_balance(self.store_a, self.item)
        self.assertEqual(quantity, Decimal("200.000"))
        self.assertEqual(value, Decimal("30000.00"))
        self.assertEqual(average, Decimal("150.00"))

    def test_posting_with_no_lines_shows_an_error_and_does_not_post(self):
        self.client.force_login(self.storekeeper_a)
        self._create_grn(self.storekeeper_a, self.store_a)
        grn = GRN.objects.get(store=self.store_a)
        response = self.client.post(
            reverse("store:grn_post", args=[self.project.pk, self.store_a.pk, grn.pk]), follow=True
        )
        self.assertContains(response, "needs at least one line")
        grn.refresh_from_db()
        self.assertEqual(grn.status, GRN.STATUS_DRAFT)

    def test_cannot_edit_a_posted_grn(self):
        self.client.force_login(self.storekeeper_a)
        self._create_grn(self.storekeeper_a, self.store_a)
        grn = GRN.objects.get(store=self.store_a)
        self.client.post(
            reverse("store:grn_detail", args=[self.project.pk, self.store_a.pk, grn.pk]),
            {"item": self.item.pk, "quantity": "10", "unit_cost": "1.00"},
        )
        self.client.post(reverse("store:grn_post", args=[self.project.pk, self.store_a.pk, grn.pk]))

        response = self.client.post(
            reverse("store:grn_detail", args=[self.project.pk, self.store_a.pk, grn.pk]),
            {"item": self.item.pk, "quantity": "5", "unit_cost": "1.00"},
        )
        self.assertEqual(response.status_code, 403)
        grn.refresh_from_db()
        self.assertEqual(grn.lines.count(), 1)

    def test_storekeeper_b_cannot_create_a_grn_for_store_a(self):
        # store_a isn't even visible to storekeeper_b (see
        # StoreVisibilityTests), so this is a 404, not a 403 -- same
        # "don't confirm it exists" reasoning as everywhere else.
        self.client.force_login(self.storekeeper_b)
        response = self._create_grn(self.storekeeper_b, self.store_a)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(GRN.objects.filter(store=self.store_a).count(), 0)

    def test_viewer_cannot_create_a_grn(self):
        self.client.force_login(self.viewer_user)
        response = self._create_grn(self.viewer_user, self.store_a)
        self.assertEqual(response.status_code, 403)

    def test_viewer_can_still_see_the_grn_list(self):
        self.client.force_login(self.storekeeper_a)
        self._create_grn(self.storekeeper_a, self.store_a)

        self.client.force_login(self.viewer_user)
        response = self.client.get(reverse("store:grn_list", args=[self.project.pk, self.store_a.pk]))
        self.assertEqual(response.status_code, 200)

    def test_grn_numbers_are_scoped_per_store_not_globally(self):
        self.client.force_login(self.storekeeper_a)
        self._create_grn(self.storekeeper_a, self.store_a)

        self.client.force_login(self.storekeeper_b)
        self._create_grn(self.storekeeper_b, self.store_b)

        self.assertEqual(GRN.objects.get(store=self.store_a).number, 1)
        self.assertEqual(GRN.objects.get(store=self.store_b).number, 1)
