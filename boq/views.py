import json
import os
import uuid
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST
from django.db import transaction
from django.db.models import Max

from django.utils import timezone

from core.models import Project, UnitOfMeasure
from core.permissions import can_approve_boq, can_edit_boq, can_view_boq, user_can_access_project

from . import exporter, importer, numbers
from .forms import BillForm, BOQItemForm, VariationOrderForm
from .models import BOQ, Bill, BOQItem, VariationOrder

IMPORT_TMP_DIR = os.path.join(settings.BASE_DIR, "boq", "import_tmp")


def _get_project_and_check_boq_access(request, project_pk):
    """
    Shared entry check for every BOQ view: 404 if the user isn't on the
    project at all (same reasoning as core.views.project_detail — don't
    confirm the project exists to a stranger), 403 if they're a project
    member but their role has no BOQ access (a Storekeeper, per Section
    2's "Create and edit BOQ" row).
    """
    project = get_object_or_404(Project, pk=project_pk)
    if not user_can_access_project(request.user, project):
        raise Http404("No Project matches the given query.")
    if not can_view_boq(request.user, project):
        raise PermissionDenied("Your role does not have access to the BOQ.")
    return project


def _current_boq(project):
    """
    The BOQ version the plain "/boq/" URL (no version specified) shows
    and edits by default: the most recent Draft in progress if there is
    one (there's normally at most one — either the original before its
    first approval, or a revision/VO being worked on now), else the
    current Approved version (view-only at that point), else whichever
    version is newest, else a brand-new v1 Draft if the project has no
    BOQ at all yet — Section 4.2's "Create BOQ manually" starts from an
    empty BOQ, not a form asking to create one.

    A specific version, once it exists, is always reachable by its own
    URL (`boq_version_detail`) regardless of which one this picks.
    """
    boq = project.boqs.filter(status=BOQ.STATUS_DRAFT).order_by("-version_number").first()
    if boq is None:
        boq = project.boqs.filter(status=BOQ.STATUS_APPROVED).order_by("-version_number").first()
    if boq is None:
        boq = project.boqs.order_by("-version_number").first()
    if boq is None:
        boq = BOQ.objects.create(
            project=project, version_number=1, type=BOQ.TYPE_ORIGINAL, status=BOQ.STATUS_DRAFT
        )
    return boq


def _require_boq_editable(request, project, boq):
    if not can_edit_boq(request.user, project):
        raise PermissionDenied("Your role cannot edit the BOQ.")
    if not boq.is_editable:
        raise PermissionDenied("Only a Draft BOQ version can be edited.")


def _render_boq_detail(request, project, boq):
    """Shared by `boq_detail` (the default version) and
    `boq_version_detail` (an explicit one): bills, totals, the
    add-a-bill form, and — for this version's own status — the
    revise/approve actions `boq_list` links to."""
    can_edit = can_edit_boq(request.user, project)

    if request.method == "POST":
        _require_boq_editable(request, project, boq)
        bill_form = BillForm(request.POST)
        if bill_form.is_valid():
            bill = bill_form.save(commit=False)
            bill.boq = boq
            bill.save()
            return redirect("boq:boq_version_detail", project_pk=project.pk, boq_pk=boq.pk)
    else:
        bill_form = BillForm(initial={"number": boq.bills.count() + 1})

    return render(
        request,
        "boq/boq_detail.html",
        {
            "project": project,
            "boq": boq,
            "bills": boq.bills.all(),
            "bill_form": bill_form,
            "can_edit": can_edit,
            "can_approve": can_approve_boq(request.user, project),
            "variation_order": getattr(boq, "variation_order", None),
            # Import/export (still project-wide, not per-version — see
            # the README's step 5 notes) only make sense to offer from
            # the version they'd actually act on.
            "is_current": boq.pk == _current_boq(project).pk,
        },
    )


