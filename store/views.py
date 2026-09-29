from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Max
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from boq.models import BOQItem
from core.models import Project
from core.permissions import user_can_access_project

from .forms import (
    GRNAttachmentForm,
    GRNForm,
    GRNLineForm,
    IssueForm,
    IssueLineForm,
    MaterialAllowanceForm,
    RequisitionForm,
    RequisitionLineForm,
    ReturnForm,
    ReturnLineForm,
    ReversalForm,
    StockCountForm,
    StockCountLineForm,
    TransferForm,
    TransferLineForm,
)
from .models import (
    GRN,
    Issue,
    MaterialAllowance,
    ReturnToStore,
    StockCount,
    StockMovement,
    Store,
    StoreItem,
    StoreRequisition,
    Supplier,
    Transfer,
)
from .permissions import (
    can_approve_over_allowance_issue,
    can_approve_stock_count,
    can_create_requisition,
    can_create_transfer,
    can_dispatch_transfer,
    can_manage_grn,
    can_manage_issue,
    can_manage_material_allowances,
    can_manage_return,
    can_manage_stock_count,
    can_receive_transfer,
    can_view_store_module,
    stores_for_user,
)


def _get_project_and_check_store_access(request, project_pk):
    project = get_object_or_404(Project, pk=project_pk)
    if not user_can_access_project(request.user, project):
        raise Http404("No Project matches the given query.")
    if not can_view_store_module(request.user, project):
        raise PermissionDenied("Your role does not have access to the store module.")
    return project


def _get_store(request, project, store_pk):
    """404s (not 403) for a store this user can't see at all — either
    it isn't on this project, or they're a Storekeeper who isn't
    assigned to it (see `stores_for_user`)."""
    return get_object_or_404(stores_for_user(project, request.user), pk=store_pk)


@login_required
def store_list(request, project_pk):
    project = _get_project_and_check_store_access(request, project_pk)
    stores = stores_for_user(project, request.user)
    balances_by_store = {}
    for store in stores:
        rows = []
        for item in StoreItem.objects.filter(movements__store=store).distinct():
            quantity, value, average_unit_cost = StockMovement.current_balance(store, item)
            if quantity:
                rows.append(
                    {"item": item, "quantity": quantity, "value": value, "average_unit_cost": average_unit_cost}
                )
        balances_by_store[store.pk] = rows
    return render(
        request,
        "store/store_list.html",
        {"project": project, "stores": stores, "balances_by_store": balances_by_store},
    )


@login_required
def store_detail(request, project_pk, store_pk):
    project = _get_project_and_check_store_access(request, project_pk)
    store = _get_store(request, project, store_pk)
    rows = []
    for item in StoreItem.objects.filter(movements__store=store).distinct():
        quantity, value, average_unit_cost = StockMovement.current_balance(store, item)
        rows.append(
            {"item": item, "quantity": quantity, "value": value, "average_unit_cost": average_unit_cost}
        )
    return render(
        request,
        "store/store_detail.html",
        {
            "project": project,
            "store": store,
            "rows": rows,
            "can_manage": can_manage_grn(request.user, store),
        },
    )


@login_required
def grn_list(request, project_pk, store_pk):
    project = _get_project_and_check_store_access(request, project_pk)
    store = _get_store(request, project, store_pk)
    return render(
        request,
        "store/grn_list.html",
        {
            "project": project,
            "store": store,
            "grns": store.grns.select_related("supplier").all(),
            "can_manage": can_manage_grn(request.user, store),
        },
    )


@login_required
def grn_create(request, project_pk, store_pk):
    project = _get_project_and_check_store_access(request, project_pk)
    store = _get_store(request, project, store_pk)
    if not can_manage_grn(request.user, store):
        raise PermissionDenied("Only this store's storekeeper can record a GRN.")

    if request.method == "POST":
        form = GRNForm(request.POST)
        if form.is_valid():
            grn = form.save(commit=False)
            grn.store = store
            grn.received_by = request.user
            grn.number = _next_grn_number(store)
            grn.save()
            return redirect("store:grn_detail", project_pk=project.pk, store_pk=store.pk, grn_pk=grn.pk)
    else:
        form = GRNForm()

    return render(
        request,
        "store/grn_create.html",
        {
            "project": project,
            "store": store,
            "form": form,
            # For the offline-capable quick-GRN form below the ordinary
            # one (Section 7.2, step 10) -- plain <select> options, not
            # a second Django form, since that form posts as JSON to
            # grn_offline_sync rather than through this view.
            "offline_items": StoreItem.objects.filter(active=True),
            "offline_suppliers": Supplier.objects.all(),
        },
    )


def _next_grn_number(store):
    return (store.grns.aggregate(highest=Max("number"))["highest"] or 0) + 1


