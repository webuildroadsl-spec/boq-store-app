"""
Data-level tests for step 10's reports and dashboard (Section 7.1).
Each function in `reportdata.py` is tested against figures worked out
by hand, the same way every other step's model tests here check a
formula against its own acceptance test rather than just "it ran."

`test_views.py` covers the HTTP layer: permissions, the screen/export
dispatch, and rendering.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from boq.models import BOQ, Bill, BOQItem
from core.models import Company, Project, UnitOfMeasure
from store.models import (
    GRN,
    GRNLine,
    Issue,
    IssueLine,
    ItemCategory,
    MaterialAllowance,
    Store,
    StockMovement,
    StoreItem,
    Supplier,
)

from . import reportdata

User = get_user_model()


class BOQSummaryDataTests(TestCase):
    def setUp(self):
        company = Company.objects.create(name="Test Contractor Ltd")
        self.project = Project.objects.create(company=company, code="RPT-1", name="Reports test 1")
        self.boq = BOQ.objects.create(
            project=self.project,
            version_number=1,
            status=BOQ.STATUS_APPROVED,
            contingency_percent=Decimal("5"),
            tax_percent=Decimal("15"),
        )
        bill = Bill.objects.create(boq=self.boq, number=1, title="Earthworks")
        BOQItem.objects.create(
            bill=bill,
            item_reference="1.01",
            description="Clear site",
            item_type=BOQItem.TYPE_MEASURED,
            unit=UnitOfMeasure.objects.get(code="m²"),
            quantity=Decimal("1000"),
            rate=Decimal("10.00"),
        )

    def test_subtotal_contingency_tax_and_grand_total(self):
        data = reportdata.boq_summary_data(self.boq)
        self.assertEqual(data["subtotal"], Decimal("10000.00"))
        # 5% of 10,000.00 = 500.00
        self.assertEqual(data["contingency_amount"], Decimal("500.00"))
        # 15% of (10,000.00 + 500.00) = 1,575.00
        self.assertEqual(data["tax_amount"], Decimal("1575.00"))
        self.assertEqual(data["grand_total"], Decimal("12075.00"))

    def test_grand_total_unchanged_when_no_contingency_or_tax_set(self):
        plain_boq = BOQ.objects.create(project=self.project, version_number=2)
        Bill.objects.create(boq=plain_boq, number=1, title="Empty bill")
        data = reportdata.boq_summary_data(plain_boq)
        self.assertEqual(data["grand_total"], Decimal("0.00"))


class BOQComparisonDataTests(TestCase):
    def setUp(self):
        company = Company.objects.create(name="Test Contractor Ltd")
        self.project = Project.objects.create(company=company, code="RPT-2", name="Reports test 2")
        self.boq_a = BOQ.objects.create(project=self.project, version_number=1, status=BOQ.STATUS_SUPERSEDED)
        bill_a = Bill.objects.create(boq=self.boq_a, number=1, title="Earthworks")
        BOQItem.objects.create(
            bill=bill_a, item_reference="1.01", description="Clear site", item_type=BOQItem.TYPE_MEASURED,
            unit=UnitOfMeasure.objects.get(code="m²"), quantity=Decimal("1000"), rate=Decimal("10.00"),
        )
        self.boq_b = BOQ.objects.create(project=self.project, version_number=2, status=BOQ.STATUS_APPROVED)
        bill_b = Bill.objects.create(boq=self.boq_b, number=1, title="Earthworks")
        BOQItem.objects.create(
            bill=bill_b, item_reference="1.01", description="Clear site", item_type=BOQItem.TYPE_MEASURED,
            unit=UnitOfMeasure.objects.get(code="m²"), quantity=Decimal("1200"), rate=Decimal("10.00"),
        )
        BOQItem.objects.create(
            bill=bill_b, item_reference="1.02", description="New item", item_type=BOQItem.TYPE_MEASURED,
            unit=UnitOfMeasure.objects.get(code="m²"), quantity=Decimal("100"), rate=Decimal("5.00"),
        )

    def test_added_and_changed_counts_and_value_difference(self):
        data = reportdata.boq_comparison_data(self.boq_a, self.boq_b)
        self.assertEqual(data["changed"], 1)  # 1.01: 10,000.00 -> 12,000.00
        self.assertEqual(data["added"], 1)  # 1.02
        self.assertEqual(data["removed"], 0)
        self.assertEqual(data["value_difference"], Decimal("2500.00"))  # 12,500 - 10,000


class StockReportDataTests(TestCase):
    def setUp(self):
        company = Company.objects.create(name="Test Contractor Ltd")
        self.project = Project.objects.create(company=company, code="RPT-3", name="Reports test 3")
        self.user = User.objects.create_user(username="reportkeeper", password="pw")
        self.store = Store.objects.create(project=self.project, code="MAIN", name="Main yard", storekeeper=self.user)
        category = ItemCategory.objects.create(name="Cement and Binders RPT")
        self.item = StoreItem.objects.create(
            code="CEM-RPT", name="Cement", category=category, unit=UnitOfMeasure.objects.get(code="t"),
            minimum_stock_level=Decimal("50"), reorder_quantity=Decimal("100"),
        )

    def test_stock_balance_as_of_a_past_date_excludes_later_movements(self):
        first = StockMovement.objects.create(
            store=self.store, item=self.item, quantity=Decimal("100"), unit_cost=Decimal("150.00"),
            document_type=StockMovement.DOCUMENT_GRN, document_id=1, created_by=self.user,
        )
        first.created_at = timezone.now() - timedelta(days=10)
        first.save(update_fields=["created_at"])

        second = StockMovement.objects.create(
            store=self.store, item=self.item, quantity=Decimal("50"), unit_cost=Decimal("150.00"),
            document_type=StockMovement.DOCUMENT_GRN, document_id=2, created_by=self.user,
        )
        second.created_at = timezone.now() - timedelta(days=2)
        second.save(update_fields=["created_at"])

        as_of_before_second = date.today() - timedelta(days=5)
        data = reportdata.stock_balance_data([self.store], as_of_before_second)
        self.assertEqual(len(data["rows"]), 1)
        self.assertEqual(data["rows"][0]["quantity"], Decimal("100.000"))

        data_today = reportdata.stock_balance_data([self.store], date.today())
        self.assertEqual(data_today["rows"][0]["quantity"], Decimal("150.000"))

    def test_stock_ledger_running_balance(self):
        StockMovement.objects.create(
            store=self.store, item=self.item, quantity=Decimal("100"), unit_cost=Decimal("150.00"),
            document_type=StockMovement.DOCUMENT_GRN, document_id=1, created_by=self.user,
        )
        StockMovement.objects.create(
            store=self.store, item=self.item, quantity=Decimal("-30"), unit_cost=Decimal("150.00"),
            document_type=StockMovement.DOCUMENT_ISSUE, document_id=1, created_by=self.user,
        )
        data = reportdata.stock_ledger_data(self.store, self.item)
        self.assertEqual([row["running_quantity"] for row in data["rows"]], [Decimal("100.000"), Decimal("70.000")])

    def test_reorder_alert_shows_items_below_minimum(self):
        StockMovement.objects.create(
            store=self.store, item=self.item, quantity=Decimal("30"), unit_cost=Decimal("150.00"),
            document_type=StockMovement.DOCUMENT_GRN, document_id=1, created_by=self.user,
        )
        data = reportdata.reorder_alert_data([self.store])
        self.assertEqual(len(data["rows"]), 1)
        self.assertEqual(data["rows"][0]["item"], self.item)
        self.assertEqual(data["rows"][0]["quantity"], Decimal("30.000"))

    def test_reorder_alert_omits_items_at_or_above_minimum(self):
        StockMovement.objects.create(
            store=self.store, item=self.item, quantity=Decimal("60"), unit_cost=Decimal("150.00"),
            document_type=StockMovement.DOCUMENT_GRN, document_id=1, created_by=self.user,
        )
        data = reportdata.reorder_alert_data([self.store])
        self.assertEqual(data["rows"], [])

    def test_grn_register_only_lists_posted_grns(self):
        supplier = Supplier.objects.create(name="ACME")
        posted = GRN.objects.create(number=1, date="2026-04-01", store=self.store, supplier=supplier, received_by=self.user)
        GRNLine.objects.create(grn=posted, item=self.item, quantity=Decimal("10"), unit_cost=Decimal("150.00"))
        posted.post(self.user)
        GRN.objects.create(number=2, date="2026-04-02", store=self.store, supplier=supplier, received_by=self.user)

        data = reportdata.grn_register_data([self.store])
        self.assertEqual(list(data["rows"]), [posted])


class DashboardDataTests(TestCase):
    def setUp(self):
        company = Company.objects.create(name="Test Contractor Ltd")
        self.project = Project.objects.create(company=company, code="RPT-4", name="Reports test 4")
        self.user = User.objects.create_user(username="reportkeeper4", password="pw")
        self.store = Store.objects.create(project=self.project, code="MAIN", name="Main yard", storekeeper=self.user)
        category = ItemCategory.objects.create(name="Cement and Binders RPT4")
        self.item = StoreItem.objects.create(
            code="CEM-RPT4", name="Cement", category=category, unit=UnitOfMeasure.objects.get(code="t"),
        )
        self.boq = BOQ.objects.create(project=self.project, version_number=1, status=BOQ.STATUS_APPROVED)
        bill = Bill.objects.create(boq=self.boq, number=1, title="Concrete works")
        self.boq_item = BOQItem.objects.create(
            bill=bill, item_reference="4.02", description="Class 20 concrete", item_type=BOQItem.TYPE_MEASURED,
            unit=UnitOfMeasure.objects.get(code="m³"), quantity=Decimal("100"), rate=Decimal("50.00"),
        )

    def test_dashboard_reports_boq_total_stock_value_and_over_allowance(self):
        StockMovement.objects.create(
            store=self.store, item=self.item, quantity=Decimal("200"), unit_cost=Decimal("150.00"),
            document_type=StockMovement.DOCUMENT_GRN, document_id=1, created_by=self.user,
        )
        MaterialAllowance.objects.create(
            boq_item=self.boq_item, store_item=self.item, quantity_per_unit=Decimal("0.32"), wastage_percent=Decimal("5")
        )
        issue = Issue.objects.create(number=1, date=date.today(), store=self.store, issued_to="Crew")
        IssueLine.objects.create(issue=issue, item=self.item, quantity=Decimal("36"), boq_item=self.boq_item)
        issue.post(self.user, over_allowance_reason="Extra for wider wall")
        issue.approve_over_allowance(User.objects.create_user(username="reportpm4", password="pw"))

        data = reportdata.dashboard_data(self.project, can_see_boq=True, stores=[self.store])
        self.assertEqual(data["boq_grand_total"], self.boq.final_total)
        self.assertEqual(data["stock_value"], Decimal("24600.00"))  # 164 t left @ 150.00
        self.assertEqual(len(data["top_over_allowance"]), 1)
        self.assertEqual(data["top_over_allowance"][0]["variance"], Decimal("2.400"))
        self.assertEqual(data["value_issued_this_month"], Decimal("5400.00"))  # 36 t @ 150.00

    def test_dashboard_skips_boq_total_when_caller_cannot_see_boq(self):
        data = reportdata.dashboard_data(self.project, can_see_boq=False, stores=[self.store])
        self.assertIsNone(data["boq_grand_total"])