@login_required
def boq_detail(request, project_pk):
    """The default BOQ view for a project — see `_current_boq`."""
    project = _get_project_and_check_boq_access(request, project_pk)
    boq = _current_boq(project)
    return _render_boq_detail(request, project, boq)


@login_required
def boq_version_detail(request, project_pk, boq_pk):
    """One specific BOQ version, by id — how `boq_list`, `compare_versions`
    and "create revision"/"new VO" link to a version that isn't
    necessarily the default one `boq_detail` would show."""
    project = _get_project_and_check_boq_access(request, project_pk)
    boq = get_object_or_404(BOQ, pk=boq_pk, project=project)
    return _render_boq_detail(request, project, boq)


@login_required
def boq_list(request, project_pk):
    """
    Every version of this project's BOQ (Section 4.2's "Compare
    versions" starting point, and where "Create revision" / "New
    variation order" live) — original, revisions, and VOs together,
    newest first.
    """
    project = _get_project_and_check_boq_access(request, project_pk)
    boqs = project.boqs.select_related("approved_by").order_by("-version_number")
    current_approved = boqs.filter(status=BOQ.STATUS_APPROVED).first()
    return render(
        request,
        "boq/boq_list.html",
        {
            "project": project,
            "boqs": boqs,
            "current_approved": current_approved,
            "can_edit": can_edit_boq(request.user, project),
            "can_approve": can_approve_boq(request.user, project),
        },
    )


@login_required
@require_POST
def create_revision(request, project_pk, boq_pk):
    """
    Section 4.2's "Revise BOQ": "copying an approved BOQ creates a new
    Draft version." Only makes sense starting from the current Approved
    version — copying a Draft would just be a second, redundant Draft,
    and copying a Superseded one would resurrect old figures instead of
    revising the current contract.
    """
    project = _get_project_and_check_boq_access(request, project_pk)
    boq = get_object_or_404(BOQ, pk=boq_pk, project=project)
    if not can_edit_boq(request.user, project):
        raise PermissionDenied("Your role cannot edit the BOQ.")
    if boq.status != BOQ.STATUS_APPROVED:
        raise PermissionDenied("Only the Approved version can be revised.")
    new_boq = boq.create_revision()
    messages.success(request, f"Created v{new_boq.version_number} (Revision) as a new Draft.")
    return redirect("boq:boq_version_detail", project_pk=project.pk, boq_pk=new_boq.pk)


@login_required
@require_POST
def approve_boq(request, project_pk, boq_pk):
    """
    Rules 4/5/8: approves this Draft version, superseding whichever
    version was previously Approved on the project — the step 5
    acceptance test ("Approving Rev 1 supersedes Original").
    """
    project = _get_project_and_check_boq_access(request, project_pk)
    boq = get_object_or_404(BOQ, pk=boq_pk, project=project)
    if not can_approve_boq(request.user, project):
        raise PermissionDenied("Your role cannot approve a BOQ version.")
    if boq.status != BOQ.STATUS_DRAFT:
        raise PermissionDenied("Only a Draft version can be approved.")
    boq.approve(request.user)
    messages.success(
        request, f"v{boq.version_number} is now Approved. Any previous Approved version is now Superseded."
    )
    return redirect("boq:boq_version_detail", project_pk=project.pk, boq_pk=boq.pk)


