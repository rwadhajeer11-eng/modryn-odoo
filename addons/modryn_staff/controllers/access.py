"""The gate every staff PAGE route asks, in one place.

Six controllers used to carry their own copies of _is_staff/_is_manager; page
access now also depends on the owner's role→page matrix, and one copy of that
question is the only way it stays answered consistently. ACTION routes
(/floor/assign, /roster/publish, /tasks/done …) keep their level gates — the
matrix decides what a role can SEE, levels decide what it can DO.
"""

from odoo.http import request

GROUP_STAFF = 'modryn_staff.group_boutique_staff'
GROUP_MANAGER = 'modryn_staff.group_shift_manager'
GROUP_OWNER = 'modryn_staff.group_boutique_owner'


def _user():
    user = request.env.user
    return None if (not user or user._is_public()) else user


def is_staff():
    user = _user()
    return bool(user) and user.has_group(GROUP_STAFF)


def is_manager():
    user = _user()
    return bool(user) and user.has_group(GROUP_MANAGER)


def is_owner():
    user = _user()
    return bool(user) and user.has_group(GROUP_OWNER)


def can_view(page_key):
    return request.env['modryn.role.page'].modryn_can_view(page_key)


def deny():
    """What a refused PAGE request sees.

    Staff get a themed page wearing the unified nav — so the refusal itself
    shows exactly which pages she does have. Anyone who is not boutique staff
    at all keeps getting a 404: what exists here is not theirs to learn.
    (Anonymous users never reach this — the routes are auth='user', so Odoo
    already bounced them to the login page.)
    """
    if not is_staff():
        return request.not_found()
    return request.render('modryn_staff.no_access', {}, status=403)
