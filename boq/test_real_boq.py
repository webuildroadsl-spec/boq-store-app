"""
Real contract BOQs: precision, units, and amounts that survive a revision.

Found while importing a real road BOQ (Step B). Its rates are priced to
3 decimals (26.325 per m3) and some quantities come out of take-off
formulas with 4-5 decimals (2.1504 ha, 1.03125 t). With quantities
stored to 3 decimals and rates to 2, the app's grand total came out
different from the signed contract, and a QS couldn't type a 3-decimal
rate into the grid at all.

The figures below are made up but reproduce each of those cases. The
real BOQ itself is not committed (the repository is public).
"""

import json
import tempfile
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from openpyxl import Workbook

from core.models import Company, Project, ProjectMembership, ROLE_QS, UnitOfMeasure

from . import importer
from .models import BOQ, Bill, BOQItem

User = get_user_model()

HEADERS = ["Bill number", "Bill title", "Item reference", "Description", "Unit", "Quantity", "Rate"]

# (ref, description, unit as typed in a real BOQ, quantity, rate, expected amount)
# Expected amounts are ROUND(quantity x rate, 2), exactly as the contract
# spreadsheet calculates them.
CASES = [
    ("2.11", "General site clearance", "ha", "2.1504", "30298.808", "65154.56"),
    ("2.12", "Excavate topsoil 200mm", "m3", "4300.8", "26.325", "113218.56"),
    ("3.21(b)", "Blinding material on primed surface", "m2", "21504", "3.413", "73393.15"),
    ("4.54", "Extra over for haulage", "m3*km", "6720", "1.463", "9831.36"),
    ("4.31", "High yield steel reinforcement", "Kg", "26675.706215", "11.7", "312105.76"),
    ("11.12", "Temporary river crossing", "L.S.", "1", "7917", "7917.00"),
    ("11.32(d)(i)", "Reinforcement bars, abutments", "tonne", "1.03125", "11700", "12065.63"),
    ("11.42(d)", "Precast concrete pads", "No.", "6", "255.06", "1530.36"),
]
EXPECTED_TOTAL = sum(Decimal(c[5]) for c in CASES)


def write_workbook(rows):
    wb = Workbook()
    ws = wb.active
    ws.append(HEADERS)
    for row in rows:
        ws.append(row)
    tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
    wb.save(tmp.name)
    return tmp.name


class RealBOQTestCase(TestCase):
    def setUp(self):
        company = Company.objects.create(name="Test Contractor Ltd")
        self.project = Project.objects.create(company=company, code="REAL-1", name="Real BOQ test")
        self.boq = BOQ.objects.create(project=self.project, version_number=1)

    def import_cases(self):
        path = write_workbook([[2, "Road works", ref, desc, unit, float(q), float(r)] for ref, desc, unit, q, r, _ in CASES])
        mapping = importer.auto_detect_mapping(importer.read_headers(path))
        rows = importer.parse_rows(path, mapping, self.project, self.boq)
        for row in rows:
            self.assertEqual(row["errors"], [], f"row {row['row_number']}")
        bill = Bill.objects.create(boq=self.boq, number=2, title="Road works")
        for i, row in enumerate(rows):
            BOQItem.objects.create(
                bill=bill, item_reference=row["item_reference"], description=row["description"],
                item_type=row["item_type"], unit_id=row["unit_id"],
                quantity=Decimal(row["quantity"]), rate=Decimal(row["rate"]), sort_order=i,
            )
        return rows


class RealBOQImportTests(RealBOQTestCase):
    def test_real_unit_spellings_are_recognised(self):
        rows = self.import_cases()
        self.assertEqual(
            [r["unit_code"] for r in rows],
            ["ha", "m³", "m²", "m³·km", "kg", "sum", "t", "nr"],
        )

    def test_every_amount_and_the_total_match_the_contract_to_the_cent(self):
        self.import_cases()
        # Read back from the database, so rounding on storage would show.
        for ref, _, _, quantity, rate, amount in CASES:
            item = BOQItem.objects.get(item_reference=ref)
            self.assertEqual(item.quantity, Decimal(quantity), ref)
            self.assertEqual(item.rate, Decimal(rate), ref)
            self.assertEqual(item.amount, Decimal(amount), ref)
        self.boq.refresh_from_db()
        self.assertEqual(self.boq.grand_total, EXPECTED_TOTAL)

    def test_a_revision_keeps_every_amount(self):
        # create_revision re-saves every item, which recalculates its
        # amount from the STORED quantity and rate. If storage rounded
        # them, a revision would silently change the contract value.
        self.import_cases()
        self.boq.status = BOQ.STATUS_APPROVED
        self.boq.save()
        revision = self.boq.create_revision()
        self.assertEqual(revision.grand_total, EXPECTED_TOTAL)

    def test_unknown_unit_is_still_reported(self):
        path = write_workbook([[2, "Road works", "9.1", "Something", "furlong", 1, 1]])
        mapping = importer.auto_detect_mapping(importer.read_headers(path))
        rows = importer.parse_rows(path, mapping, self.project, self.boq)
        self.assertIn("Unit 'furlong' is not in the unit-of-measure list.", rows[0]["errors"])


class GridAcceptsRealRatesTests(RealBOQTestCase):
    def test_qs_can_enter_a_three_decimal_rate_in_the_grid(self):
        qs = User.objects.create_user(username="qs-real", password="a-strong-test-password-1")
        ProjectMembership.objects.create(user=qs, project=self.project, role=ROLE_QS)
        self.client.force_login(qs)
        bill = Bill.objects.create(boq=self.boq, number=2, title="Road works")
        response = self.client.post(
            reverse("boq:bill_items_save", args=[self.project.pk, bill.pk]),
            data=json.dumps({"rows": [{
                "id": None, "item_reference": "2.12", "description": "Excavate topsoil 200mm",
                "item_type": BOQItem.TYPE_MEASURED, "unit": UnitOfMeasure.objects.get(code="m³").pk,
                "quantity": "4300.8", "rate": "26.325", "sort_order": 0,
            }]}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()["ok"], response.content)
        self.assertEqual(BOQItem.objects.get(item_reference="2.12").amount, Decimal("113218.56"))
