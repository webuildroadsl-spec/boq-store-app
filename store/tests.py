"""
Model-level tests for step 6: the weighted-average stock balance
formula and GRN posting (Section 5, rules from Section 2: "Posted
documents ... cannot be edited or deleted").

Step 7 (requisition, issue, return) model tests follow further down,
covering the literal acceptance test: "Issuing 250 bags when stock is
200 is blocked; issue without a BOQ item is blocked."
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from boq.models import BOQ, Bill, BOQItem
from core.models import Company, Project, Section, UnitOfMeasure

from .models import (
    GRN,
    GRNLine,
    Issue,
    IssueLine,
    ItemCategory,
    RequisitionLine,
    ReturnLine,
    ReturnToStore,
    Store,
    StockMovement,
    StoreItem,
    StoreRequisition,
    Supplier,
)

User = get_user_model()


class StockBalanceTests(TestCase):
    def setUp(self):
        company = Company.objects.create(name="Test Contractor Ltd")
        self.project = Project.objects.create(company=company, code="STORE-1", name="Store test")
        self.user = User.objects.create_user(username="storekeeper", password="pw")
        self.store = Store.objects.create(
            project=self.project, code="MAIN", name="Main yard", storekeeper=self.user
        )
        category = ItemCategory.objects.create(name="Cement and Binders")
        self.item = StoreItem.objects.create(
            code="CEM-01", name="Cement, 50kg bag", category=category, unit=UnitOfMeasure.objects.get(code="t")
        )

    def _receive(self, quantity, unit_cost):
        StockMovement.objects.create(
            store=self.store,
            item=self.item,
            quantity=Decimal(quantity),
            unit_cost=Decimal(unit_cost),
            document_type=StockMovement.DOCUMENT_GRN,
            document_id=1,
            created_by=self.user,
        )

    def test_step6_acceptance_test_200_bags_at_150(self):
        """GRN of 200 bags cement at 150.00 shows stock 200, value 30,000.00."""
        self._receive("200", "150.00")
        quantity, value, average = StockMovement.current_balance(self.store, self.item)
        self.assertEqual(quantity, Decimal("200.000"))
        self.assertEqual(value, Decimal("30000.00"))
        self.assertEqual(average, Decimal("150.00"))

    def test_weighted_average_test(self):
        """100@150.00 then 100@170.00 -> average 160.00; issuing 50 leaves value 24,000.00."""
        self._receive("100", "150.00")
        self._receive("100", "170.00")
        quantity, value, average = StockMovement.current_balance(self.store, self.item)
        self.assertEqual(quantity, Decimal("200.000"))
        self.assertEqual(value, Decimal("32000.00"))
        self.assertEqual(average, Decimal("160.00"))

        # An issue posts at the average cost *at the time*, same as a
        # real issue (step 7) would -- this only exercises the balance
        # formula, not the issue workflow itself.
        self._receive("-50", "160.00")
        quantity, value, average = StockMovement.current_balance(self.store, self.item)
        self.assertEqual(quantity, Decimal("150.000"))
        self.assertEqual(value, Decimal("24000.00"))
        self.assertEqual(average, Decimal("160.00"))

    def test_balance_with_no_movements_is_zero(self):
        quantity, value, average = StockMovement.current_balance(self.store, self.item)
        self.assertEqual(quantity, Decimal("0.000"))
        self.assertEqual(value, Decimal("0.00"))
        self.assertEqual(average, Decimal("0.00"))

    def test_total_cost_is_computed_on_save(self):
        movement = StockMovement.objects.create(
            store=self.store, item=self.item, quantity=Decimal("10"), unit_cost=Decimal("12.345"),
            document_type=StockMovement.DOCUMENT_GRN, document_id=1, created_by=self.user,
        )
        self.assertEqual(movement.total_cost, Decimal("123.45"))


class GRNPostTests(TestCase):
    def setUp(self):
        company = Company.objects.create(name="Test Contractor Ltd")
        self.project = Project.objects.create(company=company, code="STORE-2", name="Store test 2")
        self.user = User.objects.create_user(username="storekeeper2", password="pw")
        self.store = Store.objects.create(
            project=self.project, code="MAIN", name="Main yard", storekeeper=self.user
        )
        category = ItemCategory.objects.create(name="Cement and Binders")
        self.item = StoreItem.objects.create(
            code="CEM-01", name="Cement, 50kg bag", category=category, unit=UnitOfMeasure.objects.get(code="t")
        )
        self.supplier = Supplier.objects.create(name="ACME Building Supplies")
        self.grn = GRN.objects.create(
            number=1, date="2026-01-15", store=self.store, supplier=self.supplier, received_by=self.user
        )

    def test_posting_creates_a_stock_movement_per_line(self):
        GRNLine.objects.create(grn=self.grn, item=self.item, quantity=Decimal("200"), unit_cost=Decimal("150.00"))
        self.grn.post(self.user)
        self.assertEqual(self.grn.status, GRN.STATUS_POSTED)
        movement = StockMovement.objects.get(document_type=StockMovement.DOCUMENT_GRN, document_id=self.grn.pk)
        self.assertEqual(movement.quantity, Decimal("200.000"))
        self.assertEqual(movement.total_cost, Decimal("30000.00"))
        quantity, value, average = StockMovement.current_balance(self.store, self.item)
        self.assertEqual(quantity, Decimal("200.000"))
        self.assertEqual(value, Decimal("30000.00"))

    def test_cannot_post_a_grn_with_no_lines(self):
        with self.assertRaises(ValidationError):
            self.grn.post(self.user)
        self.assertEqual(self.grn.status, GRN.STATUS_DRAFT)
        self.assertEqual(StockMovement.objects.count(), 0)

    def test_cannot_post_an_already_posted_grn(self):
        GRNLine.objects.create(grn=self.grn, item=self.item, quantity=Decimal("10"), unit_cost=Decimal("1.00"))
        self.grn.post(self.user)
        with self.assertRaises(ValidationError):
            self.grn.post(self.user)
        # Still exactly one movement -- it wasn't posted twice.
        self.assertEqual(StockMovement.objects.count(), 1)

    def test_grn_is_not_editable_once_posted(self):
        GRNLine.objects.create(grn=self.grn, item=self.item, quantity=Decimal("10"), unit_cost=Decimal("1.00"))
        self.assertTrue(self.grn.is_editable)
        self.grn.post(self.user)
        self.assertFalse(self.grn.is_editable)

    def test_grn_line_total_cost(self):
        line = GRNLine.objects.create(grn=self.grn, item=self.item, quantity=Decimal("3"), unit_cost=Decimal("2.505"))
        self.assertEqual(line.total_cost, Decimal("7.52"))  # 3 x 2.505 = 7.515 -> rounds up


class RequisitionIssueReturnTests(TestCase):
    """
    Step 7's acceptance test, at the model level: "Issuing 250 bags
    when stock is 200 is blocked; issue without a BOQ item is
    blocked." Plus the requisition status machine and return posting.
    """

    def setUp(self):
        company = Company.objects.create(name="Test Contractor Ltd")
        self.project = Project.objects.create(company=company, code="STORE-3", name="Store test 3")
        self.section = Section.objects.create(
            project=self.project, code="S1", start_chainage="0.000", end_chainage="2.500"
        )
        self.user = User.objects.create_user(username="storekeeper3", password="pw")
        self.store = Store.objects.create(
            project=self.project, code="MAIN", name="Main yard", storekeeper=self.user
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

        # Stock at exactly 200 bags, the literal acceptance-test figure.
        StockMovement.objects.create(
            store=self.store,
            item=self.item,
            quantity=Decimal("200"),
            unit_cost=Decimal("150.00"),
            document_type=StockMovement.DOCUMENT_GRN,
            document_id=1,
            created_by=self.user,
        )

    def _make_issue(self):
        return Issue.objects.create(
            number=1, date="2026-02-01", store=self.store, issued_to="Site crew A"
        )

    def test_issuing_250_when_stock_is_200_is_blocked(self):
        issue = self._make_issue()
        IssueLine.objects.create(issue=issue, item=self.item, quantity=Decimal("250"), boq_item=self.boq_item)
        with self.assertRaises(ValidationError) as ctx:
            issue.post(self.user)
        self.assertIn("exceed", " ".join(ctx.exception.messages))
        issue.refresh_from_db()
        self.assertEqual(issue.status, Issue.STATUS_DRAFT)
        self.assertEqual(StockMovement.objects.filter(document_type=StockMovement.DOCUMENT_ISSUE).count(), 0)

    def test_issuing_up_to_the_full_balance_is_allowed(self):
        issue = self._make_issue()
        IssueLine.objects.create(issue=issue, item=self.item, quantity=Decimal("200"), boq_item=self.boq_item)
        issue.post(self.user)
        issue.refresh_from_db()
        self.assertEqual(issue.status, Issue.STATUS_POSTED)
        quantity, _, _ = StockMovement.current_balance(self.store, self.item)
        self.assertEqual(quantity, Decimal("0.000"))

    def test_issue_without_a_boq_item_is_blocked_at_the_model_level(self):
        # IssueLine.boq_item has no null=True, so this fails before
        # post() is even reached -- the same rule the form and post()
        # both re-check.
        issue = self._make_issue()
        with self.assertRaises(Exception):
            IssueLine.objects.create(issue=issue, item=self.item, quantity=Decimal("10"), boq_item=None)

    # post()'s own `line.boq_item_id is None` check is defense-in-depth
    # for anything that might bypass the field/form layer (e.g. a
    # fixture or data migration) -- the database's NOT NULL constraint
    # on IssueLine.boq_item already makes it unreachable through the
    # ORM itself, as test_issue_without_a_boq_item_is_blocked_at_the_model_level
    # confirms.

    def test_issue_uses_current_average_cost_not_grn_cost(self):
        # A second GRN at a different cost shifts the average before
        # the issue is posted -- the issue should use the new average.
        StockMovement.objects.create(
            store=self.store,
            item=self.item,
            quantity=Decimal("200"),
            unit_cost=Decimal("170.00"),
            document_type=StockMovement.DOCUMENT_GRN,
            document_id=2,
            created_by=self.user,
        )
        issue = self._make_issue()
        IssueLine.objects.create(issue=issue, item=self.item, quantity=Decimal("100"), boq_item=self.boq_item)
        issue.post(self.user)
        movement = StockMovement.objects.get(document_type=StockMovement.DOCUMENT_ISSUE, document_id=issue.pk)
        self.assertEqual(movement.unit_cost, Decimal("160.00"))  # (200x150 + 200x170) / 400

    def test_requisition_status_recomputed_from_issues(self):
        requisition = StoreRequisition.objects.create(
            number=1, date="2026-02-01", project=self.project, section=self.section, requested_by=self.user
        )
        RequisitionLine.objects.create(
            requisition=requisition, item=self.item, quantity=Decimal("200"), boq_item=self.boq_item
        )
        self.assertEqual(requisition.status, StoreRequisition.STATUS_PENDING)

        issue = Issue.objects.create(
            number=1, date="2026-02-01", store=self.store, requisition=requisition, issued_to="Site crew A"
        )
        IssueLine.objects.create(issue=issue, item=self.item, quantity=Decimal("100"), boq_item=self.boq_item)
        issue.post(self.user)
        requisition.refresh_from_db()
        self.assertEqual(requisition.status, StoreRequisition.STATUS_PARTLY_ISSUED)

        issue2 = Issue.objects.create(
            number=2, date="2026-02-02", store=self.store, requisition=requisition, issued_to="Site crew A"
        )
        IssueLine.objects.create(issue=issue2, item=self.item, quantity=Decimal("100"), boq_item=self.boq_item)
        issue2.post(self.user)
        requisition.refresh_from_db()
        self.assertEqual(requisition.status, StoreRequisition.STATUS_ISSUED)

    def test_rejecting_a_requisition(self):
        requisition = StoreRequisition.objects.create(
            number=1, date="2026-02-01", project=self.project, section=self.section, requested_by=self.user
        )
        requisition.reject()
        self.assertEqual(requisition.status, StoreRequisition.STATUS_REJECTED)
        with self.assertRaises(ValidationError):
            requisition.reject()

    def test_return_restores_stock_at_current_average_cost(self):
        issue = self._make_issue()
        IssueLine.objects.create(issue=issue, item=self.item, quantity=Decimal("150"), boq_item=self.boq_item)
        issue.post(self.user)
        quantity, _, average = StockMovement.current_balance(self.store, self.item)
        self.assertEqual(quantity, Decimal("50.000"))
        self.assertEqual(average, Decimal("150.00"))

        ret = ReturnToStore.objects.create(
            number=1, date="2026-02-03", store=self.store, returned_by=self.user, linked_issue=issue
        )
        ReturnLine.objects.create(ret=ret, item=self.item, quantity=Decimal("50"), boq_item=self.boq_item)
        ret.post(self.user)
        ret.refresh_from_db()
        self.assertEqual(ret.status, ReturnToStore.STATUS_POSTED)
        quantity, value, average = StockMovement.current_balance(self.store, self.item)
        self.assertEqual(quantity, Decimal("100.000"))
        self.assertEqual(average, Decimal("150.00"))  # returned at the average cost the store still carried

    def test_cannot_post_a_return_with_no_lines(self):
        ret = ReturnToStore.objects.create(number=1, date="2026-02-03", store=self.store, returned_by=self.user)
        with self.assertRaises(ValidationError):
            ret.post(self.user)
        self.assertEqual(ret.status, ReturnToStore.STATUS_DRAFT)