@login_required
def grn_detail(request, project_pk, store_pk, grn_pk):
    project = _get_project_and_check_store_access(request, project_pk)
    store = _get_store(request, project, store_pk)
    grn = get_object_or_404(GRN, pk=grn_pk, store=store)
    can_manage = can_manage_grn(request.user, store)

    if request.method == "POST":
        if not can_manage:
            raise PermissionDenied("Only this store's storekeeper can edit this GRN.")
        if not grn.is_editable:
            raise PermissionDenied("A Posted GRN cannot be edited.")
        if "add_attachment" in request.POST:
            attachment_form = GRNAttachmentForm(request.POST, request.FILES)
            line_form = GRNLineForm()
            if attachment_form.is_valid():
                attachment = attachment_form.save(commit=False)
                attachment.grn = grn
                attachment.save()
                return redirect("store:grn_detail", project_pk=project.pk, store_pk=store.pk, grn_pk=grn.pk)
        else:
            line_form = GRNLineForm(request.POST)
            attachment_form = GRNAttachmentForm()
            if line_form.is_valid():
                line = line_form.save(commit=False)
                line.grn = grn
                line.save()
                return redirect("store:grn_detail", project_pk=project.pk, store_pk=store.pk, grn_pk=grn.pk)
    else:
        line_form = GRNLineForm()
        attachment_form = GRNAttachmentForm()

    return render(
        request,
        "store/grn_detail.html",
        {
            "project": project,
            "store": store,
            "grn": grn,
            "lines": grn.lines.select_related("item"),
            "attachments": grn.attachments.all(),
            "line_form": line_form,
            "attachment_form": attachment_form,
            "can_manage": can_manage,
            "reversal_form": ReversalForm(),
        },
    )


@login_required
@require_POST
def grn_line_delete(request, project_pk, store_pk, grn_pk, line_pk):
    project = _get_project_and_check_store_access(request, project_pk)
    store = _get_store(request, project, store_pk)
    grn = get_object_or_404(GRN, pk=grn_pk, store=store)
    if not can_manage_grn(request.user, store):
        raise PermissionDenied("Only this store's storekeeper can edit this GRN.")
    if not grn.is_editable:
        raise PermissionDenied("A Posted GRN cannot be edited.")
    line = get_object_or_404(grn.lines, pk=line_pk)
    line.delete()
    return redirect("store:grn_detail", project_pk=project.pk, store_pk=store.pk, grn_pk=grn.pk)


@login_required
@require_POST
def grn_post(request, project_pk, store_pk, grn_pk):
    project = _get_project_and_check_store_access(request, project_pk)
    store = _get_store(request, project, store_pk)
    grn = get_object_or_404(GRN, pk=grn_pk, store=store)
    if not can_manage_grn(request.user, store):
        raise PermissionDenied("Only this store's storekeeper can post this GRN.")
    try:
        grn.post(request.user)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(request, f"GRN {grn.number} posted. Stock at {store.code} has been updated.")
    return redirect("store:grn_detail", project_pk=project.pk, store_pk=store.pk, grn_pk=grn.pk)


@login_required
@require_POST
def grn_reverse(request, project_pk, store_pk, grn_pk):
    project = _get_project_and_check_store_access(request, project_pk)
    store = _get_store(request, project, store_pk)
    grn = get_object_or_404(GRN, pk=grn_pk, store=store)
    if not can_manage_grn(request.user, store):
        raise PermissionDenied("Only this store's storekeeper can reverse this GRN.")
    form = ReversalForm(request.POST)
    if form.is_valid():
        try:
            grn.reverse(request.user, form.cleaned_data["reason"])
        except ValidationError as exc:
            for message in exc.messages:
                messages.error(request, message)
        else:
            messages.success(request, f"GRN {grn.number} reversed. Stock at {store.code} has been updated.")
    else:
        messages.error(request, "A reason is required to reverse a document.")
    return redirect("store:grn_detail", project_pk=project.pk, store_pk=store.pk, grn_pk=grn.pk)


def _next_number(queryset):
    return (queryset.aggregate(highest=Max("number"))["highest"] or 0) + 1


# ---------------------------------------------------------------------------
# Store requisitions (Section 5.2 point 1). Project-scoped, not
# store-scoped — the requester doesn't pick a store, the storekeeper
# does at issue time.
# ---------------------------------------------------------------------------


@login_required
def requisition_list(request, project_pk):
    project = _get_project_and_check_store_access(request, project_pk)
    return render(
        request,
        "store/requisition_list.html",
        {
            "project": project,
            "requisitions": project.requisitions.select_related("section", "requested_by"),
            "can_create": can_create_requisition(request.user, project),
        },
    )


