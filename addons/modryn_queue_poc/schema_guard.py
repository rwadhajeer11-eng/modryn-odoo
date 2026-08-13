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

from odoo.addons.modryn_portal.models.sms import normalize_il_phone

from .models.queue_entry import OPEN_PHONE_INDEX

_logger = logging.getLogger(__name__)

REQUIRED_INDEXES = (OPEN_PHONE_INDEX,)

OPEN_STATES_SQL = "('pending', 'waiting', 'called')"


def _normalize_open_phones(cr):
    """Rewrite open rows' phones to E.164, so the duplicate scan can see them.

    Legacy rows stored numbers exactly as typed ('054-7778888', '0500000009'),
    which the normalized search in modryn_check_in can never match — the very
    hole that let format-mismatched duplicates in. Unnormalizable phones go to
    NULL: they can never be texted, and NULL is exempt from the unique index.
    """
    cr.execute("""
        SELECT id, phone FROM modryn_queue_entry
         WHERE state IN %s AND phone IS NOT NULL
    """ % OPEN_STATES_SQL)
    for row_id, phone in cr.fetchall():
        normalized = normalize_il_phone(phone)
        if normalized != phone:
            cr.execute(
                "UPDATE modryn_queue_entry SET phone = %s WHERE id = %s",
                (normalized or None, row_id))
            _logger.info(
                "modryn_queue_poc: normalized open entry %s phone %r -> %r",
                row_id, phone, normalized)


def _expire_duplicate_open_phones(cr):
    """One open place per number before the index demands it.

    Oldest wins: create_date asc IS the queue order, so the oldest row is her
    true place — and the one _notify_joined fired for. Losers are expired, not
    deleted: they were texted ticket links, and /q renders expired as the same
    warm exit every closed ticket gets.
    """
    cr.execute("""
        WITH ranked AS (
            SELECT id, row_number() OVER (PARTITION BY phone
                                          ORDER BY create_date, id) AS rn
            FROM modryn_queue_entry
            WHERE state IN %s AND phone IS NOT NULL
        )
        UPDATE modryn_queue_entry e
           SET state = 'expired'
          FROM ranked r
         WHERE e.id = r.id AND r.rn > 1
        RETURNING e.id, e.phone
    """ % OPEN_STATES_SQL)
    losers = cr.fetchall()
    if losers:
        _logger.warning(
            "modryn_queue_poc: expired %d duplicate open entr%s so that %s "
            "can be created; affected ids and phones: %s",
            len(losers), 'y' if len(losers) == 1 else 'ies', OPEN_PHONE_INDEX,
            ', '.join('%s@%s' % (i, p) for i, p in losers))


def dedupe(cr):
    """Clear the rows that would make the unique index un-buildable."""
    if not sql.table_exists(cr, 'modryn_queue_entry'):
        # Fresh install: this module owns the table, so it does not exist yet.
        return
    _normalize_open_phones(cr)
    _expire_duplicate_open_phones(cr)


def assert_indexes(cr):
    """Fail the deploy if the unique index silently did not get created."""
    missing = [name for name in REQUIRED_INDEXES if not sql.index_exists(cr, name)]
    if missing:
        raise RuntimeError(
            "modryn_queue_poc: %s missing after install/upgrade. Without it "
            "two verifies for the same number racing past the search both "
            "create, and one person holds two places in the line. Check the "
            "_schema logger for the CREATE that failed, clean the conflicting "
            "rows, and re-run." % ', '.join(missing))


def pre_init_hook(env):
    dedupe(env.cr)


def post_init_hook(env):
    assert_indexes(env.cr)
