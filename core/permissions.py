"""
Project-level access control (Section 2).

Design decision worth flagging: the spec's "Admin" role can see and edit
every project, while the other five roles only see the projects they're
a member of. Rather than adding an "admin" row to ProjectMembership for
every project (which would need updating every time a new project is
created), this MVP maps the spec's "Admin" onto Django's own
`is_superuser` flag — a superuser bypasses project scoping entirely.
Everyone else's access comes only from ProjectMembership rows.
"""

from .models import Project


def projects_for_user(user):
    """
    The queryset of projects `user` is allowed to see at all.

    A superuser (the spec's company-wide "Admin") sees every project.
    Everyone else sees only projects they have a ProjectMembership row
    for — which is exactly what keeps a Storekeeper on Project A from
    seeing Project B.
    """
    if user.is_superuser:
        return Project.objects.all()
    return Project.objects.filter(members=user).distinct()


def get_role(user, project):
    """
    The role `user` holds on `project`, or None if they have no
    membership on it. Superusers don't get a fake role here — this is
    specifically "what does the membership table say", used by callers
    that need to know the exact role (e.g. "is this person the
    Storekeeper on this project?").
    """
    membership = project.memberships.filter(user=user).first()
    return membership.role if membership else None


def user_can_access_project(user, project):
    """True if `user` is allowed to see `project` at all."""
    return user.is_superuser or project.memberships.filter(user=user).exists()
