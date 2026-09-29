from django.urls import path

from . import views

app_name = "reports"

urlpatterns = [
    path("projects/<int:project_pk>/dashboard/", views.dashboard, name="dashboard"),
    path("projects/<int:project_pk>/reports/boq-summary/", views.boq_summary, name="boq_summary"),
    path("projects/<int:project_pk>/reports/boq-comparison/", views.boq_comparison, name="boq_comparison"),
    path("projects/<int:project_pk>/reports/stock-balance/", views.stock_balance, name="stock_balance"),
    path("projects/<int:project_pk>/reports/stock-ledger/", views.stock_ledger, name="stock_ledger"),
    path(
        "projects/<int:project_pk>/reports/material-reconciliation/",
        views.material_reconciliation,
        name="material_reconciliation",
    ),
    path(
        "projects/<int:project_pk>/reports/issues-by-boq-item/",
        views.issues_by_boq_item,
        name="issues_by_boq_item",
    ),
    path("projects/<int:project_pk>/reports/reorder-alert/", views.reorder_alert, name="reorder_alert"),
    path("projects/<int:project_pk>/reports/grn-register/", views.grn_register, name="grn_register"),
]
