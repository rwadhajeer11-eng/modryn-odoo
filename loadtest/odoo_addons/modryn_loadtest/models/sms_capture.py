import logging

from odoo import api, fields, models

from odoo.addons.modryn_portal.models.sms import normalize_il_phone

_logger = logging.getLogger(__name__)

P_ENABLED = 'modryn.loadtest.enabled'
P_SECRET = 'modryn.loadtest.secret'


class ModrynLoadtestMessage(models.Model):
    """Every SMS body this database tried to send, for the harness to read back.

    Stores the WHOLE body, not a pre-extracted code: the OTP text is translated
    (he_IL is the default language, so the English wording in otp.py is not what
    ships) and other message bodies carry 6-digit runs inside links and tokens.
    Extraction is the reader's job, where the caller knows which message it wants.
    """

    _name = 'modryn.loadtest.message'
    _description = 'Captured outbound SMS (load-test only)'
    _order = 'id desc'

    phone = fields.Char(required=True, index=True)
    body = fields.Text(required=True)


class ModrynSmsCapture(models.AbstractModel):
    _inherit = 'modryn.sms'

    @api.model
    def _send_now(self, to, body):
        """Capture, then send as normal.

        _send_now and NOT send: sends route send -> _send_now for the synchronous
        callers and send_async -> outbox -> cron drain -> _send_now for the rest
        (sms_outbox.py `_drain` calls sender._send_now). Overriding `send` would
        miss every message that went through the outbox — which includes booking
        confirmations and waitlist offers.

        Capture happens BEFORE delegating so a tenant whose Twilio credentials are
        misconfigured still yields codes: a 4xx would otherwise stop the harness
        dead with no message saying why.
        """
        if self.env['ir.config_parameter'].sudo().get_param(P_ENABLED) == '1':
            # Store the E.164 form the rest of the stack uses, so the reader can
            # look a number up by exactly the string it typed into /my/login.
            # Falling back to `to` keeps an unparseable number visible rather than
            # crashing the send it was only observing.
            self.env['modryn.loadtest.message'].sudo().create({
                'phone': normalize_il_phone(to) or to,
                'body': body,
            })
        return super()._send_now(to, body)
