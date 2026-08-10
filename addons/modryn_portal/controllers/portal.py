from datetime import datetime

import pytz

from odoo import http
from odoo.http import request
from odoo.tools.translate import LazyTranslate

from ..models.sms import normalize_il_phone

# LazyTranslate, not _(): these live in a module-level dict, so _() would run at
# import time with no request language — and worse, wrapping the LOOKUP instead
# of the literal (`_(ERRORS[key])`) hides the strings from the extractor
# entirely, so they never reach the .po and silently stay English forever.
# _lt defers the lookup to render time and still exposes the literals.
_lt = LazyTranslate(__name__)

TZ = pytz.timezone('Asia/Jerusalem')
SESSION_KEY = 'modryn_customer_partner_id'
SESSION_PHONE = 'modryn_customer_phone'

ERRORS = {
    'invalid_number': _lt("That does not look like a valid phone number."),
    'rate_limited': _lt("Too many codes requested. Please try again in an hour."),
    'send_failed': _lt("We could not send the code. Please try again shortly."),
    'no_code': _lt("Please request a code first."),
    'too_many_attempts': _lt("Too many attempts. Please request a new code."),
    'expired': _lt("That code has expired. Please request a new one."),
    'wrong_code': _lt("That code is not correct."),
}
FALLBACK_ERROR = _lt("Something went wrong.")


def phone_variants(e164):
    """Every way this number might be stored.

    The booking flow saves what the customer typed with spaces and dashes
    stripped ("0521234567"); the portal works in E.164 ("+972521234567"). Both
    have to find the same person, so match on either.
    """
    if not e164:
        return []
    variants = {e164}
    if e164.startswith('+972'):
        variants.add('0' + e164[4:])
        variants.add('972' + e164[4:])
    return list(variants)


