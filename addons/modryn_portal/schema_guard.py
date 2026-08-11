"""Schema guards that run on the INSTALL path as well as on the upgrade path.

Odoo gives an upgrade a `migrations/<version>/pre-` and `post-migrate.py` pair,
and an install a `pre_init_hook` / `post_init_hook` pair — both called as
`hook(env)` (odoo/odoo/modules/loading.py:182 and :241). Nothing is invoked on
both. A guard that lives only under `migrations/` therefore protects the two
hand-built tenants and nobody else: `modryn_template` ships all seven modryn
modules UNINSTALLED, `new_boutique.sh` clones it, so every boutique provisioned
from it INSTALLS and never migrates.

The install path is also the quieter of the two. In odoo/odoo/orm/registry.py,
`post_constraint()` logs a failed constraint with `_schema.error` and DROPS it
when `_is_install` (:731); on an upgrade the same failure is queued and retried
once in `finalize_constraints()`, which itself only warns because "this is not a
deployment showstopper" (:743). Either way the run exits 0, records the version,
and leaves NO INDEX. That silence is what `assert_indexes` converts into a
failed deploy.

Raw SQL throughout: on the install path `pre_init_hook` runs before
`registry.load(package)`, so this module's own fields are not columns yet and the
ORM cannot see them.

Caveat, since it is invisible from here: importing this module from a migration
script pulls `odoo.addons.modryn_portal` into `sys.modules` early, and
`load_openerp_module` then early-returns and skips the manifest's `post_load`
hook (odoo/odoo/modules/module.py). This manifest declares no `post_load`. If one
is ever added, it will not fire during an upgrade of this module.
"""

import logging

from odoo.tools import sql

from .models.calendar_event import ONE_LIVE_BOOKING_PER_SLOT_INDEX
from .models.day_waitlist import ONE_OFFER_PER_DAY_INDEX, PHONE_DAY_INDEX

_logger = logging.getLogger(__name__)

# Must stay identical to the index predicate in models/calendar_event.py,
# including `active IS TRUE`. If the two disagree, this cleans one set of rows
# and _auto_init then rejects a different one — a run that logs "resolved N
# conflicts" and still ends with no index.
LIVE_BOOKING = ('modryn_is_booking IS TRUE AND modryn_cancelled_at IS NULL '
                'AND active IS TRUE')

# Existence only. A same-named index hand-built with a different predicate would
# pass this check — and Odoo would keep it, because Index.apply_to_database
# treats an index with no COMMENT as a deliberate support tweak
# (odoo/odoo/orm/table_objects.py). Comparing predicates here would fail that
# legitimate case, so this asserts the cheap half: that something is there at all.
REQUIRED_INDEXES = (
    ONE_LIVE_BOOKING_PER_SLOT_INDEX,
    ONE_OFFER_PER_DAY_INDEX,
    PHONE_DAY_INDEX,
)


