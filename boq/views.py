from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render

from core.models import Project
from core.permissions import can_edit_boq, can_view_boq, user_can_access_project

from .forms import BillForm, BOQItemFormSet
from .models import BOQ, Bill


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


@login_required
def bill_items(request, project_pk, bill_pk):
    """The manual-entry grid for one bill's items (Section 4.2)."""
    project = _get_project_and_check_boq_access(request, project_pk)
    bill = get_object_or_404(Bill, pk=bill_pk, boq__project=project)
    boq = bill.boq
    can_edit = can_edit_boq(request.user, project)

    if request.method == "POST":
        if not can_edit:
            raise PermissionDenied("Your role cannot edit the BOQ.")
        if not boq.is_editable:
            raise PermissionDenied("Only a Draft BOQ version can be edited.")
        formset = BOQItemFormSet(
            request.POST,
            instance=bill,
            form_kwargs={"boq": boq},
        )
        if formset.is_valid():
            formset.save()
            return redirect("boq:bill_items", project_pk=project.pk, bill_pk=bill.pk)
    else:
        formset = BOQItemFormSet(instance=bill, form_kwargs={"boq": boq})

    return render(
        request,
        "boq/bill_items.html",
        {
            "project": project,
            "boq": boq,
            "bill": bill,
            "formset": formset,
            "can_edit": can_edit,
        },
    )
