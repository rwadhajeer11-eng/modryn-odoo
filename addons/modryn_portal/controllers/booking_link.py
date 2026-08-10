from datetime import datetime

import pytz

from odoo import fields, http
from odoo.http import request

TZ = pytz.timezone('Asia/Jerusalem')


class ModrynBookingLink(http.Controller):
    """The one-tap page a reminder SMS points at.

    Public and token-authenticated on purpose: asking a bride to log in before
    she can say "yes I'm coming" is how reminders go unanswered. The token is a
    signed HMAC of the booking id, so it cannot be guessed or enumerated.
    """

    def _render(self, event, done=None):
        local = pytz.utc.localize(event.start).astimezone(TZ)
        variant = event.modryn_variant_id
        return request.render('modryn_portal.booking_link', {
            'token': event._modryn_token(),
            'date': local.strftime('%d.%m.%Y'),
            'time': local.strftime('%H:%M'),
            'dress': variant.product_tmpl_id.name if variant else '',
            'size': variant.product_template_attribute_value_ids[:1].name if variant else '',
            'cancelled': bool(event.modryn_cancelled_at),
            'confirmed': bool(event.modryn_confirmed_at),
            'past': event.start < datetime.utcnow(),
            'done': done,
            'terms': request.env['ir.config_parameter'].sudo().get_param(
                'modryn.cancellation_terms', ''),
        })

    @http.route('/b/<string:token>', type='http', auth='public', website=True,
                methods=['GET'], sitemap=False)
    def booking_link(self, token, **kw):
        event = request.env['calendar.event']._modryn_from_token(token)
        if not event:
            return request.not_found()
        # CSRF is an HMAC over session.sid, and Odoo only sends the cookie when
        # the session is dirty — she arrives here straight from an SMS with no
        # session at all, so without this the buttons 400.
        request.session.touch()
        return self._render(event)

    @http.route('/b/<string:token>/confirm', type='http', auth='public', website=True,
                methods=['POST'], csrf=True, sitemap=False)
    def booking_confirm(self, token, **post):
        event = request.env['calendar.event']._modryn_from_token(token)
        if not event:
            return request.not_found()
        if not event.modryn_cancelled_at and not event.modryn_confirmed_at:
            event.sudo().modryn_confirmed_at = fields.Datetime.now()
        return self._render(event, done='confirmed')

    @http.route('/b/<string:token>/cancel', type='http', auth='public', website=True,
                methods=['POST'], csrf=True, sitemap=False)
    def booking_cancel(self, token, **post):
        event = request.env['calendar.event']._modryn_from_token(token)
        if not event:
            return request.not_found()
        # Cancelling a fitting that already happened would rewrite history, and
        # frees a slot nobody can use.
        if not event.modryn_cancelled_at and event.start >= datetime.utcnow():
            event.sudo().modryn_cancel(by='customer')
        return self._render(event, done='cancelled')