@login_required
def vo_list(request, project_pk):
    """Every variation order on this project, and the "New variation order" form."""
    project = _get_project_and_check_boq_access(request, project_pk)
    can_edit = can_edit_boq(request.user, project)
    current_approved = project.boqs.filter(status=BOQ.STATUS_APPROVED).order_by("-version_number").first()

    if request.method == "POST":
        if not can_edit:
            raise PermissionDenied("Your role cannot create a variation order.")
        if current_approved is None:
            messages.error(
                request, "You need an Approved BOQ version before you can create a variation order."
            )
            return redirect("boq:vo_list", project_pk=project.pk)
        form = VariationOrderForm(request.POST)
        if form.is_valid():
            new_boq = current_approved.create_revision(revision_type=BOQ.TYPE_VARIATION)
            next_number = (
                project.variation_orders.aggregate(highest=Max("number"))["highest"] or 0
            ) + 1
            vo = form.save(commit=False)
            vo.project = project
            vo.number = next_number
            vo.base_boq = current_approved
            vo.linked_boq = new_boq
            vo.save()
            messages.success(
                request, f"VO {vo.number} created as v{new_boq.version_number} — add, omit or change items, then approve it."
            )
            return redirect("boq:boq_version_detail", project_pk=project.pk, boq_pk=new_boq.pk)
    else:
        form = VariationOrderForm(initial={"date": timezone.localdate()})

    return render(
        request,
        "boq/vo_list.html",
        {
            "project": project,
            "variation_orders": project.variation_orders.select_related("linked_boq", "base_boq"),
            "form": form,
            "can_edit": can_edit,
            "current_approved": current_approved,
        },
    )


@login_required
def compare_versions(request, project_pk):
    """
    Section 4.2's "Compare versions": pick any two of this project's
    BOQ versions and see, item by item (matched by item_reference),
    what was added, removed or changed, plus the overall value
    difference — the other half of the step 5 acceptance test.
    """
    project = _get_project_and_check_boq_access(request, project_pk)
    boqs = project.boqs.order_by("-version_number")

    a_pk = request.GET.get("a")
    b_pk = request.GET.get("b")
    context = {"project": project, "boqs": boqs, "a_pk": a_pk, "b_pk": b_pk}

    if a_pk and b_pk:
        boq_a = get_object_or_404(BOQ, pk=a_pk, project=project)
        boq_b = get_object_or_404(BOQ, pk=b_pk, project=project)
        context.update(
            {
                "boq_a": boq_a,
                "boq_b": boq_b,
                "rows": _compare_boq_versions(boq_a, boq_b),
                "value_difference": boq_b.grand_total - boq_a.grand_total,
            }
        )

    return render(request, "boq/compare.html", context)


def _compare_boq_versions(boq_a, boq_b):
    """
    One row per item_reference that appears in either version:
    "added" (only in b), "removed" (only in a), "changed" (in both but
    some field differs), or "unchanged". Headings compare by
    description only, since they carry no quantity/rate/amount.
    """
    items_a = {item.item_reference: item for bill in boq_a.bills.all() for item in bill.items.all()}
    items_b = {item.item_reference: item for bill in boq_b.bills.all() for item in bill.items.all()}

    rows = []
    for reference in sorted(set(items_a) | set(items_b)):
        item_a = items_a.get(reference)
        item_b = items_b.get(reference)
        if item_a is None:
            status = "added"
        elif item_b is None:
            status = "removed"
        else:
            fields = ("description", "item_type", "unit_id", "quantity", "rate", "amount")
            status = "changed" if any(getattr(item_a, f) != getattr(item_b, f) for f in fields) else "unchanged"
        rows.append(
            {
                "reference": reference,
                "item_a": item_a,
                "item_b": item_b,
                "status": status,
                "value_difference": (item_b.amount if item_b and item_b.amount else Decimal("0.00"))
                - (item_a.amount if item_a and item_a.amount else Decimal("0.00")),
            }
        )
    return rows


def _item_to_dict(item):
    """The shape the JS grid works with for one row."""
    return {
        "id": item.pk,
        "item_reference": item.item_reference,
        "description": item.description,
        "item_type": item.item_type,
        "unit": item.unit_id,
        "quantity": numbers.quantity(item.quantity),
        "rate": numbers.rate(item.rate),
        "amount": str(item.amount) if item.amount is not None else "",
        "section": item.section_id,
        "parent_item": item.parent_item_id,
        "sort_order": item.sort_order,
    }


