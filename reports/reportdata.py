"""
Data assembly for every report in Section 7.1's table, plus the
per-project dashboard. Kept separate from `exporter.py` (which only
turns this data into Excel/PDF bytes) and from `views.py` (which only
does permission checks and HTTP plumbing) -- one place computes each
report's figures, whether it ends up on screen, in a PDF, or in an
Excel file.

Nothing here writes to the database. Every function takes model
instances or querysets already scoped to the right project/store by
the caller (`views.py`) -- this module doesn't re-check permissions.
"""

from datetime import date
from decimal import Decimal

from django.db.models import Q, Sum

from boq.models import BOQ
from store.models import GRN, MaterialAllowance, StockMovement, StoreItem


def boq_summary_data(boq):
    """
    Section 7.1's "BOQ summary": "Bill totals, contingency, tax, grand
    total for the current version." `Bill.total` / `BOQ.grand_total`
    (sum of bills) are unchanged from step 3; contingency and tax are
    the two rate fields step 10 added directly to `BOQ` (rule 7).
    """
    bills = list(boq.bills.all())
    return {
        "boq": boq,
        "bills": bills,
        "subtotal": boq.grand_total,
        "contingency_percent": boq.contingency_percent,
        "contingency_amount": boq.contingency_amount,
        "tax_percent": boq.tax_percent,
        "tax_amount": boq.tax_amount,
        "grand_total": boq.final_total,
    }


def boq_comparison_data(boq_a, boq_b):
    """
    Section 7.1's "BOQ version comparison": "Items added, removed,
    changed; value difference." Reuses `boq.views._compare_boq_versions`
    rather than re-implementing the same item-by-item diff a second
    time -- that function already is exactly this report's data.
    """
    from boq.views import _compare_boq_versions  # local import: avoid a views->views cross-app import cycle

    rows = _compare_boq_versions(boq_a, boq_b)
    return {
        "boq_a": boq_a,
        "boq_b": boq_b,
        "rows": rows,
        "value_difference": boq_b.grand_total - boq_a.grand_total,
        "added": sum(1 for r in rows if r["status"] == "added"),
        "removed": sum(1 for r in rows if r["status"] == "removed"),
        "changed": sum(1 for r in rows if r["status"] == "changed"),
    }


def stock_balance_data(stores, as_of_date=None):
    """
    Section 7.1's "Stock balance": "Quantity and value per item per
    store, as at any date." One row per (store, item) that has ever
    had a movement, using `StockMovement.balance_as_of()` (today, by
    default) so the report can be re-run for any date in the past
    exactly as the spec asks, not just "right now."
    """
    as_of_date = as_of_date or date.today()
    rows = []
    for store in stores:
        item_ids = (
            StockMovement.objects.filter(store=store).values_list("item_id", flat=True).distinct()
        )
        for item in StoreItem.objects.filter(pk__in=item_ids).order_by("code"):
            quantity, value, average_cost = StockMovement.balance_as_of(store, item, as_of_date)
            if quantity == 0 and value == 0:
                continue  # nothing ever moved, or it's fully netted out by this date
            rows.append(
                {"store": store, "item": item, "quantity": quantity, "value": value, "average_cost": average_cost}
            )
    return {"as_of_date": as_of_date, "rows": rows}


def stock_ledger_data(store, item):
    """
    Section 7.1's "Stock ledger (bin card)": "Every movement for one
    item in one store, with running balance." Movements are already
    ordered oldest-first (`StockMovement.Meta.ordering`), so the
    running balance is just a left-to-right cumulative sum.
    """
    movements = list(StockMovement.objects.filter(store=store, item=item).select_related("created_by"))
    running_quantity = Decimal("0.000")
    running_value = Decimal("0.00")
    rows = []
    for movement in movements:
        running_quantity += movement.quantity
        running_value += movement.total_cost
        rows.append({"movement": movement, "running_quantity": running_quantity, "running_value": running_value})
    return {"store": store, "item": item, "rows": rows}


def material_reconciliation_data(project):
    """
    Section 7.1's "Material reconciliation": "Allowed vs net issued
    per BOQ item and material, variance, colour flags." Exactly
    `MaterialAllowance.reconciliation()` from step 9, run for every
    allowance set on this project -- the store module's own
    `reconciliation_report` view (step 9) already builds this same
    list; this module gives the export/report layer the identical data
    without duplicating the aggregation logic.
    """
    allowances = MaterialAllowance.objects.filter(boq_item__bill__boq__project=project).select_related(
        "boq_item", "store_item"
    )
    return {"rows": [allowance.reconciliation() for allowance in allowances]}


