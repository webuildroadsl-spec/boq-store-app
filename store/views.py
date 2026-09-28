from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Max
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from core.models import Project
from core.permissions import user_can_access_project

from .forms import (
    GRNAttachmentForm,
    GRNForm,
    GRNLineForm,
    IssueForm,
    IssueLineForm,
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
    ReturnToStore,
    StockCount,
    StockMovement,
    Store,
    StoreItem,
    StoreRequisition,
    Transfer,
)
from .permissions import (
    can_approve_stock_count,
    can_create_requisition,
    can_create_transfer,
    can_dispatch_transfer,
    can_manage_grn,
    can_manage_issue,
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

    return render(request, "store/grn_create.html", {"project": project, "store": store, "form": form})


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

    return render(request, "store/issue_create.html", {"project": project, "store": store, "form": form})


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
    try:
        issue.post(request.user)
    except ValidationError as exc:
        for message in exc.messages:
            messages.error(request, message)
    else:
        messages.success(request, f"Issue {issue.number} posted. Stock at {store.code} has been updated.")
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
