import re
from collections import Counter
from datetime import datetime, timedelta

import pytz
from psycopg2.errors import UniqueViolation

from odoo import _, http
from odoo.http import request

from ..models.opening_hours import SLOT_MINUTES

DAYS_AHEAD = 14
# The lead time that was never written down: the day loop started at offset 1,
# and that — not a comment anywhere — is what "no same-day booking" meant. Named
# so the policy is legible. Deliberately NOT owner-editable: a boutique that
# wants somebody in today already has the walk-in queue.
LEAD_DAYS = 1
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


def _utc_on(day, hour):
    """A local wall-clock float hour on `day`, as the naive UTC the column holds.

    Localize then convert. Israel observes DST, so a fixed offset — or any
    arithmetic done on a UTC value — drifts by an hour for half the year,
    including on the transition day itself.

    Float, not int: opening hours mirror modryn.shift.template, so 9.5 is a real
    window start and must land on 09:30, not 09:00.
    """
    whole = int(hour)
    naive = datetime.combine(day, datetime.min.time()).replace(
        hour=whole, minute=int(round((hour - whole) * 60)))
    return TZ.localize(naive).astimezone(pytz.utc).replace(tzinfo=None)


class ModrynBooking(http.Controller):

    # ---------------------------------------------------------------- helpers
    def _slots(self):
        """Open slots for the next fortnight, minus the ones already full.

        The grid is the boutique's own week, read from modryn.opening.hours,
        not a constant in this file. sudo() because /book is public: an
        anonymous visitor cannot read the owner's configuration, and a
        non-sudo read would make the page 403 for exactly the people it exists
        for.

        Capacity comes from the same windows, so an hour keeps being offered
        until its seats run out rather than until it is touched once. Full is
        therefore a count, not a membership test.

        ponytail: still one fixed slot length, and per-appointment DURATION is
        deliberately out of scope rather than merely unbuilt. A 90-minute
        fitting overlapping the following hour cannot be expressed by ANY
        unique index — it needs a tstzrange EXCLUDE constraint, btree_gist, and
        a grid that is no longer uniform — and a half-built version that offers
        11:00 while a fitting runs through it is worse than not shipping one.
        Staff rosters remain the Phase-2 booking engine (XL).
        """
        now_local = datetime.now(TZ)
        # Bound the scan to exactly the fortnight the loop below renders. Without
        # an upper bound /book — a primary load-test page — reads every booking
        # the boutique will ever take and throws away all but 14 days of it.
        #
        # The bound is local midnight AFTER the last rendered day, converted to
        # UTC: every hour the page can show is a wall-clock time on that day, so
        # all of it falls strictly before it — which stays true whatever hours
        # the owner sets, since a window ends at 24:00 at the latest. NOT
        # utcnow() + DAYS_AHEAD —
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
        # A COUNT per start, not a set of starts: with more than one seat an hour
        # the question stopped being "is this instant spoken for" and became "how
        # many of its seats are gone". Counter answers 0 for an hour nobody has
        # booked without inserting a key, so the lookup below stays a plain read.
        #
        # A comprehension, NOT recordset.mapped(lambda): on an EMPTY recordset
        # Odoo's mapped() calls the callable once with the recordset itself, so
        # `ev.start` is False and this raises. With zero bookings — i.e. every
        # fresh boutique — that made this page a guaranteed 500.
        taken = Counter(ev.start.replace(second=0, microsecond=0) for ev in booked)

        Hours = request.env['modryn.opening.hours'].sudo()
        # The whole week in ONE read, then a dict lookup per day. Asking
        # modryn_hours_on() inside the loop cost fourteen queries against a
        # five-row table on every render of the page whose scan bound above
        # exists precisely to keep it cheap — and three times that on a failed
        # submit, which regenerates the grid twice more.
        #
        # Each weekday maps to {hour: capacity}, so the seats come out of the
        # same read as the hours and no second query per day appears.
        # Keyed by DATE and not by weekday, so a date the owner has written her
        # own hours against answers for itself. Still two reads for the whole
        # fortnight — see modryn_capacities_over for why that matters here.
        dates = [(now_local + timedelta(days=offset)).date()
                 for offset in range(LEAD_DAYS, DAYS_AHEAD + 1)]
        by_date = Hours.modryn_capacities_over(dates)
        # And every blackout date in ONE search, for the same reason: asking
        # modryn_is_closed() inside the loop would put back the fourteen queries
        # by_weekday() was introduced to remove. The window is exactly the range
        # the loop renders, both ends inclusive.
        closed = request.env['modryn.closure'].sudo().modryn_closed_dates(
            (now_local + timedelta(days=LEAD_DAYS)).date(), last_day)
        # Part-day closures: the date stays, its blocked hours go. One search for
        # the fortnight, for the same reason as `closed` above.
        blocked_hours = request.env['modryn.closure'].sudo().modryn_closed_hours(
            (now_local + timedelta(days=LEAD_DAYS)).date(), last_day)
        # What the boutique can STAFF, on top of what the room can hold. Empty
        # unless something is installed that knows — modryn_roster caps a date by
        # the stylists its published rota puts on the floor. Asked once for the
        # whole fortnight, like the two reads above and for the same reason.
        #
        # A date missing from this mapping is UNCAPPED, which is the answer for
        # every date the rota has nothing to say about. It is never 0.
        caps = Hours.modryn_daily_caps(dates)
        days = []
        for day in dates:
            hours = by_date.get(day, {})
            # No hours means shut, and a SHUT day is not a FULL one. A full day
            # is still rendered below, with a waitlist form, because she should
            # learn she could be first in line. A day the boutique does not open
            # has nothing to offer and nothing to queue for, so it does not
            # appear at all — which is also the weekday filter, now that the
            # week is the owner's rather than a constant here.
            #
            # A blackout date is the same answer arrived at differently: the
            # weekday is open, this particular date is not. Yom Kippur must
            # vanish exactly as Saturday does — offering it with a waitlist form
            # would invite her to queue for a day nobody is coming in.
            if not hours or day in closed:
                continue
            cap = caps.get(day)
            times = []
            # sorted(), so the order she reads is chronological whatever order
            # the model happened to build its dict in — a page that lists 14:00
            # above 11:00 reads as a bug even when every hour on it is right.
            day_blocked = blocked_hours.get(day, ())
            for hour, capacity in sorted(hours.items()):
                # Half-open on purpose: a closure "from 14:00" takes 14:00 itself
                # and a closure "until 14:00" gives it back, so two adjacent
                # closures can never both claim the same slot or both drop it.
                if any(lo <= hour < hi for lo, hi in day_blocked):
                    continue
                utc = _utc_on(day, hour)
                # Capacity is per HOUR, never boutique-wide: a shop that takes
                # two on a Thursday evening and one the rest of the week is the
                # ordinary case this exists for. The day's cap trims that hour
                # rather than replacing it — a rota of three does not make a
                # one-chair window seat three.
                seats = capacity if cap is None else min(capacity, cap)
                if taken[utc.replace(second=0, microsecond=0)] >= seats:
                    continue
                # The EFFECTIVE seat count rides along, so /book/submit sizes its
                # retry from the same read that offered the hour. Passing the
                # window's number here instead would hand a bride a seat the rota
                # does not staff — the guard would exist and change nothing.
                times.append({'value': utc.strftime('%Y-%m-%d %H:%M:%S'),
                              'label': Hours.modryn_hour_label(hour),
                              'capacity': seats})
            # A day with no free hours is still shown — with a waitlist form
            # instead of a time picker. Hiding it would mean she never learns
            # she could have been first in line. "No free hours" now means every
            # hour is at capacity, which is the same predicate as before for a
            # boutique that seats one.
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
            # Empty when the boutique has written no list: the form then does
            # not ask, rather than asking a question with no answers in it.
            'customer_kinds': request.env['modryn.customer.kind'].sudo().search([]),
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

        # WHO SHE IS, checked against the boutique's own list and not against
        # anything this module decided. A shop that only sees brides has one
        # line in that list, so "a bridesmaid" is not something the form can be
        # made to accept by editing the page - the id has to be one of hers.
        kinds = request.env['modryn.customer.kind'].sudo().search([])
        kind = None
        if kinds:
            raw_kind = post.get('customer_kind') or ''
            kind = kinds.filtered(lambda k: str(k.id) == raw_kind)[:1]
            if not kind:
                errors['customer_kind'] = _("Please say who is coming")

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

        capacity = 0
        if start and not errors:
            # ONE regenerated offer set answers every question the form asked:
            # which hours the boutique sells, which dates it opens, and how many
            # seats this hour has left. Restating any of that here — the opening
            # hours, DAYS_AHEAD, or now the capacity arithmetic — is what lets the
            # form and the submit drift apart, and they would drift the moment the
            # owner edits her week. Asking the same function that offers them keeps
            # them true by construction, and keeps DST in one place: _slots()
            # derives every candidate by localising a local wall clock, never by
            # UTC arithmetic.
            #
            # KEPT but demoted, as before: this is a UX affordance, not the
            # guarantee. A read-then-write check can never close the window
            # between the read and the write. Correctness belongs to the partial
            # unique index on calendar_event (modryn_portal) and to the seat retry
            # below.
            offered = {t['value']: t['capacity']
                       for d in self._slots() for t in d['times']}
            capacity = offered.get(start.strftime('%Y-%m-%d %H:%M:%S'), 0)
            if not capacity:
                # Two different reasons an hour is not on offer, and each earns
                # its own sentence. A live booking on it means she was seconds
                # late — the common case, a form rendered minutes ago. None at all
                # means the hour was never for sale: the index does not police
                # that either (it enforces one booking per seat, not which slots
                # the boutique sells), so a crafted POST booked 03:00 on a closed
                # Saturday eight months out and got a confirmation SMS for it.
                taken_domain = [('modryn_is_booking', '=', True), ('start', '=', start)]
                # A cancelled booking holds nothing. This guard used to disagree
                # with _slots(): the freed time was offered on the form and then
                # rejected here as "just taken", so a cancelled slot could never
                # be rebooked by anyone — which would also have silently broken
                # the waitlist.
                if 'modryn_cancelled_at' in request.env['calendar.event']._fields:
                    taken_domain.append(('modryn_cancelled_at', '=', False))
                errors['slot'] = (
                    _("That time was just taken, please choose another")
                    if request.env['calendar.event'].sudo().search_count(taken_domain)
                    else _("That time isn't valid"))

        if errors:
            return self._render_form(dress=dress, variant=variant, errors=errors, values=post)

        Partner = request.env['res.partner'].sudo()
        Event = request.env['calendar.event'].sudo()
        vals = {
            'name': (_("Fitting: %s") % dress.name) if dress else (_("Consultation: %s") % name),
            'start': start,
            'stop': start + timedelta(minutes=SLOT_MINUTES),
            'modryn_is_booking': True,
            'modryn_booking_type': 'dress' if dress else 'consult',
            'modryn_variant_id': variant.id if variant else False,
            'modryn_customer_phone': phone,
            'modryn_customer_kind_id': kind.id if kind else False,
            'modryn_terms_accepted_at': datetime.utcnow(),
        }
        # Named explicitly because sudo() below does NOT change env.user — see
        # _organizer(). Omitted rather than set to False when there is no
        # candidate at all, so calendar.event keeps its own default.
        organizer = self._organizer()
        if organizer:
            vals['user_id'] = organizer.id

        # Which rows already hold a seat at this hour. Character for character the
        # index predicate: search() drops archived rows itself via active_test, so
        # the seats counted here are exactly the seats Postgres will police.
        seated_domain = [('modryn_is_booking', '=', True), ('start', '=', start)]
        if 'modryn_cancelled_at' in Event._fields:
            seated_domain.append(('modryn_cancelled_at', '=', False))

        event = None
        # One attempt per seat. The read below is a HINT and nothing more —
        # between it and the INSERT another bride can take the seat it picked, and
        # that is precisely what the unique index is for. But losing ONE seat is
        # not losing the hour while others are free, so recompute and try the next
        # one; never reuse the seat the violation just rejected, or the retry
        # loses to the same row forever. Bounded by capacity because that is how
        # many times it can happen before the hour genuinely is full, and an
        # unbounded loop under contention would spin instead of answering her.
        #
        # The read sits OUTSIDE the savepoint on purpose: it writes nothing, and
        # bailing out of a savepoint that had already created the partner would
        # release it rather than roll it back — the orphan-bride bug below, back
        # by another door.
        # Seats this transaction has already had refused. Carried in PYTHON on
        # purpose: Odoo runs every cursor at REPEATABLE READ
        # (odoo/odoo/sql_db.py:373), so the snapshot was fixed at this request's
        # first query and re-reading after a violation returns the SAME rows the
        # first read did. The unique index, by contrast, validates against the
        # live heap. Without this set the loop would re-pick the seat it just
        # lost on every attempt, burn all of them, and tell a bride the hour was
        # full while the seat beside her sat empty.
        rejected = set()
        for _attempt in range(capacity):
            seats = {ev.modryn_slot_seat for ev in Event.search(seated_domain)} | rejected
            seat = next((s for s in range(capacity) if s not in seats), None)
            if seat is None:
                break
            vals['modryn_slot_seat'] = seat
            try:
                # The savepoint is load-bearing, not decoration. Catching the
                # UniqueViolation turns the losing racer's 500 into a message, but
                # it does NOT undo the rejected INSERT — and an aborted transaction
                # fails every query issued after it, including the next attempt's
                # seat read and the _slots() read that re-renders the form below.
                # Same bug floor.py's set_room() already had to fix.
                with request.env.cr.savepoint():
                    # The partner belongs INSIDE the savepoint. A savepoint rolls
                    # back only what it wraps, and entering one flushes everything
                    # written before it — so a losing racer's res.partner used to
                    # survive the rollback and commit. One orphan bride per lost
                    # race, 44 of them in the last concurrency run. Before the
                    # savepoint existed the exception took the whole request cursor
                    # down and nothing survived; the fix for the 500 is what let
                    # these leak.
                    partner = Partner.search([('phone', '=', phone)], limit=1) or Partner.create({
                        'name': name, 'phone': phone,
                    })
                    vals['partner_ids'] = [(6, 0, partner.ids)]
                    event = Event.create(vals)
            except UniqueViolation as exc:
                # Only OUR index means "seat taken". create() also writes
                # calendar_attendee and mail_followers, and either can raise
                # UniqueViolation for reasons no retry will ever fix — looping on
                # those would spin while hiding a real bug. Anything else is a
                # 500, which is honest.
                if exc.diag.constraint_name != ONE_LIVE_BOOKING_PER_SLOT_INDEX:
                    raise
                rejected.add(vals['modryn_slot_seat'])
                continue
            break

        if not event:
            # Every seat gone — either the read saw it or she lost the last one by
            # microseconds. Deliberately the same sentence the offer-set check
            # gives: which of the guards caught it is our problem, not hers.
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
