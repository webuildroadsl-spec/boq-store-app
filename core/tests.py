from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from .models import Company, Project, ProjectMembership, ROLE_STOREKEEPER, ROLE_ADMIN

User = get_user_model()


class ProjectScopingTests(TestCase):
    """
    Covers step 2's acceptance test: "A Storekeeper on Project A cannot
    see Project B."
    """

    def setUp(self):
        self.company = Company.objects.create(name="Test Contractor Ltd")
        self.project_a = Project.objects.create(
            company=self.company, code="BTR-A", name="Project A"
        )
        self.project_b = Project.objects.create(
            company=self.company, code="BTR-B", name="Project B"
        )

        self.storekeeper = User.objects.create_user(
            username="storekeeper", password="a-strong-test-password-1"
        )
        ProjectMembership.objects.create(
            user=self.storekeeper, project=self.project_a, role=ROLE_STOREKEEPER
        )

        self.client.login(username="storekeeper", password="a-strong-test-password-1")

    def test_project_list_only_shows_projects_the_user_belongs_to(self):
        response = self.client.get(reverse("core:project_list"))
        self.assertContains(response, "BTR-A")
        self.assertNotContains(response, "BTR-B")

    def test_project_detail_for_own_project_succeeds(self):
        response = self.client.get(
            reverse("core:project_detail", args=[self.project_a.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Project A")

    def test_project_detail_for_other_project_is_404(self):
        response = self.client.get(
            reverse("core:project_detail", args=[self.project_b.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_anonymous_user_is_redirected_to_login(self):
        self.client.logout()
        response = self.client.get(reverse("core:project_list"))
        self.assertRedirects(
            response,
            f"{reverse('accounts:login')}?next={reverse('core:project_list')}",
        )

    def test_superuser_sees_every_project(self):
        admin_user = User.objects.create_superuser(
            username="admin", email="admin@example.com", password="a-strong-test-password-1"
        )
        self.client.login(username="admin", password="a-strong-test-password-1")
        response = self.client.get(reverse("core:project_list"))
        self.assertContains(response, "BTR-A")
        self.assertContains(response, "BTR-B")

    def test_a_user_can_hold_different_roles_on_different_projects(self):
        # The same storekeeper is also the Admin on Project B, via a
        # separate ProjectMembership row.
        ProjectMembership.objects.create(
            user=self.storekeeper, project=self.project_b, role=ROLE_ADMIN
        )
        membership_a = ProjectMembership.objects.get(
            user=self.storekeeper, project=self.project_a
        )
        membership_b = ProjectMembership.objects.get(
            user=self.storekeeper, project=self.project_b
        )
        self.assertEqual(membership_a.role, ROLE_STOREKEEPER)
        self.assertEqual(membership_b.role, ROLE_ADMIN)
        # And now they can see both projects' detail pages.
        response = self.client.get(
            reverse("core:project_detail", args=[self.project_b.pk])
        )
        self.assertEqual(response.status_code, 200)


class MembershipConstraintTests(TestCase):
    def test_a_user_cannot_have_two_memberships_on_the_same_project(self):
        company = Company.objects.create(name="Test Contractor Ltd")
        project = Project.objects.create(company=company, code="BTR-C", name="Project C")
        user = User.objects.create_user(username="dup", password="a-strong-test-password-1")
        ProjectMembership.objects.create(user=user, project=project, role=ROLE_STOREKEEPER)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ProjectMembership.objects.create(user=user, project=project, role=ROLE_ADMIN)