@login_required
def requisition_create(request, project_pk):
    project = _get_project_and_check_store_access(request, project_pk)
    if not can_create_requisition(request.user, project):
        raise PermissionDenied("Your role cannot create a store requisition.")

    if request.method == "POST":
        form = RequisitionForm(request.POST, project=project)
        if form.is_valid():
            requisition = form.save(commit=False)
            requisition.project = project
            requisition.requested_by = request.user
            requisition.number = _next_number(project.requisitions)
            requisition.save()
            return redirect("store:requisition_detail", project_pk=project.pk, requisition_pk=requisition.pk)
    else:
        form = RequisitionForm(project=project)

    return render(request, "store/requisition_create.html", {"project": project, "form": form})


@login_required
def requisition_detail(request, project_pk, requisition_pk):
    project = _get_project_and_check_store_access(request, project_pk)
    requisition = get_object_or_404(StoreRequisition, pk=requisition_pk, project=project)
    can_create = can_create_requisition(request.user, project)

    if request.method == "POST":
        if not can_create:
            raise PermissionDenied("Your role cannot edit this requisition.")
        if "reject" in request.POST:
            try:
                requisition.reject()
                messages.success(request, f"Requisition {requisition.number} rejected.")
            except ValidationError as exc:
                messages.error(request, " ".join(exc.messages))
            return redirect("store:requisition_detail", project_pk=project.pk, requisition_pk=requisition.pk)

        if not requisition.is_editable:
            raise PermissionDenied("Only a Pending requisition can have lines added.")
        line_form = RequisitionLineForm(request.POST, project=project)
        if line_form.is_valid():
            line = line_form.save(commit=False)
            line.requisition = requisition
            line.save()
            return redirect("store:requisition_detail", project_pk=project.pk, requisition_pk=requisition.pk)
    else:
        line_form = RequisitionLineForm(project=project)

    return render(
        request,
        "store/requisition_detail.html",
        {
            "project": project,
            "requisition": requisition,
            "lines": requisition.lines.select_related("item", "boq_item"),
            "line_form": line_form,
            "can_create": can_create,
        },
    )


# ---------------------------------------------------------------------------
# Issue (Section 5.2 point 3). The step 7 acceptance test: issuing more
# than is in stock is blocked, and an issue without a BOQ item is
# blocked. Both are enforced in Issue.post(); the second is also
# enforced by IssueLineForm's boq_item field simply being required.
# ---------------------------------------------------------------------------


@login_required
def issue_list(request, project_pk, store_pk):
    project = _get_project_and_check_store_access(request, project_pk)
    store = _get_store(request, project, store_pk)
    return render(
        request,
        "store/issue_list.html",
        {
            "project": project,
            "store": store,
            "issues": store.issues.select_related("requisition"),
            "can_manage": can_manage_issue(request.user, store),
        },
    )


@login_required
def issue_create(request, project_pk, store_pk):
    project = _get_project_and_check_store_access(request, project_pk)
    store = _get_store(request, project, store_pk)
    if not can_manage_issue(request.user, store):
        raise PermissionDenied("Only this store's storekeeper can record an issue.")

    if request.method == "POST":
        form = IssueForm(request.POST, project=project)
        if form.is_valid():
            issue = form.save(commit=False)
            issue.store = store
            issue.number = _next_number(store.issues)
            issue.save()
            return redirect("store:issue_detail", project_pk=project.pk, store_pk=store.pk, issue_pk=issue.pk)
    else:
        form = IssueForm(project=project)

    return render(
        request,
        "store/issue_create.html",
        {
            "project": project,
            "store": store,
            "form": form,
            # For the offline-capable quick-issue form (see grn_create's
            # own comment above).
            "offline_items": StoreItem.objects.filter(active=True),
            "offline_boq_items": BOQItem.objects.filter(bill__boq__project=project).exclude(
                item_type=BOQItem.TYPE_HEADING
            ),
        },
    )


@login_required
def issue_detail(request, project_pk, store_pk, issue_pk):
    project = _get_project_and_check_store_access(request, project_pk)
    store = _get_store(request, project, store_pk)
    issue = get_object_or_404(Issue, pk=issue_pk, store=store)
    can_manage = can_manage_issue(request.user, store)

    if request.method == "POST":
        if not can_manage:
            raise PermissionDenied("Only this store's storekeeper can edit this issue.")
        if not issue.is_editable:
            raise PermissionDenied("A Posted issue cannot be edited.")
        line_form = IssueLineForm(request.POST, project=project)
        if line_form.is_valid():
            line = line_form.save(commit=False)
            line.issue = issue
            line.save()
            return redirect("store:issue_detail", project_pk=project.pk, store_pk=store.pk, issue_pk=issue.pk)
    else:
        line_form = IssueLineForm(project=project)

    return render(
        request,
        "store/issue_detail.html",
        {
            "project": project,
            "store": store,
            "issue": issue,
            "lines": issue.lines.select_related("item", "boq_item", "section"),
            "line_form": line_form,
            "can_manage": can_manage,
            "reversal_form": ReversalForm(),
            "can_approve_over_allowance": can_approve_over_allowance_issue(request.user, project),
        },
    )


