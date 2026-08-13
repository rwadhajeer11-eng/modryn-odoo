"""The one list every navbar renders from.

Modules used to inject links into two competing nav templates by xpath, at the
same anchor, in whatever order module load happened to apply them — which is
exactly how a shift manager clicking "Workshop" landed on the owner's navbar
full of links that 404 for her. Now each module registers its pages HERE at
import time, and ONE template loops over the result, sorted by sequence,
filtered per user by modryn.role.page.

`section` is 'staff' or 'manage'. Manage pages are owner-only, always — they
never enter the owner's access matrix, because a matrix that could grant
/manage/staff would let a mis-tick hand out account administration.
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


def register(key, url, label, sequence, section='staff'):
    """Add a page to the shared nav. Idempotent: a re-import is not a new page."""
    if any(p['key'] == key for p in PAGES):
        return
    PAGES.append({
        'key': key,
        'url': url,
        'label': label,
        'sequence': sequence,
        'section': section,
    })
    PAGES.sort(key=lambda p: (p['section'] != 'staff', p['sequence']))


def page(key):
    return next((p for p in PAGES if p['key'] == key), None)


register('home', '/staff/home', _lt("My day"), 5)
register('floor', '/floor', _lt("Floor board"), 10)
register('roster', '/roster', _lt("Roster"), 20)
register('checkin', '/queue/checkin', _lt("Walk-in check-in"), 60)
register('staff', '/manage/staff', _lt("Team"), 10, 'manage')
register('roles', '/manage/roles', _lt("Roles"), 20, 'manage')
register('rooms', '/manage/rooms', _lt("Rooms"), 40, 'manage')
register('hours', '/manage/hours', _lt("Hours"), 50, 'manage')
