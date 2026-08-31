"""The one list every navbar renders from.

Modules used to inject links into two competing nav templates by xpath, at the
same anchor, in whatever order module load happened to apply them — which is
exactly how a shift manager clicking "Workshop" landed on the owner's navbar
full of links that 404 for her. Now each module registers its pages HERE at
import time, and ONE template loops over the result, sorted by sequence,
filtered per user by modryn.role.page.

`section` is 'staff' or 'manage' — the top row of the navbar and the bottom one.
BOTH are grantable through the owner's access matrix, except the two pages in
NEVER_GRANTABLE below: a matrix that can grant account administration hands it
out with a mis-tick, and one that can grant itself is not a permission system.
Everything else in the bottom row is ordinary boutique work an owner should be
able to delegate.
`key` doubles as the page's active_tab value, so existing templates keep
their highlight without changes.

Labels are LazyTranslate: these live at module level, where _() would run at
import time with no language and freeze English in (the portal's ERRORS dict
precedent). Each module registers with its OWN _lt so the msgid lands in that
module's POT.
"""

from odoo.tools.translate import LazyTranslate

_lt = LazyTranslate(__name__)

PAGES = []

# The two pages no role may be granted, whatever the matrix says.
#
#   staff  - accounts, passwords and permission levels. A tick here hands out
#            the ability to make somebody an owner.
#   roles  - the matrix itself. A role that can open it can grant itself
#            everything else on it.
#
# Named here rather than in the screen that draws the matrix, because the rule
# is about what these pages ARE and every reader of the matrix needs it.
# 'staff' no longer resolves to a page - account administration is a state of
# the manager's screen, gated on owner there. The key stays anyway: it costs
# nothing, it refuses a hand-made grant for a key somebody might re-register
# later, and it is the sentence that records WHY that power was never grantable.
NEVER_GRANTABLE = ('staff', 'roles')

# Pages whose ROUTE requires the owner herself. The navbar and the access matrix
# both read this, because all three have to agree: a page a tick cannot really
# open is a tick that lies, and the woman it was ticked for gets a tab that 404s.
# That was not hypothetical - it was true of every one of these until measured.
#
# Different from NEVER_GRANTABLE, which is about power a tick must never HAND
# OUT. This is about power a tick cannot hand out however much anybody wants it
# to, because the route on the far side refuses. Both end up owner-only; only
# one of them is a decision.
OWNER_ONLY = ('dresses', 'audit', 'checklists')


def grantable():
    """Every page an owner may hand to a role, both rows."""
    return [p for p in PAGES
            if p['key'] not in NEVER_GRANTABLE and p['key'] not in OWNER_ONLY]


def matrix_pages():
    """Every page the access matrix DRAWS, each saying whether it can be ticked.

    Wider than grantable() on purpose. The owner asked for a matrix showing all
    her screens, and one that silently drops the three she may not delegate
    answers "where did the dresses go" with nothing. They are drawn, and drawn
    as hers.
    """
    return [dict(p, owner_only=p['key'] in OWNER_ONLY)
            for p in PAGES if p['key'] not in NEVER_GRANTABLE]


def register(key, url, label, sequence, section='staff', icon='fa-circle-o'):
    """Add a page to the shared nav. Idempotent: a re-import is not a new page."""
    if any(p['key'] == key for p in PAGES):
        return
    PAGES.append({
        'key': key,
        'url': url,
        'label': label,
        'sequence': sequence,
        'section': section,
        'icon': icon,
    })
    PAGES.sort(key=lambda p: (p['section'] != 'staff', p['sequence']))


def page(key):
    return next((p for p in PAGES if p['key'] == key), None)


# Icon names are Font Awesome **4**, which is what Odoo 19 actually ships — the
# served frontend bundle has `.fa-clock-o` and NO `.fa-clock`. An FA5/6 name does
# not error; it renders an invisible empty box, so every name here was checked
# against the real bundle rather than from memory.
register('home', '/staff/home', _lt("Main screen"), 5, icon='fa-home')
# Her own details. Registered in the STAFF section, so it can never enter the
# owner's grant matrix as something to take away: every woman on the floor must
# be able to correct her own phone number. fa-user-o is FA4 - fa-user-circle is
# FA5 and would render an invisible box with nothing in the log.
register('profile', '/staff/profile', _lt("My details"), 90, icon='fa-user-o')
register('floor', '/floor', _lt("Floor board"), 10, icon='fa-th-large')
register('roster', '/roster', _lt("Work schedule"), 20, icon='fa-calendar')
register('checkin', '/queue/checkin', _lt("Walk-in check-in"), 60, icon='fa-qrcode')
# The TEAM and the ROOMS are boxes on the manager's screen now, not rows of
# their own - two screens about the same people was one too many, and a page
# holding one job is a page. Their routes still live in manage.py and still
# refuse anybody but the owner; what went is the navbar entry.

register('roles', '/manage/roles', _lt("Roles"), 20, 'manage', 'fa-briefcase')
# Opening hours are a TILE on the manager's screen now, not a row of their own -
# one page holding one job, sitting between the dresses and the reports. The
# routes still live in manage.py and still refuse anybody but the owner; what
# went is the navbar entry.

