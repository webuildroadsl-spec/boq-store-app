"""
Integration tests for the import upload -> preview -> confirm flow and
the export endpoints, driven through the Django test client exactly as
a browser would (real HTTP requests, real file upload).
"""

import io
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from openpyxl import Workbook, load_workbook

from core.models import (
    Company,
    Project,
    ProjectMembership,
    ROLE_QS,
    ROLE_STOREKEEPER,
    ROLE_VIEWER,
)

from .models import BOQ, Bill, BOQItem

User = get_user_model()

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


def _xlsx_upload(rows, filename="boq.xlsx"):
    wb = Workbook()
    ws = wb.active
    ws.append(FLAT_HEADERS)
    for row in rows:
        ws.append(row)
    buffer = io.BytesIO()
    wb.save(buffer)
    return SimpleUploadedFile(
        filename,
        buffer.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


class ImportExportViewsTestCase(TestCase):
    def setUp(self):
        company = Company.objects.create(name="Test Contractor Ltd")
        self.project = Project.objects.create(company=company, code="VIEW-1", name="View test project")
        self.qs_user = User.objects.create_user(username="qs", password="pw")
        ProjectMembership.objects.create(user=self.qs_user, project=self.project, role=ROLE_QS)
        self.viewer_user = User.objects.create_user(username="viewer", password="pw")
        ProjectMembership.objects.create(user=self.viewer_user, project=self.project, role=ROLE_VIEWER)
        self.storekeeper_user = User.objects.create_user(username="store", password="pw")
        ProjectMembership.objects.create(
            user=self.storekeeper_user, project=self.project, role=ROLE_STOREKEEPER
        )

    def _login(self, user):
        self.client.force_login(user)


class ImportUploadPermissionTests(ImportExportViewsTestCase):
    def test_qs_can_see_upload_form(self):
        self._login(self.qs_user)
        response = self.client.get(reverse("boq:import_upload", args=[self.project.pk]))
        self.assertEqual(response.status_code, 200)

    def test_viewer_is_forbidden(self):
        self._login(self.viewer_user)
        response = self.client.get(reverse("boq:import_upload", args=[self.project.pk]))
        self.assertEqual(response.status_code, 403)

    def test_storekeeper_is_forbidden_same_as_a_viewer(self):
        # A Storekeeper is a project member but has no BOQ access at all
        # (Section 2's "Create and edit BOQ" row) — 403, same as a
        # Viewer here; 404 is reserved for someone who isn't on the
        # project at all (see core.tests for that acceptance test).
        self._login(self.storekeeper_user)
        response = self.client.get(reverse("boq:import_upload", args=[self.project.pk]))
        self.assertEqual(response.status_code, 403)

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get(reverse("boq:import_upload", args=[self.project.pk]))
        self.assertEqual(response.status_code, 302)


class ImportUploadAndPreviewTests(ImportExportViewsTestCase):
    def test_upload_with_no_file_shows_an_error(self):
        self._login(self.qs_user)
        response = self.client.post(reverse("boq:import_upload", args=[self.project.pk]), {})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Choose a file to upload.")

    def test_upload_a_valid_file_renders_the_preview_with_no_errors(self):
        self._login(self.qs_user)
        upload = _xlsx_upload(
            [
                [4, "Sub-base and Base", "4.01", "Sub-base", "m³", 100, 50, ""],
                [4, "Sub-base and Base", "4.02", "Sub-base, 150mm", "m³", 1250.500, 185.00, ""],
            ]
        )
        response = self.client.post(
            reverse("boq:import_upload", args=[self.project.pk]), {"boq_file": upload}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "2 row(s) found, across 1 bill(s).")
        self.assertContains(response, "No errors found.")
        self.assertContains(response, "231342.5")  # str(Decimal) drops the trailing zero

    def test_upload_a_non_excel_file_shows_an_error(self):
        self._login(self.qs_user)
        bad_upload = SimpleUploadedFile("boq.xlsx", b"not an excel file", content_type="application/octet-stream")
        response = self.client.post(
            reverse("boq:import_upload", args=[self.project.pk]), {"boq_file": bad_upload}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Could not read that file")

    def test_preview_with_an_unknown_token_redirects_to_upload(self):
        self._login(self.qs_user)
        response = self.client.post(
            reverse("boq:import_preview", args=[self.project.pk]), {"token": "not-a-real-token"}
        )
        self.assertRedirects(response, reverse("boq:import_upload", args=[self.project.pk]))

    def test_adjusting_the_mapping_reparses_with_the_new_mapping(self):
        self._login(self.qs_user)
        # Headers deliberately don't match any alias, so auto-detect finds nothing.
        upload = _xlsx_upload(
            [[4, "Sub-base and Base", "4.01", "Sub-base", "m³", 100, 50, ""]],
            filename="boq.xlsx",
        )
        upload_response = self.client.post(
            reverse("boq:import_upload", args=[self.project.pk]), {"boq_file": upload}
        )
        token = upload_response.context["token"]
        # Now explicitly map every column and confirm the row parses cleanly.
        response = self.client.post(
            reverse("boq:import_preview", args=[self.project.pk]),
            {
                "token": token,
                "map_bill_number": "0",
                "map_bill_title": "1",
                "map_item_reference": "2",
                "map_description": "3",
                "map_unit": "4",
                "map_quantity": "5",
                "map_rate": "6",
                "map_section_code": "7",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No errors found.")


class ImportConfirmTests(ImportExportViewsTestCase):
    def _upload_and_get_token(self, rows):
        response = self.client.post(
            reverse("boq:import_upload", args=[self.project.pk]),
            {"boq_file": _xlsx_upload(rows)},
        )
        return response.context["token"], response

    def _confirm(self, token):
        return self.client.post(
            reverse("boq:import_confirm", args=[self.project.pk]),
            {
                "token": token,
                "map_bill_number": "0",
                "map_bill_title": "1",
                "map_item_reference": "2",
                "map_description": "3",
                "map_unit": "4",
                "map_quantity": "5",
                "map_rate": "6",
                "map_section_code": "7",
            },
        )

    def test_confirming_a_clean_file_creates_bills_and_items(self):
        self._login(self.qs_user)
        token, _ = self._upload_and_get_token(
            [
                [4, "Sub-base and Base", "4", "Sub-base and Base", "", "", "", ""],
                [4, "Sub-base and Base", "4.02", "Sub-base, 150mm", "m³", 1250.500, 185.00, ""],
                [5, "Drainage", "5.01", "Culvert pipes", "sum", "", 12000, ""],
            ]
        )
        response = self._confirm(token)
        self.assertRedirects(response, reverse("boq:boq_detail", args=[self.project.pk]))

        boq = BOQ.objects.get(project=self.project, version_number=1)
        self.assertEqual(boq.bills.count(), 2)
        bill4 = boq.bills.get(number=4)
        self.assertEqual(bill4.items.count(), 2)
        item_402 = bill4.items.get(item_reference="4.02")
        self.assertEqual(item_402.amount, Decimal("231342.50"))
        heading = bill4.items.get(item_reference="4")
        self.assertEqual(heading.item_type, BOQItem.TYPE_HEADING)
        self.assertIsNone(heading.amount)

        bill5 = boq.bills.get(number=5)
        item_501 = bill5.items.get(item_reference="5.01")
        self.assertEqual(item_501.item_type, BOQItem.TYPE_LUMP_SUM)
        self.assertEqual(item_501.quantity, Decimal("1"))
        self.assertEqual(item_501.amount, Decimal("12000.00"))

        self.assertEqual(boq.grand_total, Decimal("243342.50"))

    def test_confirming_a_file_with_errors_imports_nothing(self):
        self._login(self.qs_user)
        token, _ = self._upload_and_get_token(
            [
                [4, "Sub-base and Base", "4.02", "Sub-base, 150mm", "m³", 1250.500, 185.00, ""],
                [4, "Sub-base and Base", "4.02", "Duplicate reference", "m³", 1, 1, ""],
            ]
        )
        response = self._confirm(token)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Fix every row")
        self.assertEqual(BOQItem.objects.count(), 0)
        self.assertEqual(Bill.objects.count(), 0)

    def test_confirming_twice_the_second_time_rejects_the_now_missing_token(self):
        self._login(self.qs_user)
        token, _ = self._upload_and_get_token(
            [[4, "Sub-base and Base", "4.02", "Sub-base", "m³", 10, 5, ""]]
        )
        first = self._confirm(token)
        self.assertEqual(first.status_code, 302)
        second = self._confirm(token)
        self.assertRedirects(second, reverse("boq:import_upload", args=[self.project.pk]))

    def test_viewer_cannot_confirm_an_import(self):
        self._login(self.qs_user)
        token, _ = self._upload_and_get_token(
            [[4, "Sub-base and Base", "4.02", "Sub-base", "m³", 10, 5, ""]]
        )
        self.client.logout()
        self._login(self.viewer_user)
        response = self._confirm(token)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(BOQItem.objects.count(), 0)


class ExportViewsTests(ImportExportViewsTestCase):
    def setUp(self):
        super().setUp()
        self.boq = BOQ.objects.create(project=self.project, version_number=1)
        bill = Bill.objects.create(boq=self.boq, number=4, title="Sub-base and Base")
        from core.models import UnitOfMeasure

        BOQItem.objects.create(
            bill=bill,
            item_reference="4.02",
            description="Sub-base, 150mm",
            item_type=BOQItem.TYPE_MEASURED,
            unit=UnitOfMeasure.objects.get(code="m³"),
            quantity=Decimal("1250.500"),
            rate=Decimal("185.00"),
        )

    def test_viewer_can_download_xlsx_export(self):
        self._login(self.viewer_user)
        response = self.client.get(reverse("boq:export_xlsx", args=[self.project.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response["Content-Type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        wb = load_workbook(io.BytesIO(response.content))
        self.assertIn("BOQ", wb.sheetnames)
        self.assertIn("Summary", wb.sheetnames)

    def test_viewer_can_download_pdf_export(self):
        self._login(self.viewer_user)
        response = self.client.get(reverse("boq:export_pdf", args=[self.project.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))

    def test_storekeeper_cannot_download_either_export(self):
        self._login(self.storekeeper_user)
        xlsx_response = self.client.get(reverse("boq:export_xlsx", args=[self.project.pk]))
        pdf_response = self.client.get(reverse("boq:export_pdf", args=[self.project.pk]))
        self.assertEqual(xlsx_response.status_code, 403)
        self.assertEqual(pdf_response.status_code, 403)

    def test_import_template_download(self):
        self._login(self.qs_user)
        response = self.client.get(reverse("boq:import_template", args=[self.project.pk]))
        self.assertEqual(response.status_code, 200)
        wb = load_workbook(io.BytesIO(response.content))
        ws = wb.active
        headers = [cell.value for cell in next(ws.iter_rows(min_row=1, max_row=1))]
        self.assertEqual(headers, FLAT_HEADERS)
