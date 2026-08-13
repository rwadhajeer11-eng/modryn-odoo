from odoo import _, http
from odoo.http import request

from odoo.addons.modryn_portal.models.sms import normalize_il_phone

from ..models.queue_entry import QUEUE_CHANNEL

# The half-finished check-in, parked between the form and the code. Popped the
# moment a ticket exists: an abandoned verification must leave no ghost for the
# next person on a shared terminal to inherit.
SESSION_PENDING = 'modryn_queue_pending'

# Staff drive the same two routes from the floor terminal. Membership decides two
# things and nothing else: which chrome the page wears, and where she lands.
STAFF_GROUP = 'modryn_staff.group_boutique_staff'


class ModrynQueue(http.Controller):

    # ------------------------------------------------------------------- shell
    def _staff_mode(self):
        """True when a signed-in staff member is driving this terminal.

        Recomputed per request, never carried in the session: the flag decides
        where the redirect lands, and a stale one would strand a bride on
        /floor. Safe when modryn_staff is absent — `_has_group` resolves the
        xmlid through `_get_group_definitions().get_id()`, which returns None
        for an unknown name, and `None in (...)` is False rather than an error.
        """
        return request.env.user.has_group(STAFF_GROUP)

    def _shell(self):
        """Which chrome this page wears. One dict, merged into every render.

        A dynamic t-call, so the staff terminal gets the real MODRYN nav — and
        keeps its way back to /floor — without a second copy of the form.
        staff_layout only resolves when modryn_staff is installed, which is the
        same condition that makes _staff_mode true.

        `staff` rides along because staff_layout ALREADY opens a `<div
        id="wrap">` (modryn_staff/views/floor_templates.xml:14). Nesting our own
        inside it would put the same DOM id on the page twice, so the template
        drops its wrapper when this is set.
        """
        staff = self._staff_mode()
        return {
            'staff': staff,
            'layout': 'modryn_staff.staff_layout' if staff else 'website.layout',
        }

    def _errors(self):
        # Built per request rather than at module level. A module-level dict
        # would need LazyTranslate: `_()` around a dictionary *lookup* hides the
        # literals from the extractor entirely (.memory/odoo-traps.md #9).
        return {
            'invalid_number': _("Please enter a valid phone number"),
            'rate_limited': _("Too many codes sent to that number. Try again in an hour."),
            'send_failed': _("We couldn't send the code. Check the number and try again."),
            'no_code': _("Start again to get a new code."),
            'too_many_attempts': _("Too many wrong codes. Start again to get a new one."),
            'expired': _("That code has expired. Start again to get a new one."),
            'wrong_code': _("That code is not right."),
        }

    def _error_for(self, key):
        return self._errors().get(key, _("Something went wrong."))

    # ---------------------------------------------------------------- check-in
    @http.route('/queue/checkin', type='http', auth='public', website=True, sitemap=False)
    def checkin_form(self, **kw):
        # CSRF is an HMAC over session.sid, and she arrives here from a QR code
        # carrying no session at all. See .memory/odoo-traps.md #6.
        request.session.touch()
        return request.render('modryn_queue_poc.checkin_form',
                              dict(self._shell(), errors={}, values={}))

    @http.route('/queue/checkin/submit', type='http', auth='public', website=True,
                methods=['POST'], csrf=True, sitemap=False)
    def checkin_submit(self, **post):
        """Validate, then text a code. No ticket exists yet."""
        name = (post.get('name') or '').strip()
        phone = (post.get('phone') or '').strip()

        errors = {}
        if not name:
            errors['name'] = _("Please enter your full name")
        # A ticket with no phone can never be texted, which is the whole
        # experience — so the number is required and validated here, not merely
        # asked for.
        if not normalize_il_phone(phone):
            errors['phone'] = _("Please enter a valid phone number")
        if errors:
            return request.render('modryn_queue_poc.checkin_form',
                                  dict(self._shell(), errors=errors, values=post))

        # Deliberately NOT short-circuiting the de-dupe here. Asking "is this
        # number already in the queue?" before the code, and redirecting to the
        # ticket it finds, is exactly the hijack this step exists to close.
        ok, error, e164 = request.env['modryn.otp.code'].sudo().issue(phone)
        if not ok:
            return request.render('modryn_queue_poc.checkin_form', dict(
                self._shell(), errors={'phone': self._error_for(error)}, values=post))

        request.session[SESSION_PENDING] = {
            'name': name,
            'phone': e164,
            'client_type': post.get('client_type') or 'bride',
        }
        return request.redirect('/queue/verify')

    # ------------------------------------------------------------------ verify
    @http.route('/queue/verify', type='http', auth='public', website=True,
                methods=['GET'], sitemap=False)
    def verify_form(self, **kw):
        pending = request.session.get(SESSION_PENDING)
        if not pending:
            return request.redirect('/queue/checkin')
        request.session.touch()
        return request.render('modryn_queue_poc.verify_form',
                              dict(self._shell(), error=None, phone=pending['phone']))

    @http.route('/queue/verify', type='http', auth='public', website=True,
                methods=['POST'], csrf=True, sitemap=False)
    def verify_submit(self, **post):
        pending = request.session.get(SESSION_PENDING)
        if not pending:
            return request.redirect('/queue/checkin')

        ok, error = request.env['modryn.otp.code'].sudo().verify(
            pending['phone'], post.get('code'))
        if not ok:
            return request.render('modryn_queue_poc.verify_form', dict(
                self._shell(), error=self._error_for(error), phone=pending['phone']))

        # Create BEFORE popping the session. If the check-in raises, the whole
        # request rolls back — including verify()'s used_at burn — so the code
        # she just typed still works on a retry.
        entry = request.env['modryn.queue.entry'].modryn_check_in(
            name=pending['name'],
            phone=pending['phone'],
            client_type=pending['client_type'],
        )
        staff_mode = self._staff_mode()
        request.session.pop(SESSION_PENDING, None)

        # The terminal goes back to the board; she keeps her own page, and
        # re-scanning later returns the same ticket rather than a rival one.
        return request.redirect('/floor' if staff_mode else '/q/%s' % entry.access_token)

    # ------------------------------------------------------------------ ticket
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
