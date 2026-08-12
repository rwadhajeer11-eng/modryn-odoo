import pytz

from odoo import fields, models

TZ = pytz.timezone('Asia/Jerusalem')

# Postgres reports this in diag.constraint_name, so every `except UniqueViolation`
# that means "slot taken" must compare against it rather than catching the class
# — calendar.event.create() also touches calendar_attendee and mail_followers,
# whose unique violations are not a scheduling problem and must not be swallowed.
# Authoritative copy; modryn_booking mirrors it in controllers/main.py because it
# cannot import from here (modryn_portal depends on modryn_booking, not the reverse).
ONE_LIVE_BOOKING_PER_SLOT_INDEX = 'calendar_event_modryn_one_live_booking_per_slot'


class CalendarEvent(models.Model):
    _inherit = 'calendar.event'

    # Cancelling never deletes. The boutique still needs to see that Tuesday
    # 14:00 was booked and dropped — no-show and cancellation history is exactly
    # what the deposit policy exists to price.
    modryn_cancelled_at = fields.Datetime(string="Cancelled at", readonly=True)
    modryn_cancelled_by = fields.Selection(
        selection=[('customer', "Customer"), ('boutique', "Boutique")],
        string="Cancelled by",
        readonly=True,
    )

    # The database decides who gets the slot, not the controller's read-then-write
    # search_count: two POSTs a millisecond apart both pass that check and both
    # create, which is two brides in one fitting room. A unique index is the only
    # guard that survives concurrency.
    #
    # Partial, on three columns, for three different reasons. modryn_is_booking:
    # staff meetings share this table and may legitimately overlap. modryn_cancelled_at:
    # a cancellation never deletes the row (see above), so a cancelled 14:00 keeps
    # its `start` forever and would block that hour for good. Note the NULL sense
    # is inverted from the reflex — cancelled is IS NOT NULL, so LIVE is IS NULL,
    # and `WHERE modryn_cancelled_at IS NOT NULL` would index exactly the rows
    # that hold nothing.
    #
    # `active IS TRUE` is the third and it is not decoration: search() defaults to
    # active_test=True, so an ARCHIVED booking is invisible to /book's slot list,
    # to both pre-checks and to the floor board — while still sitting in the index.
    # Without this condition, an owner archiving a booking instead of cancelling it
    # (or core calendar.event archiving one itself, which it does when detaching
    # recurrences — see _get_archive_values) poisons that hour permanently: it is
    # offered to every bride and every bride is told "just taken" by a row nobody
    # can see. The predicate must index exactly the set the application reads back.
    #
    # It lives here rather than in modryn_booking because this is the only class
    # where both columns are in scope: modryn_is_booking arrives with
    # modryn_booking, which modryn_portal depends on.
    #
    # modryn_slot_seat is the second key column and it is what makes capacity a
    # number the owner chooses instead of a constant nobody can see. The seat is
    # 0-based and bounded by the window's capacity, so a window taking two
    # fittings admits exactly seats 0 and 1 at that start and refuses a third —
    # at capacity 1 the index is (start, 0) for every row, i.e. exactly the
    # (start) index it replaces. Per-appointment-type DURATION is deliberately
    # NOT part of this: an overlapping 90-minute fitting cannot be expressed by
    # any unique index and needs a tstzrange EXCLUDE constraint over btree_gist.
    #
    # The attribute name is unchanged on purpose — the index NAME derives from
    # it, and that name is the literal three separate `except UniqueViolation`
    # handlers compare `exc.diag.constraint_name` against.
    _modryn_one_live_booking_per_slot = models.UniqueIndex(
        "(start, modryn_slot_seat) WHERE modryn_is_booking IS TRUE"
        " AND modryn_cancelled_at IS NULL AND active IS TRUE",
        "That time was just taken, please choose another",
    )

    def write(self, vals):
        # Stock Odoo's Archive button on a live booking used to be worse than the
        # bug the `active IS TRUE` index term fixed. The archived row still reads
        # modryn_is_booking IS TRUE AND modryn_cancelled_at IS NULL — a live
        # booking by this module's own definition — but active=false hides it from
        # every reader that uses search()'s default active_test: the 24h reminder
        # cron, /my/bookings, the floor board and /book's slot list. It also leaves
        # the index, so the hour is resold. One click, two brides, one fitting room.
        #
        # Archiving a booking IS cancelling it, so say so before the archive lands:
        # the row leaves the index honestly, stays visible as history, and the
        # freed hour reaches the waitlist through the one path that already knows
        # how to offer it.
        #
        # Any falsy `active`, not just False: the index predicate is `active IS
        # TRUE`, so NULL and 0 hide the row exactly as False does.
        #
        # Cannot recurse: modryn_cancel() writes modryn_cancelled_at only, never
        # `active`, so the inner write fails this test — and by then the filter
        # would exclude the record anyway.
        #
        # Core's own archives are unaffected. Core self-archives calendar events
        # in five places (create()'s detached recurrences, _update_future_events,
        # and three inside action_mass_archive / rewrite_recurrence) and every one
        # ends at a write of `active: False`. This override never blocks, delays or
        # rewrites that write — super() always runs with the caller's vals
        # untouched, so archiving still happens exactly when core asks. It only
        # ADDS a stamp, and only to rows carrying modryn_is_booking, which core's
        # own meetings and recurrences never carry. Exercised on noga (rolled
        # back): a plain event, a 3-occurrence recurrence through
        # action_mass_archive('all_events'), and a future_events recurrence edit
        # all archive unchanged and unstamped.
        #
        # The one case this DOES change: a modryn booking that someone made
        # recurring in the backend. Core archiving its occurrences would now stamp
        # and waitlist-offer each one. Nothing creates such a record today — both
        # controllers create single events — and cancelling an occurrence core is
        # discarding is the right reading anyway, but it is a behaviour change, not
        # a no-op, and narrowing further (e.g. `not e.recurrence_id`) would reopen
        # the hole for exactly the recurring bookings hardest to see.
        #
        # Only FUTURE bookings are cancelled. modryn_cancel() offers the freed day
        # to the waitlist, and a day that has already happened has nothing to hand
        # back: tidying away last season's completed fittings would text every
        # waiting customer "a place has opened up on 11.07.2026" and send her to a
        # claim form with zero selectable hours. Both existing cancel routes guard
        # the past the same way (controllers/portal.py _owned_event and
        # controllers/booking_link.py), so this is the third of three, not a new
        # rule. A past booking left archived-but-uncancelled is harmless: its hour
        # is never offered again and the submit-time validation refuses it.
        if 'active' in vals and not vals['active']:
            now = fields.Datetime.now()
            for event in self.filtered(
                lambda e: e.modryn_is_booking and not e.modryn_cancelled_at
                and e.start and e.start >= now
            ):
                # 'boutique': whoever reaches the backend Archive action is staff.
                event.modryn_cancel(by='boutique')
        return super().write(vals)

    def modryn_cancel(self, by='customer'):
        """Release the slot without losing the record."""
        self.ensure_one()
        if self.modryn_cancelled_at:
            return self
        self.write({
            'modryn_cancelled_at': fields.Datetime.now(),
            'modryn_cancelled_by': by,
        })
        # The freed hour belongs to whoever asked for this day first. Offering
        # here — in the one method both the portal and the reminder link call —
        # means no cancellation path can forget to do it.
        local_day = pytz.utc.localize(self.start).astimezone(TZ).date()
        self.env['modryn.day.waitlist'].sudo().modryn_offer_next(local_day)
        return self
