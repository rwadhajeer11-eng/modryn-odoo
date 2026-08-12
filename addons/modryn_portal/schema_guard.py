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

# The slot column that turns the slot index from "one booking per hour" into
# "capacity bookings per hour". The slot index alone gets a second, stricter
# check than the two above, and only for this one substring, because it is the
# one index here whose definition CHANGED under an unchanged name. Existence is
# no longer proof: a surviving (start) index passes REQUIRED_INDEXES while
# pinning every window to one fitting — an owner would set capacity to 2, see it
# saved, and watch the second bride be told the hour was just taken.
SEAT_COLUMN = 'modryn_slot_seat'


def _ensure_slot_seat(cr):
    """Make `modryn_slot_seat` a filled, NOT NULL column before the index needs it.

    The column is modryn_booking's field, not this module's, so on a plain
    `-u modryn_portal` that module's _auto_init never runs and the column simply
    would not exist when the widened index is applied. Creating it here makes the
    upgrade self-sufficient whatever set of modules the deploy happens to name;
    _auto_init skips columns that already exist (fields.py:update_db_column), so
    a run that DOES include modryn_booking is unaffected.

    Raw DDL rather than sql.create_column() because that helper writes no DEFAULT
    for anything but a boolean, and this field is `required=True`: the rows have
    to be filled before NOT NULL can hold. Every live booking today is unique on
    `start`, so seat 0 for all of them satisfies the new index by construction;
    cancelled and archived rows sit outside its predicate and cannot collide.
    """
    if sql.column_exists(cr, 'calendar_event', 'modryn_slot_seat'):
        # Still backfill: an earlier partial run may have added the column
        # without filling it, and one NULL is enough to fail SET NOT NULL.
        cr.execute("UPDATE calendar_event SET modryn_slot_seat = 0 "
                   "WHERE modryn_slot_seat IS NULL")
        # And still set the DEFAULT, so an INSTALL ends with the same schema an
        # UPGRADE does. On the install path modryn_booking's _auto_init got here
        # first, and sql.create_column() writes a DEFAULT for booleans only — so
        # without this the two paths differ in exactly the way that makes a raw
        # SQL INSERT omitting the column succeed on one and fail on the other.
        # verify.sh plants rows that way.
        cr.execute('ALTER TABLE calendar_event '
                   'ALTER COLUMN modryn_slot_seat SET DEFAULT 0')
        return
    cr.execute('ALTER TABLE calendar_event '
               'ADD COLUMN modryn_slot_seat integer NOT NULL DEFAULT 0')
    cr.execute("COMMENT ON COLUMN calendar_event.modryn_slot_seat IS 'Slot seat'")


def _drop_stale_slot_index(cr):
    """Drop a slot index that predates the seat column, so it gets rebuilt.

    Odoo usually does this itself: Index.apply_to_database drops and recreates
    when the stored COMMENT differs from the new definition. But the same method
    returns early — keeping whatever is there — when the index carries NO comment
    at all, because that is how support hand-tunes an index
    (`if db_comment == definition or (not db_comment and db_definition)`,
    odoo/odoo/orm/table_objects.py). An index built by anything other than this
    model therefore survives the widening under its own name, `assert_indexes`
    finds it present, and the deploy reports success while every window stays
    pinned to one fitting.

    Narrow on purpose: only when the definition is missing the seat column, so a
    correctly-widened index (comment or not) is never dropped and rebuilt for
    nothing.
    """
    definition, _comment = sql.index_definition(cr, ONE_LIVE_BOOKING_PER_SLOT_INDEX)
    if definition and SEAT_COLUMN not in definition:
        sql.drop_index(cr, ONE_LIVE_BOOKING_PER_SLOT_INDEX, 'calendar_event')
        _logger.info("modryn_portal: dropped pre-capacity %s so it is rebuilt over "
                     "(start, %s)", ONE_LIVE_BOOKING_PER_SLOT_INDEX, SEAT_COLUMN)


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
    # Partitioned by (start, seat), not by start alone — the pair IS the new
    # uniqueness. On every database that exists today the two are the same query,
    # because _ensure_slot_seat() has just put every row on seat 0; they diverge
    # the moment a window takes two fittings, and partitioning by start alone
    # would then cancel the perfectly legal second booking of the hour. This is
    # not a re-run guard, it is the difference between cleaning conflicts and
    # manufacturing them.
    #
    # Cancelled, never deleted — same rule modryn_cancel() follows, and the same
    # two fields, so the losing rows stay in the customer's history and on the
    # boutique's board as booked-and-dropped. 'boutique' because no customer
    # asked for this. Unlike modryn_cancel() we do NOT offer the slot to the
    # waitlist: the hour is not free, the winning booking still holds it.
    cr.execute("""
        WITH ranked AS (
            SELECT id, row_number() OVER (PARTITION BY "start", modryn_slot_seat
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


def _ensure_window_capacity(cr):
    """Make `capacity` exist on modryn_opening_hours before anything reads it.

    Same reasoning as the seat column and the same blind spot: `capacity` is
    modryn_booking's field, and Odoo marks only DEPENDENTS for upgrade, never
    dependencies (ir_module.button_upgrade). So `-u modryn_portal` alone — a
    perfectly ordinary deploy line, and the one _ensure_slot_seat exists to
    survive — would leave this column missing while every other part of the
    feature lands, and /book, /claim and /manage/hours would all 500 on a model
    they cannot read.

    Guarded on the table existing: modryn_booking could in principle not be
    installed yet on a database this hook runs against, and a missing table is
    that module's business, not ours.
    """
    if not sql.table_exists(cr, 'modryn_opening_hours'):
        return
    if not sql.column_exists(cr, 'modryn_opening_hours', 'capacity'):
        cr.execute('ALTER TABLE modryn_opening_hours '
                   'ADD COLUMN capacity integer NOT NULL DEFAULT 1')
        return
    cr.execute("UPDATE modryn_opening_hours SET capacity = 1 WHERE capacity IS NULL")
    cr.execute('ALTER TABLE modryn_opening_hours ALTER COLUMN capacity SET DEFAULT 1')


def dedupe(cr):
    """Get the table into a shape the unique indexes can be built over."""
    # Order matters: the seat column has to exist and be filled before anything
    # partitions by it, and the stale index has to go before _auto_init decides
    # it is entitled to keep it.
    _ensure_slot_seat(cr)
    _ensure_window_capacity(cr)
    _drop_stale_slot_index(cr)
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
    definition, _comment = sql.index_definition(cr, ONE_LIVE_BOOKING_PER_SLOT_INDEX)
    if SEAT_COLUMN not in (definition or ''):
        raise RuntimeError(
            "modryn_portal: %s exists but does not key on %s — it is the "
            "pre-capacity index, which admits exactly one booking per hour. Every "
            "opening-hours window would behave as capacity 1 while the owner's "
            "screen says two. Drop it and re-run the upgrade. Found: %s"
            % (ONE_LIVE_BOOKING_PER_SLOT_INDEX, SEAT_COLUMN, definition))


def pre_init_hook(env):
    dedupe(env.cr)


def post_init_hook(env):
    assert_indexes(env.cr)