@login_required
@require_POST
def issue_line_delete(request, project_pk, store_pk, issue_pk, line_pk):
    project = _get_project_and_check_store_access(request, project_pk)
    store = _get_store(request, project, store_pk)
    issue = get_object_or_404(Issue, pk=issue_pk, store=store)
    if not can_manage_issue(request.user, store):
        raise PermissionDenied("Only this store's storekeeper can edit this issue.")
    if not issue.is_editable:
        raise PermissionDenied("A Posted issue cannot be edited.")
    line = get_object_or_404(issue.lines, pk=line_pk)
    line.delete()
    return redirect("store:issue_detail", project_pk=project.pk, store_pk=store.pk, issue_pk=issue.pk)


@login_required
@require_POST
def issue_post(request, project_pk, store_pk, issue_pk):
    project = _get_project_and_check_store_access(request, project_pk)
    store = _get_store(request, project, store_pk)
    issue = get_object_or_404(Issue, pk=issue_pk, store=store)
    if not can_manage_issue(request.user, store):
        raise PermissionDenied("Only this store's storekeeper can post this issue.")
    reason = request.POST.get("over_allowance_reason", "").strip()
    try:
        issue.post(request.user, over_allowance_reason=reason)
    except ValidationError as exc:
        for message in exc.messages:
            messages.error(request, message)
    else:
        issue.refresh_from_db()
        if issue.status == Issue.STATUS_PENDING_APPROVAL:
            messages.success(
                request,
                f"Issue {issue.number} would exceed its material allowance and has been routed to a "
                f"Project Manager for approval. No stock has moved yet.",
            )
        else:
            messages.success(request, f"Issue {issue.number} posted. Stock at {store.code} has been updated.")
    return redirect("store:issue_detail", project_pk=project.pk, store_pk=store.pk, issue_pk=issue.pk)


@login_required
@require_POST
def issue_approve_over_allowance(request, project_pk, store_pk, issue_pk):
    project = _get_project_and_check_store_access(request, project_pk)
    store = _get_store(request, project, store_pk)
    issue = get_object_or_404(Issue, pk=issue_pk, store=store)
    if not can_approve_over_allowance_issue(request.user, project):
        raise PermissionDenied("Only a Project Manager can approve an over-allowance issue.")
    try:
        issue.approve_over_allowance(request.user)
    except ValidationError as exc:
        for message in exc.messages:
            messages.error(request, message)
    else:
        messages.success(
            request, f"Issue {issue.number} approved and posted. Stock at {store.code} has been updated."
        )
    return redirect("store:issue_detail", project_pk=project.pk, store_pk=store.pk, issue_pk=issue.pk)


@login_required
@require_POST
def issue_reverse(request, project_pk, store_pk, issue_pk):
    project = _get_project_and_check_store_access(request, project_pk)
    store = _get_store(request, project, store_pk)
    issue = get_object_or_404(Issue, pk=issue_pk, store=store)
    if not can_manage_issue(request.user, store):
        raise PermissionDenied("Only this store's storekeeper can reverse this issue.")
    form = ReversalForm(request.POST)
    if form.is_valid():
        try:
            issue.reverse(request.user, form.cleaned_data["reason"])
        except ValidationError as exc:
            for message in exc.messages:
                messages.error(request, message)
        else:
            messages.success(request, f"Issue {issue.number} reversed. Stock at {store.code} has been updated.")
    else:
        messages.error(request, "A reason is required to reverse a document.")
    return redirect("store:issue_detail", project_pk=project.pk, store_pk=store.pk, issue_pk=issue.pk)


# ---------------------------------------------------------------------------
# Return to store (Section 5.2 point 5).
# ---------------------------------------------------------------------------


@login_required
def return_list(request, project_pk, store_pk):
    project = _get_project_and_check_store_access(request, project_pk)
    store = _get_store(request, project, store_pk)
    return render(
        request,
        "store/return_list.html",
        {
            "project": project,
            "store": store,
            "returns": store.returns.select_related("linked_issue"),
            "can_manage": can_manage_return(request.user, store),
        },
    )


@login_required
def return_create(request, project_pk, store_pk):
    project = _get_project_and_check_store_access(request, project_pk)
    store = _get_store(request, project, store_pk)
    if not can_manage_return(request.user, store):
        raise PermissionDenied("Only this store's storekeeper can record a return.")

    if request.method == "POST":
        form = ReturnForm(request.POST, store=store)
        if form.is_valid():
            ret = form.save(commit=False)
            ret.store = store
            ret.returned_by = request.user
            ret.number = _next_number(store.returns)
            ret.save()
            return redirect("store:return_detail", project_pk=project.pk, store_pk=store.pk, return_pk=ret.pk)
    else:
        form = ReturnForm(store=store)

    return render(request, "store/return_create.html", {"project": project, "store": store, "form": form})


