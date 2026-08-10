from datetime import datetime, timedelta

import pytz

from odoo import _, http
from odoo.http import request

TZ = pytz.timezone('Asia/Jerusalem')
OPEN_HOUR, CLOSE_HOUR = 10, 18


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
        Event = request.env['calendar.event'].sudo()
        domain = [('modryn_is_booking', '=', True)]
        if 'modryn_cancelled_at' in Event._fields:
            domain.append(('modryn_cancelled_at', '=', False))
        taken = {e.start.replace(second=0, microsecond=0) for e in Event.search(domain)}

        slots = []
        now = datetime.utcnow()
        for hour in range(OPEN_HOUR, CLOSE_HOUR):
            naive = datetime.combine(day_date, datetime.min.time()).replace(hour=hour)
            utc = TZ.localize(naive).astimezone(pytz.utc).replace(tzinfo=None)
            if utc <= now or utc.replace(second=0, microsecond=0) in taken:
                continue
            slots.append({'value': utc.strftime('%Y-%m-%d %H:%M:%S'),
                          'label': '%02d:00' % hour})
        return slots

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
        return request.render('modryn_portal.claim_form', {
            'offer': offer,
            'day_label': offer.day.strftime('%d.%m.%Y'),
            'slots': self._free_slots_on(offer.day),
            'error': None,
        })

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

        if error:
            return request.render('modryn_portal.claim_form', {
                'offer': offer,
                'day_label': offer.day.strftime('%d.%m.%Y'),
                'slots': self._free_slots_on(offer.day),
                'error': error,
            })

        Partner = request.env['res.partner'].sudo()
        partner = Partner.search([('phone', '=', offer.phone)], limit=1) or Partner.create(
            {'name': offer.name, 'phone': offer.phone})
        event = Event.create({
            'name': _("Consultation: %s") % offer.name,
            'start': start,
            'stop': start + timedelta(hours=1),
            'partner_ids': [(6, 0, partner.ids)],
            'modryn_is_booking': True,
            'modryn_booking_type': 'consult',
            'modryn_customer_phone': offer.phone,
            'modryn_terms_accepted_at': datetime.utcnow(),
            'modryn_lang': request.env.lang or 'he_IL',
        })
        event.modryn_send_confirmation()
        offer.write({'state': 'claimed', 'offer_token': False})
        return request.redirect('/book/confirmed/%s' % event.id)
