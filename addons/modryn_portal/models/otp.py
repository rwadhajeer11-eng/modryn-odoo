import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

from odoo import _, api, fields, models
from odoo.http import request

from .sms import normalize_il_phone

CODE_DIGITS = 6
CODE_TTL_MINUTES = 5
MAX_VERIFY_ATTEMPTS = 5
MAX_SENDS_PER_HOUR = 3
# Second dimension, per client IP across ALL phone numbers. The per-phone cap
# stops nuisance to one victim; only this stops one visitor turning a public
# form on a Twilio-live tenant into an SMS-bomb relay by rotating numbers.
# 30 keeps repeated local qa runs breathing (each run issues a handful from
# 127.0.0.1) while capping a public IP at 30 texts/hour. Railway runs with
# proxy_mode on, so remote_addr is the real client, not the edge proxy.
IP_MAX_SENDS_PER_HOUR = 30
# Codes older than this are pruned by cron; kept briefly past expiry so a user
# who is a minute late gets "that code expired" instead of "wrong code".
RETENTION_HOURS = 24


class ModrynOtpCode(models.Model):
    """A one-time code texted to a phone number.

    Codes are stored HASHED. A stolen database dump should not hand over live
    login codes, and nothing in the product ever needs to read one back — we
    only ever compare.
    """

    _name = 'modryn.otp.code'
    _description = 'Customer login code'
    _order = 'create_date desc'

    phone = fields.Char(required=True, index=True)
    code_hash = fields.Char(required=True)
    expires_at = fields.Datetime(required=True, index=True)
    attempts = fields.Integer(default=0)
    used_at = fields.Datetime()
    # The requesting client, for the per-IP cap. Nullable on purpose: codes
    # issued outside an HTTP request (shell, tests) carry no IP and are not
    # IP-limited.
    ip = fields.Char(index=True)

    # ------------------------------------------------------------------ crypto
    @api.model
    def _hash(self, phone, code):
        # Salted with the database secret so hashes are useless in another
        # database, and phone-bound so a code cannot be replayed for a different
        # number.
        secret = self.env['ir.config_parameter'].sudo().get_param('database.secret') or ''
        msg = ('%s|%s' % (phone, code)).encode()
        return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()

    # ------------------------------------------------------------------- issue
    @api.model
    def issue(self, raw_phone):
        """Create and text a code. Returns (ok, error_key, phone_e164, demo_code).

        demo_code is the plaintext code, returned ONLY when the SMS went to the
        server log instead of a phone ('logged' is send()'s no-provider branch)
        AND the tenant opted in with modryn.sms_demo. Both must hold: a tenant
        with working Twilio can never leak a real code onto a page, and a
        credential-less tenant shows nothing unless somebody chose demo mode.
        """
        phone = normalize_il_phone(raw_phone)
        if not phone:
            return False, 'invalid_number', None, None

        recent = self.sudo().search_count([
            ('phone', '=', phone),
            ('create_date', '>=', datetime.utcnow() - timedelta(hours=1)),
        ])
        if recent >= MAX_SENDS_PER_HOUR:
            return False, 'rate_limited', phone, None

        # request is None outside HTTP (shell, crons, tests) — those callers
        # are trusted and not IP-limited.
        ip = request.httprequest.remote_addr if request else None
        if ip:
            recent_ip = self.sudo().search_count([
                ('ip', '=', ip),
                ('create_date', '>=', datetime.utcnow() - timedelta(hours=1)),
            ])
            if recent_ip >= IP_MAX_SENDS_PER_HOUR:
                return False, 'rate_limited', phone, None

        code = ''.join(secrets.choice('0123456789') for _i in range(CODE_DIGITS))
        self.sudo().create({
            'phone': phone,
            'code_hash': self._hash(phone, code),
            'expires_at': datetime.utcnow() + timedelta(minutes=CODE_TTL_MINUTES),
            'ip': ip,
        })

        body = _("%(code)s is your MODRYN code. It expires in %(minutes)s minutes.") % {
            'code': code, 'minutes': CODE_TTL_MINUTES,
        }
        ok, detail = self.env['modryn.sms'].send(phone, body)
        if not ok:
            return False, 'send_failed', phone, None
        demo = detail == 'logged' and bool(
            self.env['ir.config_parameter'].sudo().get_param('modryn.sms_demo'))
        return True, None, phone, (code if demo else None)

    # ------------------------------------------------------------------ verify
    @api.model
    def verify(self, raw_phone, code):
        """Check a code. Returns (ok, error_key)."""
        phone = normalize_il_phone(raw_phone)
        if not phone:
            return False, 'invalid_number'

        record = self.sudo().search([
            ('phone', '=', phone), ('used_at', '=', False),
        ], order='create_date desc', limit=1)
        if not record:
            return False, 'no_code'
        if record.attempts >= MAX_VERIFY_ATTEMPTS:
            return False, 'too_many_attempts'
        if record.expires_at < datetime.utcnow():
            return False, 'expired'

        # Count the attempt BEFORE comparing, so a crash mid-verify cannot be
        # used to get unlimited free guesses.
        record.attempts += 1

        expected = record.code_hash
        given = self._hash(phone, (code or '').strip())
        if not hmac.compare_digest(expected, given):
            return False, 'wrong_code'

        # Single use: burn it so an intercepted code cannot be replayed.
        record.used_at = fields.Datetime.now()
        return True, None

    # -------------------------------------------------------------------- cron
    @api.model
    def _gc_codes(self):
        cutoff = datetime.utcnow() - timedelta(hours=RETENTION_HOURS)
        self.sudo().search([('create_date', '<', cutoff)]).unlink()