@login_required
def return_detail(request, project_pk, store_pk, return_pk):
    project = _get_project_and_check_store_access(request, project_pk)
    store = _get_store(request, project, store_pk)
    ret = get_object_or_404(ReturnToStore, pk=return_pk, store=store)
    can_manage = can_manage_return(request.user, store)

    if request.method == "POST":
        if not can_manage:
            raise PermissionDenied("Only this store's storekeeper can edit this return.")
        if not ret.is_editable:
            raise PermissionDenied("A Posted return cannot be edited.")
        line_form = ReturnLineForm(request.POST, project=project)
        if line_form.is_valid():
            line = line_form.save(commit=False)
            line.ret = ret
            line.save()
            return redirect("store:return_detail", project_pk=project.pk, store_pk=store.pk, return_pk=ret.pk)
    else:
        line_form = ReturnLineForm(project=project)

    return render(
        request,
        "store/return_detail.html",
        {
            "project": project,
            "store": store,
            "ret": ret,
            "lines": ret.lines.select_related("item", "boq_item"),
            "line_form": line_form,
            "can_manage": can_manage,
            "reversal_form": ReversalForm(),
        },
    )


@login_required
@require_POST
def return_line_delete(request, project_pk, store_pk, return_pk, line_pk):
    project = _get_project_and_check_store_access(request, project_pk)
    store = _get_store(request, project, store_pk)
    ret = get_object_or_404(ReturnToStore, pk=return_pk, store=store)
    if not can_manage_return(request.user, store):
        raise PermissionDenied("Only this store's storekeeper can edit this return.")
    if not ret.is_editable:
        raise PermissionDenied("A Posted return cannot be edited.")
    line = get_object_or_404(ret.lines, pk=line_pk)
    line.delete()
    return redirect("store:return_detail", project_pk=project.pk, store_pk=store.pk, return_pk=ret.pk)


@login_required
@require_POST
def return_post(request, project_pk, store_pk, return_pk):
    project = _get_project_and_check_store_access(request, project_pk)
    store = _get_store(request, project, store_pk)
    ret = get_object_or_404(ReturnToStore, pk=return_pk, store=store)
    if not can_manage_return(request.user, store):
        raise PermissionDenied("Only this store's storekeeper can post this return.")
    try:
        ret.post(request.user)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(request, f"Return {ret.number} posted. Stock at {store.code} has been updated.")
    return redirect("store:return_detail", project_pk=project.pk, store_pk=store.pk, return_pk=ret.pk)


@login_required
@require_POST
def return_reverse(request, project_pk, store_pk, return_pk):
    project = _get_project_and_check_store_access(request, project_pk)
    store = _get_store(request, project, store_pk)
    ret = get_object_or_404(ReturnToStore, pk=return_pk, store=store)
    if not can_manage_return(request.user, store):
        raise PermissionDenied("Only this store's storekeeper can reverse this return.")
    form = ReversalForm(request.POST)
    if form.is_valid():
        try:
            ret.reverse(request.user, form.cleaned_data["reason"])
        except ValidationError as exc:
            for message in exc.messages:
                messages.error(request, message)
        else:
            messages.success(request, f"Return {ret.number} reversed. Stock at {store.code} has been updated.")
    else:
        messages.error(request, "A reason is required to reverse a document.")
    return redirect("store:return_detail", project_pk=project.pk, store_pk=store.pk, return_pk=ret.pk)


# ---------------------------------------------------------------------------
# Transfer between stores (Section 5.2 point 4).
# ---------------------------------------------------------------------------


@login_required
def transfer_list(request, project_pk):
    project = _get_project_and_check_store_access(request, project_pk)
    return render(
        request,
        "store/transfer_list.html",
        {"project": project, "transfers": project.transfers.select_related("from_store", "to_store")},
    )


@login_required
def transfer_create(request, project_pk):
    project = _get_project_and_check_store_access(request, project_pk)

    if request.method == "POST":
        form = TransferForm(request.POST, project=project)
        if form.is_valid():
            from_store = form.cleaned_data["from_store"]
            if not can_create_transfer(request.user, project, from_store):
                raise PermissionDenied("Only the sending store's storekeeper can start a transfer.")
            transfer = form.save(commit=False)
            transfer.project = project
            transfer.number = _next_number(project.transfers)
            transfer.save()
            return redirect("store:transfer_detail", project_pk=project.pk, transfer_pk=transfer.pk)
    else:
        form = TransferForm(project=project)

    return render(request, "store/transfer_create.html", {"project": project, "form": form})


