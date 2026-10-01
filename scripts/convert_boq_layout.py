"""
Convert a contract-style BOQ sheet into the app's import layout.

Contract BOQs are laid out for printing: a bill row ("2  ROAD WORKS
GENERAL"), numbered items, unnumbered sub-items labelled "(a)" and
"(i)", subtotal and grand-total rows, and quantities that are Excel
formulas. The importer wants one flat row per item. This script does
that reshaping and checks the result against the sheet's own totals.

Usage (from the app folder, with the venv active):

    python scripts/convert_boq_layout.py "Real Sample BOQ.xlsx"
    python scripts/convert_boq_layout.py in.xlsx out.xlsx --sheet "04-Gbaima Road"

What it does:
  - finds the header row (ITEM, DESCRIPTION, UNIT, QUANTITY, RATE, AMOUNT);
  - a row whose ITEM is a whole number and has no unit starts a new bill;
  - rows with no ITEM get a reference from their label and the item
    above them: "(a)" under 3.21 -> 3.21(a); an indented "(i)" under
    that -> 3.21(a)(i); an unlabelled line under 3.15 -> 3.15(1);
  - subtotal / "carried to summary" / grand-total rows are left out
    (the app calculates its own totals);
  - quantities are the values Excel calculated from the formulas;
    a priced item with no quantity is imported with quantity 0;
  - units are written as the app's codes (m3 -> m³, No. -> nr, L.S. -> sum).

It then recalculates every amount the way the app does and compares
each bill and the grand total with the sheet's own subtotal and
grand-total cells, so you know before importing whether it will match.

Needs the cached formula results Excel saves with the file. If the file
was produced by a program that doesn't store them, open it in Excel and
save it once.
"""

import argparse
import re
import sys
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill

QTY_PLACES = Decimal("0.000001")   # matches BOQItem.quantity (6 dp)
RATE_PLACES = Decimal("0.0001")    # matches BOQItem.rate (4 dp)
CENT = Decimal("0.01")

UNIT_CODES = {
    "m3": "m³", "m³": "m³", "cum": "m³",
    "m2": "m²", "m²": "m²", "sqm": "m²",
    "m": "m", "lm": "m", "km": "km",
    "no": "nr", "nos": "nr", "nr": "nr", "number": "nr", "each": "nr",
    "ls": "sum", "lumpsum": "sum", "sum": "sum",
    "tonne": "t", "tonnes": "t", "t": "t", "ton": "t",
    "kg": "kg", "kgs": "kg",
    "l": "L", "ltr": "L", "litre": "L",
    "ha": "ha", "hectare": "ha",
    "m3km": "m³·km", "m³km": "m³·km", "tkm": "t·km",
    "item": "item", "day": "day", "days": "day", "hr": "hr", "hrs": "hr",
}

LETTER_LABEL = re.compile(r"^\(([a-z])\)\s*", re.I)
ROMAN_LABEL = re.compile(r"^\(((?:x{0,3})(?:ix|iv|v?i{0,3}))\)\s*", re.I)
SKIP_WORDS = ("subtotal", "sub-total", "sub total", "carried to summary", "grand total", "carried forward", "brought forward")


def unit_code(raw):
    key = re.sub(r"[\s.*·×/-]+", "", str(raw).strip().lower())
    return UNIT_CODES.get(key)


