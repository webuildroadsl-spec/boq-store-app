"""
Section 7.1's dashboard and eight reports. Every view here is
read-only (none of them writes to the database) and reachable by
anyone who can see the *half* of the app the report is about --
BOQ reports gate on `can_view_boq`, store reports on
`can_view_store_module` (scoped to a Storekeeper's own store via
`stores_for_user`, per Section 2's "View reports and dashboards ...
Storekeeper: Own store"), and the dashboard shows whichever of those
two halves the viewer actually has.

Every report view supports `?format=xlsx` or `?format=pdf` in
addition to its default HTML screen -- offered uniformly rather than
only for the reports Section 7.1's table lists an export column for
(a disclosed simplification; see `exporter.py`'s own docstring).
"""

from datetime import date, datetime

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, render

from boq.models import BOQ
from core.models import Project
from core.permissions import can_view_boq, user_can_access_project
from store.models import Store, StoreItem
from store.permissions import can_view_store_module, stores_for_user

from . import exporter, reportdata


def _get_project(request, project_pk):
    project = get_object_or_404(Project, pk=project_pk)
    if not user_can_access_project(request.user, project):
        raise Http404("No Project matches the given query.")
    return project


def _require_boq_access(request, project):
    if not can_view_boq(request.user, project):
        raise PermissionDenied("Your role does not have access to the BOQ.")


def _require_store_access(request, project):
    if not can_view_store_module(request.user, project):
        raise PermissionDenied("Your role does not have access to the store module.")


