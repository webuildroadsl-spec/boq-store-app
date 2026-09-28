import json

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST
from django.db import transaction

from core.models import Project, UnitOfMeasure
from core.permissions import can_edit_boq, can_view_boq, user_can_access_project

from .forms import BillForm, BOQItemForm
from .models import BOQ, Bill, BOQItem


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


@login_required
def boq_detail(request, project_pk):
    """
    The current BOQ for a project: its bills, each bill's total, and the
    grand total. Creates the project's first (Draft, Original, v1) BOQ
    on first visit if one doesn't exist yet — Section 4.2's "Create BOQ
    manually" starts from an empty BOQ, not a form asking to create one.
    """
    project = _get_project_and_check_boq_access(request, project_pk)
    boq, _ = BOQ.objects.get_or_create(
        project=project,
        version_number=1,
        defaults={"type": BOQ.TYPE_ORIGINAL, "status": BOQ.STATUS_DRAFT},
    )

    can_edit = can_edit_boq(request.user, project)

    if request.method == "POST":
        if not can_edit:
            raise PermissionDenied("Your role cannot edit the BOQ.")
        if not boq.is_editable:
            raise PermissionDenied("Only a Draft BOQ version can be edited.")
        bill_form = BillForm(request.POST)
        if bill_form.is_valid():
            bill = bill_form.save(commit=False)
            bill.boq = boq
            bill.save()
            return redirect("boq:boq_detail", project_pk=project.pk)
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
        },
    )


def _item_to_dict(item):
    """The shape the JS grid works with for one row."""
    return {
        "id": item.pk,
        "item_reference": item.item_reference,
        "description": item.description,
        "item_type": item.item_type,
        "unit": item.unit_id,
        "quantity": str(item.quantity) if item.quantity is not None else "",
        "rate": str(item.rate) if item.rate is not None else "",
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

    if not can_edit_boq(request.user, project):
        raise PermissionDenied("Your role cannot edit the BOQ.")
    if not boq.is_editable:
        raise PermissionDenied("Only a Draft BOQ version can be edited.")

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
