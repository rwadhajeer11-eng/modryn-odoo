from odoo import _, http
from odoo.http import request

from ..models.queue_entry import QUEUE_CHANNEL


class ModrynQueue(http.Controller):

    @http.route('/queue/checkin', type='http', auth='public', website=True, sitemap=False)
    def checkin_form(self, **kw):
        return request.render('modryn_queue_poc.checkin_form', {'errors': {}, 'values': {}})

    @http.route('/queue/checkin/submit', type='http', auth='public', website=True,
                methods=['POST'], csrf=True)
    def checkin_submit(self, **post):
        name = (post.get('name') or '').strip()
        if not name:
            return request.render('modryn_queue_poc.checkin_form', {
                'errors': {'name': _("Please enter your full name")}, 'values': post,
            })

        entry = request.env['modryn.queue.entry'].sudo().create({
            'name': name,
            'phone': (post.get('phone') or '').strip(),
            'client_type': post.get('client_type') or 'bride',
        })
        # Position is computed on read, never stored: a stored number goes stale
        # the moment anyone ahead is served.
        position = request.env['modryn.queue.entry'].sudo().search_count([
            ('state', '=', 'waiting'), ('id', '<=', entry.id),
        ])
        return request.render('modryn_queue_poc.checkin_done', {
            'entry': entry, 'position': position,
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
