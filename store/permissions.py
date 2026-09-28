"""
Store-module access control (Section 2's Store rows).

Section 5.1 gives each Store its own single "storekeeper" — a
specific user, not just "anyone with the Storekeeper role on this
project" — so GRN posting is gated on that exact assignment, not
merely on role. Viewing is looser: any project member can see a
store's stock and GRN history (there's no "No" for viewing in the
spec's table the way there explicitly is for BOQ), except the table's
"View reports and dashboards" row scopes a Storekeeper to their own
store rather than every store on the project.
"""

from core.permissions import get_role, user_can_access_project
from core.models import ROLE_PROJECT_MANAGER, ROLE_SITE_ENGINEER, ROLE_STOREKEEPER


def can_view_store_module(user, project):
    """True if `user` can see the store module for `project` at all."""
    return user_can_access_project(user, project)


def stores_for_user(project, user):
    """
    The stores in `project` this user can see: every store, unless
    they're specifically here as a Storekeeper (not an Admin/
    superuser), in which case only the store(s) they're assigned to —
    Section 2's "View reports and dashboards ... Storekeeper: Own
    store."
    """
    stores = project.stores.all()
    if user.is_superuser:
        return stores
    if get_role(user, project) == ROLE_STOREKEEPER:
        return stores.filter(storekeeper=user)
    return stores


def can_manage_grn(user, store):
    """
    True if `user` can record/edit/post a GRN for this specific store —
    Section 2's "Record goods received (GRN)" row (Storekeeper only),
    narrowed to *this store's* assigned storekeeper since Section 5.1
    assigns exactly one.
    """
    return user.is_superuser or store.storekeeper_id == user.id


# Issuing and returning are also store-specific storekeeper actions —
# Section 2's "Issue materials to site" row is Storekeeper-only, same
# shape as "Record goods received (GRN)".
can_manage_issue = can_manage_grn
can_manage_return = can_manage_grn


# Section 2's "Request materials (store requisition)" row: Project
# Manager and Site Engineer only (not QS, not Storekeeper).
_REQUISITION_ROLES = {ROLE_PROJECT_MANAGER, ROLE_SITE_ENGINEER}


def can_create_requisition(user, project):
    if user.is_superuser:
        return True
    return get_role(user, project) in _REQUISITION_ROLES
