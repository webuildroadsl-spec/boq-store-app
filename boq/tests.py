from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import (
    Company,
    Project,
    ProjectMembership,
    ROLE_QS,
    ROLE_STOREKEEPER,
    ROLE_VIEWER,
    UnitOfMeasure,
)

from .models import BOQ, Bill, BOQItem

User = get_user_model()


class BOQItemCalculationTests(TestCase):
    """Rule 1 and the step 3 acceptance test: 1,250.500 m3 x 185.00 = 231,342.50."""

    def setUp(self):
        company = Company.objects.create(name="Test Contractor Ltd")
        project = Project.objects.create(company=company, code="BTR-1", name="Test Project")
        boq = BOQ.objects.create(project=project, version_number=1)
        self.bill = Bill.objects.create(boq=boq, number=4, title="Sub-base and Base")
        self.m3 = UnitOfMeasure.objects.get(code="m³")

    def test_item_4_02_amount_matches_acceptance_test(self):
        item = BOQItem.objects.create(
            bill=self.bill,
            item_reference="4.02",
            description="Sub-base, 150mm compacted",
            item_type=BOQItem.TYPE_MEASURED,
            unit=self.m3,
            quantity=Decimal("1250.500"),
            rate=Decimal("185.00"),
        )
        self.assertEqual(item.amount, Decimal("231342.50"))

    def test_bill_total_updates_when_item_is_added(self):
        self.assertEqual(self.bill.total, Decimal("0.00"))
        BOQItem.objects.create(
            bill=self.bill,
            item_reference="4.02",
            description="Sub-base, 150mm compacted",
            item_type=BOQItem.TYPE_MEASURED,
            unit=self.m3,
            quantity=Decimal("1250.500"),
            rate=Decimal("185.00"),
        )
        self.assertEqual(self.bill.total, Decimal("231342.50"))
        BOQItem.objects.create(
            bill=self.bill,
            item_reference="4.03",
            description="Base, 100mm compacted",
            item_type=BOQItem.TYPE_MEASURED,
            unit=self.m3,
            quantity=Decimal("500.000"),
            rate=Decimal("200.00"),
        )
        self.assertEqual(self.bill.total, Decimal("331342.50"))

    def test_amount_rounds_to_two_decimal_places(self):
        item = BOQItem.objects.create(
            bill=self.bill,
            item_reference="4.04",
            description="Rounding check",
            item_type=BOQItem.TYPE_MEASURED,
            unit=self.m3,
            quantity=Decimal("1.005"),
            rate=Decimal("10.005"),
        )
        # 1.005 x 10.005 = 10.055025 -> rounds to 10.06 (ROUND_HALF_UP)
        self.assertEqual(item.amount, Decimal("10.06"))

    def test_heading_has_no_quantity_rate_or_amount(self):
        heading = BOQItem.objects.create(
            bill=self.bill,
            item_reference="4",
            description="Sub-base and Base",
            item_type=BOQItem.TYPE_HEADING,
        )
        self.assertIsNone(heading.quantity)
        self.assertIsNone(heading.rate)
        self.assertIsNone(heading.amount)
        self.assertEqual(self.bill.total, Decimal("0.00"))

    def test_lump_sum_item_forces_quantity_one_and_unit_sum(self):
        item = BOQItem.objects.create(
            bill=self.bill,
            item_reference="1.01",
            description="Mobilisation",
            item_type=BOQItem.TYPE_LUMP_SUM,
            rate=Decimal("50000.00"),
        )
        self.assertEqual(item.quantity, Decimal("1"))
        self.assertEqual(item.unit.code, "sum")
        self.assertEqual(item.amount, Decimal("50000.00"))

    def test_grand_total_sums_every_bill(self):
        BOQItem.objects.create(
            bill=self.bill,
            item_reference="4.02",
            description="Sub-base",
            item_type=BOQItem.TYPE_MEASURED,
            unit=self.m3,
            quantity=Decimal("1250.500"),
            rate=Decimal("185.00"),
        )
        other_bill = Bill.objects.create(boq=self.bill.boq, number=1, title="General Items")
        BOQItem.objects.create(
            bill=other_bill,
            item_reference="1.01",
            description="Mobilisation",
            item_type=BOQItem.TYPE_LUMP_SUM,
            rate=Decimal("10000.00"),
        )
        self.assertEqual(self.bill.boq.grand_total, Decimal("241342.50"))


