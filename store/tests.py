"""
Model-level tests for step 6: the weighted-average stock balance
formula and GRN posting (Section 5, rules from Section 2: "Posted
documents ... cannot be edited or deleted").

Step 7 (requisition, issue, return) model tests follow further down,
covering the literal acceptance test: "Issuing 250 bags when stock is
200 is blocked; issue without a BOQ item is blocked."

Step 8 (transfer, stock count, reversal) tests follow after that,
covering their own literal acceptance test: "Stock in transit counts
in neither store; posted GRN cannot be edited."
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
    DocumentReversal,
    Issue,
    IssueLine,
    ItemCategory,
    RequisitionLine,
    ReturnLine,
    ReturnToStore,
    StockCount,
    StockCountLine,
    Store,
    StockMovement,
    StoreItem,
    StoreRequisition,
    Supplier,
    Transfer,
    TransferLine,
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


class TransferTests(TestCase):
    """
    Step 8's acceptance test, at the model level: "Stock in transit
    counts in neither store." A transfer's dispatch() removes stock
    from `from_store` immediately; receive() is the only thing that
    adds it to `to_store` -- so the balance formula excludes it from
    both stores for free while it's in between.
    """

    def setUp(self):
        company = Company.objects.create(name="Test Contractor Ltd")
        self.project = Project.objects.create(company=company, code="STORE-4", name="Store test 4")
        self.user = User.objects.create_user(username="storekeeper4", password="pw")
        self.store_a = Store.objects.create(
            project=self.project, code="A", name="Store A", storekeeper=self.user
        )
        self.store_b = Store.objects.create(
            project=self.project, code="B", name="Store B", storekeeper=self.user
        )
        category = ItemCategory.objects.create(name="Cement and Binders")
        self.item = StoreItem.objects.create(
            code="CEM-01", name="Cement, 50kg bag", category=category, unit=UnitOfMeasure.objects.get(code="t")
        )
        StockMovement.objects.create(
            store=self.store_a,
            item=self.item,
            quantity=Decimal("200"),
            unit_cost=Decimal("150.00"),
            document_type=StockMovement.DOCUMENT_GRN,
            document_id=1,
            created_by=self.user,
        )

    def _make_transfer(self):
        return Transfer.objects.create(
            number=1, date="2026-03-01", project=self.project, from_store=self.store_a, to_store=self.store_b
        )

    def test_stock_in_transit_counts_in_neither_store(self):
        transfer = self._make_transfer()
        TransferLine.objects.create(transfer=transfer, item=self.item, quantity=Decimal("80"))
        transfer.dispatch(self.user)
        transfer.refresh_from_db()
        self.assertEqual(transfer.status, Transfer.STATUS_IN_TRANSIT)

        a_quantity, _, _ = StockMovement.current_balance(self.store_a, self.item)
        b_quantity, _, _ = StockMovement.current_balance(self.store_b, self.item)
        self.assertEqual(a_quantity, Decimal("120.000"))  # left store A
        self.assertEqual(b_quantity, Decimal("0.000"))  # not arrived at store B yet

        transfer.receive(self.user)
        transfer.refresh_from_db()
        self.assertEqual(transfer.status, Transfer.STATUS_RECEIVED)
        a_quantity, _, _ = StockMovement.current_balance(self.store_a, self.item)
        b_quantity, _, b_average = StockMovement.current_balance(self.store_b, self.item)
        self.assertEqual(a_quantity, Decimal("120.000"))
        self.assertEqual(b_quantity, Decimal("80.000"))
        self.assertEqual(b_average, Decimal("150.00"))  # arrives at the cost it left at

    def test_dispatching_more_than_available_is_blocked(self):
        transfer = self._make_transfer()
        TransferLine.objects.create(transfer=transfer, item=self.item, quantity=Decimal("250"))
        with self.assertRaises(ValidationError) as ctx:
            transfer.dispatch(self.user)
        self.assertIn("exceed", " ".join(ctx.exception.messages))
        transfer.refresh_from_db()
        self.assertEqual(transfer.status, Transfer.STATUS_DRAFT)

    def test_cannot_transfer_a_store_to_itself(self):
        transfer = Transfer(
            number=2, date="2026-03-01", project=self.project, from_store=self.store_a, to_store=self.store_a
        )
        with self.assertRaises(ValidationError):
            transfer.full_clean()

    def test_cannot_receive_before_dispatch(self):
        transfer = self._make_transfer()
        TransferLine.objects.create(transfer=transfer, item=self.item, quantity=Decimal("50"))
        with self.assertRaises(ValidationError):
            transfer.receive(self.user)


class StockCountTests(TestCase):
    """Step 8's stock count: differences post as adjustments after
    Project Manager approval (Section 5.2 point 6), and the approver
    can never be the person who counted (Section 2)."""

    def setUp(self):
        company = Company.objects.create(name="Test Contractor Ltd")
        self.project = Project.objects.create(company=company, code="STORE-5", name="Store test 5")
        self.counter = User.objects.create_user(username="counter", password="pw")
        self.pm = User.objects.create_user(username="pm_approver", password="pw")
        self.store = Store.objects.create(
            project=self.project, code="MAIN", name="Main yard", storekeeper=self.counter
        )
        category = ItemCategory.objects.create(name="Cement and Binders")
        self.item = StoreItem.objects.create(
            code="CEM-01", name="Cement, 50kg bag", category=category, unit=UnitOfMeasure.objects.get(code="t")
        )
        StockMovement.objects.create(
            store=self.store,
            item=self.item,
            quantity=Decimal("200"),
            unit_cost=Decimal("150.00"),
            document_type=StockMovement.DOCUMENT_GRN,
            document_id=1,
            created_by=self.counter,
        )

    def test_shortage_posts_as_a_negative_adjustment(self):
        stock_count = StockCount.objects.create(
            number=1, date="2026-03-05", store=self.store, counted_by=self.counter
        )
        StockCountLine.objects.create(
            stock_count=stock_count, item=self.item, system_quantity=Decimal("200"), counted_quantity=Decimal("190"),
            reason="Spillage",
        )
        stock_count.submit()
        stock_count.approve(self.pm)
        stock_count.refresh_from_db()
        self.assertEqual(stock_count.status, StockCount.STATUS_APPROVED)
        self.assertEqual(stock_count.approved_by, self.pm)
        quantity, _, _ = StockMovement.current_balance(self.store, self.item)
        self.assertEqual(quantity, Decimal("190.000"))
        movement = StockMovement.objects.get(document_type=StockMovement.DOCUMENT_ADJUSTMENT, document_id=stock_count.pk)
        self.assertEqual(movement.quantity, Decimal("-10.000"))

    def test_no_difference_posts_no_movement(self):
        stock_count = StockCount.objects.create(
            number=1, date="2026-03-05", store=self.store, counted_by=self.counter
        )
        StockCountLine.objects.create(
            stock_count=stock_count, item=self.item, system_quantity=Decimal("200"), counted_quantity=Decimal("200")
        )
        stock_count.submit()
        stock_count.approve(self.pm)
        self.assertEqual(StockMovement.objects.filter(document_type=StockMovement.DOCUMENT_ADJUSTMENT).count(), 0)

    def test_counter_cannot_approve_their_own_count(self):
        stock_count = StockCount.objects.create(
            number=1, date="2026-03-05", store=self.store, counted_by=self.counter
        )
        StockCountLine.objects.create(
            stock_count=stock_count, item=self.item, system_quantity=Decimal("200"), counted_quantity=Decimal("190")
        )
        stock_count.submit()
        with self.assertRaises(ValidationError):
            stock_count.approve(self.counter)

    def test_cannot_submit_an_empty_stock_count(self):
        stock_count = StockCount.objects.create(
            number=1, date="2026-03-05", store=self.store, counted_by=self.counter
        )
        with self.assertRaises(ValidationError):
            stock_count.submit()


class DocumentReversalTests(TestCase):
    """
    Step 8's other acceptance-test-adjacent rule: "posted GRN cannot
    be edited" (already true since step 6) is joined by the
    reversing-document mechanism Section 2 calls for as the actual
    correction path.
    """

    def setUp(self):
        company = Company.objects.create(name="Test Contractor Ltd")
        self.project = Project.objects.create(company=company, code="STORE-6", name="Store test 6")
        self.user = User.objects.create_user(username="storekeeper6", password="pw")
        self.store = Store.objects.create(
            project=self.project, code="MAIN", name="Main yard", storekeeper=self.user
        )
        category = ItemCategory.objects.create(name="Cement and Binders")
        self.item = StoreItem.objects.create(
            code="CEM-01", name="Cement, 50kg bag", category=category, unit=UnitOfMeasure.objects.get(code="t")
        )
        self.supplier = Supplier.objects.create(name="ACME Building Supplies")

    def test_posted_grn_still_cannot_be_edited(self):
        grn = GRN.objects.create(
            number=1, date="2026-03-10", store=self.store, supplier=self.supplier, received_by=self.user
        )
        GRNLine.objects.create(grn=grn, item=self.item, quantity=Decimal("200"), unit_cost=Decimal("150.00"))
        grn.post(self.user)
        self.assertFalse(grn.is_editable)
        # is_editable is the model-level flag the view layer checks
        # before allowing any edit (see test_views.py's
        # test_cannot_edit_a_posted_grn, from step 6) -- the
        # correction path for a mistake here is a reversal:
        reversal = grn.reverse(self.user, "Wrong quantity delivered")
        self.assertIsInstance(reversal, DocumentReversal)
        quantity, value, _ = StockMovement.current_balance(self.store, self.item)
        self.assertEqual(quantity, Decimal("0.000"))
        self.assertEqual(value, Decimal("0.00"))
        self.assertTrue(grn.is_reversed)
        self.assertEqual(grn.status, GRN.STATUS_POSTED)  # the original document itself is untouched

    def test_cannot_reverse_the_same_document_twice(self):
        grn = GRN.objects.create(
            number=1, date="2026-03-10", store=self.store, supplier=self.supplier, received_by=self.user
        )
        GRNLine.objects.create(grn=grn, item=self.item, quantity=Decimal("200"), unit_cost=Decimal("150.00"))
        grn.post(self.user)
        grn.reverse(self.user, "Wrong quantity delivered")
        with self.assertRaises(ValidationError):
            grn.reverse(self.user, "Trying again")

    def test_cannot_reverse_a_draft_document(self):
        grn = GRN.objects.create(
            number=1, date="2026-03-10", store=self.store, supplier=self.supplier, received_by=self.user
        )
        with self.assertRaises(ValidationError):
            grn.reverse(self.user, "Nothing posted yet")

    def test_reversing_a_grn_is_blocked_if_stock_has_moved_on(self):
        grn = GRN.objects.create(
            number=1, date="2026-03-10", store=self.store, supplier=self.supplier, received_by=self.user
        )
        GRNLine.objects.create(grn=grn, item=self.item, quantity=Decimal("200"), unit_cost=Decimal("150.00"))
        grn.post(self.user)
        # Issue away most of what the GRN delivered.
        issue = Issue.objects.create(number=1, date="2026-03-11", store=self.store, issued_to="Site crew")
        boq = BOQ.objects.create(project=self.project, version_number=1, status=BOQ.STATUS_APPROVED)
        bill = Bill.objects.create(boq=boq, number=1, title="Earthworks")
        boq_item = BOQItem.objects.create(
            bill=bill, item_reference="1.01", description="Supply and lay cement",
            item_type=BOQItem.TYPE_MEASURED, unit=UnitOfMeasure.objects.get(code="t"),
            quantity=Decimal("1000"), rate=Decimal("10.00"),
        )
        IssueLine.objects.create(issue=issue, item=self.item, quantity=Decimal("180"), boq_item=boq_item)
        issue.post(self.user)
        # Only 20 t left -- reversing the 200 t GRN would take stock to -180.
        with self.assertRaises(ValidationError) as ctx:
            grn.reverse(self.user, "Wrong quantity delivered")
        self.assertIn("negative", " ".join(ctx.exception.messages))

    def test_reversing_an_issue_restores_stock(self):
        grn = GRN.objects.create(
            number=1, date="2026-03-10", store=self.store, supplier=self.supplier, received_by=self.user
        )
        GRNLine.objects.create(grn=grn, item=self.item, quantity=Decimal("200"), unit_cost=Decimal("150.00"))
        grn.post(self.user)
        boq = BOQ.objects.create(project=self.project, version_number=1, status=BOQ.STATUS_APPROVED)
        bill = Bill.objects.create(boq=boq, number=1, title="Earthworks")
        boq_item = BOQItem.objects.create(
            bill=bill, item_reference="1.01", description="Supply and lay cement",
            item_type=BOQItem.TYPE_MEASURED, unit=UnitOfMeasure.objects.get(code="t"),
            quantity=Decimal("1000"), rate=Decimal("10.00"),
        )
        issue = Issue.objects.create(number=1, date="2026-03-11", store=self.store, issued_to="Site crew")
        IssueLine.objects.create(issue=issue, item=self.item, quantity=Decimal("50"), boq_item=boq_item)
        issue.post(self.user)
        quantity, _, _ = StockMovement.current_balance(self.store, self.item)
        self.assertEqual(quantity, Decimal("150.000"))

        issue.reverse(self.user, "Issued to the wrong site")
        quantity, _, _ = StockMovement.current_balance(self.store, self.item)
        self.assertEqual(quantity, Decimal("200.000"))
