"""Rename the shipped fitting-room names to Hebrew, where untouched.

Same reasoning as the shift templates: modryn.fitting.room.name is a plain Char
the owner edits at /manage/rooms, the seed shipped English, and the data file is
noupdate so an upgrade alone cannot reach the tenants that already exist.

Only names still exactly as seeded are touched.
"""

import logging

_logger = logging.getLogger(__name__)

RENAMES = [
    ('Room 1', 'חדר 1'),
    ('Room 2', 'חדר 2'),
    ('Bridal suite', 'סוויטת כלה'),
]


def migrate(cr, version):
    cr.execute("SELECT to_regclass('modryn_fitting_room')")
    if not cr.fetchone()[0]:
        return
    renamed = 0
    for english, hebrew in RENAMES:
        cr.execute(
            "UPDATE modryn_fitting_room SET name = %s WHERE name = %s",
            (hebrew, english))
        renamed += cr.rowcount
    _logger.info("modryn_staff: renamed %d untouched room name(s) to Hebrew",
                 renamed)