@login_required
@require_GET
def bill_items(request, project_pk, bill_pk):
    """
    The manual-entry grid for one bill's items (Section 4.2).

    Rendering only: the grid itself is a vanilla-JS spreadsheet-style
    table (static/boq/grid.js) that reads its starting data from the
    JSON blob embedded below and saves through `bill_items_save`. This
    view never handles a POST — all writes go through that JSON API, so
    the same BOQItemForm validation (rules 1-3) that guarded the old
    Django-formset grid still guards every save, just over JSON instead
    of form-encoded fields.
    """
    project = _get_project_and_check_boq_access(request, project_pk)
    bill = get_object_or_404(Bill, pk=bill_pk, boq__project=project)
    boq = bill.boq
    can_edit = can_edit_boq(request.user, project) and boq.is_editable

    grid_data = {
        "items": [_item_to_dict(item) for item in bill.items.all()],
        "units": list(UnitOfMeasure.objects.values("id", "code", "name")),
        "sections": list(project.sections.values("id", "code", "name")),
        "item_types": list(BOQItem.TYPE_CHOICES),
        "lump_sum_types": list(BOQItem.LUMP_SUM_TYPES),
        "heading_type": BOQItem.TYPE_HEADING,
        "can_edit": can_edit,
        "save_url": reverse(
            "boq:bill_items_save", kwargs={"project_pk": project.pk, "bill_pk": bill.pk}
        ),
        "bill_total": str(bill.total),
    }

    return render(
        request,
        "boq/bill_items.html",
        {
            "project": project,
            "boq": boq,
            "bill": bill,
            "can_edit": can_edit,
            "grid_data_json": json.dumps(grid_data),
        },
    )