@login_required
def transfer_detail(request, project_pk, transfer_pk):
    project = _get_project_and_check_store_access(request, project_pk)
    transfer = get_object_or_404(Transfer, pk=transfer_pk, project=project)
    can_dispatch = can_dispatch_transfer(request.user, transfer)
    can_receive = can_receive_transfer(request.user, transfer)

    if request.method == "POST":
        if not can_dispatch:
            raise PermissionDenied("Only the sending store's storekeeper can edit this transfer.")
        if not transfer.is_editable:
            raise PermissionDenied("A transfer can only have lines added while it's still a Draft.")
        line_form = TransferLineForm(request.POST)
        if line_form.is_valid():
            line = line_form.save(commit=False)
            line.transfer = transfer
            line.save()
            return redirect("store:transfer_detail", project_pk=project.pk, transfer_pk=transfer.pk)
    else:
        line_form = TransferLineForm()

    return render(
        request,
        "store/transfer_detail.html",
        {
            "project": project,
            "transfer": transfer,
            "lines": transfer.lines.select_related("item"),
            "line_form": line_form,
            "can_dispatch": can_dispatch,
            "can_receive": can_receive,
        },
    )


@login_required
@require_POST
def transfer_line_delete(request, project_pk, transfer_pk, line_pk):
    project = _get_project_and_check_store_access(request, project_pk)
    transfer = get_object_or_404(Transfer, pk=transfer_pk, project=project)
    if not can_dispatch_transfer(request.user, transfer):
        raise PermissionDenied("Only the sending store's storekeeper can edit this transfer.")
    if not transfer.is_editable:
        raise PermissionDenied("A transfer can only have lines removed while it's still a Draft.")
    line = get_object_or_404(transfer.lines, pk=line_pk)
    line.delete()
    return redirect("store:transfer_detail", project_pk=project.pk, transfer_pk=transfer.pk)


@login_required
@require_POST
def transfer_dispatch(request, project_pk, transfer_pk):
    project = _get_project_and_check_store_access(request, project_pk)
    transfer = get_object_or_404(Transfer, pk=transfer_pk, project=project)
    if not can_dispatch_transfer(request.user, transfer):
        raise PermissionDenied("Only the sending store's storekeeper can dispatch this transfer.")
    try:
        transfer.dispatch(request.user)
    except ValidationError as exc:
        for message in exc.messages:
            messages.error(request, message)
    else:
        messages.success(
            request,
            f"Transfer {transfer.number} dispatched. It is in transit and counts in neither store's stock "
            f"until received.",
        )
    return redirect("store:transfer_detail", project_pk=project.pk, transfer_pk=transfer.pk)


@login_required
@require_POST
def transfer_receive(request, project_pk, transfer_pk):
    project = _get_project_and_check_store_access(request, project_pk)
    transfer = get_object_or_404(Transfer, pk=transfer_pk, project=project)
    if not can_receive_transfer(request.user, transfer):
        raise PermissionDenied("Only the receiving store's storekeeper can receive this transfer.")
    try:
        transfer.receive(request.user)
    except ValidationError as exc:
        for message in exc.messages:
            messages.error(request, message)
    else:
        messages.success(
            request, f"Transfer {transfer.number} received. Stock at {transfer.to_store.code} has been updated."
        )
    return redirect("store:transfer_detail", project_pk=project.pk, transfer_pk=transfer.pk)


# ---------------------------------------------------------------------------
# Stock count (Section 5.2 point 6).
# ---------------------------------------------------------------------------


@login_required
def stock_count_list(request, project_pk, store_pk):
    project = _get_project_and_check_store_access(request, project_pk)
    store = _get_store(request, project, store_pk)
    return render(
        request,
        "store/stock_count_list.html",
        {
            "project": project,
            "store": store,
            "stock_counts": store.stock_counts.all(),
            "can_manage": can_manage_stock_count(request.user, store),
        },
    )


@login_required
def stock_count_create(request, project_pk, store_pk):
    project = _get_project_and_check_store_access(request, project_pk)
    store = _get_store(request, project, store_pk)
    if not can_manage_stock_count(request.user, store):
        raise PermissionDenied("Only this store's storekeeper can start a stock count.")

    if request.method == "POST":
        form = StockCountForm(request.POST)
        if form.is_valid():
            stock_count = form.save(commit=False)
            stock_count.store = store
            stock_count.counted_by = request.user
            stock_count.number = _next_number(store.stock_counts)
            stock_count.save()
            return redirect(
                "store:stock_count_detail", project_pk=project.pk, store_pk=store.pk, stock_count_pk=stock_count.pk
            )
    else:
        form = StockCountForm()

    return render(request, "store/stock_count_create.html", {"project": project, "store": store, "form": form})