def _respond(request, template_name, context, xlsx_bytes_fn, pdf_bytes_fn, filename):
    """
    Shared "screen, or export" dispatch every report view below uses:
    `?format=xlsx` / `?format=pdf` return the export as an attachment,
    anything else renders the HTML screen. `xlsx_bytes_fn` / `pdf_bytes_fn`
    are called with `context` lazily -- only the format actually asked
    for is ever built.
    """
    fmt = request.GET.get("format")
    if fmt == "xlsx" and xlsx_bytes_fn is not None:
        content = xlsx_bytes_fn(context)
        response = HttpResponse(
            content, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        response["Content-Disposition"] = f'attachment; filename="{filename}.xlsx"'
        return response
    if fmt == "pdf" and pdf_bytes_fn is not None:
        content = pdf_bytes_fn(context)
        response = HttpResponse(content, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}.pdf"'
        return response
    return render(request, template_name, context)


def _parse_date(value, default):
    if not value:
        return default
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return default


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@login_required
def dashboard(request, project_pk):
    project = _get_project(request, project_pk)
    can_see_boq = can_view_boq(request.user, project)
    can_see_store = can_view_store_module(request.user, project)
    stores = stores_for_user(project, request.user) if can_see_store else Store.objects.none()

    data = reportdata.dashboard_data(project, can_see_boq, stores)
    data.update({"project": project, "can_see_boq": can_see_boq, "can_see_store": can_see_store})
    return render(request, "reports/dashboard.html", data)


# ---------------------------------------------------------------------------
# BOQ summary
# ---------------------------------------------------------------------------


@login_required
def boq_summary(request, project_pk):
    project = _get_project(request, project_pk)
    _require_boq_access(request, project)
    boq_pk = request.GET.get("boq")
    if boq_pk:
        boq = get_object_or_404(BOQ, pk=boq_pk, project=project)
    else:
        boq = (
            project.boqs.filter(status=BOQ.STATUS_APPROVED).order_by("-version_number").first()
            or project.boqs.order_by("-version_number").first()
        )
        if boq is None:
            raise Http404("This project has no BOQ yet.")

    data = reportdata.boq_summary_data(boq)
    data.update({"project": project, "boqs": project.boqs.order_by("-version_number")})
    return _respond(
        request,
        "reports/boq_summary.html",
        data,
        exporter.boq_summary_xlsx,
        exporter.boq_summary_pdf,
        f"boq_summary_{project.code}_v{boq.version_number}",
    )


# ---------------------------------------------------------------------------
# BOQ version comparison
# ---------------------------------------------------------------------------


@login_required
def boq_comparison(request, project_pk):
    project = _get_project(request, project_pk)
    _require_boq_access(request, project)
    boqs = project.boqs.order_by("-version_number")

    a_pk = request.GET.get("a")
    b_pk = request.GET.get("b")
    context = {"project": project, "boqs": boqs, "a_pk": a_pk, "b_pk": b_pk}
    if a_pk and b_pk:
        boq_a = get_object_or_404(BOQ, pk=a_pk, project=project)
        boq_b = get_object_or_404(BOQ, pk=b_pk, project=project)
        context.update(reportdata.boq_comparison_data(boq_a, boq_b))
        return _respond(
            request,
            "reports/boq_comparison.html",
            context,
            exporter.boq_comparison_xlsx,
            exporter.boq_comparison_pdf,
            f"boq_comparison_{project.code}_v{boq_a.version_number}_v{boq_b.version_number}",
        )
    return render(request, "reports/boq_comparison.html", context)


# ---------------------------------------------------------------------------
# Stock balance
# ---------------------------------------------------------------------------


@login_required
def stock_balance(request, project_pk):
    project = _get_project(request, project_pk)
    _require_store_access(request, project)
    stores = stores_for_user(project, request.user)
    as_of_date = _parse_date(request.GET.get("date"), date.today())

    data = reportdata.stock_balance_data(stores, as_of_date)
    data.update({"project": project})
    return _respond(
        request,
        "reports/stock_balance.html",
        data,
        exporter.stock_balance_xlsx,
        exporter.stock_balance_pdf,
        f"stock_balance_{project.code}_{as_of_date}",
    )


# ---------------------------------------------------------------------------
# Stock ledger (bin card)
# ---------------------------------------------------------------------------


@login_required
def stock_ledger(request, project_pk):
    project = _get_project(request, project_pk)
    _require_store_access(request, project)
    stores = stores_for_user(project, request.user)

    store_pk = request.GET.get("store")
    item_pk = request.GET.get("item")
    context = {"project": project, "stores": stores, "items": StoreItem.objects.filter(active=True)}
    if store_pk and item_pk:
        store = get_object_or_404(stores, pk=store_pk)
        item = get_object_or_404(StoreItem, pk=item_pk)
        context.update(reportdata.stock_ledger_data(store, item))
        return _respond(
            request,
            "reports/stock_ledger.html",
            context,
            exporter.stock_ledger_xlsx,
            exporter.stock_ledger_pdf,
            f"stock_ledger_{store.code}_{item.code}",
        )
    return render(request, "reports/stock_ledger.html", context)


# ---------------------------------------------------------------------------
# Material reconciliation
# ---------------------------------------------------------------------------


@login_required
def material_reconciliation(request, project_pk):
    project = _get_project(request, project_pk)
    _require_store_access(request, project)
    data = reportdata.material_reconciliation_data(project)
    data.update({"project": project})
    return _respond(
        request,
        "reports/material_reconciliation.html",
        data,
        exporter.material_reconciliation_xlsx,
        exporter.material_reconciliation_pdf,
        f"material_reconciliation_{project.code}",
    )


# ---------------------------------------------------------------------------
# Issues by BOQ item / section
# ---------------------------------------------------------------------------


@login_required
def issues_by_boq_item(request, project_pk):
    project = _get_project(request, project_pk)
    _require_store_access(request, project)
    data = reportdata.issues_by_boq_item_data(project)
    data.update({"project": project})
    return _respond(
        request,
        "reports/issues_by_boq_item.html",
        data,
        exporter.issues_by_boq_item_xlsx,
        None,  # Section 7.1 lists Excel only for this report
        f"issues_by_boq_item_{project.code}",
    )


# ---------------------------------------------------------------------------
# Reorder alert
# ---------------------------------------------------------------------------


@login_required
def reorder_alert(request, project_pk):
    project = _get_project(request, project_pk)
    _require_store_access(request, project)
    stores = stores_for_user(project, request.user)
    data = reportdata.reorder_alert_data(stores)
    data.update({"project": project})
    return _respond(
        request,
        "reports/reorder_alert.html",
        data,
        exporter.reorder_alert_xlsx,
        None,  # Section 7.1 lists Screen, Excel for this report -- no PDF
        f"reorder_alert_{project.code}",
    )


# ---------------------------------------------------------------------------
# GRN register
# ---------------------------------------------------------------------------


@login_required
def grn_register(request, project_pk):
    project = _get_project(request, project_pk)
    _require_store_access(request, project)
    stores = stores_for_user(project, request.user)
    date_from = _parse_date(request.GET.get("from"), None)
    date_to = _parse_date(request.GET.get("to"), None)
    data = reportdata.grn_register_data(stores, date_from, date_to)
    data.update({"project": project, "date_from": date_from, "date_to": date_to})
    return _respond(
        request,
        "reports/grn_register.html",
        data,
        exporter.grn_register_xlsx,
        None,  # Section 7.1 lists Excel only for this report
        f"grn_register_{project.code}",
    )
