from odoo import _, http
from odoo.http import request

from ..models.queue_entry import QUEUE_CHANNEL


class ModrynQueue(http.Controller):

    @http.route('/queue/checkin', type='http', auth='public', website=True, sitemap=False)
    def checkin_form(self, **kw):
        # CSRF is an HMAC over session.sid and the cookie is only sent when the
        # session is dirty — she arrives here from a QR code with no session.
        request.session.touch()
        return request.render('modryn_queue_poc.checkin_form', {'errors': {}, 'values': {}})

    @http.route('/queue/checkin/submit', type='http', auth='public', website=True,
                methods=['POST'], csrf=True)
    def checkin_submit(self, **post):
        name = (post.get('name') or '').strip()
        errors = {}
        if not name:
            errors['name'] = _("Please enter your full name")
        # A ticket with no phone can never be texted, which is the whole
        # experience — so the number is required and validated here, not merely
        # asked for.
        phone = (post.get('phone') or '').strip()
        from odoo.addons.modryn_portal.models.sms import normalize_il_phone
        if not normalize_il_phone(phone):
            errors['phone'] = _("Please enter a valid phone number")
        if errors:
            return request.render('modryn_queue_poc.checkin_form',
                                  {'errors': errors, 'values': post})

        entry = request.env['modryn.queue.entry'].modryn_check_in(
            name=name, phone=phone, client_type=post.get('client_type') or 'bride')
        # Straight to her own page — she keeps the link, and re-scanning later
        # returns the same ticket rather than a rival one.
        return request.redirect('/q/%s' % entry.access_token)

    @http.route('/q/<string:token>', type='http', auth='public', website=True, sitemap=False)
    def ticket(self, token, **kw):
        """Her private ticket.

        Deliberately shows NO position number and no hint that staff accepted
        or held her: a premium boutique's line is invisible machinery. She sees
        one of three warm states, and a way out if today is not the day.
        """
        entry = request.env['modryn.queue.entry'].sudo().search(
            [('access_token', '=', token)], limit=1)
        if not entry:
            return request.not_found()

        # "Next" is a fact about the line, computed on read — never stored,
        # because a stored flag is wrong the moment anyone ahead is served.
        first = request.env['modryn.queue.entry'].sudo().search(
            [('state', '=', 'waiting')], order='create_date asc', limit=1)
        return request.render('modryn_queue_poc.ticket', {
            'entry': entry,
            'is_next': entry.state == 'waiting' and first and first.id == entry.id,
            'stylist': entry.modryn_employee_id.name if 'modryn_employee_id' in entry._fields else '',
            'book_url': '/book?name=%s&phone=%s' % (entry.name or '', entry.phone or ''),
        })

    @http.route('/queue/sign', type='http', auth='public', website=True, sitemap=False)
    def queue_sign(self, **kw):
        """The printable lounge sign: a QR code pointing at the check-in form.

        The barcode image is Odoo's own /report/barcode endpoint — no qrcode
        library call, no controller of ours.
        """
        base = request.httprequest.host_url.rstrip('/')
        return request.render('modryn_queue_poc.queue_sign', {
            'checkin_url': '%s/queue/checkin' % base,
        })

    @http.route('/queue/channel', type='json', auth='user')
    def queue_channel(self):
        return {'channel': QUEUE_CHANNEL}
