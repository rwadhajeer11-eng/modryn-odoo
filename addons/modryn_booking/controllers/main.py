import re
from datetime import datetime, timedelta

import pytz
from psycopg2.errors import UniqueViolation

from odoo import _, http
from odoo.http import request

# Israeli retail week: Sunday-Thursday. Python's weekday() is Mon=0..Sun=6.
OPEN_WEEKDAYS = {6, 0, 1, 2, 3}
OPEN_HOUR, CLOSE_HOUR = 10, 18
SLOT_MINUTES = 60
DAYS_AHEAD = 14
TZ = pytz.timezone('Asia/Jerusalem')

# Mirror of ONE_LIVE_BOOKING_PER_SLOT_INDEX in
# modryn_portal/models/calendar_event.py, which owns the index. Copied rather
# than imported because the dependency runs the other way — modryn_portal depends
# on this module — and importing upwards would be a load cycle.
ONE_LIVE_BOOKING_PER_SLOT_INDEX = 'calendar_event_modryn_one_live_booking_per_slot'

# Israeli mobile/landline, tolerant of spaces and dashes: 05X-XXXXXXX or +9725X...
PHONE_RE = re.compile(r'^(?:\+972|0)\d{1,2}[\d\-\s]{6,10}$')


def _norm_phone(raw):
    return re.sub(r'[\s\-]', '', (raw or '').strip())