@login_required
def stock_count_detail(request, project_pk, store_pk, stock_count_pk):
    project = _get_project_and_check_store_access(request, project_pk)
    store = _get_store(request, project, store_pk)
    stock_count = get_object_or_404(StockCount, pk=stock_count_pk, store=store)
    can_manage = can_manage_stock_count(request.user, store)
    can_approve = can_approve_stock_count(request.user, project)

    if request.method == "POST":
        if "submit" in request.POST:
            if not can_manage:
                raise PermissionDenied("Only this store's storekeeper can submit this stock count.")
            try:
                stock_count.submit()
                messages.success(request, f"Stock count {stock_count.number} submitted for approval.")
            except ValidationError as exc:
                for message in exc.messages:
                    messages.error(request, message)
            return redirect(
                "store:stock_count_detail", project_pk=project.pk, store_pk=store.pk, stock_count_pk=stock_count.pk
            )

        if not can_manage:
            raise PermissionDenied("Only this store's storekeeper can add lines to this stock count.")
        if not stock_count.is_editable:
            raise PermissionDenied("A stock count can only have lines added while it's still a Draft.")
        line_form = StockCountLineForm(request.POST)
        if line_form.is_valid():
            line = line_form.save(commit=False)
            line.stock_count = stock_count
            available, _, _ = StockMovement.current_balance(store, line.item)
            line.system_quantity = available
            line.save()
            return redirect(
                "store:stock_count_detail", project_pk=project.pk, store_pk=store.pk, stock_count_pk=stock_count.pk
            )
    else:
        line_form = StockCountLineForm()

    return render(
        request,
        "store/stock_count_detail.html",
        {
            "project": project,
            "store": store,
            "stock_count": stock_count,
            "lines": stock_count.lines.select_related("item"),
            "line_form": line_form,
            "can_manage": can_manage,
            "can_approve": can_approve,
        },
    )


@login_required
@require_POST
def stock_count_approve(request, project_pk, store_pk, stock_count_pk):
    project = _get_project_and_check_store_access(request, project_pk)
    store = _get_store(request, project, store_pk)
    stock_count = get_object_or_404(StockCount, pk=stock_count_pk, store=store)
    if not can_approve_stock_count(request.user, project):
        raise PermissionDenied("Only a Project Manager can approve a stock count.")
    try:
        stock_count.approve(request.user)
    except ValidationError as exc:
        for message in exc.messages:
            messages.error(request, message)
    else:
        messages.success(
            request, f"Stock count {stock_count.number} approved. Stock at {store.code} has been updated."
        )
    return redirect(
        "store:stock_count_detail", project_pk=project.pk, store_pk=store.pk, stock_count_pk=stock_count.pk
    )


# ---------------------------------------------------------------------------
# Material allowances and reconciliation (Section 6).
# ---------------------------------------------------------------------------


@login_required
def material_allowance_list(request, project_pk):
    project = _get_project_and_check_store_access(request, project_pk)
    allowances = MaterialAllowance.objects.filter(boq_item__bill__boq__project=project).select_related(
        "boq_item", "store_item"
    )
    return render(
        request,
        "store/material_allowance_list.html",
        {
            "project": project,
            "allowances": allowances,
            "can_manage": can_manage_material_allowances(request.user, project),
        },
    )


@login_required
def material_allowance_create(request, project_pk):
    project = _get_project_and_check_store_access(request, project_pk)
    if not can_manage_material_allowances(request.user, project):
        raise PermissionDenied("Only a QS can set material allowances.")

    if request.method == "POST":
        form = MaterialAllowanceForm(request.POST, project=project)
        if form.is_valid():
            form.save()
            return redirect("store:material_allowance_list", project_pk=project.pk)
    else:
        form = MaterialAllowanceForm(project=project)

    return render(request, "store/material_allowance_create.html", {"project": project, "form": form})


@login_required
@require_POST
def material_allowance_delete(request, project_pk, allowance_pk):
    project = _get_project_and_check_store_access(request, project_pk)
    if not can_manage_material_allowances(request.user, project):
        raise PermissionDenied("Only a QS can change material allowances.")
    allowance = get_object_or_404(
        MaterialAllowance.objects.filter(boq_item__bill__boq__project=project), pk=allowance_pk
    )
    allowance.delete()
    return redirect("store:material_allowance_list", project_pk=project.pk)


@login_required
def reconciliation_report(request, project_pk):
    """
    Section 6's "used vs allowed" report and step 9's acceptance
    test: "Allowance 0.32 t/m³, 5% wastage, 100 m³ = 33.6 t allowed;
    issuing 36 t shows +2.4 t, amber." Every `MaterialAllowance` set
    for this project's BOQ items, each with its own reconciliation.
    """
    project = _get_project_and_check_store_access(request, project_pk)
    allowances = MaterialAllowance.objects.filter(boq_item__bill__boq__project=project).select_related(
        "boq_item", "store_item"
    )
    rows = [allowance.reconciliation() for allowance in allowances]
    return render(request, "store/reconciliation_report.html", {"project": project, "rows": rows})


