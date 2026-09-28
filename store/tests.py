"""
Model-level tests for step 6: the weighted-average stock balance
formula and GRN posting (Section 5, rules from Section 2: "Posted
documents ... cannot be edited or deleted").
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from core.models import Company, Project, UnitOfMeasure

from .models import GRN, GRNLine, ItemCategory, Store, StockMovement, StoreItem, Supplier

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
