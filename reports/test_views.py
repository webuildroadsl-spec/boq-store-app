"""
View-level tests for step 10's dashboard and reports: permission
gating per Section 2's "View reports and dashboards" row (everyone
Yes except a Storekeeper, who is scoped to "Own store"), and the
`?format=xlsx`/`?format=pdf` export dispatch actually returning the
right content type.
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
    ROLE_STOREKEEPER,
    ROLE_VIEWER,
    UnitOfMeasure,
)
from store.models import GRN, GRNLine, ItemCategory, Store, StockMovement, StoreItem, Supplier

User = get_user_model()


class ReportsViewsTestCase(TestCase):
    def setUp(self):
        company = Company.objects.create(name="Test Contractor Ltd")
        self.project = Project.objects.create(company=company, code="RPTV-1", name="Reports views test")

        self.pm_user = User.objects.create_user(username="reportpm", password="pw")
        self.viewer_user = User.objects.create_user(username="reportviewer", password="pw")
        self.storekeeper_a = User.objects.create_user(username="reportkeeper_a", password="pw")
        self.storekeeper_b = User.objects.create_user(username="reportkeeper_b", password="pw")
        self.outsider = User.objects.create_user(username="reportoutsider", password="pw")

        ProjectMembership.objects.create(user=self.pm_user, project=self.project, role=ROLE_PROJECT_MANAGER)
        ProjectMembership.objects.create(user=self.viewer_user, project=self.project, role=ROLE_VIEWER)
        ProjectMembership.objects.create(user=self.storekeeper_a, project=self.project, role=ROLE_STOREKEEPER)
        ProjectMembership.objects.create(user=self.storekeeper_b, project=self.project, role=ROLE_STOREKEEPER)

        self.store_a = Store.objects.create(project=self.project, code="A", name="Store A", storekeeper=self.storekeeper_a)
        self.store_b = Store.objects.create(project=self.project, code="B", name="Store B", storekeeper=self.storekeeper_b)

        category = ItemCategory.objects.create(name="Cement and Binders RPTV")
        self.item = StoreItem.objects.create(
            code="CEM-RPTV", name="Cement", category=category, unit=UnitOfMeasure.objects.get(code="t")
        )
        StockMovement.objects.create(
            store=self.store_a, item=self.item, quantity=Decimal("100"), unit_cost=Decimal("150.00"),
            document_type=StockMovement.DOCUMENT_GRN, document_id=1, created_by=self.storekeeper_a,
        )
        StockMovement.objects.create(
            store=self.store_b, item=self.item, quantity=Decimal("50"), unit_cost=Decimal("150.00"),
            document_type=StockMovement.DOCUMENT_GRN, document_id=1, created_by=self.storekeeper_b,
        )

        self.boq = BOQ.objects.create(project=self.project, version_number=1, status=BOQ.STATUS_APPROVED)
        bill = Bill.objects.create(boq=self.boq, number=1, title="Earthworks")
        BOQItem.objects.create(
            bill=bill, item_reference="1.01", description="Clear site", item_type=BOQItem.TYPE_MEASURED,
            unit=UnitOfMeasure.objects.get(code="m²"), quantity=Decimal("1000"), rate=Decimal("10.00"),
        )

    def test_dashboard_shows_boq_total_and_project_wide_stock_value_to_a_pm(self):
        self.client.force_login(self.pm_user)
        response = self.client.get(reverse("reports:dashboard", args=[self.project.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "10000")  # BOQ grand total
        self.assertContains(response, "22500")  # 150 t @ 150.00 across both stores

    def test_dashboard_scopes_stock_value_to_a_storekeepers_own_store(self):
        self.client.force_login(self.storekeeper_a)
        response = self.client.get(reverse("reports:dashboard", args=[self.project.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "15000")  # only store A's 100 t @ 150.00
        self.assertNotContains(response, "22500")

    def test_outsider_gets_404_on_dashboard(self):
        self.client.force_login(self.outsider)
        response = self.client.get(reverse("reports:dashboard", args=[self.project.pk]))
        self.assertEqual(response.status_code, 404)

    def test_boq_summary_screen_and_exports(self):
        self.client.force_login(self.viewer_user)
        html_response = self.client.get(reverse("reports:boq_summary", args=[self.project.pk]))
        self.assertEqual(html_response.status_code, 200)
        self.assertContains(html_response, "10000")

        xlsx_response = self.client.get(reverse("reports:boq_summary", args=[self.project.pk]), {"format": "xlsx"})
        self.assertEqual(xlsx_response.status_code, 200)
        self.assertEqual(
            xlsx_response["Content-Type"], "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

        pdf_response = self.client.get(reverse("reports:boq_summary", args=[self.project.pk]), {"format": "pdf"})
        self.assertEqual(pdf_response.status_code, 200)
        self.assertEqual(pdf_response["Content-Type"], "application/pdf")

    def test_storekeeper_cannot_view_boq_summary(self):
        self.client.force_login(self.storekeeper_a)
        response = self.client.get(reverse("reports:boq_summary", args=[self.project.pk]))
        self.assertEqual(response.status_code, 403)

    def test_stock_balance_scoped_to_storekeepers_own_store(self):
        self.client.force_login(self.storekeeper_b)
        response = self.client.get(reverse("reports:stock_balance", args=[self.project.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<td>B</td>")  # store B's own row
        self.assertNotContains(response, "<td>A</td>")  # store A's row is not this storekeeper's to see

    def test_stock_ledger_running_balance_screen(self):
        self.client.force_login(self.pm_user)
        response = self.client.get(
            reverse("reports:stock_ledger", args=[self.project.pk]),
            {"store": self.store_a.pk, "item": self.item.pk},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "100.000")

    def test_reorder_alert_xlsx_export(self):
        self.client.force_login(self.pm_user)
        response = self.client.get(reverse("reports:reorder_alert", args=[self.project.pk]), {"format": "xlsx"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response["Content-Type"], "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    def test_grn_register_lists_posted_grns_and_exports_xlsx(self):
        supplier = Supplier.objects.create(name="ACME")
        grn = GRN.objects.create(number=1, date="2026-04-01", store=self.store_a, supplier=supplier, received_by=self.storekeeper_a)
        GRNLine.objects.create(grn=grn, item=self.item, quantity=Decimal("10"), unit_cost=Decimal("150.00"))
        grn.post(self.storekeeper_a)

        self.client.force_login(self.pm_user)
        response = self.client.get(reverse("reports:grn_register", args=[self.project.pk]))
        self.assertContains(response, str(grn.number))

        xlsx_response = self.client.get(reverse("reports:grn_register", args=[self.project.pk]), {"format": "xlsx"})
        self.assertEqual(
            xlsx_response["Content-Type"], "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    def test_material_reconciliation_screen_accessible_to_viewer(self):
        self.client.force_login(self.viewer_user)
        response = self.client.get(reverse("reports:material_reconciliation", args=[self.project.pk]))
        self.assertEqual(response.status_code, 200)

    def test_boq_comparison_screen_with_two_versions(self):
        second_boq = BOQ.objects.create(project=self.project, version_number=2)
        Bill.objects.create(boq=second_boq, number=1, title="Earthworks")
        self.client.force_login(self.pm_user)
        response = self.client.get(
            reverse("reports:boq_comparison", args=[self.project.pk]),
            {"a": self.boq.pk, "b": second_boq.pk},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "removed")
