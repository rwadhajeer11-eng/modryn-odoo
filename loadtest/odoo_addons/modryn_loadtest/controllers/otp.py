import hmac
import re
from datetime import timedelta

from odoo import fields, http
from odoo.http import request

from odoo.addons.modryn_portal.models.otp import CODE_TTL_MINUTES
from odoo.addons.modryn_portal.models.sms import normalize_il_phone

from ..models.sms_capture import P_ENABLED, P_SECRET

# Every standalone run of exactly 6 digits in a message body. These are only
# CANDIDATES — see read_code for why picking one by position does not work.
CODE_RE = re.compile(r'(?<!\d)(\d{6})(?!\d)')

# How far back to scan for the code. Small on purpose: MAX_SENDS_PER_HOUR is 3,
# so one phone cannot have many live codes, and the hash check below already
# rejects everything that is not the current one.
RECENT_SCANNED = 5


class ModrynLoadtestOtp(http.Controller):

    @http.route('/loadtest/ping', type='http', auth='public', methods=['GET'],
                csrf=False, save_session=False)
    def ping(self, secret=None, **kw):
        """Answer 200 only where this staging module is installed AND enabled
        AND the secret matches — the same three gates read_code uses, so this
        leaks nothing read_code does not already leak.

        It exists because guardTenants() had to stop requiring localtest.me when
        the ramp moved onto the production domain, and shape alone cannot then
        prove a target is not a boutique: 'lt01' is a legal slug under
        new_boutique_prod.sh's own grammar. This is the one property that is a
        CAPABILITY rather than a name, and a production boutique provably cannot
        have it — loadtest/odoo_addons is not on production's addons_path, so
        the module is not even discoverable there.

        /loadtest/otp cannot serve as that probe: every refusal there is
        deliberately the same 404, which is exactly what makes it useless as
        evidence of anything.
        """
        icp = request.env['ir.config_parameter'].sudo()
        if icp.get_param(P_ENABLED) != '1':
            return request.not_found()
        expected = icp.get_param(P_SECRET) or ''
        if not expected or not hmac.compare_digest(expected, secret or ''):
            return request.not_found()
        return request.make_json_response({'loadtest': True})

    @http.route('/loadtest/otp', type='http', auth='public', methods=['GET'],
                csrf=False, save_session=False)
    def read_code(self, phone=None, secret=None, **kw):
        """Read back the code just texted to `phone`.

        Every refusal is the SAME 404 — wrong secret, flag off, unknown number,
        no live code. An attacker who finds this route on a misconfigured host
        cannot tell it from the module not being installed, so probing it yields
        no signal about which of the three gates it failed.
        """
        icp = request.env['ir.config_parameter'].sudo()
        if icp.get_param(P_ENABLED) != '1':
            return request.not_found()

        expected = icp.get_param(P_SECRET) or ''
        # Both keys must be set. An empty stored secret would otherwise match an
        # empty query parameter, collapsing the two-key gate back into one.
        if not expected or not hmac.compare_digest(expected, secret or ''):
            return request.not_found()

        number = normalize_il_phone(phone)
        if not number:
            return request.not_found()

        Otp = request.env['modryn.otp.code'].sudo()
        record = Otp.search(
            [('phone', '=', number), ('used_at', '=', False)],
            order='create_date desc', limit=1)
        if not record or record.expires_at < fields.Datetime.now():
            return request.not_found()

        # Bounded scan; the otp row's own expiry above is the real freshness rule.
        cutoff = fields.Datetime.now() - timedelta(minutes=CODE_TTL_MINUTES)
        messages = request.env['modryn.loadtest.message'].sudo().search(
            [('phone', '=', number), ('create_date', '>=', cutoff)],
            order='id desc', limit=RECENT_SCANNED)

        # Every candidate is checked against modryn.otp.code's OWN hash, and the
        # one that matches is returned. Not "the newest 6-digit run", which is
        # what this did first and what testing killed twice over:
        #
        #  - the customer journey books before it logs in, and the confirmation
        #    goes out through send_async -> outbox -> cron drain, so it can be
        #    DELIVERED after the synchronous OTP and win a newest-first scan;
        #  - and that confirmation carries a booking link whose access token
        #    (`/b/24-09d7260700cfdc8f0d261165`) ends in a standalone 6-digit run,
        #    so position-based extraction returned 261165 for a code of 098381.
        #    Every heuristic on wording would also have failed: he_IL is the
        #    default language, so the English text in otp.py is not what ships.
        #
        # Reusing _hash means the route cannot return a wrong code at all — if it
        # answers, the answer verifies. It reads nothing back out of the hash, so
        # the stored codes stay one-way exactly as designed.
        for message in messages:
            for candidate in CODE_RE.findall(message.body or ''):
                if hmac.compare_digest(Otp._hash(number, candidate), record.code_hash):
                    return request.make_json_response({'code': candidate})
        return request.not_found()
