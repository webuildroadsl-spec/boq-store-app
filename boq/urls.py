from django.urls import path

from . import views

app_name = "boq"

urlpatterns = [
    path("projects/<int:project_pk>/boq/", views.boq_detail, name="boq_detail"),
    path("projects/<int:project_pk>/boq/versions/", views.boq_list, name="boq_list"),
    path(
        "projects/<int:project_pk>/boq/versions/compare/",
        views.compare_versions,
        name="compare_versions",
    ),
    path(
        "projects/<int:project_pk>/boq/versions/<int:boq_pk>/",
        views.boq_version_detail,
        name="boq_version_detail",
    ),
    path(
        "projects/<int:project_pk>/boq/versions/<int:boq_pk>/revise/",
        views.create_revision,
        name="create_revision",
    ),
    path(
        "projects/<int:project_pk>/boq/versions/<int:boq_pk>/approve/",
        views.approve_boq,
        name="approve_boq",
    ),
    path(
        "projects/<int:project_pk>/boq/variation-orders/",
        views.vo_list,
        name="vo_list",
    ),
    path(
        "projects/<int:project_pk>/boq/bills/<int:bill_pk>/",
        views.bill_items,
        name="bill_items",
    ),
    path(
        "projects/<int:project_pk>/boq/bills/<int:bill_pk>/save/",
        views.bill_items_save,
        name="bill_items_save",
    ),
    path("projects/<int:project_pk>/boq/export/xlsx/", views.export_xlsx, name="export_xlsx"),
    path("projects/<int:project_pk>/boq/export/pdf/", views.export_pdf, name="export_pdf"),
    path(
        "projects/<int:project_pk>/boq/import/template/",
        views.import_template,
        name="import_template",
    ),
    path("projects/<int:project_pk>/boq/import/", views.import_upload, name="import_upload"),
    path(
        "projects/<int:project_pk>/boq/import/preview/",
        views.import_preview,
        name="import_preview",
    ),
    path(
        "projects/<int:project_pk>/boq/import/confirm/",
        views.import_confirm,
        name="import_confirm",
    ),
]
