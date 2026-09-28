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


# Section 2's "Transfers between stores" row: Storekeeper "Yes",
# Project Manager "Approve". Unlike Stock count, the data model gives
# Transfer no separate "approved by" field (5.1's field list is just
# "dispatched by, received by"), so there's no distinct approval step
# to gate here -- "Approve" is read as the Project Manager also
# having full operational access (dispatch/receive), on top of each
# store's own storekeeper, rather than a workflow the storekeeper must
# wait on. This is a disclosed interpretation, not a literal field.
def can_dispatch_transfer(user, transfer):
    """Dispatching removes stock from `from_store`, so it's gated the
    same way GRN/issue posting is: that store's own storekeeper, a
    project's Project Manager, or a superuser."""
    if user.is_superuser:
        return True
    if transfer.from_store.storekeeper_id == user.id:
        return True
    return get_role(user, transfer.project) == ROLE_PROJECT_MANAGER


def can_receive_transfer(user, transfer):
    """Receiving adds stock to `to_store` -- gated on *that* store's
    storekeeper instead, since the two stores can have different
    storekeepers and it's whoever is physically receiving the
    delivery who should confirm it landed."""
    if user.is_superuser:
        return True
    if transfer.to_store.storekeeper_id == user.id:
        return True
    return get_role(user, transfer.project) == ROLE_PROJECT_MANAGER


def can_create_transfer(user, project, from_store):
    """Creating (and later dispatching) a transfer starts from the
    sending store, so it's gated the same way as dispatching."""
    if user.is_superuser:
        return True
    if from_store.storekeeper_id == user.id:
        return True
    return get_role(user, project) == ROLE_PROJECT_MANAGER


# Section 2's "Stock count and adjustment" row: Storekeeper "Yes"
# (does the count), Project Manager "Approve" -- and this one *does*
# match an explicit field ("approved by") and an explicit sentence
# (5.2.6: "differences post as adjustments after Project Manager
# approval"), so it's a real, distinct gate, unlike Transfer's.
def can_manage_stock_count(user, store):
    """Creating a stock count and entering counted quantities -- the
    store's own storekeeper, same shape as GRN."""
    return user.is_superuser or store.storekeeper_id == user.id


def can_approve_stock_count(user, project):
    """Approving (which posts the adjustment) is a Project Manager's
    action -- Section 2's rule "No user can approve their own
    adjustment" is enforced separately, in StockCount.approve()
    itself, since it depends on *who counted*, not just the role."""
    if user.is_superuser:
        return True
    return get_role(user, project) == ROLE_PROJECT_MANAGER
