from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Max
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from core.models import Project
from core.permissions import user_can_access_project

from .forms import GRNAttachmentForm, GRNForm, GRNLineForm
from .models import GRN, Store, StockMovement, StoreItem
from .permissions import can_manage_grn, can_view_store_module, stores_for_user


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