# ---------------------------------------------------------------------------
# Offline sync (Section 7.2's "storekeepers can create GRNs and issues
# offline; they sync when a connection returns"). JSON in, JSON out --
# see pwa/static/pwa/offline-queue.js, which is what actually calls
# these from a form queued while the browser had no connection.
# ---------------------------------------------------------------------------


def _form_errors(*forms):
    errors = []
    for form in forms:
        for field, field_errors in form.errors.items():
            errors.extend(f"{field}: {message}" for message in field_errors)
    return errors


@login_required
@require_POST
def grn_offline_sync(request, project_pk, store_pk):
    """
    Creates a GRN, adds exactly one line, and posts it -- the
    single-line shape an offline-queued GRN always has (a disclosed
    simplification: the online screen supports several lines per GRN,
    added one at a time; the offline form does not). A GRN can't
    itself cause negative stock (it only adds), so posting here only
    fails if the request's own fields don't validate.
    """
    project = _get_project_and_check_store_access(request, project_pk)
    store = _get_store(request, project, store_pk)
    if not can_manage_grn(request.user, store):
        return JsonResponse(
            {"ok": False, "errors": ["Only this store's storekeeper can record a GRN."]}, status=403
        )

    grn_form = GRNForm(request.POST)
    line_form = GRNLineForm(request.POST)
    if not grn_form.is_valid() or not line_form.is_valid():
        return JsonResponse({"ok": False, "errors": _form_errors(grn_form, line_form)})

    with transaction.atomic():
        grn = grn_form.save(commit=False)
        grn.store = store
        grn.received_by = request.user
        grn.number = _next_grn_number(store)
        grn.save()
        line = line_form.save(commit=False)
        line.grn = grn
        line.save()

    try:
        grn.post(request.user)
        posted, errors = True, []
    except ValidationError as exc:
        posted, errors = False, exc.messages

    return JsonResponse(
        {
            "ok": True,
            "posted": posted,
            "errors": errors,
            "message": (
                f"GRN {grn.number} synced and posted."
                if posted
                else f"GRN {grn.number} synced but not posted: {' '.join(errors)}"
            ),
            "detail_url": reverse("store:grn_detail", args=[project.pk, store.pk, grn.pk]),
        }
    )


@login_required
@require_POST
def issue_offline_sync(request, project_pk, store_pk):
    """
    Creates an Issue, adds exactly one line, and attempts to post it
    -- same single-line simplification as `grn_offline_sync`. This is
    where Section 7.2's "the server rejects any synced document that
    would cause negative stock and tells the user" actually applies:
    `Issue.post()` raises `ValidationError` (caught below, not
    propagated as an HTTP error) if the line would exceed stock on
    hand, has no BOQ item, or would exceed a material allowance with
    no `over_allowance_reason` given. The Issue itself is still
    created as a Draft in every case -- rejected only means "not
    posted," so nothing is silently lost; the storekeeper (or a
    Project Manager, for the allowance case) can fix and post it from
    the ordinary online screen.
    """
    project = _get_project_and_check_store_access(request, project_pk)
    store = _get_store(request, project, store_pk)
    if not can_manage_issue(request.user, store):
        return JsonResponse(
            {"ok": False, "errors": ["Only this store's storekeeper can record an issue."]}, status=403
        )

    issue_form = IssueForm(request.POST, project=project)
    line_form = IssueLineForm(request.POST, project=project)
    if not issue_form.is_valid() or not line_form.is_valid():
        return JsonResponse({"ok": False, "errors": _form_errors(issue_form, line_form)})

    with transaction.atomic():
        issue = issue_form.save(commit=False)
        issue.store = store
        issue.number = _next_number(store.issues)
        issue.save()
        line = line_form.save(commit=False)
        line.issue = issue
        line.save()

    reason = request.POST.get("over_allowance_reason", "").strip()
    try:
        issue.post(request.user, over_allowance_reason=reason)
        posted, errors = True, []
    except ValidationError as exc:
        posted, errors = False, exc.messages
    issue.refresh_from_db()

    return JsonResponse(
        {
            "ok": True,
            "posted": posted,
            "pending_approval": issue.status == Issue.STATUS_PENDING_APPROVAL,
            "errors": errors,
            "message": (
                f"Issue {issue.number} synced and posted."
                if posted
                else f"Issue {issue.number} synced but not posted: {' '.join(errors)}"
            ),
            "detail_url": reverse("store:issue_detail", args=[project.pk, store.pk, issue.pk]),
        }
    )
