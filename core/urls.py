from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("projects/", views.project_list, name="project_list"),
    path("projects/<int:pk>/", views.project_detail, name="project_detail"),
]
