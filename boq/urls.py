from django.urls import path

from . import views

app_name = "boq"

urlpatterns = [
    path("projects/<int:project_pk>/boq/", views.boq_detail, name="boq_detail"),
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
]