def _dedupe_live_bookings(cr):
    if not sql.column_exists(cr, 'calendar_event', 'modryn_is_booking'):
        # modryn_booking is a declared dependency, so this only happens if the
        # table predates it entirely. No row can be a booking; nothing to clean.
        return
    # pre_init_hook runs before this module's own fields become columns, and a
    # loser must be stamped CANCELLED rather than archived — an archived-but-not-
    # cancelled booking is exactly the ghost row calendar_event.write() exists to
    # prevent. _auto_init creates these two a moment later and skips columns that
    # already exist (fields.py:update_db_column), so creating them early is not a
    # conflict; the labels match the model so the column is indistinguishable.
    for name, coltype, label in (('modryn_cancelled_at', 'timestamp', 'Cancelled at'),
                                 ('modryn_cancelled_by', 'varchar', 'Cancelled by')):
        if not sql.column_exists(cr, 'calendar_event', name):
            sql.create_column(cr, 'calendar_event', name, coltype, label)

    # Earliest create_date wins: whoever booked the hour first keeps it. Ties
    # break on id so two rows created in the same transaction resolve the same
    # way on every tenant and on a re-run.
    #
    # Cancelled, never deleted — same rule modryn_cancel() follows, and the same
    # two fields, so the losing rows stay in the customer's history and on the
    # boutique's board as booked-and-dropped. 'boutique' because no customer
    # asked for this. Unlike modryn_cancel() we do NOT offer the slot to the
    # waitlist: the hour is not free, the winning booking still holds it.
    cr.execute("""
        WITH ranked AS (
            SELECT id, row_number() OVER (PARTITION BY "start"
                                          ORDER BY create_date, id) AS rn
            FROM calendar_event
            WHERE %s
        )
        UPDATE calendar_event e
           SET modryn_cancelled_at = now() at time zone 'UTC',
               modryn_cancelled_by = 'boutique',
               write_date = now() at time zone 'UTC'
          FROM ranked r
         WHERE e.id = r.id AND r.rn > 1
        RETURNING e.id, e."start"
    """ % LIVE_BOOKING)
    losers = cr.fetchall()
    if not losers:
        _logger.info("modryn_portal: no duplicate live bookings, %s is safe to create",
                     ONE_LIVE_BOOKING_PER_SLOT_INDEX)
        return
    # Loud and countable on purpose: this rewrites customer-visible bookings, and
    # the only reason it ran is that a slot was double-sold. Somebody has to ring
    # those brides.
    _logger.warning(
        "modryn_portal: cancelled %d duplicate live booking(s) so that %s can be "
        "created; affected event ids and slots: %s",
        len(losers), ONE_LIVE_BOOKING_PER_SLOT_INDEX,
        ', '.join('%s@%s' % (i, s) for i, s in losers))


def _dedupe_standing_offers(cr):
    if not sql.table_exists(cr, 'modryn_day_waitlist'):
        # Fresh install: this module owns the table, so it does not exist yet.
        return
    # Earliest expiry wins, because that is the offer whose SMS went out first and
    # whose link she is most likely holding. The losers are expired rather than
    # deleted: her place in the queue is her create_date, and modryn_join revives
    # a non-live row instead of raising on _phone_day_uniq. Clearing offer_token
    # matters — a live token on a non-offered row is a claim link that would
    # bypass the state check.
    cr.execute("""
        WITH ranked AS (
            SELECT id, row_number() OVER (PARTITION BY day
                                          ORDER BY offer_expires_at, create_date, id) AS rn
            FROM modryn_day_waitlist
            WHERE state = 'offered'
        )
        UPDATE modryn_day_waitlist w
           SET state = 'expired', offer_token = NULL, offer_expires_at = NULL,
               write_date = now() at time zone 'UTC'
          FROM ranked r
         WHERE w.id = r.id AND r.rn > 1
        RETURNING w.id, w.day
    """)
    losers = cr.fetchall()
    if losers:
        # Her link now renders claim_expired, which it would have done within two
        # hours anyway — but she was told to expect it, so say who.
        _logger.warning(
            "modryn_portal: expired %d duplicate standing offer(s) so that %s can be "
            "created; affected waitlist ids and days: %s",
            len(losers), ONE_OFFER_PER_DAY_INDEX,
            ', '.join('%s@%s' % (i, d) for i, d in losers))


def dedupe(cr):
    """Clear the rows that would make a unique index un-buildable."""
    _dedupe_live_bookings(cr)
    _dedupe_standing_offers(cr)


def assert_indexes(cr):
    """Fail the deploy if a unique index silently did not get created."""
    missing = [name for name in REQUIRED_INDEXES if not sql.index_exists(cr, name)]
    if missing:
        raise RuntimeError(
            "modryn_portal: %s missing after install/upgrade. These are the only "
            "guard against selling one fitting room twice; a tenant running without "
            "them is not safe to serve. Check the _schema logger for the CREATE that "
            "failed, clean the conflicting rows, and re-run." % ', '.join(missing))


def pre_init_hook(env):
    dedupe(env.cr)


def post_init_hook(env):
    assert_indexes(env.cr)
