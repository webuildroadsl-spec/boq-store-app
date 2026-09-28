from django.urls import path

from . import views

app_name = "store"

urlpatterns = [
    path("projects/<int:project_pk>/stores/", views.store_list, name="store_list"),
    path("projects/<int:project_pk>/stores/<int:store_pk>/", views.store_detail, name="store_detail"),
    path(
        "projects/<int:project_pk>/stores/<int:store_pk>/grns/",
        views.grn_list,
        name="grn_list",
    ),
    path(
        "projects/<int:project_pk>/stores/<int:store_pk>/grns/new/",
        views.grn_create,
        name="grn_create",
    ),
    path(
        "projects/<int:project_pk>/stores/<int:store_pk>/grns/<int:grn_pk>/",
        views.grn_detail,
        name="grn_detail",
    ),
    path(
        "projects/<int:project_pk>/stores/<int:store_pk>/grns/<int:grn_pk>/lines/<int:line_pk>/delete/",
        views.grn_line_delete,
        name="grn_line_delete",
    ),
    path(
        "projects/<int:project_pk>/stores/<int:store_pk>/grns/<int:grn_pk>/post/",
        views.grn_post,
        name="grn_post",
    ),
    # Requisitions are project-scoped, not store-scoped (Section 5.2 point 1).
    path("projects/<int:project_pk>/requisitions/", views.requisition_list, name="requisition_list"),
    path(
        "projects/<int:project_pk>/requisitions/new/",
        views.requisition_create,
        name="requisition_create",
    ),
    path(
        "projects/<int:project_pk>/requisitions/<int:requisition_pk>/",
        views.requisition_detail,
        name="requisition_detail",
    ),
    path(
        "projects/<int:project_pk>/stores/<int:store_pk>/issues/",
        views.issue_list,
        name="issue_list",
    ),
    path(
        "projects/<int:project_pk>/stores/<int:store_pk>/issues/new/",
        views.issue_create,
        name="issue_create",
    ),
    path(
        "projects/<int:project_pk>/stores/<int:store_pk>/issues/<int:issue_pk>/",
        views.issue_detail,
        name="issue_detail",
    ),
    path(
        "projects/<int:project_pk>/stores/<int:store_pk>/issues/<int:issue_pk>/lines/<int:line_pk>/delete/",
        views.issue_line_delete,
        name="issue_line_delete",
    ),
    path(
        "projects/<int:project_pk>/stores/<int:store_pk>/issues/<int:issue_pk>/post/",
        views.issue_post,
        name="issue_post",
    ),
    path(
        "projects/<int:project_pk>/stores/<int:store_pk>/returns/",
        views.return_list,
        name="return_list",
    ),
    path(
        "projects/<int:project_pk>/stores/<int:store_pk>/returns/new/",
        views.return_create,
        name="return_create",
    ),
    path(
        "projects/<int:project_pk>/stores/<int:store_pk>/returns/<int:return_pk>/",
        views.return_detail,
        name="return_detail",
    ),
    path(
        "projects/<int:project_pk>/stores/<int:store_pk>/returns/<int:return_pk>/lines/<int:line_pk>/delete/",
        views.return_line_delete,
        name="return_line_delete",
    ),
    path(
        "projects/<int:project_pk>/stores/<int:store_pk>/returns/<int:return_pk>/post/",
        views.return_post,
        name="return_post",
    ),
]
