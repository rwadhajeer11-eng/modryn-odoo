from collections import Counter
from datetime import datetime, timedelta

import pytz
from psycopg2.errors import UniqueViolation

from odoo import _, http
from odoo.http import request

from odoo.addons.modryn_booking.models.opening_hours import SLOT_MINUTES

TZ = pytz.timezone('Asia/Jerusalem')
# The partial unique index on calendar_event that decides who owns a slot. Named
# here because a losing racer must be told apart from an unrelated constraint.
SLOT_INDEX = 'calendar_event_modryn_one_live_booking_per_slot'


class ModrynWaitlist(http.Controller):
    """Joining a full day, and claiming a place when one opens."""

    # ------------------------------------------------------------------- join
    @http.route('/waitlist/join', type='http', auth='public', website=True,
                methods=['POST'], csrf=True, sitemap=False)
    def join(self, **post):
        day = (post.get('day') or '').strip()
        try:
            day_date = datetime.strptime(day, '%Y-%m-%d').date()
        except ValueError:
            return request.redirect('/book')
        # A hand-crafted POST could otherwise park a row on a past date that no
        # cancellation will ever reach.
        if day_date <= datetime.now(TZ).date():
            return request.redirect('/book')
        # /book stops OFFERING a shut day, which is not the same as refusing one.
        # This route is public and takes a bare date: a form left open in a tab
        # before the owner declared a holiday still posts, and would park a bride
        # on a list for a day nobody will ever open. Ask the server its own
        # question rather than restating the rule — the same seam /book/submit
        # and /claim use.
        if not request.env['modryn.opening.hours'].sudo().modryn_hours_on(day_date) \
                or request.env['modryn.closure'].sudo().modryn_is_closed(day_date):
            return request.redirect('/book')
        ok, code, _entry = request.env['modryn.day.waitlist'].sudo().modryn_join(
            name=post.get('name'), phone=post.get('phone'), day=day_date,
            lang=request.env.lang)
        # Already-waiting is a success from her point of view: she asked twice,
        # and she is on the list either way.
        status = 'joined' if ok else 'invalid'
        return request.redirect('/waitlist/done?status=%s&day=%s' % (status, day))

    @http.route('/waitlist/done', type='http', auth='public', website=True, sitemap=False)
    def joined(self, status='joined', day=None, **kw):
        return request.render('modryn_portal.waitlist_joined', {
            'status': status,
            'day': day,
        })

    # ------------------------------------------------------------------ claim
    def _free_slots_on(self, day_date):
        """The hours still open on that day, newest truth at read time."""
        def _utc_at(hour):
            # Localize the local wall clock, then convert. Israel observes DST,
            # so the offset flips between +02:00 and +03:00 and any arithmetic
            # done on a UTC value ("+24h", "+8h") lands an hour out for half the
            # year — including on the transition day itself.
            #
            # Float hours, because a window may start at 9.5 — the same encoding
            # modryn.shift.template uses.
            whole = int(hour)
            naive = datetime.combine(day_date, datetime.min.time()).replace(
                hour=whole, minute=int(round((hour - whole) * 60)))
            return TZ.localize(naive).astimezone(pytz.utc).replace(tzinfo=None)

        # sudo() because /claim is public — the link holder is anonymous and
        # cannot read the owner's configuration.
        Hours = request.env['modryn.opening.hours'].sudo()
        # {hour: capacity} in one read, rather than the hours and then the seats:
        # this page is rendered on every /claim GET and every failed POST, and the
        # seat count is needed for every hour it shows.
        capacities = Hours.modryn_capacities_on(day_date)
        # Sorted keys, because the scan bounds below and the list she reads both
        # need chronological order and a dict promises neither.
        hours = sorted(capacities)
        # This copy of the grid never had a weekday filter at all, so a claim
        # link happily offered Friday hours the boutique does not sell. The
        # empty list a shut day returns IS that filter.
        if not hours:
            return []
        # Same question, asked of the date rather than the weekday: her offer
        # was texted for a Thursday the boutique has since closed for a holiday.
        # An empty list here renders the page with no times, which is the truth.
        if request.env['modryn.closure'].sudo().modryn_is_closed(day_date):
            return []
        # What the boutique can STAFF that day, on top of what the room holds.
        # None — the answer for every date nothing knows anything about, and the
        # only answer at all until modryn_roster is installed — leaves the
        # window's own capacity standing. A single-date call because this page
        # renders a single date; the list form is /book's.
        cap = Hours.modryn_daily_caps([day_date]).get(day_date)
        # This day's own window, read from the boutique's hours instead of a
        # hardcoded 10-18. Deriving it matters for the SCAN, not just the
        # display: against a fixed 10-18 a boutique opening at 12 or closing at
        # 21 would have its real bookings fall outside the window below, drop
        # out of `taken`, and be offered to a second bride.
        #
        # The upper edge is an INSTANT — the last start plus one slot — not a
        # wall-clock hour. A window closing at midnight is legal (the model
        # allows end_hour == 24, exactly as a shift template does), and that
        # made the old `hours[-1] + 1` reach 24.0, which datetime.replace(hour=)
        # rejects outright: every /claim on that weekday would 500. Adding the
        # timedelta AFTER localisation also keeps the edge DST-correct, since a
        # slot is sixty minutes of real time whatever the offset does that night.
        first_start = _utc_at(hours[0])
        after_last = _utc_at(hours[-1]) + timedelta(minutes=SLOT_MINUTES)

        Event = request.env['calendar.event'].sudo()
        # Bound the scan to the single day this renders. Unbounded it read every
        # booking the boutique will ever take — on every /claim GET and every
        # failed /claim POST — to decide eight hours.
        #
        # Both edges come from _utc_at(), the same expression the loop below
        # uses, so the bound cannot drift from what is rendered: the loop offers
        # slot STARTS, so the first start is the first instant shown and one
        # slot past the last start is the first instant not shown — half-open.
        domain = [
            ('modryn_is_booking', '=', True),
            ('start', '>=', first_start),
            ('start', '<', after_last),
        ]
        if 'modryn_cancelled_at' in Event._fields:
            domain.append(('modryn_cancelled_at', '=', False))
        # A COUNT per start, not a set of starts: with more than one seat an hour
        # the question is how many of its seats are gone, not whether the instant
        # appears at all. Counter answers 0 for an unbooked hour without inserting
        # a key, so the lookup below stays a plain read.
        taken = Counter(e.start.replace(second=0, microsecond=0) for e in Event.search(domain))

        slots = []
        now = datetime.utcnow()
        for hour in hours:
            utc = _utc_at(hour)
            # Capacity is per HOUR, never per day: the owner may take two on a
            # Thursday evening and one the rest of the week. The day's cap trims
            # that hour rather than replacing it — a rota of three does not make
            # a one-chair window seat three.
            capacity = capacities[hour] if cap is None else min(capacities[hour], cap)
            if utc <= now or taken[utc.replace(second=0, microsecond=0)] >= capacity:
                continue
            # The EFFECTIVE seat count rides along, so claim_submit sizes its
            # retry from the same read that offered the hour. The window's own
            # number here would hand the link holder a seat the rota does not
            # staff, which is the whole thing this guard exists to stop.
            slots.append({'value': utc.strftime('%Y-%m-%d %H:%M:%S'),
                          'label': Hours.modryn_hour_label(hour),
                          'capacity': capacity})
        return slots

    def _render_claim(self, offer, error=None):
        """Render the claim form with the slot list refreshed, not the stale one."""
        return request.render('modryn_portal.claim_form', {
            'offer': offer,
            'day_label': offer.day.strftime('%d.%m.%Y'),
            'slots': self._free_slots_on(offer.day),
            'error': error,
        })

    def _offer(self, token):
        offer = request.env['modryn.day.waitlist'].sudo().search(
            [('offer_token', '=', token), ('state', '=', 'offered')], limit=1)
        if not offer or (offer.offer_expires_at and offer.offer_expires_at < datetime.utcnow()):
            return None
        return offer

    @http.route('/claim/<string:token>', type='http', auth='public', website=True,
                methods=['GET'], sitemap=False)
    def claim_form(self, token, **kw):
        offer = self._offer(token)
        if not offer:
            return request.render('modryn_portal.claim_expired', {})
        request.session.touch()
        return self._render_claim(offer)

    @http.route('/claim/<string:token>', type='http', auth='public', website=True,
                methods=['POST'], csrf=True, sitemap=False)
    def claim_submit(self, token, **post):
        offer = self._offer(token)
        if not offer:
            return request.render('modryn_portal.claim_expired', {})

        slot = post.get('slot') or ''
        error = None
        start = None
        if not post.get('terms'):
            error = _("Please accept the cancellation terms")
        else:
            try:
                start = datetime.strptime(slot, '%Y-%m-%d %H:%M:%S')
            except ValueError:
                error = _("Please choose a time")

        Event = request.env['calendar.event'].sudo()
        capacity = 0
        if start and not error:
            # A valid token was once the ONLY thing checked about the posted hour.
            # The unique index does not close that either — it enforces one
            # booking per seat, not which slots exist — so one leaked claim link
            # booked any hour on any date, including a closed day months away.
            #
            # ONE regenerated offer set answers all of it: which hours the
            # boutique sells on offer.day, whether it opens at all, and how many
            # seats each hour has left. Ask the function that offers them rather
            # than restating the boutique's hours or the capacity arithmetic here,
            # where they could drift apart — and they would, the first time the
            # owner edits her week. It also keeps DST in one place:
            # _free_slots_on() localises a local wall clock, never adds hours to a
            # UTC value.
            offered = {s['value']: s['capacity'] for s in self._free_slots_on(offer.day)}
            capacity = offered.get(start.strftime('%Y-%m-%d %H:%M:%S'), 0)
            if not capacity:
                # Two reasons an hour is not on offer, each with its own sentence.
                # Live bookings on it means a normal booker took it between her
                # SMS and this click — the common case this path was built for.
                # None at all means the hour was never hers to claim.
                taken = [('modryn_is_booking', '=', True), ('start', '=', start)]
                if 'modryn_cancelled_at' in Event._fields:
                    taken.append(('modryn_cancelled_at', '=', False))
                error = (_("That time was just taken, please choose another")
                         if Event.search_count(taken) else _("Please choose a time"))

        if error:
            return self._render_claim(offer, error)

        Partner = request.env['res.partner'].sudo()
        # Which rows already hold a seat at this hour. Character for character the
        # index predicate: search() drops archived rows itself via active_test, so
        # the seats counted here are exactly the seats Postgres will police.
        seated = [('modryn_is_booking', '=', True), ('start', '=', start)]
        if 'modryn_cancelled_at' in Event._fields:
            seated.append(('modryn_cancelled_at', '=', False))

        event = None
        # The same shape as modryn_booking's /book/submit, deliberately duplicated
        # rather than shared: modryn_portal DEPENDS on modryn_booking, so a helper
        # here is not callable from there, and a helper there reaching back would
        # be a module load cycle — the same reason the code above tests
        # `in Event._fields` instead of importing.
        #
        # This path needs the guard most, not least: modryn_cancel() frees a slot
        # and texts a claim link for that day in the same call, so a /book visitor
        # and the link holder are pointed at one hour by design.
        #
        # One attempt per seat. The read is a HINT — another bride can take the
        # seat it picked before the INSERT lands, which is what the unique index
        # is for — but losing ONE seat is not losing the hour while others are
        # free, so recompute and try the next; never reuse the rejected seat, or
        # the retry loses to the same row forever. Bounded by capacity: that is
        # how many times it can happen before the hour genuinely is full, and an
        # unbounded loop under contention would spin instead of answering her.
        #
        # The read sits OUTSIDE the savepoint deliberately: it writes nothing, and
        # bailing out of a savepoint that had already created the partner would
        # release it rather than roll it back — the orphan-bride bug below, back
        # by another door.
        # Seats already refused to THIS transaction, carried in Python because a
        # re-read cannot see them: Odoo cursors run at REPEATABLE READ
        # (odoo/odoo/sql_db.py:373), so the snapshot is fixed at the request's
        # first query while the unique index validates against the live heap.
        # Re-reading after a violation returns exactly the rows that led to the
        # losing pick, so without this the loop re-picks the same seat until it
        # runs out of attempts and tells her the hour is full with seats free.
        rejected = set()
        seat = None
        for _attempt in range(capacity):
            seats = {e.modryn_slot_seat for e in Event.search(seated)} | rejected
            seat = next((s for s in range(capacity) if s not in seats), None)
            if seat is None:
                break
            try:
                # The savepoint is load-bearing. Catching the violation does not
                # undo the rejected INSERT, and an aborted transaction fails every
                # query after it — including the next attempt's seat read and the
                # _free_slots_on() read that re-renders the form below.
                with request.env.cr.savepoint():
                    # The partner belongs INSIDE the savepoint. A savepoint rolls
                    # back only what it wraps, and entering one flushes everything
                    # written before it — so a losing racer's res.partner used to
                    # survive the rollback and commit. Worse here than on /book:
                    # every claimant for one offer shares offer.phone, so N losers
                    # left N copies of the same bride.
                    partner = (Partner.search([('phone', '=', offer.phone)], limit=1)
                               or Partner.create({'name': offer.name, 'phone': offer.phone}))
                    event = Event.create({
                        'name': _("Consultation: %s") % offer.name,
                        'start': start,
                        'stop': start + timedelta(minutes=SLOT_MINUTES),
                        'partner_ids': [(6, 0, partner.ids)],
                        'modryn_is_booking': True,
                        'modryn_booking_type': 'consult',
                        'modryn_slot_seat': seat,
                        'modryn_customer_phone': offer.phone,
                        'modryn_terms_accepted_at': datetime.utcnow(),
                        'modryn_lang': request.env.lang or 'he_IL',
                    })
            except UniqueViolation as exc:
                # Only OUR slot index means "that seat is gone". A violation from
                # calendar_attendee or mail_followers is a different bug entirely,
                # and retrying on it would spin while hiding that bug.
                if exc.diag.constraint_name != SLOT_INDEX:
                    raise
                rejected.add(seat)
                continue
            break

        if not event:
            # Every seat gone — seen by the read, or lost by microseconds. Same
            # sentence the offer-set check gives above: which of the guards caught
            # the race is our problem, not hers.
            return self._render_claim(offer, _("That time was just taken, please choose another"))
        event.modryn_send_confirmation()
        offer.write({'state': 'claimed', 'offer_token': False})
        # Token, not id: that page prints her phone and her cancel token, so a
        # sequential integer made both enumerable. See book_confirmed.
        return request.redirect('/book/confirmed/%s' % event._modryn_token())