def dec(value):
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def clean(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def find_header(ws):
    wanted = {"item": None, "description": None, "unit": None, "quantity": None, "rate": None, "amount": None}
    for row in ws.iter_rows(min_row=1, max_row=40):
        found = {}
        for cell in row:
            text = clean(cell.value).lower()
            for key in wanted:
                if text.startswith(key) and key not in found:
                    found[key] = cell.column
        if len(found) == len(wanted):
            return row[0].row, found
    sys.exit("Couldn't find a header row with ITEM, DESCRIPTION, UNIT, QUANTITY, RATE and AMOUNT.")


def convert(ws):
    header_row, col = find_header(ws)
    get = lambda r, k: ws.cell(r, col[k]).value

    items, problems, sheet_bill_totals = [], [], {}
    bill_no = bill_title = None
    numbered = letter_ref = letter_text = None
    unlabelled_count = 0
    sheet_grand_total = None

    for r in range(header_row + 1, ws.max_row + 1):
        ref_raw, desc_raw = get(r, "item"), get(r, "description")
        unit_raw, qty_raw, rate_raw, amt_raw = get(r, "unit"), get(r, "quantity"), get(r, "rate"), get(r, "amount")
        ref, desc = clean(ref_raw), str(desc_raw or "")
        lowered = (ref + " " + desc).lower()

        if not ref and not desc.strip():
            continue
        if any(word in lowered for word in SKIP_WORDS):
            if "grand total" in lowered:
                sheet_grand_total = dec(amt_raw)
            elif bill_no is not None and dec(amt_raw) is not None:
                sheet_bill_totals[bill_no] = dec(amt_raw)
            continue

        unit = unit_code(unit_raw) if unit_raw not in (None, "") else None
        if unit_raw not in (None, "") and unit is None:
            problems.append(f"Row {r}: unit '{unit_raw}' not recognised; add it to the app's units first.")
        qty, rate = dec(qty_raw), dec(rate_raw)
        is_heading = unit_raw in (None, "") and qty is None and (rate is None or rate == 0)

        # A new bill: "2  ROAD WORKS GENERAL".
        if re.fullmatch(r"\d+", ref) and is_heading:
            bill_no, bill_title = int(ref), clean(desc)
            numbered, letter_ref, letter_text, unlabelled_count = ref, None, None, 0
            continue
        if bill_no is None:
            continue  # title block above the first bill

        indent = len(desc) - len(desc.lstrip(" "))
        text = desc.strip()
        if ref:
            item_ref = ref
            numbered, letter_ref, letter_text, unlabelled_count = ref, None, None, 0
            description = clean(text)
        elif (m := ROMAN_LABEL.match(text)) and m.group(1) and indent >= 4 and letter_ref:
            item_ref = f"{letter_ref}({m.group(1).lower()})"
            own = clean(text[m.end():])
            description = f"{letter_text.rstrip(':')}: {own}" if letter_text else own
        elif (m := LETTER_LABEL.match(text)):
            item_ref = f"{numbered}({m.group(1).lower()})"
            letter_ref, letter_text = item_ref, clean(text[m.end():])
            description = letter_text
        elif is_heading:
            item_ref = f"{bill_no}-{re.sub(r'[^A-Z0-9]+', '', text.upper())[:12]}"
            numbered, letter_ref, letter_text, unlabelled_count = item_ref, None, None, 0
            description = clean(text)
        else:
            unlabelled_count += 1
            item_ref = f"{numbered}({unlabelled_count})"
            description = clean(text)

        if is_heading:
            items.append(dict(row=r, bill=bill_no, title=bill_title, ref=item_ref, desc=description,
                              unit=None, qty=None, rate=None, amount=None, sheet_amount=None))
            continue

        if qty is None:
            qty = Decimal("0")
            problems.append(f"Row {r} ({item_ref}): rate but no quantity; imported with quantity 0.")
        if rate is None:
            rate = Decimal("0")
        if unit == "sum":
            qty = Decimal("1")
        q6, r4 = qty.quantize(QTY_PLACES, ROUND_HALF_UP), rate.quantize(RATE_PLACES, ROUND_HALF_UP)
        if abs(r4 - rate) > Decimal("0.000000001"):  # ignore Excel float noise like 2034.2400000000002
            problems.append(f"Row {r} ({item_ref}): rate {rate} has more than 4 decimals; stored as {r4}.")
        amount = (q6 * r4).quantize(CENT, ROUND_HALF_UP)
        sheet_amount = dec(amt_raw)
        if sheet_amount is not None and sheet_amount.quantize(CENT, ROUND_HALF_UP) != amount:
            problems.append(f"Row {r} ({item_ref}): sheet amount {sheet_amount} but qty x rate = {amount}.")
        items.append(dict(row=r, bill=bill_no, title=bill_title, ref=item_ref, desc=description[:500],
                          unit=unit or unit_raw, qty=q6, rate=r4, amount=amount, sheet_amount=sheet_amount))

    refs = [i["ref"] for i in items]
    for ref in {x for x in refs if refs.count(x) > 1}:
        problems.append(f"Item reference '{ref}' appears more than once; rename one before importing.")
    return items, problems, sheet_bill_totals, sheet_grand_total


def write_output(items, path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "BOQ import"
    headers = ["Bill number", "Bill title", "Item reference", "Description", "Unit", "Quantity", "Rate", "Amount (check only)"]
    ws.append(headers)
    for i in items:
        ws.append([i["bill"], i["title"], i["ref"], i["desc"], i["unit"],
                   float(i["qty"]) if i["qty"] is not None else None,
                   float(i["rate"]) if i["rate"] is not None else None,
                   float(i["amount"]) if i["amount"] is not None else None])
    bold = Font(bold=True)
    for cell in ws[1]:
        cell.font = bold
        cell.fill = PatternFill("solid", fgColor="DDEBF7")
    heading_fill = PatternFill("solid", fgColor="F2F2F2")
    for row in ws.iter_rows(min_row=2):
        if row[5].value is None:
            for cell in row:
                cell.font = bold
                cell.fill = heading_fill
        row[3].alignment = Alignment(wrap_text=True, vertical="top")
        row[5].number_format = "#,##0.000###"
        row[6].number_format = "#,##0.000#"
        row[7].number_format = "#,##0.00"
    for letter, width in zip("ABCDEFGH", (8, 28, 14, 70, 8, 14, 12, 16)):
        ws.column_dimensions[letter].width = width
    ws.freeze_panes = "A2"
    wb.save(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("source")
    parser.add_argument("output", nargs="?")
    parser.add_argument("--sheet")
    args = parser.parse_args()

    source = Path(args.source)
    output = Path(args.output) if args.output else source.with_name(source.stem + " - import.xlsx")
    wb = openpyxl.load_workbook(source, data_only=True)
    ws = wb[args.sheet] if args.sheet else wb.active

    # A formula cell with no saved result means the values can't be trusted.
    formulas = openpyxl.load_workbook(source)[ws.title]
    for row in formulas.iter_rows():
        for cell in row:
            if isinstance(cell.value, str) and cell.value.startswith("=") and ws[cell.coordinate].value is None:
                sys.exit(f"Cell {cell.coordinate} is a formula with no saved result. "
                         "Open the file in Excel, save it, and run this again.")

    items, problems, sheet_bill_totals, sheet_grand_total = convert(ws)
    write_output(items, output)

    print(f"Sheet '{ws.title}': {len(items)} rows "
          f"({sum(i['qty'] is None for i in items)} headings, {sum(i['qty'] is not None for i in items)} items)")
    print(f"\n{'Bill':>4}  {'Converted':>16}  {'Sheet subtotal':>16}")
    ok = True
    grand = Decimal("0")
    for bill in sorted({i["bill"] for i in items}):
        total = sum((i["amount"] for i in items if i["bill"] == bill and i["amount"] is not None), Decimal("0"))
        grand += total
        sheet = sheet_bill_totals.get(bill)
        match = sheet is None or sheet.quantize(CENT, ROUND_HALF_UP) == total
        ok &= match
        print(f"{bill:>4}  {total:>16,.2f}  {(f'{sheet:,.2f}' if sheet is not None else '-'):>16}  {'OK' if match else 'DIFFERENT'}")
    if sheet_grand_total is not None:
        match = sheet_grand_total.quantize(CENT, ROUND_HALF_UP) == grand
        ok &= match
        print(f"\nGrand total: converted {grand:,.2f}, sheet {sheet_grand_total:,.2f}  {'OK' if match else 'DIFFERENT'}")
    else:
        print(f"\nGrand total: converted {grand:,.2f} (no grand-total row found to compare)")
    if problems:
        print("\nNotes:")
        for p in problems:
            print("  -", p)
    print(f"\nWritten: {output}")
    print("All totals match." if ok else "Some totals differ -- check the notes above before importing.")


if __name__ == "__main__":
    main()
