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
        hours = Hours.modryn_hours_on(day_date)
        # This copy of the grid never had a weekday filter at all, so a claim
        # link happily offered Friday hours the boutique does not sell. The
        # empty list a shut day returns IS that filter.
        if not hours:
            return []
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
        taken = {e.start.replace(second=0, microsecond=0) for e in Event.search(domain)}

        slots = []
        now = datetime.utcnow()
        for hour in hours:
            utc = _utc_at(hour)
            if utc <= now or utc.replace(second=0, microsecond=0) in taken:
                continue
            slots.append({'value': utc.strftime('%Y-%m-%d %H:%M:%S'),
                          'label': Hours.modryn_hour_label(hour)})
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
        if start and not error:
            # Between her SMS and this click a normal booker may have taken the
            # hour. Re-check now rather than trusting the page she was sent.
            taken = [('modryn_is_booking', '=', True), ('start', '=', start)]
            if 'modryn_cancelled_at' in Event._fields:
                taken.append(('modryn_cancelled_at', '=', False))
            if Event.search_count(taken):
                error = _("That time was just taken, please choose another")
            # A valid token was the ONLY thing checked about the posted hour. The
            # unique index does not close this either — it enforces one booking
            # per slot, not which slots exist — so one leaked claim link booked
            # any hour on any date, including a closed day months away.
            #
            # The honest test is "is this a value this page just offered", so ask
            # the function that offers them rather than restating the boutique's
            # hours and offer.day here where they could drift apart — and they
            # would, the first time the owner edits her week. It also
            # keeps DST in one place: _free_slots_on() localises a local wall
            # clock, never adds hours to a UTC value. Runs AFTER the taken check
            # because it omits taken hours too, and "just taken" is the more
            # useful sentence when both apply.
            elif start.strftime('%Y-%m-%d %H:%M:%S') not in {
                s['value'] for s in self._free_slots_on(offer.day)
            }:
                error = _("Please choose a time")

        if error:
            return self._render_claim(offer, error)

        Partner = request.env['res.partner'].sudo()
        try:
            # The same three lines as modryn_booking's /book/submit, deliberately
            # duplicated rather than shared: modryn_portal DEPENDS on
            # modryn_booking, so a helper here is not callable from there, and a
            # helper there reaching back would be a module load cycle — the same
            # reason the code above tests `in Event._fields` instead of importing.
            #
            # This path needs the guard most, not least: modryn_cancel() frees a
            # slot and texts a claim link for that day in the same call, so a
            # /book visitor and the link holder are pointed at one hour by design.
            #
            # The savepoint is load-bearing. Catching the violation does not undo
            # the rejected INSERT, and an aborted transaction fails every query
            # after it — including the _free_slots_on() read that re-renders the
            # form below.
            with request.env.cr.savepoint():
                # The partner belongs INSIDE the savepoint. A savepoint rolls back
                # only what it wraps, and entering one flushes everything written
                # before it — so a losing racer's res.partner used to survive the
                # rollback and commit. Worse here than on /book: every claimant
                # for one offer shares offer.phone, so N losers left N copies of
                # the same bride. Before the savepoint existed the exception took
                # the whole request cursor down and nothing survived; the fix for
                # the 500 is what let these leak.
                partner = Partner.search([('phone', '=', offer.phone)], limit=1) or Partner.create(
                    {'name': offer.name, 'phone': offer.phone})
                event = Event.create({
                    'name': _("Consultation: %s") % offer.name,
                    'start': start,
                    'stop': start + timedelta(minutes=SLOT_MINUTES),
                    'partner_ids': [(6, 0, partner.ids)],
                    'modryn_is_booking': True,
                    'modryn_booking_type': 'consult',
                    'modryn_customer_phone': offer.phone,
                    'modryn_terms_accepted_at': datetime.utcnow(),
                    'modryn_lang': request.env.lang or 'he_IL',
                })
        except UniqueViolation as exc:
            # Only OUR slot index means "that hour is gone". A violation from
            # calendar_attendee or mail_followers is a different bug entirely, and
            # telling her to pick another time would be advice she cannot act on.
            if exc.diag.constraint_name != SLOT_INDEX:
                raise
            # Same sentence the pre-check gives four lines up: which of the two
            # guards caught the race is our problem, not hers.
            return self._render_claim(offer, _("That time was just taken, please choose another"))
        event.modryn_send_confirmation()
        offer.write({'state': 'claimed', 'offer_token': False})
        # Token, not id: that page prints her phone and her cancel token, so a
        # sequential integer made both enumerable. See book_confirmed.
        return request.redirect('/book/confirmed/%s' % event._modryn_token())