class ModrynCustomerPortal(http.Controller):
    """Customer self-service.

    A verified customer is a partner id in the session — NOT a res.users. Brides
    are not employees; giving each one a login record would grow the user table
    forever and drag them into Odoo's seat accounting for no benefit.
    """

    # ---------------------------------------------------------------- helpers
    def _current_partner(self):
        partner_id = request.session.get(SESSION_KEY)
        if not partner_id:
            return None
        return request.env['res.partner'].sudo().browse(partner_id).exists()

    def _bookings_for(self, partner):
        phones = phone_variants(request.session.get(SESSION_PHONE))
        domain = ['&', ('modryn_is_booking', '=', True),
                  '|', ('partner_ids', 'in', partner.ids),
                  ('modryn_customer_phone', 'in', phones)]
        events = request.env['calendar.event'].sudo().search(domain, order='start desc')

        now = datetime.utcnow()
        upcoming, past = [], []
        for event in events:
            variant = event.modryn_variant_id
            row = {
                'id': event.id,
                'date': pytz.utc.localize(event.start).astimezone(TZ).strftime('%d.%m.%Y'),
                'time': pytz.utc.localize(event.start).astimezone(TZ).strftime('%H:%M'),
                'dress': variant.product_tmpl_id.name if variant else '',
                'size': variant.product_template_attribute_value_ids[:1].name if variant else '',
                'cancelled': bool(event.modryn_cancelled_at),
            }
            if event.modryn_cancelled_at or event.start < now:
                past.append(row)
            else:
                upcoming.append(row)
        upcoming.reverse()
        return upcoming, past

    # ------------------------------------------------------------------ login
    @http.route('/my/login', type='http', auth='public', website=True,
                methods=['GET'], sitemap=False)
    def login_form(self, **kw):
        if self._current_partner():
            return request.redirect('/my/bookings')
        # The CSRF token is an HMAC over session.sid and Odoo only sends the
        # cookie when the session is dirty — without this, a customer whose
        # first ever request is this page gets a bare 400 on submit.
        request.session.touch()
        return request.render('modryn_portal.login', {'error': None, 'phone': ''})

    @http.route('/my/login', type='http', auth='public', website=True,
                methods=['POST'], csrf=True, sitemap=False)
    def login_submit(self, **post):
        phone = (post.get('phone') or '').strip()
        ok, error, e164 = request.env['modryn.otp.code'].sudo().issue(phone)
        if not ok:
            return request.render('modryn_portal.login', {
                'error': ERRORS.get(error, FALLBACK_ERROR), 'phone': phone,
            })
        request.session[SESSION_PHONE] = e164
        return request.redirect('/my/verify')

    # ----------------------------------------------------------------- verify
    @http.route('/my/verify', type='http', auth='public', website=True,
                methods=['GET'], sitemap=False)
    def verify_form(self, **kw):
        if not request.session.get(SESSION_PHONE):
            return request.redirect('/my/login')
        request.session.touch()
        return request.render('modryn_portal.verify', {
            'error': None, 'phone': request.session.get(SESSION_PHONE),
        })

    @http.route('/my/verify', type='http', auth='public', website=True,
                methods=['POST'], csrf=True, sitemap=False)
    def verify_submit(self, **post):
        phone = request.session.get(SESSION_PHONE)
        if not phone:
            return request.redirect('/my/login')

        ok, error = request.env['modryn.otp.code'].sudo().verify(phone, post.get('code'))
        if not ok:
            return request.render('modryn_portal.verify', {
                'error': ERRORS.get(error, FALLBACK_ERROR), 'phone': phone,
            })

        Partner = request.env['res.partner'].sudo()
        partner = Partner.search([('phone', 'in', phone_variants(phone))], limit=1)
        if not partner:
            # She verified a number we have never booked for. Create the partner
            # so the session has an identity; her booking list is simply empty.
            partner = Partner.create({'name': phone, 'phone': phone})
        request.session[SESSION_KEY] = partner.id
        return request.redirect('/my/bookings')

    # --------------------------------------------------------------- bookings
    @http.route('/my/bookings', type='http', auth='public', website=True, sitemap=False)
    def bookings(self, **kw):
        partner = self._current_partner()
        if not partner:
            return request.redirect('/my/login')
        upcoming, past = self._bookings_for(partner)
        return request.render('modryn_portal.bookings', {
            'partner': partner, 'upcoming': upcoming, 'past': past,
        })

    @http.route('/my/cancel/<int:event_id>', type='http', auth='public', website=True,
                methods=['GET'], sitemap=False)
    def cancel_confirm(self, event_id, **kw):
        partner = self._current_partner()
        if not partner:
            return request.redirect('/my/login')
        event = self._owned_event(event_id, partner)
        if not event:
            return request.not_found()
        request.session.touch()
        local = pytz.utc.localize(event.start).astimezone(TZ)
        return request.render('modryn_portal.cancel_confirm', {
            'event_id': event.id,
            'date': local.strftime('%d.%m.%Y'),
            'time': local.strftime('%H:%M'),
            'terms': request.env['ir.config_parameter'].sudo().get_param(
                'modryn.cancellation_terms', ''),
        })

    @http.route('/my/cancel/<int:event_id>', type='http', auth='public', website=True,
                methods=['POST'], csrf=True, sitemap=False)
    def cancel_submit(self, event_id, **post):
        partner = self._current_partner()
        if not partner:
            return request.redirect('/my/login')
        event = self._owned_event(event_id, partner)
        if not event:
            return request.not_found()
        event.modryn_cancel(by='customer')
        return request.redirect('/my/bookings')

    def _owned_event(self, event_id, partner):
        """Her own, still upcoming, not already cancelled — or nothing.

        Checked server-side on every call: an id in a URL is not authorisation.
        """
        event = request.env['calendar.event'].sudo().browse(event_id).exists()
        if not event or not event.modryn_is_booking or event.modryn_cancelled_at:
            return None
        if event.start < datetime.utcnow():
            return None
        phones = phone_variants(request.session.get(SESSION_PHONE))
        if partner not in event.partner_ids and event.modryn_customer_phone not in phones:
            return None
        return event

    # ----------------------------------------------------------------- logout
    @http.route('/my/logout', type='http', auth='public', website=True, sitemap=False)
    def logout(self, **kw):
        request.session.pop(SESSION_KEY, None)
        request.session.pop(SESSION_PHONE, None)
        return request.redirect('/')
