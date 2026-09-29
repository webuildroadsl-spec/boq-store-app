from django.urls import path

from . import views

app_name = "pwa"

urlpatterns = [
    path("manifest.json", views.manifest, name="manifest"),
    path("service-worker.js", views.service_worker, name="service_worker"),
]
