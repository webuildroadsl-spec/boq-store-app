from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, render

from .models import Project
from .permissions import projects_for_user, user_can_access_project


@login_required
def project_list(request):
    """
    Every project the logged-in user is allowed to see — scoped through
    `projects_for_user`, never the raw Project.objects.all().
    """
    projects = projects_for_user(request.user)
    return render(request, "core/project_list.html", {"projects": projects})


@login_required
def project_detail(request, pk):
    """
    A single project's overview page.

    We deliberately raise a plain 404 (not a 403) for a project the user
    can't access: confirming "yes, project 7 exists, you're just not
    allowed to see it" leaks more than a flat "not found" does, and it's
    the same response an unknown pk gets.
    """
    project = get_object_or_404(Project, pk=pk)
    if not user_can_access_project(request.user, project):
        raise Http404("No Project matches the given query.")
    return render(request, "core/project_detail.html", {"project": project})
