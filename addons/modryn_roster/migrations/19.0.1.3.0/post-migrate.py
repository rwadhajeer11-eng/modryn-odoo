"""Rename the shipped shift names to Hebrew, where nobody has renamed them.

modryn.shift.template.name is a plain Char the owner edits herself at
/manage/shifts, and the seed shipped it in English - so a Hebrew-first boutique's
main screen read "Sunday", "Monday", "Thursday late" in the middle of Hebrew
prose. The data file is noupdate (a boutique's own hours must survive an
upgrade), which is right and which is also why an upgrade alone cannot fix the
tenants that already exist.

ONLY where the name is still exactly the seeded English. A boutique that has
already renamed a shift has said what it wants it called, and this must not
argue with her.

Not translate=True on the column instead: that turns a live varchar into jsonb,
which this codebase has already recorded as a migration hazard, and it is the
wrong shape anyway - these are one shop's own words, not a phrase with a correct
translation.
"""

import logging

_logger = logging.getLogger(__name__)

RENAMES = [
    ('Sunday', 'ראשון'),
    ('Monday', 'שני'),
    ('Tuesday', 'שלישי'),
    ('Wednesday', 'רביעי'),
    ('Thursday late', 'חמישי ערב'),
]


def migrate(cr, version):
    cr.execute("SELECT to_regclass('modryn_shift_template')")
    if not cr.fetchone()[0]:
        return
    renamed = 0
    for english, hebrew in RENAMES:
        cr.execute(
            "UPDATE modryn_shift_template SET name = %s WHERE name = %s",
            (hebrew, english))
        renamed += cr.rowcount
    _logger.info("modryn_roster: renamed %d untouched shift name(s) to Hebrew",
                 renamed)
