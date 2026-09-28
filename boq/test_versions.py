"""
Tests for step 5: BOQ versions, approval, variation orders, and compare
(Section 4.2's "Revise BOQ" / "Variation orders" / "Compare versions",
rules 4, 5 and 8).
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from core.models import (
    Company,
    Project,
    ProjectMembership,
    ROLE_PROJECT_MANAGER,
    ROLE_QS,
    ROLE_STOREKEEPER,
    ROLE_VIEWER,
    UnitOfMeasure,
)

from .models import BOQ, Bill, BOQItem, VariationOrder

User = get_user_model()


class CreateRevisionTests(TestCase):
    def setUp(self):
        company = Company.objects.create(name="Test Contractor Ltd")
        self.project = Project.objects.create(company=company, code="REV-1", name="Revision test")
        self.m3 = UnitOfMeasure.objects.get(code="m³")
        self.original = BOQ.objects.create(
            project=self.project, version_number=1, type=BOQ.TYPE_ORIGINAL, status=BOQ.STATUS_APPROVED
        )
        self.bill = Bill.objects.create(boq=self.original, number=4, title="Sub-base and Base")
        self.heading = BOQItem.objects.create(
            bill=self.bill, item_reference="4", description="Sub-base and Base", item_type=BOQItem.TYPE_HEADING
        )
        self.item = BOQItem.objects.create(
            bill=self.bill,
            item_reference="4.02",
            description="Sub-base, 150mm",
            item_type=BOQItem.TYPE_MEASURED,
            unit=self.m3,
            quantity=Decimal("1250.500"),
            rate=Decimal("185.00"),
            parent_item=self.heading,
        )

    def test_revision_is_a_new_draft_version(self):
        revision = self.original.create_revision()
        self.assertEqual(revision.version_number, 2)
        self.assertEqual(revision.type, BOQ.TYPE_REVISION)
        self.assertEqual(revision.status, BOQ.STATUS_DRAFT)
        self.assertTrue(revision.is_editable)

    def test_revision_copies_bills_and_items(self):
        revision = self.original.create_revision()
        self.assertEqual(revision.bills.count(), 1)
        new_bill = revision.bills.get(number=4)
        self.assertEqual(new_bill.title, "Sub-base and Base")
        self.assertEqual(new_bill.items.count(), 2)
        new_item = new_bill.items.get(item_reference="4.02")
        self.assertEqual(new_item.amount, Decimal("231342.50"))
        # It's a genuinely separate row, not the same one reused.
        self.assertNotEqual(new_item.pk, self.item.pk)

    def test_revision_remaps_parent_item_to_the_copy_not_the_original(self):
        revision = self.original.create_revision()
        new_item = revision.bills.get(number=4).items.get(item_reference="4.02")
        new_heading = revision.bills.get(number=4).items.get(item_reference="4")
        self.assertEqual(new_item.parent_item_id, new_heading.pk)

    def test_editing_the_revision_does_not_change_the_original(self):
        revision = self.original.create_revision()
        new_item = revision.bills.get(number=4).items.get(item_reference="4.02")
        new_item.rate = Decimal("999.00")
        new_item.save()
        self.item.refresh_from_db()
        self.assertEqual(self.item.rate, Decimal("185.00"))

    def test_next_version_number_accounts_for_every_existing_version(self):
        self.original.create_revision()  # v2
        revision3 = self.original.create_revision()  # should still be v3, not re-use 2
        self.assertEqual(revision3.version_number, 3)


class ApproveTests(TestCase):
    def setUp(self):
        company = Company.objects.create(name="Test Contractor Ltd")
        self.project = Project.objects.create(company=company, code="APR-1", name="Approve test")
        self.pm = User.objects.create_user(username="pm", password="pw")
        self.original = BOQ.objects.create(
            project=self.project, version_number=1, type=BOQ.TYPE_ORIGINAL, status=BOQ.STATUS_APPROVED
        )

    def test_approving_rev1_supersedes_original(self):
        """The step 5 acceptance test: approving Rev 1 supersedes Original."""
        rev1 = self.original.create_revision()
        rev1.approve(self.pm)

        self.original.refresh_from_db()
        rev1.refresh_from_db()
        self.assertEqual(self.original.status, BOQ.STATUS_SUPERSEDED)
        self.assertEqual(rev1.status, BOQ.STATUS_APPROVED)
        self.assertEqual(rev1.approved_by, self.pm)
        self.assertIsNotNone(rev1.approved_date)

    def test_only_one_approved_version_per_project(self):
        rev1 = self.original.create_revision()
        rev1.approve(self.pm)
        rev2 = rev1.create_revision()
        rev2.approve(self.pm)

        self.original.refresh_from_db()
        rev1.refresh_from_db()
        self.assertEqual(
            [self.original.status, rev1.status, rev2.status],
            [BOQ.STATUS_SUPERSEDED, BOQ.STATUS_SUPERSEDED, BOQ.STATUS_APPROVED],
        )

    def test_approving_a_non_draft_version_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.original.approve(self.pm)  # already Approved


class BOQVersionViewsTests(TestCase):
    def setUp(self):
        company = Company.objects.create(name="Test Contractor Ltd")
        self.project = Project.objects.create(company=company, code="VER-VIEW-1", name="Version views test")
        self.qs_user = User.objects.create_user(username="qs", password="pw")
        ProjectMembership.objects.create(user=self.qs_user, project=self.project, role=ROLE_QS)
        self.pm_user = User.objects.create_user(username="pm", password="pw")
        ProjectMembership.objects.create(user=self.pm_user, project=self.project, role=ROLE_PROJECT_MANAGER)
        self.viewer_user = User.objects.create_user(username="viewer", password="pw")
        ProjectMembership.objects.create(user=self.viewer_user, project=self.project, role=ROLE_VIEWER)
        self.original = BOQ.objects.create(
            project=self.project, version_number=1, type=BOQ.TYPE_ORIGINAL, status=BOQ.STATUS_APPROVED
        )

    def test_qs_can_create_a_revision_from_the_approved_version(self):
        self.client.force_login(self.qs_user)
        response = self.client.post(reverse("boq:create_revision", args=[self.project.pk, self.original.pk]))
        self.original.refresh_from_db()
        new_boq = self.project.boqs.get(version_number=2)
        self.assertRedirects(
            response, reverse("boq:boq_version_detail", args=[self.project.pk, new_boq.pk])
        )
        self.assertEqual(new_boq.status, BOQ.STATUS_DRAFT)

    def test_viewer_cannot_create_a_revision(self):
        self.client.force_login(self.viewer_user)
        response = self.client.post(reverse("boq:create_revision", args=[self.project.pk, self.original.pk]))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.project.boqs.count(), 1)

    def test_cannot_revise_a_draft_version(self):
        self.client.force_login(self.qs_user)
        draft = BOQ.objects.create(project=self.project, version_number=2, status=BOQ.STATUS_DRAFT)
        response = self.client.post(reverse("boq:create_revision", args=[self.project.pk, draft.pk]))
        self.assertEqual(response.status_code, 403)

    def test_pm_can_approve_a_draft(self):
        self.client.force_login(self.qs_user)
        self.client.post(reverse("boq:create_revision", args=[self.project.pk, self.original.pk]))
        revision = self.project.boqs.get(version_number=2)

        self.client.force_login(self.pm_user)
        response = self.client.post(reverse("boq:approve_boq", args=[self.project.pk, revision.pk]))
        revision.refresh_from_db()
        self.original.refresh_from_db()
        self.assertRedirects(
            response, reverse("boq:boq_version_detail", args=[self.project.pk, revision.pk])
        )
        self.assertEqual(revision.status, BOQ.STATUS_APPROVED)
        self.assertEqual(self.original.status, BOQ.STATUS_SUPERSEDED)

    def test_qs_cannot_approve_even_though_they_can_edit(self):
        self.client.force_login(self.qs_user)
        self.client.post(reverse("boq:create_revision", args=[self.project.pk, self.original.pk]))
        revision = self.project.boqs.get(version_number=2)

        response = self.client.post(reverse("boq:approve_boq", args=[self.project.pk, revision.pk]))
        self.assertEqual(response.status_code, 403)
        revision.refresh_from_db()
        self.assertEqual(revision.status, BOQ.STATUS_DRAFT)

    def test_boq_list_shows_every_version(self):
        self.client.force_login(self.qs_user)
        self.client.post(reverse("boq:create_revision", args=[self.project.pk, self.original.pk]))
        response = self.client.get(reverse("boq:boq_list", args=[self.project.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "v1")
        self.assertContains(response, "v2")


class VariationOrderTests(TestCase):
    def setUp(self):
        company = Company.objects.create(name="Test Contractor Ltd")
        self.project = Project.objects.create(company=company, code="VO-1", name="VO test")
        self.qs_user = User.objects.create_user(username="qs", password="pw")
        ProjectMembership.objects.create(user=self.qs_user, project=self.project, role=ROLE_QS)
        self.viewer_user = User.objects.create_user(username="viewer", password="pw")
        ProjectMembership.objects.create(user=self.viewer_user, project=self.project, role=ROLE_VIEWER)
        self.m3 = UnitOfMeasure.objects.get(code="m³")
        self.original = BOQ.objects.create(
            project=self.project, version_number=1, type=BOQ.TYPE_ORIGINAL, status=BOQ.STATUS_APPROVED
        )
        self.bill = Bill.objects.create(boq=self.original, number=4, title="Sub-base and Base")
        BOQItem.objects.create(
            bill=self.bill,
            item_reference="4.02",
            description="Sub-base, 150mm",
            item_type=BOQItem.TYPE_MEASURED,
            unit=self.m3,
            quantity=Decimal("1000"),
            rate=Decimal("100.00"),
        )

    def test_creating_a_vo_without_an_approved_boq_is_refused(self):
        self.original.status = BOQ.STATUS_DRAFT
        self.original.save()
        self.client.force_login(self.qs_user)
        response = self.client.post(
            reverse("boq:vo_list", args=[self.project.pk]),
            {"date": "2026-01-01", "description": "Add culvert", "reason": "Client request", "instructed_by": "Eng. Smith"},
        )
        self.assertRedirects(response, reverse("boq:vo_list", args=[self.project.pk]))
        self.assertEqual(VariationOrder.objects.count(), 0)

    def test_creating_a_vo_copies_the_approved_boq_into_a_new_draft(self):
        self.client.force_login(self.qs_user)
        response = self.client.post(
            reverse("boq:vo_list", args=[self.project.pk]),
            {"date": "2026-01-01", "description": "Add culvert", "reason": "Client request", "instructed_by": "Eng. Smith"},
        )
        vo = VariationOrder.objects.get()
        self.assertRedirects(
            response, reverse("boq:boq_version_detail", args=[self.project.pk, vo.linked_boq.pk])
        )
        self.assertEqual(vo.number, 1)
        self.assertEqual(vo.base_boq, self.original)
        self.assertEqual(vo.linked_boq.type, BOQ.TYPE_VARIATION)
        self.assertEqual(vo.linked_boq.status, BOQ.STATUS_DRAFT)
        self.assertEqual(vo.linked_boq.bills.get(number=4).items.get(item_reference="4.02").amount, Decimal("100000.00"))
        self.assertEqual(vo.value_impact, Decimal("0.00"))  # nothing changed yet

    def test_vo_value_impact_reflects_edits_made_on_its_version(self):
        self.client.force_login(self.qs_user)
        self.client.post(
            reverse("boq:vo_list", args=[self.project.pk]),
            {"date": "2026-01-01", "description": "Add culvert", "reason": "Client request", "instructed_by": "Eng. Smith"},
        )
        vo = VariationOrder.objects.get()
        BOQItem.objects.create(
            bill=vo.linked_boq.bills.get(number=4),
            item_reference="4.03",
            description="New culvert pipe",
            item_type=BOQItem.TYPE_MEASURED,
            unit=self.m3,
            quantity=Decimal("10"),
            rate=Decimal("500.00"),
        )
        self.assertEqual(vo.value_impact, Decimal("5000.00"))

    def test_approving_the_vos_boq_also_approves_the_vo(self):
        self.client.force_login(self.qs_user)
        self.client.post(
            reverse("boq:vo_list", args=[self.project.pk]),
            {"date": "2026-01-01", "description": "Add culvert", "reason": "Client request", "instructed_by": "Eng. Smith"},
        )
        vo = VariationOrder.objects.get()
        vo.linked_boq.approve(self.qs_user)  # approve() itself doesn't check role -- the view does
        vo.refresh_from_db()
        self.assertEqual(vo.status, VariationOrder.STATUS_APPROVED)

    def test_viewer_cannot_create_a_vo(self):
        self.client.force_login(self.viewer_user)
        response = self.client.post(
            reverse("boq:vo_list", args=[self.project.pk]),
            {"date": "2026-01-01", "description": "Add culvert", "reason": "", "instructed_by": ""},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(VariationOrder.objects.count(), 0)

    def test_vo_numbers_increment_per_project(self):
        self.client.force_login(self.qs_user)
        self.client.post(
            reverse("boq:vo_list", args=[self.project.pk]),
            {"date": "2026-01-01", "description": "First VO", "reason": "", "instructed_by": ""},
        )
        first_vo = VariationOrder.objects.get(description="First VO")
        first_vo.linked_boq.approve(self.qs_user)
        self.client.post(
            reverse("boq:vo_list", args=[self.project.pk]),
            {"date": "2026-01-02", "description": "Second VO", "reason": "", "instructed_by": ""},
        )
        second_vo = VariationOrder.objects.get(description="Second VO")
        self.assertEqual(first_vo.number, 1)
        self.assertEqual(second_vo.number, 2)


class CompareVersionsTests(TestCase):
    def setUp(self):
        company = Company.objects.create(name="Test Contractor Ltd")
        self.project = Project.objects.create(company=company, code="CMP-1", name="Compare test")
        self.qs_user = User.objects.create_user(username="qs", password="pw")
        ProjectMembership.objects.create(user=self.qs_user, project=self.project, role=ROLE_QS)
        self.m3 = UnitOfMeasure.objects.get(code="m³")

        self.v1 = BOQ.objects.create(project=self.project, version_number=1, status=BOQ.STATUS_APPROVED)
        bill1 = Bill.objects.create(boq=self.v1, number=4, title="Sub-base and Base")
        BOQItem.objects.create(
            bill=bill1, item_reference="4.01", description="Kept the same", item_type=BOQItem.TYPE_MEASURED,
            unit=self.m3, quantity=Decimal("10"), rate=Decimal("100.00"),
        )
        BOQItem.objects.create(
            bill=bill1, item_reference="4.02", description="Will change", item_type=BOQItem.TYPE_MEASURED,
            unit=self.m3, quantity=Decimal("10"), rate=Decimal("100.00"),
        )
        BOQItem.objects.create(
            bill=bill1, item_reference="4.03", description="Will be removed", item_type=BOQItem.TYPE_MEASURED,
            unit=self.m3, quantity=Decimal("10"), rate=Decimal("100.00"),
        )

        self.v2 = self.v1.create_revision()
        bill2 = self.v2.bills.get(number=4)
        bill2.items.get(item_reference="4.02").delete()
        BOQItem.objects.create(
            bill=bill2, item_reference="4.02", description="Changed rate", item_type=BOQItem.TYPE_MEASURED,
            unit=self.m3, quantity=Decimal("10"), rate=Decimal("150.00"),
        )
        bill2.items.get(item_reference="4.03").delete()
        BOQItem.objects.create(
            bill=bill2, item_reference="4.04", description="A brand new item", item_type=BOQItem.TYPE_MEASURED,
            unit=self.m3, quantity=Decimal("5"), rate=Decimal("20.00"),
        )

    def test_compare_reports_added_removed_and_changed_items_with_value_difference(self):
        self.client.force_login(self.qs_user)
        response = self.client.get(
            reverse("boq:compare_versions", args=[self.project.pk]),
            {"a": self.v1.pk, "b": self.v2.pk},
        )
        self.assertEqual(response.status_code, 200)
        rows_by_ref = {row["reference"]: row for row in response.context["rows"]}

        self.assertEqual(rows_by_ref["4.01"]["status"], "unchanged")
        self.assertEqual(rows_by_ref["4.02"]["status"], "changed")
        self.assertEqual(rows_by_ref["4.02"]["value_difference"], Decimal("500.00"))
        self.assertEqual(rows_by_ref["4.03"]["status"], "removed")
        self.assertEqual(rows_by_ref["4.03"]["value_difference"], Decimal("-1000.00"))
        self.assertEqual(rows_by_ref["4.04"]["status"], "added")
        self.assertEqual(rows_by_ref["4.04"]["value_difference"], Decimal("100.00"))

        # v1 grand total: 1000 + 1000 + 1000 = 3000. v2: 1000 + 1500 + 100 = 2600.
        self.assertEqual(response.context["value_difference"], Decimal("-400.00"))

    def test_compare_without_versions_selected_just_shows_the_picker(self):
        self.client.force_login(self.qs_user)
        response = self.client.get(reverse("boq:compare_versions", args=[self.project.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("rows", response.context)
