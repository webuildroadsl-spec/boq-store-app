"""
Tests for boq/importer.py's parsing/validation logic, independent of any
view — these exercise `auto_detect_mapping` and `parse_rows` directly
against workbooks built in-memory with openpyxl.
"""

import io
from decimal import Decimal

from django.test import TestCase
from openpyxl import Workbook

from core.models import Company, Project, Section, UnitOfMeasure

from . import importer
from .models import BOQ, Bill, BOQItem

FLAT_HEADERS = [
    "Bill Number",
    "Bill Title",
    "Item Reference",
    "Description",
    "Unit",
    "Quantity",
    "Rate",
    "Section",
]


def _workbook_path(rows, headers=FLAT_HEADERS, tmp_path_factory=None):
    """Writes `rows` (a list of lists) under `headers` to a temp .xlsx and returns its path."""
    wb = Workbook()
    ws = wb.active
    ws.append(headers)
    for row in rows:
        ws.append(row)
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    import tempfile

    tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
    tmp.write(buffer.getvalue())
    tmp.close()
    return tmp.name


class AutoDetectMappingTests(TestCase):
    def test_matches_the_template_headers_exactly(self):
        mapping = importer.auto_detect_mapping(FLAT_HEADERS)
        self.assertEqual(
            mapping,
            {
                "bill_number": 0,
                "bill_title": 1,
                "item_reference": 2,
                "description": 3,
                "unit": 4,
                "quantity": 5,
                "rate": 6,
                "section_code": 7,
            },
        )

    def test_matches_common_alias_spellings_case_and_space_insensitively(self):
        headers = ["Bill No", "Bill Name", "Item No", "Desc", "UOM", "Qty", "Unit Rate", "Chainage"]
        mapping = importer.auto_detect_mapping(headers)
        self.assertEqual(mapping["bill_number"], 0)
        self.assertEqual(mapping["bill_title"], 1)
        self.assertEqual(mapping["item_reference"], 2)
        self.assertEqual(mapping["description"], 3)
        self.assertEqual(mapping["unit"], 4)
        self.assertEqual(mapping["quantity"], 5)
        self.assertEqual(mapping["rate"], 6)
        self.assertEqual(mapping["section_code"], 7)

    def test_unmatched_header_is_left_unmapped(self):
        mapping = importer.auto_detect_mapping(["Something else entirely"])
        self.assertEqual(mapping, {})


class ParseRowsTests(TestCase):
    def setUp(self):
        company = Company.objects.create(name="Test Contractor Ltd")
        self.project = Project.objects.create(company=company, code="IMP-1", name="Import Test")
        self.section = Section.objects.create(
            project=self.project, code="S1", start_chainage="0.000", end_chainage="2.500"
        )
        self.boq = BOQ.objects.create(project=self.project, version_number=1)
        self.mapping = importer.auto_detect_mapping(FLAT_HEADERS)

    def _parse(self, rows):
        path = _workbook_path(rows)
        return importer.parse_rows(path, self.mapping, self.project, self.boq)

    def test_measured_item_computes_amount_matching_acceptance_test(self):
        rows = self._parse([[4, "Sub-base and Base", "4.02", "Sub-base, 150mm", "m³", 1250.500, 185.00, ""]])
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["errors"], [])
        self.assertEqual(row["item_type"], BOQItem.TYPE_MEASURED)
        self.assertEqual(Decimal(row["amount"]), Decimal("231342.50"))

    def test_blank_quantity_and_rate_is_a_heading(self):
        rows = self._parse([[4, "Sub-base and Base", "4", "Sub-base and Base", "", "", "", ""]])
        row = rows[0]
        self.assertEqual(row["errors"], [])
        self.assertEqual(row["item_type"], BOQItem.TYPE_HEADING)
        self.assertEqual(row["quantity"], "")
        self.assertEqual(row["rate"], "")

    def test_sum_unit_is_inferred_as_lump_sum_with_quantity_forced_to_one(self):
        rows = self._parse(
            [[4, "Sub-base and Base", "4.09", "Provisional sum for unforeseen ground", "sum", "", 50000, ""]]
        )
        row = rows[0]
        self.assertEqual(row["errors"], [])
        self.assertEqual(row["item_type"], BOQItem.TYPE_LUMP_SUM)
        self.assertEqual(row["quantity"], "1")
        self.assertEqual(Decimal(row["amount"]), Decimal("50000.00"))

    def test_missing_bill_number_is_an_error(self):
        rows = self._parse([["", "Sub-base and Base", "4.02", "Sub-base", "m³", 10, 5, ""]])
        self.assertIn("Bill number is required.", rows[0]["errors"])

    def test_missing_item_reference_is_an_error(self):
        rows = self._parse([[4, "Sub-base and Base", "", "Sub-base", "m³", 10, 5, ""]])
        self.assertIn("Item reference is required.", rows[0]["errors"])

    def test_duplicate_item_reference_within_the_file_is_an_error(self):
        rows = self._parse(
            [
                [4, "Sub-base and Base", "4.02", "Sub-base", "m³", 10, 5, ""],
                [4, "Sub-base and Base", "4.02", "Duplicate", "m³", 20, 5, ""],
            ]
        )
        self.assertEqual(rows[0]["errors"], [])
        self.assertTrue(any("duplicated on row 2" in e for e in rows[1]["errors"]))

    def test_item_reference_already_in_the_boq_is_an_error(self):
        bill = Bill.objects.create(boq=self.boq, number=1, title="Existing bill")
        BOQItem.objects.create(
            bill=bill,
            item_reference="4.02",
            description="Already here",
            item_type=BOQItem.TYPE_MEASURED,
            unit=UnitOfMeasure.objects.get(code="m³"),
            quantity=Decimal("1"),
            rate=Decimal("1"),
        )
        rows = self._parse([[4, "Sub-base and Base", "4.02", "Sub-base", "m³", 10, 5, ""]])
        self.assertTrue(any("already exists in this BOQ version" in e for e in rows[0]["errors"]))

    def test_unknown_unit_is_an_error(self):
        rows = self._parse([[4, "Sub-base and Base", "4.02", "Sub-base", "bogus", 10, 5, ""]])
        self.assertTrue(any("is not in the unit-of-measure list" in e for e in rows[0]["errors"]))

    def test_missing_unit_on_a_non_heading_row_is_an_error(self):
        rows = self._parse([[4, "Sub-base and Base", "4.02", "Sub-base", "", 10, 5, ""]])
        self.assertIn("Unit is required for a non-heading row.", rows[0]["errors"])

    def test_unknown_section_code_is_an_error(self):
        rows = self._parse([[4, "Sub-base and Base", "4.02", "Sub-base", "m³", 10, 5, "NOPE"]])
        self.assertTrue(any("was not found on this project" in e for e in rows[0]["errors"]))

    def test_known_section_code_resolves_to_its_id(self):
        rows = self._parse([[4, "Sub-base and Base", "4.02", "Sub-base", "m³", 10, 5, "S1"]])
        self.assertEqual(rows[0]["section_id"], self.section.pk)

    def test_blank_row_is_skipped_entirely(self):
        rows = self._parse(
            [
                [4, "Sub-base and Base", "4.02", "Sub-base", "m³", 10, 5, ""],
                [None, None, None, None, None, None, None, None],
            ]
        )
        self.assertEqual(len(rows), 1)

    def test_bill_title_only_required_on_the_bills_first_row(self):
        rows = self._parse(
            [
                [4, "Sub-base and Base", "4.02", "Sub-base", "m³", 10, 5, ""],
                [4, "", "4.03", "Base", "m³", 20, 5, ""],
            ]
        )
        self.assertEqual(rows[0]["errors"], [])
        self.assertEqual(rows[1]["errors"], [])
        self.assertEqual(rows[1]["bill_title"], "Sub-base and Base")

    def test_non_numeric_quantity_is_an_error(self):
        rows = self._parse([[4, "Sub-base and Base", "4.02", "Sub-base", "m³", "not-a-number", 5, ""]])
        self.assertTrue(any("is not a valid number" in e for e in rows[0]["errors"]))


