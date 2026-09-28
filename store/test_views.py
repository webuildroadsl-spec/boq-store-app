"""
View and permission tests for step 6: recording, editing and posting a
GRN through the Django test client, and the store-visibility scoping
("Own store" for a Storekeeper) from store.permissions.

Step 7's view/permission tests (requisitions, issues, returns) follow
further down, and step 8's (transfer, stock count, reversal) after
that.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from boq.models import BOQ, Bill, BOQItem
from core.models import (
    Company,
    Project,
    ProjectMembership,
    ROLE_PROJECT_MANAGER,
    ROLE_QS,
    ROLE_SITE_ENGINEER,
    ROLE_STOREKEEPER,
    ROLE_VIEWER,
    Section,
    UnitOfMeasure,
)

from .models import (
    GRN,
    Issue,
    ItemCategory,
    ReturnToStore,
    StockCount,
    Store,
    StockMovement,
    StoreItem,
    StoreRequisition,
    Supplier,
    Transfer,
)

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


class RequisitionIssueReturnViewsTestCase(TestCase):
    """Step 7: requisitions (project-scoped), issues and returns
    (store-scoped), through the Django test client."""

    def setUp(self):
        company = Company.objects.create(name="Test Contractor Ltd")
        self.project = Project.objects.create(company=company, code="STOREV-2", name="Store views test 2")
        self.section = Section.objects.create(
            project=self.project, code="S1", start_chainage="0.000", end_chainage="2.500"
        )

        self.storekeeper = User.objects.create_user(username="keeper", password="pw")
        self.pm_user = User.objects.create_user(username="pm2", password="pw")
        self.site_engineer = User.objects.create_user(username="site_eng", password="pw")
        self.qs_user = User.objects.create_user(username="qs", password="pw")

        ProjectMembership.objects.create(user=self.storekeeper, project=self.project, role=ROLE_STOREKEEPER)
        ProjectMembership.objects.create(user=self.pm_user, project=self.project, role=ROLE_PROJECT_MANAGER)
        ProjectMembership.objects.create(user=self.site_engineer, project=self.project, role=ROLE_SITE_ENGINEER)

        self.store = Store.objects.create(
            project=self.project, code="MAIN", name="Main yard", storekeeper=self.storekeeper
        )
        category = ItemCategory.objects.create(name="Cement and Binders")
        self.item = StoreItem.objects.create(
            code="CEM-01", name="Cement, 50kg bag", category=category, unit=UnitOfMeasure.objects.get(code="t")
        )

        boq = BOQ.objects.create(project=self.project, version_number=1, status=BOQ.STATUS_APPROVED)
        bill = Bill.objects.create(boq=boq, number=1, title="Earthworks")
        self.boq_item = BOQItem.objects.create(
            bill=bill,
            item_reference="1.01",
            description="Supply and lay cement",
            item_type=BOQItem.TYPE_MEASURED,
            unit=UnitOfMeasure.objects.get(code="t"),
            quantity=Decimal("1000"),
            rate=Decimal("10.00"),
        )

        StockMovement.objects.create(
            store=self.store,
            item=self.item,
            quantity=Decimal("200"),
            unit_cost=Decimal("150.00"),
            document_type=StockMovement.DOCUMENT_GRN,
            document_id=1,
            created_by=self.storekeeper,
        )

    def test_project_manager_can_create_a_requisition(self):
        self.client.force_login(self.pm_user)
        response = self.client.post(
            reverse("store:requisition_create", args=[self.project.pk]),
            {"date": "2026-02-01", "section": self.section.pk},
        )
        requisition = StoreRequisition.objects.get(project=self.project)
        self.assertRedirects(
            response, reverse("store:requisition_detail", args=[self.project.pk, requisition.pk])
        )
        self.assertEqual(requisition.requested_by, self.pm_user)
        self.assertEqual(requisition.status, StoreRequisition.STATUS_PENDING)

    def test_qs_cannot_create_a_requisition(self):
        ProjectMembership.objects.create(user=self.qs_user, project=self.project, role=ROLE_QS)
        self.client.force_login(self.qs_user)
        response = self.client.post(
            reverse("store:requisition_create", args=[self.project.pk]),
            {"date": "2026-02-01", "section": self.section.pk},
        )
        self.assertEqual(response.status_code, 403)

    def test_storekeeper_cannot_create_a_requisition(self):
        self.client.force_login(self.storekeeper)
        response = self.client.post(
            reverse("store:requisition_create", args=[self.project.pk]),
            {"date": "2026-02-01", "section": self.section.pk},
        )
        self.assertEqual(response.status_code, 403)

    def _create_requisition(self):
        requisition = StoreRequisition.objects.create(
            number=1, date="2026-02-01", project=self.project, section=self.section, requested_by=self.pm_user
        )
        return requisition

    def test_project_manager_can_reject_a_requisition(self):
        requisition = self._create_requisition()
        self.client.force_login(self.pm_user)
        response = self.client.post(
            reverse("store:requisition_detail", args=[self.project.pk, requisition.pk]), {"reject": "1"}
        )
        self.assertRedirects(
            response, reverse("store:requisition_detail", args=[self.project.pk, requisition.pk])
        )
        requisition.refresh_from_db()
        self.assertEqual(requisition.status, StoreRequisition.STATUS_REJECTED)

    def _create_issue(self):
        response = self.client.post(
            reverse("store:issue_create", args=[self.project.pk, self.store.pk]),
            {"date": "2026-02-01", "issued_to": "Site crew A"},
        )
        return response

    def test_storekeeper_can_create_and_post_an_issue(self):
        self.client.force_login(self.storekeeper)
        create_response = self._create_issue()
        issue = Issue.objects.get(store=self.store)
        self.assertRedirects(
            create_response, reverse("store:issue_detail", args=[self.project.pk, self.store.pk, issue.pk])
        )

        self.client.post(
            reverse("store:issue_detail", args=[self.project.pk, self.store.pk, issue.pk]),
            {"item": self.item.pk, "quantity": "200", "boq_item": self.boq_item.pk},
        )
        self.assertEqual(issue.lines.count(), 1)

        post_response = self.client.post(
            reverse("store:issue_post", args=[self.project.pk, self.store.pk, issue.pk])
        )
        self.assertRedirects(
            post_response, reverse("store:issue_detail", args=[self.project.pk, self.store.pk, issue.pk])
        )
        issue.refresh_from_db()
        self.assertEqual(issue.status, Issue.STATUS_POSTED)
        quantity, _, _ = StockMovement.current_balance(self.store, self.item)
        self.assertEqual(quantity, Decimal("0.000"))

    def test_issuing_250_when_stock_is_200_is_blocked_and_shows_an_error(self):
        self.client.force_login(self.storekeeper)
        self._create_issue()
        issue = Issue.objects.get(store=self.store)
        self.client.post(
            reverse("store:issue_detail", args=[self.project.pk, self.store.pk, issue.pk]),
            {"item": self.item.pk, "quantity": "250", "boq_item": self.boq_item.pk},
        )
        response = self.client.post(
            reverse("store:issue_post", args=[self.project.pk, self.store.pk, issue.pk]), follow=True
        )
        self.assertContains(response, "exceed")
        issue.refresh_from_db()
        self.assertEqual(issue.status, Issue.STATUS_DRAFT)

    def test_issue_line_form_rejects_a_line_with_no_boq_item(self):
        self.client.force_login(self.storekeeper)
        self._create_issue()
        issue = Issue.objects.get(store=self.store)
        response = self.client.post(
            reverse("store:issue_detail", args=[self.project.pk, self.store.pk, issue.pk]),
            {"item": self.item.pk, "quantity": "10", "boq_item": ""},
        )
        # The form re-renders with an error rather than redirecting --
        # the line-required-field is the "issue without a BOQ item is
        # blocked" rule enforced before post() is ever reached.
        self.assertEqual(response.status_code, 200)
        self.assertEqual(issue.lines.count(), 0)

    def test_pm_cannot_manage_issues_for_a_store_they_dont_keep(self):
        self.client.force_login(self.pm_user)
        response = self.client.post(
            reverse("store:issue_create", args=[self.project.pk, self.store.pk]),
            {"date": "2026-02-01", "issued_to": "Site crew A"},
        )
        self.assertEqual(response.status_code, 403)

    def test_storekeeper_can_create_and_post_a_return(self):
        self.client.force_login(self.storekeeper)
        self._create_issue()
        issue = Issue.objects.get(store=self.store)
        self.client.post(
            reverse("store:issue_detail", args=[self.project.pk, self.store.pk, issue.pk]),
            {"item": self.item.pk, "quantity": "150", "boq_item": self.boq_item.pk},
        )
        self.client.post(reverse("store:issue_post", args=[self.project.pk, self.store.pk, issue.pk]))

        create_response = self.client.post(
            reverse("store:return_create", args=[self.project.pk, self.store.pk]),
            {"date": "2026-02-05", "linked_issue": issue.pk},
        )
        ret = ReturnToStore.objects.get(store=self.store)
        self.assertRedirects(
            create_response, reverse("store:return_detail", args=[self.project.pk, self.store.pk, ret.pk])
        )

        self.client.post(
            reverse("store:return_detail", args=[self.project.pk, self.store.pk, ret.pk]),
            {"item": self.item.pk, "quantity": "50", "condition": "good", "boq_item": self.boq_item.pk},
        )
        post_response = self.client.post(
            reverse("store:return_post", args=[self.project.pk, self.store.pk, ret.pk])
        )
        self.assertRedirects(
            post_response, reverse("store:return_detail", args=[self.project.pk, self.store.pk, ret.pk])
        )
        ret.refresh_from_db()
        self.assertEqual(ret.status, ReturnToStore.STATUS_POSTED)
        quantity, _, _ = StockMovement.current_balance(self.store, self.item)
        self.assertEqual(quantity, Decimal("100.000"))


class TransferStockCountReversalViewsTestCase(TestCase):
    """Step 8's acceptance test over the Django test client: "Stock in
    transit counts in neither store; posted GRN cannot be edited."
    Plus stock count approval and GRN/issue reversal."""

    def setUp(self):
        company = Company.objects.create(name="Test Contractor Ltd")
        self.project = Project.objects.create(company=company, code="STOREV-3", name="Store views test 3")

        self.keeper = User.objects.create_user(username="keeper3", password="pw")
        self.pm = User.objects.create_user(username="pm3", password="pw")
        ProjectMembership.objects.create(user=self.keeper, project=self.project, role=ROLE_STOREKEEPER)
        ProjectMembership.objects.create(user=self.pm, project=self.project, role=ROLE_PROJECT_MANAGER)

        self.store_a = Store.objects.create(
            project=self.project, code="A", name="Store A", storekeeper=self.keeper
        )
        self.store_b = Store.objects.create(
            project=self.project, code="B", name="Store B", storekeeper=self.keeper
        )
        category = ItemCategory.objects.create(name="Cement and Binders")
        self.item = StoreItem.objects.create(
            code="CEM-01", name="Cement, 50kg bag", category=category, unit=UnitOfMeasure.objects.get(code="t")
        )
        self.supplier = Supplier.objects.create(name="ACME Building Supplies")

        StockMovement.objects.create(
            store=self.store_a,
            item=self.item,
            quantity=Decimal("200"),
            unit_cost=Decimal("150.00"),
            document_type=StockMovement.DOCUMENT_GRN,
            document_id=1,
            created_by=self.keeper,
        )

    def test_stock_in_transit_counts_in_neither_store_over_http(self):
        self.client.force_login(self.keeper)
        self.client.post(
            reverse("store:transfer_create", args=[self.project.pk]),
            {"date": "2026-03-01", "from_store": self.store_a.pk, "to_store": self.store_b.pk},
        )
        transfer = Transfer.objects.get(project=self.project)
        self.client.post(
            reverse("store:transfer_detail", args=[self.project.pk, transfer.pk]),
            {"item": self.item.pk, "quantity": "80"},
        )
        self.client.post(reverse("store:transfer_dispatch", args=[self.project.pk, transfer.pk]))
        transfer.refresh_from_db()
        self.assertEqual(transfer.status, Transfer.STATUS_IN_TRANSIT)

        a_quantity, _, _ = StockMovement.current_balance(self.store_a, self.item)
        b_quantity, _, _ = StockMovement.current_balance(self.store_b, self.item)
        self.assertEqual(a_quantity, Decimal("120.000"))
        self.assertEqual(b_quantity, Decimal("0.000"))

        response = self.client.post(
            reverse("store:transfer_receive", args=[self.project.pk, transfer.pk]), follow=True
        )
        self.assertContains(response, "received")
        transfer.refresh_from_db()
        self.assertEqual(transfer.status, Transfer.STATUS_RECEIVED)
        b_quantity, _, _ = StockMovement.current_balance(self.store_b, self.item)
        self.assertEqual(b_quantity, Decimal("80.000"))

    def test_posted_grn_cannot_be_edited_over_http(self):
        self.client.force_login(self.keeper)
        self.client.post(
            reverse("store:grn_create", args=[self.project.pk, self.store_a.pk]),
            {"date": "2026-03-01", "supplier": self.supplier.pk},
        )
        grn = GRN.objects.get(store=self.store_a)
        self.client.post(
            reverse("store:grn_detail", args=[self.project.pk, self.store_a.pk, grn.pk]),
            {"item": self.item.pk, "quantity": "50", "unit_cost": "150.00"},
        )
        self.client.post(reverse("store:grn_post", args=[self.project.pk, self.store_a.pk, grn.pk]))
        grn.refresh_from_db()
        self.assertEqual(grn.status, GRN.STATUS_POSTED)

        edit_response = self.client.post(
            reverse("store:grn_detail", args=[self.project.pk, self.store_a.pk, grn.pk]),
            {"item": self.item.pk, "quantity": "5", "unit_cost": "1.00"},
        )
        self.assertEqual(edit_response.status_code, 403)

        # The correction path is a reversal, not an edit.
        reverse_response = self.client.post(
            reverse("store:grn_reverse", args=[self.project.pk, self.store_a.pk, grn.pk]),
            {"reason": "Wrong quantity delivered"},
            follow=True,
        )
        self.assertContains(reverse_response, "reversed")
        grn.refresh_from_db()
        self.assertEqual(grn.status, GRN.STATUS_POSTED)  # still Posted -- the original is untouched

    def test_stock_count_approval_requires_a_project_manager_not_the_counter(self):
        self.client.force_login(self.keeper)
        self.client.post(
            reverse("store:stock_count_create", args=[self.project.pk, self.store_a.pk]),
            {"date": "2026-03-05"},
        )
        stock_count = StockCount.objects.get(store=self.store_a)
        self.client.post(
            reverse("store:stock_count_detail", args=[self.project.pk, self.store_a.pk, stock_count.pk]),
            {"item": self.item.pk, "counted_quantity": "190", "reason": "Spillage"},
        )
        self.client.post(
            reverse("store:stock_count_detail", args=[self.project.pk, self.store_a.pk, stock_count.pk]),
            {"submit": "1"},
        )
        stock_count.refresh_from_db()
        self.assertEqual(stock_count.status, StockCount.STATUS_SUBMITTED)

        # The Storekeeper who counted cannot approve it themselves.
        denied_response = self.client.post(
            reverse("store:stock_count_approve", args=[self.project.pk, self.store_a.pk, stock_count.pk])
        )
        self.assertEqual(denied_response.status_code, 403)

        self.client.force_login(self.pm)
        approve_response = self.client.post(
            reverse("store:stock_count_approve", args=[self.project.pk, self.store_a.pk, stock_count.pk]),
            follow=True,
        )
        self.assertContains(approve_response, "approved")
        stock_count.refresh_from_db()
        self.assertEqual(stock_count.status, StockCount.STATUS_APPROVED)
        self.assertEqual(stock_count.approved_by, self.pm)
        quantity, _, _ = StockMovement.current_balance(self.store_a, self.item)
        self.assertEqual(quantity, Decimal("190.000"))