@login_required
@require_POST
def bill_items_save(request, project_pk, bill_pk):
    """
    JSON API the grid saves to: the whole bill's rows in one request,
    validated and saved atomically. Every rule the old formset enforced
    (amount calculation, headings, lump sum handling, unique item
    reference within the BOQ) still runs, because each row is validated
    through the same `BOQItemForm` used before — only the transport
    changed from form-encoded formset data to a JSON body.
    """
    project = _get_project_and_check_boq_access(request, project_pk)
    bill = get_object_or_404(Bill, pk=bill_pk, boq__project=project)
    boq = bill.boq

    _require_boq_editable(request, project, boq)

    try:
        payload = json.loads(request.body)
        rows = payload["rows"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return JsonResponse({"ok": False, "error": "Malformed request body."}, status=400)

    existing_items = {item.pk: item for item in bill.items.all()}
    errors = {}
    to_delete = []
    forms_to_save = []

    for index, row in enumerate(rows):
        row_id = row.get("id")
        if row.get("delete"):
            if row_id in existing_items:
                to_delete.append(existing_items[row_id])
            continue

        if row_id is not None and row_id in existing_items:
            instance = existing_items[row_id]
        elif row_id is not None:
            # An id that doesn't belong to this bill: refuse rather than
            # silently editing a row that isn't the client's to touch.
            errors[index] = {"__all__": ["Unknown item id for this bill."]}
            continue
        else:
            instance = BOQItem(bill=bill)

        form_data = {
            "item_reference": row.get("item_reference", ""),
            "description": row.get("description", ""),
            "item_type": row.get("item_type", ""),
            "unit": row.get("unit") or "",
            "quantity": row.get("quantity", ""),
            "rate": row.get("rate", ""),
            "section": row.get("section") or "",
            "parent_item": row.get("parent_item") or "",
            "sort_order": row.get("sort_order", 0) or 0,
        }
        form = BOQItemForm(data=form_data, instance=instance, boq=boq)
        if instance.pk is None and not (
            form_data["item_reference"] or form_data["description"] or form_data["item_type"]
        ):
            # A new row with no real content typed into it yet (the
            # grid always sends every visible row, blank trailing ones
            # included): skip it rather than reporting "this field is
            # required" for a row nobody has started filling in.
            continue
        if form.is_valid():
            form.instance.bill = bill
            forms_to_save.append(form)
        else:
            errors[index] = form.errors.get_json_data()

    if errors:
        return JsonResponse({"ok": False, "errors": errors}, status=400)

    with transaction.atomic():
        for item in to_delete:
            item.delete()
        for form in forms_to_save:
            form.save()

    bill.refresh_from_db()
    return JsonResponse(
        {
            "ok": True,
            "items": [_item_to_dict(item) for item in bill.items.all()],
            "bill_total": str(bill.total),
            "grand_total": str(boq.grand_total),
        }
    )


# ---------------------------------------------------------------------------
# Excel / PDF export (Section 4.2's "Export BOQ"). Read-only: gated on
# can_view_boq, not can_edit_boq, since anyone who can see the BOQ should
# be able to download it.
# ---------------------------------------------------------------------------


@login_required
def export_xlsx(request, project_pk):
    project = _get_project_and_check_boq_access(request, project_pk)
    boq = _current_boq(project)
    content = exporter.export_xlsx_bytes(boq)
    response = HttpResponse(
        content,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = (
        f'attachment; filename="boq_{project.code}_v{boq.version_number}.xlsx"'
    )
    return response


@login_required
def export_pdf(request, project_pk):
    project = _get_project_and_check_boq_access(request, project_pk)
    boq = _current_boq(project)
    content = exporter.export_pdf_bytes(boq)
    response = HttpResponse(content, content_type="application/pdf")
    response["Content-Disposition"] = (
        f'attachment; filename="boq_{project.code}_v{boq.version_number}.pdf"'
    )
    return response


# ---------------------------------------------------------------------------
# Excel import (Section 4.2's "Import BOQ from Excel"). Upload -> preview
# (with an adjustable column mapping) -> confirm, all-or-nothing: nothing
# is saved to the database until confirm re-validates every row and finds
# no errors. Requires edit access, same as the manual-entry grid.
# ---------------------------------------------------------------------------


def _import_tmp_path(token):
    os.makedirs(IMPORT_TMP_DIR, exist_ok=True)
    # token is a uuid4 hex we generated ourselves, never taken from the
    # request unescaped into a path — see import_preview/import_confirm.
    return os.path.join(IMPORT_TMP_DIR, f"{token}.xlsx")


def _mapping_from_request(request, headers):
    mapping = {}
    for field in importer.ALL_FIELDS:
        raw = request.POST.get(f"map_{field}", "")
        if raw.isdigit() and int(raw) < len(headers):
            mapping[field] = int(raw)
    return mapping


def _render_import_preview(request, project, boq, token, headers, mapping, confirm_error=None):
    rows = importer.parse_rows(_import_tmp_path(token), mapping, project, boq)
    bill_numbers = {row["bill_number"] for row in rows if row["bill_number"] is not None}
    return render(
        request,
        "boq/import_preview.html",
        {
            "project": project,
            "boq": boq,
            "token": token,
            "headers": list(enumerate(headers)),
            "fields": importer.ALL_FIELDS,
            "field_labels": importer.FIELD_LABELS,
            "mapping": mapping,
            "rows": rows,
            "row_count": len(rows),
            "error_count": sum(1 for row in rows if row["errors"]),
            "bill_count": len(bill_numbers),
            "confirm_error": confirm_error,
        },
    )


def _valid_token_path(token):
    """
    A safe path for a token straight from the request: reject anything
    that isn't a bare uuid4 hex string before it ever reaches os.path,
    so a crafted token can't be used to read or write outside
    IMPORT_TMP_DIR.
    """
    if not token or len(token) != 32 or not all(c in "0123456789abcdef" for c in token):
        return None
    path = _import_tmp_path(token)
    return path if os.path.exists(path) else None


@login_required
def import_upload(request, project_pk):
    """GET: the upload form. POST: save the file and show the mapping/preview screen."""
    project = _get_project_and_check_boq_access(request, project_pk)
    boq = _current_boq(project)
    _require_boq_editable(request, project, boq)

    if request.method == "POST":
        uploaded = request.FILES.get("boq_file")
        if not uploaded:
            return render(
                request,
                "boq/import_upload.html",
                {"project": project, "boq": boq, "error": "Choose a file to upload."},
            )

        token = uuid.uuid4().hex
        path = _import_tmp_path(token)
        with open(path, "wb") as destination:
            for chunk in uploaded.chunks():
                destination.write(chunk)

        try:
            headers = importer.read_headers(path)
        except Exception:
            os.remove(path)
            return render(
                request,
                "boq/import_upload.html",
                {
                    "project": project,
                    "boq": boq,
                    "error": "Could not read that file as an Excel workbook (.xlsx).",
                },
            )

        mapping = importer.auto_detect_mapping(headers)
        return _render_import_preview(request, project, boq, token, headers, mapping)

    return render(request, "boq/import_upload.html", {"project": project, "boq": boq})


@login_required
@require_POST
def import_preview(request, project_pk):
    """The "Update preview" button: re-parses the same uploaded file with an adjusted mapping."""
    project = _get_project_and_check_boq_access(request, project_pk)
    boq = _current_boq(project)
    _require_boq_editable(request, project, boq)

    path = _valid_token_path(request.POST.get("token", ""))
    if path is None:
        return redirect("boq:import_upload", project_pk=project.pk)

    headers = importer.read_headers(path)
    mapping = _mapping_from_request(request, headers)
    return _render_import_preview(
        request, project, boq, request.POST.get("token", ""), headers, mapping
    )


@login_required
@require_POST
def import_confirm(request, project_pk):
    """
    The "Confirm import" button: re-validates every row server-side one
    more time (never trusts the preview the browser is showing) and,
    only if none of them have errors, creates the bills and items
    inside one transaction. A single bad row aborts the whole import —
    there is no partial import.
    """
    project = _get_project_and_check_boq_access(request, project_pk)
    boq = _current_boq(project)
    _require_boq_editable(request, project, boq)

    token = request.POST.get("token", "")
    path = _valid_token_path(token)
    if path is None:
        return redirect("boq:import_upload", project_pk=project.pk)

    headers = importer.read_headers(path)
    mapping = _mapping_from_request(request, headers)
    rows = importer.parse_rows(path, mapping, project, boq)

    if not rows:
        return _render_import_preview(
            request, project, boq, token, headers, mapping,
            confirm_error="No rows were found to import.",
        )
    if any(row["errors"] for row in rows):
        return _render_import_preview(
            request, project, boq, token, headers, mapping,
            confirm_error=(
                "Fix every row's errors below before confirming — "
                "nothing has been imported yet."
            ),
        )

    bills_by_number = {}
    with transaction.atomic():
        next_sort_order = boq.bills.count()
        for row in rows:
            bill = bills_by_number.get(row["bill_number"])
            if bill is None:
                bill, created = Bill.objects.get_or_create(
                    boq=boq,
                    number=row["bill_number"],
                    defaults={"title": row["bill_title"], "sort_order": next_sort_order},
                )
                if created:
                    next_sort_order += 1
                bills_by_number[row["bill_number"]] = bill

            item = BOQItem(
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
            # Rules 1/2 (amount calculation, lump sum quantity/unit
            # forcing) are applied by BOQItem.save() itself, same as
            # every other way an item gets created — not recomputed
            # here, so there's exactly one place that logic lives.
            item.save()

    os.remove(path)
    messages.success(
        request,
        f"Imported {len(rows)} item(s) across {len(bills_by_number)} bill(s). "
        f"New grand total: {boq.grand_total}.",
    )
    return redirect("boq:boq_detail", project_pk=project.pk)


@login_required
def import_template(request, project_pk):
    """Downloads a blank .xlsx with the flat-layout header row and a couple of example rows."""
    project = _get_project_and_check_boq_access(request, project_pk)
    _current_boq(project)  # just to reuse the same access check as the rest of import/export
    content = exporter.build_import_template_bytes()
    response = HttpResponse(
        content,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="boq_import_template.xlsx"'
    return response