class FiveHundredLineAcceptanceTest(TestCase):
    """
    Section 8 step 4's literal acceptance test: "A real 500-line BOQ
    imports with totals matching the original Excel to the cent."

    Builds a 500-row flat-layout workbook across several bills (mixing
    headings, measured items, and lump sums), independently computes
    the expected bill/grand totals using the same rounding rule the
    model itself uses, imports it, and checks the imported BOQ's totals
    against that independent computation to the cent.
    """

    def setUp(self):
        company = Company.objects.create(name="Test Contractor Ltd")
        self.project = Project.objects.create(company=company, code="IMP-500", name="500-line import")
        self.boq = BOQ.objects.create(project=self.project, version_number=1)
        self.mapping = importer.auto_detect_mapping(FLAT_HEADERS)

    def test_500_line_import_totals_match_to_the_cent(self):
        from decimal import ROUND_HALF_UP

        rows_to_write = []
        expected_bill_totals = {}
        n_bills = 10
        items_per_bill = 49  # + 1 heading row per bill = 500 rows total

        for bill_number in range(1, n_bills + 1):
            bill_title = f"Bill {bill_number} — generated"
            rows_to_write.append([bill_number, bill_title, f"{bill_number}", bill_title, "", "", "", ""])
            bill_total = Decimal("0.00")
            for item_index in range(1, items_per_bill + 1):
                item_ref = f"{bill_number}.{item_index:02d}"
                quantity = Decimal(f"{100 + item_index}.{item_index:03d}")
                rate = Decimal(f"{10 + item_index}.{(item_index * 7) % 100:02d}")
                amount = (quantity * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                bill_total += amount
                rows_to_write.append(
                    [bill_number, bill_title, item_ref, f"Item {item_ref}", "m³", str(quantity), str(rate), ""]
                )
            expected_bill_totals[bill_number] = bill_total

        self.assertEqual(len(rows_to_write), 500)

        path = _workbook_path(rows_to_write)
        rows = importer.parse_rows(path, self.mapping, self.project, self.boq)
        self.assertEqual(len(rows), 500)
        self.assertEqual([r["errors"] for r in rows], [[] for _ in rows])

        bills_by_number = {}
        for row in rows:
            bill = bills_by_number.get(row["bill_number"])
            if bill is None:
                bill = Bill.objects.create(
                    boq=self.boq, number=row["bill_number"], title=row["bill_title"]
                )
                bills_by_number[row["bill_number"]] = bill
            BOQItem.objects.create(
                bill=bill,
                item_reference=row["item_reference"],
                description=row["description"],
                item_type=row["item_type"],
                unit_id=row["unit_id"],
                quantity=Decimal(row["quantity"]) if row["quantity"] else None,
                rate=Decimal(row["rate"]) if row["rate"] else None,
                section_id=row["section_id"],
                sort_order=row["row_number"],
            )

        expected_grand_total = sum(expected_bill_totals.values(), Decimal("0.00"))
        self.boq.refresh_from_db()
        for bill_number, bill in bills_by_number.items():
            self.assertEqual(bill.total, expected_bill_totals[bill_number])
        self.assertEqual(self.boq.grand_total, expected_grand_total)
