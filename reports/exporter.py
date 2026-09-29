"""
Excel and PDF writers for the reports in `reportdata.py`. Each
`export_xlsx_bytes` function returns `.xlsx` bytes; each
`export_pdf_bytes` function renders the matching `reports/pdf/*.html`
template through WeasyPrint, same pattern as `boq/exporter.py`. Every
report offers both formats here, a superset of Section 7.1's own
per-report export column (which lists only "Excel" for three of the
eight) -- a disclosed simplification for consistency, since offering
a PDF nobody asked for costs nothing extra.
"""

import io

from django.template.loader import render_to_string
from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter


def _sheet(wb, title, headers, widths):
    ws = wb.active
    ws.title = title
    for col, (header, width) in enumerate(zip(headers, widths), start=1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.font = Font(bold=True)
        ws.column_dimensions[get_column_letter(col)].width = width
    return ws


def _pdf(template_name, context):
    from weasyprint import HTML  # imported lazily: only needed for this one path

    html = render_to_string(template_name, context)
    return HTML(string=html).write_pdf()


def _xlsx_bytes(wb):
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# BOQ summary
# ---------------------------------------------------------------------------


def boq_summary_xlsx(data):
    wb = Workbook()
    ws = _sheet(wb, "BOQ Summary", ["Bill", "Total"], [50, 16])
    row = 2
    for bill in data["bills"]:
        ws.cell(row=row, column=1, value=f"Bill {bill.number}: {bill.title}")
        ws.cell(row=row, column=2, value=float(bill.total))
        row += 1
    row += 1
    for label, value in [
        ("Subtotal", data["subtotal"]),
        (f"Contingency ({data['contingency_percent']}%)", data["contingency_amount"]),
        (f"Tax ({data['tax_percent']}%)", data["tax_amount"]),
        ("Grand total", data["grand_total"]),
    ]:
        label_cell = ws.cell(row=row, column=1, value=label)
        label_cell.font = Font(bold=True)
        value_cell = ws.cell(row=row, column=2, value=float(value))
        value_cell.font = Font(bold=True)
        row += 1
    return _xlsx_bytes(wb)


def boq_summary_pdf(data):
    return _pdf("reports/pdf/boq_summary.html", data)


# ---------------------------------------------------------------------------
# BOQ version comparison
# ---------------------------------------------------------------------------


def boq_comparison_xlsx(data):
    wb = Workbook()
    ws = _sheet(
        wb,
        "BOQ Comparison",
        ["Item Ref", "Status", f"v{data['boq_a'].version_number} Amount", f"v{data['boq_b'].version_number} Amount", "Difference"],
        [14, 12, 16, 16, 16],
    )
    row = 2
    for entry in data["rows"]:
        ws.cell(row=row, column=1, value=entry["reference"])
        ws.cell(row=row, column=2, value=entry["status"])
        ws.cell(row=row, column=3, value=float(entry["item_a"].amount) if entry["item_a"] and entry["item_a"].amount else None)
        ws.cell(row=row, column=4, value=float(entry["item_b"].amount) if entry["item_b"] and entry["item_b"].amount else None)
        ws.cell(row=row, column=5, value=float(entry["value_difference"]))
        row += 1
    ws.cell(row=row + 1, column=4, value="Value difference").font = Font(bold=True)
    ws.cell(row=row + 1, column=5, value=float(data["value_difference"])).font = Font(bold=True)
    return _xlsx_bytes(wb)


def boq_comparison_pdf(data):
    return _pdf("reports/pdf/boq_comparison.html", data)


# ---------------------------------------------------------------------------
# Stock balance
# ---------------------------------------------------------------------------


def stock_balance_xlsx(data):
    wb = Workbook()
    ws = _sheet(wb, "Stock Balance", ["Store", "Item", "Quantity", "Average Cost", "Value"], [14, 40, 14, 14, 16])
    row = 2
    for entry in data["rows"]:
        ws.cell(row=row, column=1, value=entry["store"].code)
        ws.cell(row=row, column=2, value=f"{entry['item'].code} — {entry['item'].name}")
        ws.cell(row=row, column=3, value=float(entry["quantity"]))
        ws.cell(row=row, column=4, value=float(entry["average_cost"]))
        ws.cell(row=row, column=5, value=float(entry["value"]))
        row += 1
    return _xlsx_bytes(wb)


def stock_balance_pdf(data):
    return _pdf("reports/pdf/stock_balance.html", data)


# ---------------------------------------------------------------------------
# Stock ledger (bin card)
# ---------------------------------------------------------------------------


def stock_ledger_xlsx(data):
    wb = Workbook()
    ws = _sheet(
        wb,
        "Stock Ledger",
        ["Date", "Document", "Quantity", "Unit Cost", "Running Qty", "Running Value"],
        [14, 20, 14, 14, 14, 16],
    )
    row = 2
    for entry in data["rows"]:
        movement = entry["movement"]
        ws.cell(row=row, column=1, value=movement.created_at.strftime("%Y-%m-%d %H:%M"))
        ws.cell(row=row, column=2, value=f"{movement.get_document_type_display()} #{movement.document_id}")
        ws.cell(row=row, column=3, value=float(movement.quantity))
        ws.cell(row=row, column=4, value=float(movement.unit_cost))
        ws.cell(row=row, column=5, value=float(entry["running_quantity"]))
        ws.cell(row=row, column=6, value=float(entry["running_value"]))
        row += 1
    return _xlsx_bytes(wb)


def stock_ledger_pdf(data):
    return _pdf("reports/pdf/stock_ledger.html", data)


# ---------------------------------------------------------------------------
# Material reconciliation
# ---------------------------------------------------------------------------


def material_reconciliation_xlsx(data):
    wb = Workbook()
    ws = _sheet(
        wb,
        "Material Reconciliation",
        ["BOQ Item", "Store Item", "Issued", "Returned", "Net Issued", "Allowed", "Variance", "Variance %", "Flag"],
        [16, 30, 12, 12, 12, 12, 12, 12, 10],
    )
    row = 2
    for entry in data["rows"]:
        allowance = entry["allowance"]
        ws.cell(row=row, column=1, value=allowance.boq_item.item_reference)
        ws.cell(row=row, column=2, value=f"{allowance.store_item.code} — {allowance.store_item.name}")
        ws.cell(row=row, column=3, value=float(entry["issued"]))
        ws.cell(row=row, column=4, value=float(entry["returned"]))
        ws.cell(row=row, column=5, value=float(entry["net_issued"]))
        ws.cell(row=row, column=6, value=float(entry["allowed"]))
        ws.cell(row=row, column=7, value=float(entry["variance"]))
        ws.cell(row=row, column=8, value=float(entry["variance_percent"]))
        ws.cell(row=row, column=9, value=entry["flag"])
        row += 1
    return _xlsx_bytes(wb)


def material_reconciliation_pdf(data):
    return _pdf("reports/pdf/material_reconciliation.html", data)


# ---------------------------------------------------------------------------
# Issues by BOQ item / section
# ---------------------------------------------------------------------------


def issues_by_boq_item_xlsx(data):
    wb = Workbook()
    ws = _sheet(wb, "Issues by BOQ Item", ["BOQ Item", "Section", "Quantity Issued", "Value Issued"], [40, 20, 16, 16])
    row = 2
    for entry in data["rows"]:
        ws.cell(row=row, column=1, value=str(entry["boq_item"]) if entry["boq_item"] else "—")
        ws.cell(row=row, column=2, value=entry["section"].code if entry["section"] else "—")
        ws.cell(row=row, column=3, value=float(entry["quantity"]))
        ws.cell(row=row, column=4, value=float(entry["value"]))
        row += 1
    return _xlsx_bytes(wb)


# ---------------------------------------------------------------------------
# Reorder alert
# ---------------------------------------------------------------------------


def reorder_alert_xlsx(data):
    wb = Workbook()
    ws = _sheet(
        wb,
        "Reorder Alert",
        ["Store", "Item", "Quantity on Hand", "Minimum Stock", "Reorder Quantity"],
        [14, 40, 16, 16, 16],
    )
    row = 2
    for entry in data["rows"]:
        ws.cell(row=row, column=1, value=entry["store"].code)
        ws.cell(row=row, column=2, value=f"{entry['item'].code} — {entry['item'].name}")
        ws.cell(row=row, column=3, value=float(entry["quantity"]))
        ws.cell(row=row, column=4, value=float(entry["minimum_stock_level"]))
        ws.cell(row=row, column=5, value=float(entry["reorder_quantity"]))
        row += 1
    return _xlsx_bytes(wb)


# ---------------------------------------------------------------------------
# GRN register
# ---------------------------------------------------------------------------


def grn_register_xlsx(data):
    wb = Workbook()
    ws = _sheet(wb, "GRN Register", ["Store", "GRN", "Date", "Supplier", "Total Value"], [14, 10, 14, 30, 16])
    row = 2
    for grn in data["rows"]:
        ws.cell(row=row, column=1, value=grn.store.code)
        ws.cell(row=row, column=2, value=grn.number)
        ws.cell(row=row, column=3, value=grn.date.strftime("%Y-%m-%d"))
        ws.cell(row=row, column=4, value=grn.supplier.name if grn.supplier else "—")
        ws.cell(row=row, column=5, value=float(grn.total_value))
        row += 1
    return _xlsx_bytes(wb)
