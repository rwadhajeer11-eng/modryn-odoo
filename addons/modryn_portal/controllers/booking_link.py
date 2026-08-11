from datetime import datetime

import pytz

from odoo import fields, http
from odoo.http import content_disposition, request

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
        # She arrives here straight from an SMS with no session at all, and
        # without this the buttons 400. The mechanism is NOT the one previously
        # claimed here (Odoo does send the cookie to a first-time visitor —
        # http.py sets it on `is_dirty or cookie_sid != sess.sid`); the real
        # cause is unconfirmed. See .memory/odoo-traps.md §6. touch() is
        # harmless and it works.
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

    # No website=True, and that is not a style choice. http_routing's _match
    # reads routing['website'] and returns immediately when it is falsy — no
    # lang detection, no redirect. With website=True an ar_001 frontend_lang
    # cookie would 302 to /ar/b/<token>/ics: a wasted round trip on a download.
    # It also keeps the link and the route in agreement — website's QWeb runs
    # every href through _url_for, which asks _is_multilang_url whether to
    # inject a lang prefix, and that says no for a non-website endpoint. So the
    # href stays /b/<token>/ics in all three languages, which is the only path
    # that exists.
    @http.route('/b/<string:token>/ics', type='http', auth='public',
                methods=['GET'])
    def booking_ics(self, token, **kw):
        event = request.env['calendar.event']._modryn_from_token(token)
        if not event:
            return request.not_found()
        ics = event._modryn_ics()
        if not ics:
            # vobject is a hard requirement of core Odoo, so an empty result
            # means a broken install, not a missing booking. 404 would lie about
            # the booking and bury the outage in ordinary not-found noise; a 500
            # traceback tells her nothing. 503 is the only true answer.
            return request.make_response(
                b'Calendar export is unavailable on this server.\n',
                [('Content-Type', 'text/plain; charset=utf-8')], status=503)
        return request.make_response(ics, [
            # text/calendar, not stock's application/octet-stream. The whole
            # point is a phone tap: this mimetype is what makes iOS and Android
            # hand the file to the calendar app instead of parking a blob in
            # Downloads.
            ('Content-Type', 'text/calendar; charset=utf-8'),
            ('Content-Length', len(ics)),
            # content_disposition already emits RFC 6266 filename*=UTF-8'' —
            # load-bearing, every booking name here is Hebrew and carries a colon.
            ('Content-Disposition', content_disposition('%s.ics' % event.name)),
            # The URL carries a secret token and the file changes the moment she
            # reschedules. Neither belongs in any cache.
            ('Cache-Control', 'no-store'),
        ])
