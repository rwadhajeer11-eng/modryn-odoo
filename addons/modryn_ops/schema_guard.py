"""Schema guards that run on the INSTALL path as well as on the upgrade path.

Same reasoning as modryn_portal/schema_guard.py, which is the annotated
original: every boutique is cloned from modryn_template and therefore INSTALLS
this module rather than upgrading it, and Odoo drops a failed index with a log
line and exit code 0 on exactly that path. assert_indexes converts the silence
into a failed deploy; dedupe clears the rows that would make the index
un-buildable in the first place.
"""

import logging

from odoo.tools import sql

from .models.task import ONE_INSTANCE_PER_DAY_INDEX

_logger = logging.getLogger(__name__)

REQUIRED_INDEXES = (ONE_INSTANCE_PER_DAY_INDEX,)


def _dedupe_checklist_instances(cr):
    if not sql.table_exists(cr, 'modryn_task'):
        # Fresh install: this module owns the table, so it does not exist yet.
        return
    # Lowest id wins — the instance the first board load created, whose ticks
    # (if any) are the ones staff actually made. Losers are deleted outright:
    # a duplicate checklist row carries no customer history worth keeping.
    cr.execute("""
        WITH ranked AS (
            SELECT id, row_number() OVER (PARTITION BY template_id, day
                                          ORDER BY id) AS rn
            FROM modryn_task
            WHERE template_id IS NOT NULL
        )
        DELETE FROM modryn_task t
         USING ranked r
         WHERE t.id = r.id AND r.rn > 1
        RETURNING t.id, t.day
    """)
    losers = cr.fetchall()
    if losers:
        _logger.warning(
            "modryn_ops: removed %d duplicate checklist instance(s) so that %s "
            "can be created; affected task ids and days: %s",
            len(losers), ONE_INSTANCE_PER_DAY_INDEX,
            ', '.join('%s@%s' % (i, d) for i, d in losers))


def dedupe(cr):
    """Clear the rows that would make the unique index un-buildable."""
    _dedupe_checklist_instances(cr)


def assert_indexes(cr):
    """Fail the deploy if the unique index silently did not get created."""
    missing = [name for name in REQUIRED_INDEXES if not sql.index_exists(cr, name)]
    if missing:
        raise RuntimeError(
            "modryn_ops: %s missing after install/upgrade. Without it two floor "
            "boards loading at once mint duplicate checklist rows every morning. "
            "Check the _schema logger for the CREATE that failed, clean the "
            "conflicting rows, and re-run." % ', '.join(missing))


def pre_init_hook(env):
    dedupe(env.cr)


def post_init_hook(env):
    assert_indexes(env.cr)