class BOQAccessTests(TestCase):
    """Section 2's 'Create and edit BOQ' row, applied to the BOQ views."""

    def setUp(self):
        self.company = Company.objects.create(name="Test Contractor Ltd")
        self.project = Project.objects.create(company=self.company, code="BTR-2", name="Test Project")

        self.qs_user = User.objects.create_user(username="qs1", password="a-strong-test-password-1")
        ProjectMembership.objects.create(user=self.qs_user, project=self.project, role=ROLE_QS)

        self.viewer_user = User.objects.create_user(
            username="viewer1", password="a-strong-test-password-1"
        )
        ProjectMembership.objects.create(user=self.viewer_user, project=self.project, role=ROLE_VIEWER)

        self.storekeeper_user = User.objects.create_user(
            username="storekeeper1", password="a-strong-test-password-1"
        )
        ProjectMembership.objects.create(
            user=self.storekeeper_user, project=self.project, role=ROLE_STOREKEEPER
        )

        self.outsider = User.objects.create_user(
            username="outsider", password="a-strong-test-password-1"
        )

    def test_qs_can_view_boq_detail(self):
        self.client.login(username="qs1", password="a-strong-test-password-1")
        response = self.client.get(reverse("boq:boq_detail", args=[self.project.pk]))
        self.assertEqual(response.status_code, 200)

    def test_viewer_can_view_but_not_edit(self):
        self.client.login(username="viewer1", password="a-strong-test-password-1")
        response = self.client.get(reverse("boq:boq_detail", args=[self.project.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Add bill")
        response = self.client.post(
            reverse("boq:boq_detail", args=[self.project.pk]),
            {"number": 1, "title": "General Items", "sort_order": 0},
        )
        self.assertEqual(response.status_code, 403)

    def test_storekeeper_gets_403_on_boq(self):
        self.client.login(username="storekeeper1", password="a-strong-test-password-1")
        response = self.client.get(reverse("boq:boq_detail", args=[self.project.pk]))
        self.assertEqual(response.status_code, 403)

    def test_outsider_gets_404_on_boq(self):
        self.client.login(username="outsider", password="a-strong-test-password-1")
        response = self.client.get(reverse("boq:boq_detail", args=[self.project.pk]))
        self.assertEqual(response.status_code, 404)

    def test_qs_can_add_a_bill(self):
        self.client.login(username="qs1", password="a-strong-test-password-1")
        response = self.client.post(
            reverse("boq:boq_detail", args=[self.project.pk]),
            {"number": 1, "title": "General Items", "sort_order": 0},
        )
        self.assertEqual(response.status_code, 302)
        boq = BOQ.objects.get(project=self.project)
        self.assertEqual(boq.bills.count(), 1)
        self.assertEqual(boq.bills.first().title, "General Items")


class BOQItemFormSetTests(TestCase):
    """Rule 3 and the manual-entry grid (bill_items view)."""

    def setUp(self):
        self.company = Company.objects.create(name="Test Contractor Ltd")
        self.project = Project.objects.create(company=self.company, code="BTR-3", name="Test Project")
        self.qs_user = User.objects.create_user(username="qs2", password="a-strong-test-password-1")
        ProjectMembership.objects.create(user=self.qs_user, project=self.project, role=ROLE_QS)
        self.client.login(username="qs2", password="a-strong-test-password-1")

        self.boq = BOQ.objects.create(project=self.project, version_number=1)
        self.bill = Bill.objects.create(boq=self.boq, number=4, title="Sub-base and Base")
        self.m3 = UnitOfMeasure.objects.get(code="m³")

    def _formset_post_data(self, rows, total_forms=3):
        """
        Builds POST data the way a real browser would: every rendered
        row's fields are present, even ones the user left untouched
        (they just carry their blank/default values, e.g. sort_order=0).
        """
        data = {
            "items-TOTAL_FORMS": str(total_forms),
            "items-INITIAL_FORMS": "0",
            "items-MIN_NUM_FORMS": "0",
            "items-MAX_NUM_FORMS": "1000",
        }
        blank_row = {
            "item_reference": "",
            "description": "",
            "item_type": "",
            "unit": "",
            "quantity": "",
            "rate": "",
            "section": "",
            "parent_item": "",
            "sort_order": "0",
        }
        for i in range(total_forms):
            row = blank_row | (rows[i] if i < len(rows) else {})
            for key, value in row.items():
                data[f"items-{i}-{key}"] = value
        return data

    def test_adding_item_via_grid_computes_amount_and_bill_total(self):
        url = reverse("boq:bill_items", args=[self.project.pk, self.bill.pk])
        data = self._formset_post_data(
            [
                {
                    "item_reference": "4.02",
                    "description": "Sub-base, 150mm compacted",
                    "item_type": BOQItem.TYPE_MEASURED,
                    "unit": self.m3.pk,
                    "quantity": "1250.500",
                    "rate": "185.00",
                    "sort_order": "0",
                }
            ]
        )
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302, response.context["formset"].errors if response.status_code == 200 else "")
        item = BOQItem.objects.get(item_reference="4.02")
        self.assertEqual(item.amount, Decimal("231342.50"))
        self.bill.refresh_from_db()
        self.assertEqual(self.bill.total, Decimal("231342.50"))

    def test_duplicate_item_reference_in_same_boq_is_rejected(self):
        BOQItem.objects.create(
            bill=self.bill,
            item_reference="4.02",
            description="Existing item",
            item_type=BOQItem.TYPE_MEASURED,
            unit=self.m3,
            quantity=Decimal("10.000"),
            rate=Decimal("5.00"),
        )
        url = reverse("boq:bill_items", args=[self.project.pk, self.bill.pk])
        data = self._formset_post_data(
            [
                {
                    "item_reference": "4.02",
                    "description": "Duplicate reference",
                    "item_type": BOQItem.TYPE_MEASURED,
                    "unit": self.m3.pk,
                    "quantity": "1.000",
                    "rate": "1.00",
                    "sort_order": "0",
                }
            ]
        )
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200)  # re-rendered with errors
        self.assertEqual(BOQItem.objects.filter(item_reference="4.02").count(), 1)