def issues_by_boq_item_data(project):
    """
    Section 7.1's "Issues by BOQ item / section": "Materials and cost
    issued to each BOQ item or chainage." One row per (BOQ item,
    section) pair that has any Issue movement, with quantity issued
    per store item folded together as one "materials" total (a value
    figure) since a BOQ item can be charged several different store
    items across its issues -- a disclosed simplification: this report
    shows a value total per (BOQ item, section), not a further
    breakdown by which store item made it up (that breakdown is what
    the material reconciliation report already gives, per BOQ item).
    """
    movements = (
        StockMovement.objects.filter(store__project=project, document_type=StockMovement.DOCUMENT_ISSUE)
        .exclude(boq_item__isnull=True)
        .select_related("boq_item", "section")
    )
    totals = {}
    for movement in movements:
        key = (movement.boq_item_id, movement.section_id)
        entry = totals.setdefault(
            key,
            {
                "boq_item": movement.boq_item,
                "section": movement.section,
                "quantity": Decimal("0.000"),
                "value": Decimal("0.00"),
            },
        )
        entry["quantity"] += -movement.quantity  # movement.quantity is negative for an issue
        entry["value"] += -movement.total_cost

    rows = sorted(
        totals.values(),
        key=lambda r: (r["boq_item"].item_reference if r["boq_item"] else "", r["section"].code if r["section"] else ""),
    )
    return {"rows": rows}


def reorder_alert_data(stores):
    """
    Section 7.1's "Reorder alert": "Items below minimum stock."
    `StoreItem.minimum_stock_level` already exists (step 6); this is
    simply the first report to actually read it. `reorder_quantity` is
    shown alongside as a suggested order size -- also an existing
    field, unused until now.
    """
    rows = []
    for store in stores:
        item_ids = StockMovement.objects.filter(store=store).values_list("item_id", flat=True).distinct()
        # Only items with a minimum set, or that have moved in this
        # store at all -- an item with no minimum and no history has
        # nothing to alert on either way.
        candidate_items = StoreItem.objects.filter(active=True).filter(
            Q(minimum_stock_level__gt=Decimal("0")) | Q(pk__in=item_ids)
        )
        for item in candidate_items.order_by("code"):
            quantity, _, _ = StockMovement.current_balance(store, item)
            if quantity < item.minimum_stock_level:
                rows.append(
                    {
                        "store": store,
                        "item": item,
                        "quantity": quantity,
                        "minimum_stock_level": item.minimum_stock_level,
                        "reorder_quantity": item.reorder_quantity,
                    }
                )
    return {"rows": rows}


def grn_register_data(stores, date_from=None, date_to=None):
    """Section 7.1's "GRN register": "All deliveries by supplier and
    date." Every Posted GRN across the given stores (Draft GRNs
    haven't actually delivered anything yet), optionally narrowed to a
    date range."""
    grns = GRN.objects.filter(store__in=stores, status=GRN.STATUS_POSTED).select_related("store", "supplier")
    if date_from:
        grns = grns.filter(date__gte=date_from)
    if date_to:
        grns = grns.filter(date__lte=date_to)
    return {"rows": grns.order_by("-date", "store", "number")}


def dashboard_data(project, can_see_boq, stores):
    """
    Section 7.1's "Dashboard (per project)": "BOQ grand total, value of
    stock on hand, value issued this month, top 5 materials over
    allowance, reorder alerts." Each figure is only computed if the
    caller says the viewer can see that half of the app -- a
    Storekeeper (no BOQ access at all) still gets a dashboard, just
    without the BOQ grand total, rather than a 403 for the whole page.
    """
    data = {"boq_grand_total": None, "current_boq": None}
    if can_see_boq:
        current_boq = (
            project.boqs.filter(status=BOQ.STATUS_APPROVED).order_by("-version_number").first()
            or project.boqs.order_by("-version_number").first()
        )
        if current_boq is not None:
            data["current_boq"] = current_boq
            data["boq_grand_total"] = current_boq.final_total

    stock_value = Decimal("0.00")
    for store in stores:
        item_ids = StockMovement.objects.filter(store=store).values_list("item_id", flat=True).distinct()
        for item in StoreItem.objects.filter(pk__in=item_ids):
            _, value, _ = StockMovement.current_balance(store, item)
            stock_value += value
    data["stock_value"] = stock_value

    today = date.today()
    month_start = today.replace(day=1)
    issued_totals = StockMovement.objects.filter(
        store__in=stores,
        document_type=StockMovement.DOCUMENT_ISSUE,
        created_at__date__gte=month_start,
        created_at__date__lte=today,
    ).aggregate(value=Sum("total_cost"))
    data["value_issued_this_month"] = -(issued_totals["value"] or Decimal("0.00"))

    allowances = MaterialAllowance.objects.filter(boq_item__bill__boq__project=project).select_related(
        "boq_item", "store_item"
    )
    reconciliations = [a.reconciliation() for a in allowances]
    over_allowance = [r for r in reconciliations if r["variance"] > 0]
    over_allowance.sort(key=lambda r: r["variance"], reverse=True)
    data["top_over_allowance"] = over_allowance[:5]

    data["reorder_alerts"] = reorder_alert_data(stores)["rows"]
    return data
