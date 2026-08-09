import re
from datetime import datetime, timedelta

import pytz

from odoo import http
from odoo.http import request

# Israeli retail week: Sunday-Thursday. Python's weekday() is Mon=0..Sun=6.
OPEN_WEEKDAYS = {6, 0, 1, 2, 3}
OPEN_HOUR, CLOSE_HOUR = 10, 18
SLOT_MINUTES = 60
DAYS_AHEAD = 14
TZ = pytz.timezone('Asia/Jerusalem')

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
        booked = request.env['calendar.event'].sudo().search([
            ('modryn_is_booking', '=', True),
            ('start', '>=', datetime.utcnow()),
        ])
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
            if times:
                days.append({'date': day, 'times': times})
        return days

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
    def book_standalone(self, **kw):
        """Path 2: a slot with no dress attached."""
        return self._render_form()

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
            errors['name'] = "נא למלא שם מלא"
        if not PHONE_RE.match(phone):
            errors['phone'] = "מספר טלפון לא תקין"
        # The terms checkbox is enforced HERE, server-side. A `required` attribute
        # on the input is a UI courtesy, not a control.
        if not post.get('terms'):
            errors['terms'] = "יש לאשר את תנאי הביטול"

        start = None
        if not slot:
            errors['slot'] = "נא לבחור מועד"
        else:
            try:
                start = datetime.strptime(slot, '%Y-%m-%d %H:%M:%S')
            except ValueError:
                errors['slot'] = "מועד לא תקין"

        variant = None
        if variant_id:
            variant = request.env['product.product'].sudo().browse(int(variant_id)).exists()
            if dress and variant and variant.product_tmpl_id != dress:
                variant = None
        if dress and not variant:
            errors['variant'] = "נא לבחור מידה"

        if start and not errors:
            # Last-writer-wins is not good enough for a fitting room: re-check
            # the slot at submit time, because the form was rendered minutes ago.
            if request.env['calendar.event'].sudo().search_count([
                ('modryn_is_booking', '=', True), ('start', '=', start),
            ]):
                errors['slot'] = "המועד נתפס, נא לבחור מועד אחר"

        if errors:
            return self._render_form(dress=dress, variant=variant, errors=errors, values=post)

        Partner = request.env['res.partner'].sudo()
        partner = Partner.search([('phone', '=', phone)], limit=1) or Partner.create({
            'name': name, 'phone': phone,
        })

        event = request.env['calendar.event'].sudo().create({
            'name': ("מדידה: %s" % dress.name) if dress else ("פגישת ייעוץ: %s" % name),
            'start': start,
            'stop': start + timedelta(minutes=SLOT_MINUTES),
            'partner_ids': [(6, 0, partner.ids)],
            'modryn_is_booking': True,
            'modryn_booking_type': 'dress' if dress else 'consult',
            'modryn_variant_id': variant.id if variant else False,
            'modryn_customer_phone': phone,
            'modryn_terms_accepted_at': datetime.utcnow(),
        })
        return request.redirect('/book/confirmed/%s' % event.id)

    @http.route('/book/confirmed/<int:event_id>', type='http', auth='public', website=True,
                sitemap=False)
    def book_confirmed(self, event_id, **kw):
        event = request.env['calendar.event'].sudo().browse(event_id).exists()
        if not event or not event.modryn_is_booking:
            return request.not_found()
        local = pytz.utc.localize(event.start).astimezone(TZ)
        return request.render('modryn_booking.booking_confirmed', {
            'event': event,
            'local_date': local.strftime('%d.%m.%Y'),
            'local_time': local.strftime('%H:%M'),
        })
