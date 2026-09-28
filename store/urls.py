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
]