class ModrynBooking(http.Controller):

    # ---------------------------------------------------------------- helpers
    def _slots(self):
        """Open slots for the next fortnight, minus the ones already taken.

        ponytail: a fixed Sun-Thu 10:00-18:00 grid, not an availability engine.
        Opening hours, per-window capacity, holidays and staff rosters are the
        Phase-2 booking engine (XL) — building them here would prove nothing the
        PoC needs to know.
        """
        now_local = datetime.now(TZ)
        # Bound the scan to exactly the fortnight the loop below renders. Without
        # an upper bound /book — a primary load-test page — reads every booking
        # the boutique will ever take and throws away all but 14 days of it.
        #
        # The bound is local midnight AFTER the last rendered day, converted to
        # UTC: the last slot that day starts at 17:00 local, so everything the
        # page can show falls strictly before it. NOT utcnow() + DAYS_AHEAD —
        # the loop counts days in Jerusalem local time while the column is UTC,
        # so just after local midnight (00:30, i.e. 22:30 UTC the day before)
        # that bound lands ~22 hours short of the final day's last slot. The
        # final day's bookings would drop out of the scan and be offered to a
        # second bride.
        last_day = (now_local + timedelta(days=DAYS_AHEAD)).date()
        until = TZ.localize(
            datetime.combine(last_day + timedelta(days=1), datetime.min.time())
        ).astimezone(pytz.utc).replace(tzinfo=None)
        domain = [
            ('modryn_is_booking', '=', True),
            ('start', '>=', datetime.utcnow()),
            ('start', '<', until),
        ]
        # A cancelled appointment must hand its slot back, or cancelling would
        # punish the boutique. The field arrives with modryn_portal, which
        # DEPENDS on this module — so it legitimately may not exist, and naming
        # it unconditionally would break a database without the portal.
        if 'modryn_cancelled_at' in request.env['calendar.event']._fields:
            domain.append(('modryn_cancelled_at', '=', False))
        booked = request.env['calendar.event'].sudo().search(domain)
        # A comprehension, NOT recordset.mapped(lambda): on an EMPTY recordset
        # Odoo's mapped() calls the callable once with the recordset itself, so
        # `ev.start` is False and this raises. With zero bookings — i.e. every
        # fresh boutique — that made this page a guaranteed 500.
        taken = {ev.start.replace(second=0, microsecond=0) for ev in booked}

        days = []
        for offset in range(1, DAYS_AHEAD + 1):
            day = (now_local + timedelta(days=offset)).date()
            if day.weekday() not in OPEN_WEEKDAYS:
                continue
            times = []
            for hour in range(OPEN_HOUR, CLOSE_HOUR, SLOT_MINUTES // 60):
                naive = datetime.combine(day, datetime.min.time()).replace(hour=hour)
                # Localize then convert: Israel observes DST, so a fixed offset
                # would drift by an hour for half the year.
                utc = TZ.localize(naive).astimezone(pytz.utc).replace(tzinfo=None)
                if utc.replace(second=0, microsecond=0) in taken:
                    continue
                times.append({'value': utc.strftime('%Y-%m-%d %H:%M:%S'),
                              'label': '%02d:00' % hour})
            # A day with no free hours is still shown — with a waitlist form
            # instead of a time picker. Hiding it would mean she never learns
            # she could have been first in line.
            days.append({'date': day, 'times': times, 'full': not times})
        return days

    def _organizer(self):
        """The internal user who owns the boutique's calendar, or None.

        WHY this has to be looked up at all: sudo() elevates PRIVILEGES but
        deliberately leaves env.user alone, so calendar.event's user_id default
        (lambda self: self.env.user) still resolves to the anonymous website
        user on a public route. Every booking came out organised by login
        'public'. Elevating access is not the same as changing identity, and
        the organizer is identity.

        The owner group is resolved by xmlid, defensively: modryn_staff depends
        on modryn_booking, so depending on it back would be a load cycle — the
        group legitimately may not exist in this database.
        """
        env = request.env
        owner_group = env.ref('modryn_staff.group_boutique_owner', raise_if_not_found=False)
        internal_group = env.ref('base.group_user', raise_if_not_found=False)
        if owner_group and internal_group:
            # all_group_ids, not group_ids: group_ids holds only the groups
            # assigned directly, and an owner inherits base.group_user through
            # the internal-user template rather than by explicit assignment.
            owner = env['res.users'].sudo().search([
                ('active', '=', True),
                ('all_group_ids', 'in', owner_group.ids),
                ('all_group_ids', 'in', internal_group.ids),
            ], limit=1, order='id')
            if owner:
                return owner
        # modryn_staff not installed, or no owner provisioned yet.
        return env.ref('base.user_admin', raise_if_not_found=False)

    def _render_form(self, dress=None, variant=None, errors=None, values=None):
        return request.render('modryn_booking.booking_form', {
            'days': self._slots(),
            'dress': dress,
            'variant': variant,
            'variants': dress.product_variant_ids if dress else None,
            'errors': errors or {},
            'values': values or {},
        })

    # ----------------------------------------------------------------- routes
    @http.route('/book', type='http', auth='public', website=True, sitemap=True)
    def book_standalone(self, name=None, phone=None, **kw):
        """Path 2: a slot with no dress attached.

        name/phone arrive prefilled when a walk-in taps "prefer a scheduled
        visit" on her ticket — she has already typed them once today.
        """
        prefill = {k: v for k, v in (('name', name), ('phone', phone)) if v}
        return self._render_form(values=prefill)

    @http.route('/book/dress/<int:dress_id>',
                type='http', auth='public', website=True, sitemap=False)
    def book_dress(self, dress_id, variant_id=None, **kw):
        """Path 1: entered from a product page, the dress rides along.

        A plain <int:> and an explicit sudo()+is_published check, NOT the
        <model("product.template")> converter: that converter runs the public
        user's ACL against product.template and answers a bare 403 (no
        traceback, no log line) for an ordinary published dress. Checking
        publication ourselves is both the fix and the rule we actually want —
        only a published dress is bookable.
        """
        dress = request.env['product.template'].sudo().browse(dress_id).exists()
        if not dress or not dress.is_published:
            return request.not_found()

        variant = None
        if variant_id:
            variant = request.env['product.product'].sudo().browse(int(variant_id)).exists()
            # Never trust an id from the query string to belong to this dress.
            if variant and variant.product_tmpl_id != dress:
                variant = None
        return self._render_form(dress=dress, variant=variant)

    @http.route('/book/submit', type='http', auth='public', website=True,
                methods=['POST'], csrf=True)
    def book_submit(self, **post):
        errors = {}
        name = (post.get('name') or '').strip()
        phone = _norm_phone(post.get('phone'))
        slot = post.get('slot') or ''
        dress_id = post.get('dress_id')
        variant_id = post.get('variant_id')

        dress = None
        if dress_id:
            dress = request.env['product.template'].sudo().browse(int(dress_id)).exists()

        if not name:
            errors['name'] = _("Please enter your full name")
        if not PHONE_RE.match(phone):
            errors['phone'] = _("Please enter a valid phone number")
        # The terms checkbox is enforced HERE, server-side. A `required` attribute
        # on the input is a UI courtesy, not a control.
        if not post.get('terms'):
            errors['terms'] = _("Please accept the cancellation terms")

        start = None
        if not slot:
            errors['slot'] = _("Please choose a time")
        else:
            try:
                start = datetime.strptime(slot, '%Y-%m-%d %H:%M:%S')
            except ValueError:
                errors['slot'] = _("That time isn't valid")

        variant = None
        if variant_id:
            variant = request.env['product.product'].sudo().browse(int(variant_id)).exists()
            if dress and variant and variant.product_tmpl_id != dress:
                variant = None
        if dress and not variant:
            errors['variant'] = _("Please choose a size")

        if start and not errors:
            # KEPT, but demoted: this is now a UX affordance, not the guarantee.
            # It catches the common case — the form was rendered minutes ago and
            # somebody has taken the slot since — and answers with a sentence
            # instead of a collision. Correctness belongs to the partial unique
            # index on calendar_event (modryn_portal), because a read-then-write
            # check can never close the window between the read and the write.
            taken_domain = [('modryn_is_booking', '=', True), ('start', '=', start)]
            # A cancelled booking holds nothing. This guard used to disagree with
            # _slots(): the freed time was offered on the form and then rejected
            # here as "just taken", so a cancelled slot could never be rebooked
            # by anyone — which would also have silently broken the waitlist.
            if 'modryn_cancelled_at' in request.env['calendar.event']._fields:
                taken_domain.append(('modryn_cancelled_at', '=', False))
            if request.env['calendar.event'].sudo().search_count(taken_domain):
                errors['slot'] = _("That time was just taken, please choose another")
            # Nothing above asks whether this hour EXISTS. The unique index does
            # not either — it enforces one booking per slot, not which slots the
            # boutique sells — so a crafted POST booked 03:00 on a closed Saturday
            # eight months out and got a confirmation SMS for it.
            #
            # The honest test is "is this a value the server itself just offered",
            # so ask the same function that offers them rather than restating
            # OPEN_WEEKDAYS/OPEN_HOUR/DAYS_AHEAD here where they could drift apart.
            # That also keeps the DST handling in one place: _slots() derives every
            # candidate by localising a local wall clock, never by UTC arithmetic.
            # Runs AFTER the taken check because _slots() omits taken hours too,
            # and "just taken" is the more useful sentence when both apply.
            elif start.strftime('%Y-%m-%d %H:%M:%S') not in {
                t['value'] for d in self._slots() for t in d['times']
            }:
                errors['slot'] = _("That time isn't valid")

        if errors:
            return self._render_form(dress=dress, variant=variant, errors=errors, values=post)

        Partner = request.env['res.partner'].sudo()
        vals = {
            'name': (_("Fitting: %s") % dress.name) if dress else (_("Consultation: %s") % name),
            'start': start,
            'stop': start + timedelta(minutes=SLOT_MINUTES),
            'modryn_is_booking': True,
            'modryn_booking_type': 'dress' if dress else 'consult',
            'modryn_variant_id': variant.id if variant else False,
            'modryn_customer_phone': phone,
            'modryn_terms_accepted_at': datetime.utcnow(),
        }
        # Named explicitly because sudo() below does NOT change env.user — see
        # _organizer(). Omitted rather than set to False when there is no
        # candidate at all, so calendar.event keeps its own default.
        organizer = self._organizer()
        if organizer:
            vals['user_id'] = organizer.id
        try:
            # The savepoint is load-bearing, not decoration. Catching the
            # UniqueViolation turns the losing racer's 500 into a message, but it
            # does NOT undo the rejected INSERT — and an aborted transaction
            # fails every query issued after it, including the _slots() read that
            # re-renders the form below. Same bug floor.py's set_room() already
            # had to fix.
            with request.env.cr.savepoint():
                # The partner belongs INSIDE the savepoint. A savepoint rolls back
                # only what it wraps, and entering one flushes everything written
                # before it — so a losing racer's res.partner used to survive the
                # rollback and commit. One orphan bride per lost race, 44 of them
                # in the last concurrency run. Before the savepoint existed the
                # exception took the whole request cursor down and nothing
                # survived; the fix for the 500 is what let these leak.
                partner = Partner.search([('phone', '=', phone)], limit=1) or Partner.create({
                    'name': name, 'phone': phone,
                })
                vals['partner_ids'] = [(6, 0, partner.ids)]
                event = request.env['calendar.event'].sudo().create(vals)
        except UniqueViolation as exc:
            # Only OUR index means "slot taken". create() also writes
            # calendar_attendee and mail_followers, and either can raise
            # UniqueViolation for reasons no change of time will fix — telling
            # her to pick another hour would send her round a loop forever while
            # hiding a real bug. Anything else is a 500, which is honest.
            if exc.diag.constraint_name != ONE_LIVE_BOOKING_PER_SLOT_INDEX:
                raise
            # She lost the slot by microseconds. Deliberately the same sentence
            # the pre-check gives: which of the two guards caught it is our
            # problem, not hers.
            errors['slot'] = _("That time was just taken, please choose another")
            return self._render_form(dress=dress, variant=variant, errors=errors, values=post)
        # Tell her it worked, in the language she booked in. Guarded by a
        # field check because modryn_portal owns comms and depends on THIS
        # module — the reverse dependency would be a load cycle.
        if 'modryn_lang' in event._fields:
            event.modryn_lang = request.env.lang or 'he_IL'
            event.modryn_send_confirmation()

        return request.redirect('/book/confirmed/%s' % self._modryn_ref(event))

    @staticmethod
    def _modryn_ref(event):
        """How this booking is addressed in a URL: its token where one exists.

        modryn_portal owns the token and depends on THIS module, so importing it
        would be a load cycle — hence the runtime capability check, the same one
        the confirmation branch above uses for modryn_lang.
        """
        return event._modryn_token() if hasattr(event, '_modryn_token') else event.id

    @http.route('/book/confirmed/<string:ref>', type='http', auth='public', website=True,
                sitemap=False)
    def book_confirmed(self, ref, **kw):
        # Token-addressed, not <int:event_id>. This page prints the customer's
        # phone number, and since it gained an "add to calendar" link it also
        # prints her booking token — which is the credential POST /b/<token>/
        # cancel accepts. A sequential integer meant `seq 1 500` harvested both
        # for every booking in the boutique, which is exactly what
        # _modryn_token's docstring promises cannot happen.
        events = request.env['calendar.event']
        if hasattr(events, '_modryn_from_token'):
            event = events._modryn_from_token(ref)
        else:
            # No modryn_portal, so no tokens exist and nothing sensitive is
            # rendered here; the id is all there is to address it by.
            event = events.sudo().browse(int(ref)).exists() if ref.isdigit() else events.browse()
        if not event or not event.modryn_is_booking:
            return request.not_found()
        local = pytz.utc.localize(event.start).astimezone(TZ)
        return request.render('modryn_booking.booking_confirmed', {
            'event': event,
            'local_date': local.strftime('%d.%m.%Y'),
            'local_time': local.strftime('%H:%M'),
        })
