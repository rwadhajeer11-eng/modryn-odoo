"""Carry the discount codes off `limit_kind` and onto three plain facts.

The old shape made a code's limit a MODE: it was capped by a number of uses,
or by a date, or by nothing, and never by two of those at once. The new shape
has a first day, a last day and a headcount, each of which may simply be
absent — so "twenty people during the fair week" is now sayable, and an empty
box means no limit instead of a mode nobody chose.

The carry matters because the old columns were only meaningful in their own
mode. `max_uses` defaulted to 1 on EVERY row, including the ones set to "as
many as they like" — reading it forward without looking at `limit_kind` would
silently turn every unlimited code in every boutique into a one-use code, and
nobody would find out until a bride was refused at the counter.
"""

import logging

_logger = logging.getLogger(__name__)


def _has_column(cr, table, column):
    cr.execute("""
        SELECT 1 FROM information_schema.columns
         WHERE table_name = %s AND column_name = %s
    """, (table, column))
    return bool(cr.fetchone())


def _carry_limits(cr):
    """Read `limit_kind` once, then make the new columns say the same thing."""
    if not _has_column(cr, 'modryn_discount_code', 'limit_kind'):
        _logger.info("modryn_ops: no limit_kind column, nothing to carry")
        return

    # 'times' KEEPS its count; everything else never had one. Done as two
    # statements rather than a CASE so each is readable on its own and so the
    # count below reports what actually moved.
    cr.execute("""
        UPDATE modryn_discount_code
           SET max_uses = 0
         WHERE limit_kind IS DISTINCT FROM 'times'
    """)
    freed = cr.rowcount
    # 'until' KEEPS its date. A stray use_until under any other mode was never
    # read and must not start being read now.
    cr.execute("""
        UPDATE modryn_discount_code
           SET use_until = NULL
         WHERE limit_kind IS DISTINCT FROM 'until'
           AND use_until IS NOT NULL
    """)
    cleared = cr.rowcount
    _logger.info("modryn_ops: %s codes set to no headcount, %s stray end dates "
                 "cleared", freed, cleared)

    # DROPPED, not left behind. Odoo leaves the column of a removed field in
    # place, and a stale limit_kind sitting beside the columns that replaced it
    # is the next reader's trap: it still holds the old answer and nothing
    # reads it.
    cr.execute("ALTER TABLE modryn_discount_code DROP COLUMN limit_kind")


def _carry_kind(cr):
    """Every code that existed was a percentage. Say so out loud."""
    if not _has_column(cr, 'modryn_discount_code', 'value_kind'):
        return
    cr.execute("""
        UPDATE modryn_discount_code
           SET value_kind = 'percent'
         WHERE value_kind IS NULL
    """)
    if cr.rowcount:
        _logger.info("modryn_ops: %s codes marked as percentages", cr.rowcount)


def migrate(cr, version):
    if not version:
        return
    _carry_limits(cr)
    _carry_kind(cr)
